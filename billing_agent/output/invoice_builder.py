"""
Phase 5 — invoice builder.

Assembles the draft invoice, audit trail CSV, and exceptions report from
the outputs of Phases 1–4. Writes three timestamped files to output/.

build() is the sole entry point. It uses result.approved_amount from each
RuleResult — already the correct billing figure after all rules and PL
overrides — so no rule logic is re-applied here.

Output files (all timestamped to the same second):
  output/draft-invoice-<stem>__<ts>.md
  output/audit-trail-<stem>__<ts>.csv
  output/exceptions-report-<stem>__<ts>.md
"""

import csv
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from billing_agent import run_logger
from billing_agent.config import OUTPUT_DIR
from billing_agent.exceptions.models import ExceptionItem, ExceptionReport
from billing_agent.ingestion.loader import IngestionResult
from billing_agent.matching.matcher import MatchResult
from billing_agent.models.transaction import Transaction
from billing_agent.rules.models import RuleResult

log = logging.getLogger(__name__)

_DATA_DIR   = Path(__file__).parent.parent / "rules" / "data"
_MARKUP_PCT: float = json.loads(
    (_DATA_DIR / "expense_caps.json").read_text(encoding="utf-8")
)["subcontractor_markup_pct"]
_MEAL_KWS: frozenset = frozenset(
    json.loads((_DATA_DIR / "keyword_lists.json").read_text(encoding="utf-8"))["meals"]
)

# Expense category labels in display order
_CAT_ORDER = ["AIR", "LODGING", "MEALS", "GROUND", "MILEAGE", "SUBCONTRACTOR", "OTHER"]
_CAT_LABEL = {
    "AIR":           "Air Travel",
    "LODGING":       "Lodging",
    "MEALS":         "Meals & Per Diem",
    "GROUND":        "Ground Transport",
    "MILEAGE":       "Mileage",
    "SUBCONTRACTOR": "Subcontractor",
    "OTHER":         "Other Expenses",
}

_ROLE_LABEL = {
    "ENG1": "Engineer I",
    "ENG2": "Engineer II",
    "ENG3": "Engineer III",
    "PM1":  "Project Manager",
    "PRIN": "Principal",
    "ADMIN": "Admin / Coordinator",
}


# ── Public return type ────────────────────────────────────────────────────────

@dataclass
class BuildResult:
    invoice_path: Path
    audit_path: Path
    exceptions_path: Path
    labour_total: float
    expense_total: float
    grand_total: float
    blocked_count: int    # FLAG/HOLD items that could not be invoiced


# ── Public entry point ────────────────────────────────────────────────────────

def build(
    inputs: IngestionResult,
    rule_results: List[RuleResult],
    match_results: List[MatchResult],
    exception_report: ExceptionReport,
) -> BuildResult:
    ts      = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem    = inputs.submission_file.stem
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    invoice_path    = OUTPUT_DIR / f"draft-invoice-{stem}__{ts}.md"
    audit_path      = OUTPUT_DIR / f"audit-trail-{stem}__{ts}.csv"
    exceptions_path = OUTPUT_DIR / f"exceptions-report-{stem}__{ts}.md"

    tx_by_id = {t.transaction_id: t for t in inputs.transactions}
    mr_by_id = {m.transaction_id: m for m in match_results}

    billable = [r for r in rule_results if r.status == "APPROVE"]
    labour   = [r for r in billable if tx_by_id[r.transaction_id].is_labor]
    expenses = [r for r in billable if tx_by_id[r.transaction_id].is_expense]
    non_bill = [r for r in rule_results if r.status != "APPROVE"]

    labour_total  = round(sum(r.approved_amount for r in labour),  2)
    expense_total = round(sum(r.approved_amount for r in expenses), 2)
    grand_total   = round(labour_total + expense_total, 2)

    _write_invoice(
        invoice_path, inputs, labour, expenses, non_bill,
        tx_by_id, mr_by_id, labour_total, expense_total, grand_total, ts,
    )
    _write_audit(audit_path, rule_results, tx_by_id, mr_by_id)
    _write_exceptions(exceptions_path, exception_report, ts)

    result = BuildResult(
        invoice_path    = invoice_path,
        audit_path      = audit_path,
        exceptions_path = exceptions_path,
        labour_total    = labour_total,
        expense_total   = expense_total,
        grand_total     = grand_total,
        blocked_count   = len(exception_report.blocking),
    )

    _validate(result, rule_results, tx_by_id)
    run_logger.step(
        f"Invoice built — labour ${labour_total:,.2f}  expenses ${expense_total:,.2f}  "
        f"total ${grand_total:,.2f}  ({len(exception_report.blocking)} blocked)",
        "ok" if len(exception_report.blocking) == 0 else "warn",
    )
    return result


