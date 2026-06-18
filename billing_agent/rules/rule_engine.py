"""
Phase 3 — rule engine.

Evaluates every billing rule against each transaction and returns one
RuleResult per transaction. Rule values are read from the JSON files in
billing_agent/rules/data/; nothing is hard-coded in this module.

Rule order (first match wins for hard-stop rules):
  0. PROJECT_MISMATCH   tx.project_id ≠ contract SAP project — hard reject
  1. HOLD_ITEM           SAP-flagged holds — stop, no further rules
  2. POLICY_VIOLATION    alcohol / lounge / personal items — hard reject
  3. MISCODED_LABOUR     non-billable time descriptions — hard reject
  4. RATE_MISMATCH       contracted rate not used (labour)
  5. TRAVEL_TIME         50% rate / 8 hr cap (labour)
  6. CURRENCY_MISMATCH   non-USD receipt (expense)
  7. SUBCONTRACTOR_MARKUP  vendor invoice with markup not applied (expense)
  8. OVER_CAP            lodging / meal / per diem / mileage caps (expense)
                         — runs before MISSING_BACKUP so cap violations surface
                           even when the supporting receipt isn't in the store
  9. COMPOSITE_DOCUMENT  composite scan — line-level split required (expense)
 10. UNREADABLE_DOC      unreadable scan (expense)
 11. MISSING_BACKUP      expense > $25 with no linked document (expense)
 12. AMOUNT_MISMATCH     SAP amount ≠ receipt billable amount (expense)
 13. CLEAN               no exception found
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

from billing_agent import run_logger
from billing_agent.ingestion.loader import IngestionResult
from billing_agent.models.document import ReceiptDocument
from billing_agent.models.transaction import Transaction
from billing_agent.rules.models import RuleResult
from billing_agent.rules.override_resolver import apply_overrides

log = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent / "data"
_DOC_ID_RE = re.compile(r"\b((?:RC|ML|VI)-\d{3})\b")


def _load(name: str) -> dict:
    return json.loads((_DATA_DIR / name).read_text(encoding="utf-8"))


# Load rule data once at import time
_EXP   = _load("expense_caps.json")
_LAB   = _load("labour_rules.json")
_POL   = _load("policy_rules.json")
_KWS   = _load("keyword_lists.json")

_LODGING_METRO   = _EXP["lodging"]["metro_usd_per_night"]
_LODGING_OTHER   = _EXP["lodging"]["other_usd_per_night"]
_MEAL_CAP        = _EXP["meals"]["receipt_usd_per_day"]
_PERDIEM_CAP     = _EXP["meals"]["per_diem_usd_per_day"]
_RECEIPT_THRESH  = _EXP["receipt_required_above_usd"]
_MARKUP_PCT      = _EXP["subcontractor_markup_pct"]
_MILEAGE_RATE    = _EXP["mileage"]["rate_usd_per_mile"]
_ROLE_RATES      = _LAB["role_rates_usd_per_hour"]
_TRAVEL_RATE_PCT = _LAB["travel_time_billing_rate_pct"]
_TRAVEL_MAX_HRS  = _LAB["travel_time_max_hours_per_direction"]

_ALCOHOL_KWS  = set(_KWS["alcohol"])
_PERSONAL_KWS = set(_KWS["personal_items"])
_MISCODED_KWS = set(_KWS["miscoded_labour"])
_LOUNGE_KWS   = set(_KWS["airport_lounge"])
_MEAL_KWS     = set(_KWS["meals"])


# ── Public entry point ────────────────────────────────────────────────────────

def run(inputs: IngestionResult) -> List[RuleResult]:
    """Evaluate all rules for every transaction. Returns one RuleResult per tx."""
    docs_by_id: Dict[str, ReceiptDocument] = {d.doc_id: d for d in inputs.documents}
    contract_project = inputs.contract_sap_project
    results = [_evaluate(tx, docs_by_id, contract_project) for tx in inputs.transactions]
    results = apply_overrides(
        results, inputs.instructions, inputs.exceptions, inputs.transactions
    )
    _log_summary(results)
    return results


# ── Per-transaction evaluation ────────────────────────────────────────────────

def _evaluate(tx: Transaction, docs_by_id: Dict[str, ReceiptDocument], contract_project: str) -> RuleResult:
    doc  = _find_doc(tx.note, docs_by_id)
    desc = tx.description.lower()

    # 0. Project ID must match the contract SAP project
    if contract_project and tx.project_id != contract_project:
        return _make(tx, "REJECT", "PROJECT_MISMATCH", "PROJECT_MISMATCH", 0.0,
                     f"Project ID '{tx.project_id}' does not match contract project '{contract_project}'")

    # 1. SAP hold flag (stop immediately)
    if tx.hold_flag:
        return _make(tx, "HOLD", "HOLD_ITEM", "HOLD_ITEM", tx.amount,
                     f"SAP hold: {tx.hold_reason or 'unspecified'}")

    # 2. Policy violations — expense only (alcohol, lounge, personal)
    if tx.is_expense:
        if _hit(desc, _ALCOHOL_KWS):
            return _make(tx, "REJECT", "POLICY_VIOLATION", "ALCOHOL",
                         0.0, "Alcohol — never reimbursable (contract §4)")
        if _hit(desc, _LOUNGE_KWS):
            return _make(tx, "REJECT", "POLICY_VIOLATION", "AIRPORT_LOUNGE",
                         0.0, "Airport lounge — personal comfort item not reimbursable")
        if _hit(desc, _PERSONAL_KWS):
            return _make(tx, "REJECT", "POLICY_VIOLATION", "PERSONAL_ITEM",
                         0.0, "Personal item — non-reimbursable (contract §4)")

    # 3. Miscoded labour (applies to both LABOR and EXPENSE types)
    if _hit(desc, _MISCODED_KWS):
        return _make(tx, "REJECT", "MISCODED_LABOUR", "MISCODED",
                     0.0, "Non-billable time — remove from invoice")

    if tx.is_labor:
        return _eval_labour(tx)

    if tx.is_expense:
        return _eval_expense(tx, doc, desc)

    return _approve(tx, "CLEAN", "No exceptions")


def _eval_labour(tx: Transaction) -> RuleResult:
    desc = tx.description.lower()
    contracted_rate = _ROLE_RATES.get(tx.role_code)

    # Rate mismatch
    if contracted_rate is not None and abs(tx.rate - contracted_rate) > 0.01:
        approved = round(tx.quantity * contracted_rate, 2)
        return _make(tx, "FLAG", "AMOUNT_MISMATCH", "RATE_MISMATCH", approved,
                     f"Rate {tx.rate} ≠ contracted {contracted_rate} for {tx.role_code}")

    # Travel time rules (50% rate, 8-hr cap)
    if "travel" in desc and contracted_rate:
        expected_rate = round(contracted_rate * _TRAVEL_RATE_PCT, 2)
        if abs(tx.rate - expected_rate) > 0.01:
            return _make(tx, "FLAG", "AMOUNT_MISMATCH", "TRAVEL_RATE",
                         round(tx.quantity * expected_rate, 2),
                         f"Travel-time rate should be {expected_rate:.2f} "
                         f"({int(_TRAVEL_RATE_PCT*100)}% of role rate)")
        if tx.quantity > _TRAVEL_MAX_HRS:
            return _make(tx, "FLAG", "OVER_CAP", "TRAVEL_HRS_CAP",
                         round(_TRAVEL_MAX_HRS * tx.rate, 2),
                         f"Travel hours {tx.quantity} exceeds {_TRAVEL_MAX_HRS}-hr/direction cap")

    return _approve(tx, "CLEAN", "Labour approved")


def _eval_expense(tx: Transaction, doc: Optional[ReceiptDocument], desc: str) -> RuleResult:
    # 4. Currency mismatch
    if tx.currency.upper() != "USD":
        return _make(tx, "FLAG", "CURRENCY_MISMATCH", "CURRENCY", tx.amount,
                     f"Non-USD amount ({tx.amount} {tx.currency}) — FX conversion required")

    # 5. Subcontractor markup
    if _is_subcontractor(tx, doc):
        note_l = tx.note.lower()
        if "no markup" in note_l or "not applied" in note_l or "markup not" in note_l:
            billed = round(tx.amount * (1 + _MARKUP_PCT), 2)
            return _make(tx, "FLAG", "SUBCONTRACTOR_MARKUP", "MARKUP_MISSING", billed,
                         f"Markup not applied — approved = cost × {1+_MARKUP_PCT:.2f} = {billed:.2f}")

    # 6. Cap checks run before missing-backup so a cap violation is the leading
    #    flag even when the supporting receipt isn't in the document store.
    cap = _check_caps(tx, desc)
    if cap:
        return cap

    # 7. Document quality (composite / unreadable)
    if doc:
        if doc.is_composite:
            return _make(tx, "FLAG", "COMPOSITE_DOCUMENT", "COMPOSITE_DOC", tx.amount,
                         f"{doc.doc_id} is a composite scan — needs line-level split")
        if doc.is_unreadable:
            return _make(tx, "FLAG", "MISSING_BACKUP", "UNREADABLE_DOC", tx.amount,
                         f"{doc.doc_id} is unreadable — treat as missing backup")

    # 8. Missing backup (per diem and mileage are receipt-exempt per contract)
    _receipt_exempt = "per diem" in desc or tx.unit.upper() == "MILE" or "mileage" in desc
    if tx.amount > _RECEIPT_THRESH and doc is None and not _receipt_exempt:
        return _make(tx, "FLAG", "MISSING_BACKUP", "NO_RECEIPT", tx.amount,
                     f"${tx.amount:.2f} exceeds ${_RECEIPT_THRESH:.2f} threshold with no backup")

    # 9. Amount mismatch — only flag overbilling (tx > receipt billable).
    #    Under-billing is allowed: employee may be billing a line item from a folio
    #    or excluding a portion (e.g. personal charges) without a separate receipt.
    if doc:
        billable = doc.total_amount - doc.alcohol_amount
        if tx.amount > billable + 0.50:
            return _make(tx, "FLAG", "AMOUNT_MISMATCH", "AMOUNT_MISMATCH", billable,
                         f"SAP {tx.amount:.2f} > receipt billable {billable:.2f} by "
                         f"${tx.amount - billable:.2f}")

    return _approve(tx, "CLEAN", "Expense approved")


# ── Cap checks ────────────────────────────────────────────────────────────────

def _check_caps(tx: Transaction, desc: str) -> Optional[RuleResult]:
    unit = tx.unit.upper()

    # Lodging
    if unit == "NIGHT" or "hotel" in desc:
        if tx.amount > _LODGING_METRO:
            return _make(tx, "FLAG", "OVER_CAP", "LODGING_CAP", _LODGING_OTHER,
                         f"Lodging ${tx.amount:.2f} > metro cap ${_LODGING_METRO:.2f} "
                         f"(other-location cap ${_LODGING_OTHER:.2f})")
        if tx.amount > _LODGING_OTHER:
            return _make(tx, "FLAG", "OVER_CAP", "LODGING_CAP", _LODGING_OTHER,
                         f"Lodging ${tx.amount:.2f} > other-location cap ${_LODGING_OTHER:.2f}")

    # Per diem (check before meal cap — per diem in description is definitive)
    if "per diem" in desc:
        if tx.amount > _PERDIEM_CAP:
            return _make(tx, "FLAG", "OVER_CAP", "PER_DIEM_CAP", _PERDIEM_CAP,
                         f"Per diem ${tx.amount:.2f} > ${_PERDIEM_CAP:.2f}/day cap")
        return None  # per diem within cap — approve without further meal checks

    # Meal with receipt
    if _hit(desc, _MEAL_KWS):
        if tx.amount > _MEAL_CAP:
            return _make(tx, "FLAG", "OVER_CAP", "MEAL_CAP", _MEAL_CAP,
                         f"Meal ${tx.amount:.2f} > ${_MEAL_CAP:.2f}/day cap")

    # Mileage
    if unit == "MILE" or "mileage" in desc:
        expected = round(tx.quantity * _MILEAGE_RATE, 2)
        if abs(tx.amount - expected) > 0.50:
            return _make(tx, "FLAG", "AMOUNT_MISMATCH", "MILEAGE_RATE", expected,
                         f"Mileage ${tx.amount:.2f} ≠ {tx.quantity} mi × "
                         f"${_MILEAGE_RATE}/mi = ${expected:.2f}")

    return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _find_doc(note: str, docs_by_id: Dict[str, ReceiptDocument]) -> Optional[ReceiptDocument]:
    for doc_id in _DOC_ID_RE.findall(note):
        if doc_id in docs_by_id:
            return docs_by_id[doc_id]
    return None


def _is_subcontractor(tx: Transaction, doc: Optional[ReceiptDocument]) -> bool:
    desc = tx.description.lower()
    if "subcontractor" in desc:
        return True
    if doc and doc.doc_type == "VENDOR_INVOICE":
        return True
    return False


def _hit(text: str, keywords) -> bool:
    return any(kw in text for kw in keywords)


def _make(
    tx: Transaction,
    status: str,
    exception_type: Optional[str],
    rule_id: str,
    approved_amount: float,
    note: str,
) -> RuleResult:
    return RuleResult(
        transaction_id  = tx.transaction_id,
        status          = status,
        exception_type  = exception_type,
        rule_id         = rule_id,
        override_applied= False,
        override_source = "",
        original_amount = tx.amount,
        approved_amount = approved_amount,
        note            = note,
    )


def _approve(tx: Transaction, rule_id: str, note: str) -> RuleResult:
    return _make(tx, "APPROVE", None, rule_id, tx.amount, note)


def _log_summary(results: List[RuleResult]) -> None:
    approved = sum(1 for r in results if r.status == "APPROVE")
    flagged  = sum(1 for r in results if r.status == "FLAG")
    rejected = sum(1 for r in results if r.status == "REJECT")
    held     = sum(1 for r in results if r.status == "HOLD")
    overridden = sum(1 for r in results if r.override_applied)
    run_logger.step(
        f"Rule engine — {approved} approved, {flagged} flagged, "
        f"{rejected} rejected, {held} held, {overridden} overridden",
        "ok" if flagged == 0 and rejected == 0 else "warn",
    )
