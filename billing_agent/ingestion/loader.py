"""
IngestionResult and load_inputs() — the single entry point for Phase 1.

load_inputs(submission_path) accepts the path to the dropped CSV file
(the SAP unbilled transaction extract) and loads all supporting inputs
from their fixed locations under test-data/sample-inputs/.

Returns an IngestionResult with every entity type needed by the pipeline.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Set

from billing_agent.config import DATA_DIR
from billing_agent import run_logger
from billing_agent.ingestion.contract_parser import load_contract
from billing_agent.ingestion.doc_parser import load_documents
from billing_agent.ingestion.email_parser import load_instructions
from billing_agent.ingestion.exception_loader import load_exceptions
from billing_agent.ingestion.sap_loader import load_timecards, load_transactions
from billing_agent.models.contract import ContractClause, RateEntry
from billing_agent.models.document import ReceiptDocument
from billing_agent.models.instruction import ExceptionCase, ProjectInstruction
from billing_agent.models.transaction import TimecardEntry, Transaction

log = logging.getLogger(__name__)

# Fixed paths relative to DATA_DIR (test-data/sample-inputs/)
_TIMECARD_PATH   = DATA_DIR / "sap-outputs"    / "timecards-2026-04.csv"
_CONTRACT_PATH   = DATA_DIR / "contracts"      / "contract-001.md"
_DOCS_DIR        = DATA_DIR / "documents"
_EMAILS_PATH     = DATA_DIR / "pm-instructions" / "sample-emails.md"
_EXCEPTIONS_PATH = DATA_DIR / "prior-exceptions" / "resolutions.csv"


@dataclass
class IngestionResult:
    submission_file:  Path
    transactions:     List[Transaction]     = field(default_factory=list)
    timecards:        List[TimecardEntry]   = field(default_factory=list)
    documents:        List[ReceiptDocument] = field(default_factory=list)
    rate_table:       List[RateEntry]       = field(default_factory=list)
    contract_clauses: List[ContractClause]  = field(default_factory=list)
    instructions:     List[ProjectInstruction] = field(default_factory=list)
    exceptions:       List[ExceptionCase]   = field(default_factory=list)
    loaded_at:        datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # ── Convenience accessors ──────────────────────────────────────────────

    @property
    def labour_transactions(self) -> List[Transaction]:
        return [t for t in self.transactions if t.is_labor]

    @property
    def expense_transactions(self) -> List[Transaction]:
        return [t for t in self.transactions if t.is_expense]

    @property
    def held_transactions(self) -> List[Transaction]:
        return [t for t in self.transactions if t.is_on_hold]

    def summary(self) -> str:
        return (
            f"IngestionResult for {self.submission_file.name}\n"
            f"  Transactions : {len(self.transactions)} "
            f"({len(self.labour_transactions)} labour, {len(self.expense_transactions)} expense, "
            f"{len(self.held_transactions)} held)\n"
            f"  Timecards    : {len(self.timecards)}\n"
            f"  Documents    : {len(self.documents)}\n"
            f"  Rate entries : {len(self.rate_table)}\n"
            f"  Clauses      : {len(self.contract_clauses)}\n"
            f"  Instructions : {len(self.instructions)}\n"
            f"  Exceptions   : {len(self.exceptions)}\n"
            f"  Loaded at    : {self.loaded_at.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        )


_DOC_ID_RE = re.compile(r"\b((?:RC|ML|VI)-\d{3})\b")


def _referenced_doc_ids(transactions: List[Transaction]) -> Set[str]:
    """Collect every document ID mentioned in transaction note fields."""
    ids: Set[str] = set()
    for tx in transactions:
        ids.update(_DOC_ID_RE.findall(tx.note))
    return ids


def _resolve_timecard_path(submission_path: Path) -> Path:
    """Derive the timecard path from the cycle embedded in the submission filename."""
    m = re.search(r"(\d{4}-\d{2})", submission_path.stem)
    if m:
        candidate = DATA_DIR / "sap-outputs" / f"timecards-{m.group(1)}.csv"
        if candidate.exists():
            return candidate
        log.warning(
            "No timecard file for cycle %s (expected %s); falling back to %s",
            m.group(1), candidate.name, _TIMECARD_PATH.name,
        )
    return _TIMECARD_PATH


def load_inputs(submission_path: Path) -> IngestionResult:
    """
    Load all inputs for one billing review cycle.

    submission_path — the CSV dropped into submissions/incoming/
                      (SAP unbilled transaction extract)
    """
    log.info("── ingestion start: %s", submission_path.name)

    transactions = load_transactions(submission_path)
    labour_count  = sum(t.is_labor    for t in transactions)
    expense_count = sum(t.is_expense  for t in transactions)
    held_count    = sum(t.is_on_hold  for t in transactions)
    run_logger.step(
        f"Loaded {len(transactions)} transactions "
        f"({labour_count} labour, {expense_count} expense, {held_count} held)"
    )

    employee_ids = {tx.employee_id for tx in transactions if tx.employee_id}
    doc_ids      = _referenced_doc_ids(transactions)
    run_logger.step(
        f"Scope: {len(employee_ids)} employee(s) {sorted(employee_ids)}, "
        f"{len(doc_ids)} referenced document(s) {sorted(doc_ids)}",
        "info",
    )

    timecard_path = _resolve_timecard_path(submission_path)
    timecards = load_timecards(timecard_path, employee_ids=employee_ids)
    run_logger.step(f"Loaded {len(timecards)} timecard entries for submission employees")

    rates, clauses = load_contract(_CONTRACT_PATH)
    run_logger.step(f"Loaded contract — {len(rates)} role rates, {len(clauses)} expense clauses")

    documents = load_documents(_DOCS_DIR, doc_ids=doc_ids if doc_ids else None)
    composite = sum(d.is_composite for d in documents)
    unreadable = sum(d.is_unreadable for d in documents)
    alcohol = sum(d.has_alcohol for d in documents)
    run_logger.step(
        f"Loaded {len(documents)} backup documents "
        f"({composite} composite, {unreadable} unreadable, {alcohol} with alcohol)"
    )

    instructions = load_instructions(_EMAILS_PATH)
    run_logger.step(f"Loaded {len(instructions)} PL instructions")

    exceptions = load_exceptions(_EXCEPTIONS_PATH)
    run_logger.step(f"Loaded {len(exceptions)} prior exception patterns")

    result = IngestionResult(
        submission_file  = submission_path,
        transactions     = transactions,
        timecards        = timecards,
        documents        = documents,
        rate_table       = rates,
        contract_clauses = clauses,
        instructions     = instructions,
        exceptions       = exceptions,
    )

    run_logger.step("Ingestion complete — all inputs loaded and normalised")
    log.info("── ingestion complete\n%s", result.summary())
    return result