# ── Invoice (markdown) ────────────────────────────────────────────────────────

def _write_invoice(
    path: Path,
    inputs: IngestionResult,
    labour: List[RuleResult],
    expenses: List[RuleResult],
    non_bill: List[RuleResult],
    tx_by_id: Dict[str, Transaction],
    mr_by_id: Dict[str, MatchResult],
    labour_total: float,
    expense_total: float,
    grand_total: float,
    ts: str,
) -> None:
    lines: List[str] = []
    _hdr = lines.append

    _hdr("# Draft Invoice — Coastal Greenway Feasibility Study (PRJ-NS-7421)")
    _hdr("")
    _hdr(f"**Invoice for:** Northstar Civic Group, Inc.  ")
    _hdr(f"**Project:** Coastal Greenway Feasibility Study — Phase 2 (PRJ-NS-7421)  ")
    _hdr(f"**Cycle:** {_infer_cycle(inputs)}  ")
    _hdr(f"**Submission:** {inputs.submission_file.name}  ")
    _hdr(f"**Generated:** {ts}  ")
    _hdr(f"**Currency:** USD  ")
    _hdr("")
    _hdr("---")
    _hdr("")

    # Section A — Labour
    _hdr("## Section A — Labour")
    _hdr("")
    if labour:
        _hdr("| Role | Description | Hrs | Rate (USD) | Amount (USD) |")
        _hdr("|------|-------------|----:|----------:|-------------:|")
        for r in labour:
            tx   = tx_by_id[r.transaction_id]
            role = _ROLE_LABEL.get(tx.role_code, tx.role_code)
            note = _override_note(r)
            desc = f"{tx.description}{note}"
            _hdr(f"| {role} | {desc} | {tx.quantity:.1f} | {tx.rate:,.2f} | {r.approved_amount:,.2f} |")
    else:
        _hdr("*No billable labour transactions.*")
    _hdr("")
    _hdr(f"**Labour subtotal: USD {labour_total:,.2f}**")
    _hdr("")

    # Excluded labour
    excluded_labour = [r for r in non_bill if tx_by_id[r.transaction_id].is_labor]
    if excluded_labour:
        _hdr("**Excluded labour:**")
        _hdr("")
        for r in excluded_labour:
            tx = tx_by_id[r.transaction_id]
            _hdr(f"- {r.transaction_id} — {tx.description} ({r.rule_id}): {r.note}")
        _hdr("")

    _hdr("---")
    _hdr("")

    # Section B — Expenses
    _hdr("## Section B — Reimbursable Expenses")
    _hdr("")
    if expenses:
        by_cat: Dict[str, List[RuleResult]] = {c: [] for c in _CAT_ORDER}
        for r in expenses:
            by_cat[_categorize(tx_by_id[r.transaction_id])].append(r)

        for cat in _CAT_ORDER:
            rows = by_cat[cat]
            if not rows:
                continue
            _hdr(f"### {_CAT_LABEL[cat]}")
            _hdr("")
            _hdr("| Description | Amount (USD) | Note |")
            _hdr("|-------------|-------------:|------|")
            for r in rows:
                tx   = tx_by_id[r.transaction_id]
                note = _expense_note(r, mr_by_id.get(r.transaction_id))
                if cat == "SUBCONTRACTOR" and _has_markup(r):
                    cost   = r.original_amount
                    markup = round(r.approved_amount - cost, 2)
                    _hdr(f"| {tx.description} (cost) | {cost:,.2f} | {note} |")
                    _hdr(f"| {tx.description} (markup {_MARKUP_PCT:.0%}) | {markup:,.2f} | |")
                else:
                    _hdr(f"| {tx.description} | {r.approved_amount:,.2f} | {note} |")
            _hdr("")
    else:
        _hdr("*No billable expense transactions.*")
    _hdr("")
    _hdr(f"**Expense subtotal: USD {expense_total:,.2f}**")
    _hdr("")
    _hdr("---")
    _hdr("")

    # Totals
    _hdr("## Totals")
    _hdr("")
    _hdr("| Section | Amount (USD) |")
    _hdr("|---------|-------------:|")
    _hdr(f"| Labour | {labour_total:,.2f} |")
    _hdr(f"| Reimbursable expenses | {expense_total:,.2f} |")
    _hdr(f"| **Grand total** | **{grand_total:,.2f}** |")
    _hdr("")

    # Excluded summary
    excluded_exp = [r for r in non_bill if tx_by_id[r.transaction_id].is_expense]
    if excluded_labour or excluded_exp:
        _hdr("---")
        _hdr("")
        _hdr("## Excluded / Blocked items")
        _hdr("")
        _hdr(f"*{len(non_bill)} item(s) not included in totals above. "
             "See exceptions report for routing and required actions.*")
        _hdr("")
        _hdr("| Transaction | Description | Status | Rule | Note |")
        _hdr("|-------------|-------------|--------|------|------|")
        for r in non_bill:
            tx = tx_by_id[r.transaction_id]
            _hdr(f"| {r.transaction_id} | {tx.description} | {r.status} "
                 f"| {r.rule_id} | {r.note[:80]} |")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.debug("  invoice → %s", path.name)


