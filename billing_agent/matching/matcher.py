"""
Phase 3 — document matching.

For each expense transaction, find the backup document (EXACT via the
doc_id in the note field, or FUZZY by date + amount proximity), apply
FX conversion, and compute the amount delta between SAP and the receipt.
"""
import logging
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Dict, List, Optional

from billing_agent.ingestion.loader import IngestionResult
from billing_agent.matching.currency import to_usd
from billing_agent.models.document import ReceiptDocument
from billing_agent.models.transaction import Transaction
from billing_agent.rules.models import RuleResult

log = logging.getLogger(__name__)

_DOC_ID_RE = re.compile(r"\b((?:RC|ML|VI)-\d{3})\b")
_DATE_WINDOW = timedelta(days=3)
_AMOUNT_TOLERANCE = 0.10   # 10% for fuzzy amount matching


@dataclass
class MatchResult:
    transaction_id: str
    matched_doc_id: Optional[str]
    confidence: str         # EXACT | FUZZY | NO_MATCH
    usd_amount: float       # FX-converted doc total (or tx.amount when no doc)
    fx_rate_applied: float  # 1.0 when no conversion needed
    amount_delta: float     # tx.amount − usd_amount (positive = SAP is higher)
    note: str


def reconcile(inputs: IngestionResult, rule_results: List[RuleResult]) -> List[MatchResult]:
    """Match every expense transaction to a backup document."""
    docs_by_id: Dict[str, ReceiptDocument] = {d.doc_id: d for d in inputs.documents}
    results: List[MatchResult] = []

    for tx in inputs.transactions:
        if not tx.is_expense:
            continue
        match = _match(tx, docs_by_id, inputs.documents)
        results.append(match)
        _log_match(match)

    exact  = sum(1 for r in results if r.confidence == "EXACT")
    fuzzy  = sum(1 for r in results if r.confidence == "FUZZY")
    no_doc = sum(1 for r in results if r.confidence == "NO_MATCH")
    log.info(
        "Matching complete: %d expense transactions — %d exact, %d fuzzy, %d no-match",
        len(results), exact, fuzzy, no_doc,
    )
    return results


def _match(
    tx: Transaction,
    docs_by_id: Dict[str, ReceiptDocument],
    all_docs: List[ReceiptDocument],
) -> MatchResult:
    doc = _direct_match(tx.note, docs_by_id)
    if doc:
        return _build(tx, doc, "EXACT")
    doc = _fuzzy_match(tx, all_docs)
    if doc:
        return _build(tx, doc, "FUZZY")
    return MatchResult(
        transaction_id=tx.transaction_id,
        matched_doc_id=None,
        confidence="NO_MATCH",
        usd_amount=tx.amount,
        fx_rate_applied=1.0,
        amount_delta=0.0,
        note="No backup document found",
    )


def _direct_match(note: str, docs_by_id: Dict[str, ReceiptDocument]) -> Optional[ReceiptDocument]:
    for doc_id in _DOC_ID_RE.findall(note):
        if doc_id in docs_by_id:
            return docs_by_id[doc_id]
    return None


def _fuzzy_match(tx: Transaction, all_docs: List[ReceiptDocument]) -> Optional[ReceiptDocument]:
    if not all_docs or tx.transaction_date is None:
        return None
    candidates = []
    for doc in all_docs:
        if doc.doc_date is None:
            continue
        if abs((doc.doc_date - tx.transaction_date).days) > _DATE_WINDOW.days:
            continue
        try:
            doc_usd, _ = to_usd(doc.total_amount, doc.currency)
        except ValueError:
            continue
        if doc_usd > 0:
            ratio = abs(doc_usd - tx.amount) / max(doc_usd, tx.amount)
            if ratio <= _AMOUNT_TOLERANCE:
                candidates.append(doc)
    return candidates[0] if len(candidates) == 1 else None


def _build(tx: Transaction, doc: ReceiptDocument, confidence: str) -> MatchResult:
    try:
        usd_amount, fx_rate = to_usd(doc.total_amount, doc.currency)
    except ValueError:
        usd_amount, fx_rate = doc.total_amount, 1.0

    # Alcohol-excluded billable amount
    doc_billable = usd_amount - doc.alcohol_amount
    delta = round(tx.amount - doc_billable, 2)

    notes = []
    if doc.currency != "USD":
        notes.append(
            f"FX: {doc.total_amount} {doc.currency} → {usd_amount:.2f} USD @ {fx_rate}"
        )
    if doc.is_composite:
        notes.append(f"{doc.doc_id} is composite — individual amounts may differ from total")
    if doc.is_unreadable:
        notes.append(f"{doc.doc_id} is unreadable — amounts unreliable")
    if doc.alcohol_amount > 0:
        notes.append(
            f"alcohol excluded: {doc.alcohol_amount:.2f}; billable portion {doc_billable:.2f}"
        )
    if abs(delta) > 0.01:
        notes.append(f"Δ SAP {tx.amount:.2f} vs doc {doc_billable:.2f} = {delta:+.2f}")

    return MatchResult(
        transaction_id=tx.transaction_id,
        matched_doc_id=doc.doc_id,
        confidence=confidence,
        usd_amount=doc_billable,
        fx_rate_applied=fx_rate,
        amount_delta=delta,
        note="; ".join(notes) if notes else "OK",
    )


def _log_match(m: MatchResult) -> None:
    if m.confidence == "EXACT":
        log.debug("  %s → %s (exact, Δ%+.2f)", m.transaction_id, m.matched_doc_id, m.amount_delta)
    elif m.confidence == "FUZZY":
        log.info("  %s → %s (fuzzy)", m.transaction_id, m.matched_doc_id)
    else:
        log.info("  %s → no document", m.transaction_id)
