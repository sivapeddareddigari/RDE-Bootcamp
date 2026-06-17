"""
IngestionResult and load_inputs() — the single entry point for Phase 1.

load_inputs(submission_path) accepts the path to the dropped CSV file
(the SAP unbilled transaction extract) and loads all supporting inputs
from their fixed locations under test-data/sample-inputs/.

Returns an IngestionResult with every entity type needed by the pipeline.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from billing_agent.config import DATA_DIR
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


def load_inputs(submission_path: Path) -> IngestionResult:
    """
    Load all inputs for one billing review cycle.

    submission_path — the CSV dropped into submissions/incoming/
                      (SAP unbilled transaction extract)
    """
    log.info("── ingestion start: %s", submission_path.name)

    transactions = load_transactions(submission_path)
    timecards    = load_timecards(_TIMECARD_PATH)
    rates, clauses = load_contract(_CONTRACT_PATH)
    documents    = load_documents(_DOCS_DIR)
    instructions = load_instructions(_EMAILS_PATH)
    exceptions   = load_exceptions(_EXCEPTIONS_PATH)

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

    log.info("── ingestion complete\n%s", result.summary())
    return result