# ── Audit trail (CSV) ─────────────────────────────────────────────────────────

_AUDIT_FIELDS = [
    "transaction_id", "employee_id", "type", "description",
    "original_amount", "approved_amount", "status",
    "rule_id", "exception_type", "override_applied", "override_source",
    "matched_doc_id", "match_confidence", "amount_delta", "note",
]


def _write_audit(
    path: Path,
    rule_results: List[RuleResult],
    tx_by_id: Dict[str, Transaction],
    mr_by_id: Dict[str, MatchResult],
) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_AUDIT_FIELDS)
        writer.writeheader()
        for r in rule_results:
            tx = tx_by_id[r.transaction_id]
            mr = mr_by_id.get(r.transaction_id)
            writer.writerow({
                "transaction_id":   r.transaction_id,
                "employee_id":      tx.employee_id,
                "type":             "LABOR" if tx.is_labor else "EXPENSE",
                "description":      tx.description,
                "original_amount":  f"{r.original_amount:.2f}",
                "approved_amount":  f"{r.approved_amount:.2f}",
                "status":           r.status,
                "rule_id":          r.rule_id,
                "exception_type":   r.exception_type or "",
                "override_applied": r.override_applied,
                "override_source":  r.override_source,
                "matched_doc_id":   mr.matched_doc_id if mr else "",
                "match_confidence": mr.confidence if mr else "",
                "amount_delta":     f"{mr.amount_delta:+.2f}" if mr else "",
                "note":             r.note,
            })
    log.debug("  audit  → %s", path.name)


# ── Exceptions report (markdown) ─────────────────────────────────────────────

def _write_exceptions(
    path: Path,
    report: ExceptionReport,
    ts: str,
) -> None:
    lines: List[str] = []
    w = lines.append

    w(f"# Exceptions Report — {report.submission_file}")
    w("")
    w(f"**Generated:** {ts}  ")
    w(f"**Total transactions:** {report.total_transactions}  ")
    w(f"**Clean (no exception):** {report.clean_count}  ")
    w(f"**Unresolved escalations:** {report.unresolved_count}  ")
    w(f"**Blocking invoice:** {len(report.blocking)}  ")
    w("")
    w("---")
    w("")

    _exc_section(lines, "Auto-resolved", report.auto_resolved,
                 "Items where a PL instruction or prior-exception pattern "
                 "approved or rejected the transaction.")
    _exc_section(lines, "PL rejections", report.pl_rejections,
                 "Items explicitly rejected by a PL instruction.")
    _exc_section(lines, "Hard rejections (contract)", report.hard_rejections,
                 "Items rejected by contract rule. Employee must remove from SAP.")
    _exc_section(lines, "Escalate → PL", report.escalate_pl,
                 "Require PL written approval before invoicing.")
    _exc_section(lines, "Escalate → Analyst", report.escalate_analyst,
                 "Require analyst review (amount, rate, or FX discrepancy).")
    _exc_section(lines, "Escalate → Employee", report.escalate_employee,
                 "Employee must correct in SAP (missing receipt, time-coding error).")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.debug("  exceptions → %s", path.name)


def _exc_section(lines: List[str], title: str, items: List[ExceptionItem], desc: str) -> None:
    if not items:
        return
    w = lines.append
    w(f"## {title} ({len(items)})")
    w("")
    w(f"*{desc}*")
    w("")
    w("| Transaction | Description | Amount | Rule | Override | Note |")
    w("|-------------|-------------|-------:|------|----------|------|")
    for item in items:
        blk  = " ⚠ BLOCKS" if item.blocks_invoice else ""
        src  = item.override_source if item.override_applied else "—"
        note = item.note[:80]
        w(f"| {item.transaction_id} | {item.description} | {item.original_amount:,.2f} "
          f"| {item.rule_id}{blk} | {src} | {note} |")
    w("")


# ── Validation ────────────────────────────────────────────────────────────────

def _validate(
    result: BuildResult,
    rule_results: List[RuleResult],
    tx_by_id: Dict[str, Transaction],
) -> None:
    errors: List[str] = []

    for r in rule_results:
        if r.rule_id in {"ALCOHOL", "AIRPORT_LOUNGE", "PERSONAL_ITEM"} and r.approved_amount != 0.0:
            errors.append(f"{r.transaction_id}: {r.rule_id} approved_amount should be 0")

    recon = round(result.labour_total + result.expense_total, 2)
    if abs(recon - result.grand_total) > 0.01:
        errors.append(
            f"grand_total {result.grand_total} ≠ labour {result.labour_total} "
            f"+ expense {result.expense_total}"
        )

    if errors:
        for e in errors:
            log.error("  VALIDATION FAIL: %s", e)
        run_logger.step(f"Invoice validation failed — {len(errors)} error(s)", "error")
    else:
        log.debug("  validation passed")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _categorize(tx: Transaction) -> str:
    desc = tx.description.lower()
    unit = tx.unit.upper()
    if unit == "NIGHT" or "hotel" in desc or "lodging" in desc or "accommodation" in desc:
        return "LODGING"
    if unit == "MILE" or "mileage" in desc:
        return "MILEAGE"
    if "flight" in desc or "air travel" in desc:
        return "AIR"
    if ("rideshare" in desc or "taxi" in desc or "transit" in desc
            or "parking" in desc or "ground" in desc):
        return "GROUND"
    if any(kw in desc for kw in _MEAL_KWS) or "per diem" in desc:
        return "MEALS"
    if "subcontractor" in desc or "drone" in desc or "vendor" in desc:
        return "SUBCONTRACTOR"
    return "OTHER"


def _has_markup(r: RuleResult) -> bool:
    """True when the approved amount includes a subcontractor markup."""
    return (r.exception_type == "SUBCONTRACTOR_MARKUP"
            and r.override_applied
            and r.approved_amount > r.original_amount)


def _override_note(r: RuleResult) -> str:
    if r.override_applied and r.override_source:
        return f" ¹"
    return ""


def _expense_note(r: RuleResult, mr: Optional[MatchResult]) -> str:
    parts: List[str] = []
    if r.override_applied and r.override_source:
        parts.append(f"Approved per {r.override_source}")
    if mr and abs(mr.amount_delta) > 0.01:
        direction = "corrected down" if mr.amount_delta > 0 else "line item of folio"
        parts.append(f"Δ{mr.amount_delta:+.2f} ({direction})")
    if mr and mr.fx_rate_applied != 1.0:
        parts.append(f"FX @ {mr.fx_rate_applied}")
    return "; ".join(parts)


def _infer_cycle(inputs: IngestionResult) -> str:
    """Extract YYYY-MM from the submission filename, or fall back to 'current'."""
    import re
    m = re.search(r"(\d{4}-\d{2})", inputs.submission_file.name)
    return m.group(1) if m else "current"
