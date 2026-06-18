"""Dataclasses for Phase 4 exception reporting."""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ExceptionItem:
    transaction_id: str
    employee_id: str
    description: str
    original_amount: float
    approved_amount: float
    status: str                     # FLAG | REJECT | HOLD
    exception_type: Optional[str]
    rule_id: str
    routing: str                    # ANALYST | EMPLOYEE | PL | AUTO_RESOLVED
    override_applied: bool
    override_source: str
    note: str
    blocks_invoice: bool


@dataclass
class ExceptionReport:
    submission_file: str
    generated_at: str               # ISO-8601 UTC timestamp
    total_transactions: int
    clean_count: int                # APPROVE with no override (no exception at all)
    auto_resolved: List[ExceptionItem] = field(default_factory=list)  # FLAG/HOLD → APPROVE via PL/pattern
    pl_rejections: List[ExceptionItem] = field(default_factory=list)  # APPROVE → REJECT via PL instruction
    hard_rejections: List[ExceptionItem] = field(default_factory=list)  # REJECT from contract rule
    escalate_analyst: List[ExceptionItem] = field(default_factory=list)
    escalate_employee: List[ExceptionItem] = field(default_factory=list)
    escalate_pl: List[ExceptionItem] = field(default_factory=list)

    @property
    def blocking(self) -> List[ExceptionItem]:
        """Unresolved items that prevent the transaction from appearing on the invoice."""
        return [
            i for i in self.escalate_analyst + self.escalate_employee + self.escalate_pl
            if i.blocks_invoice
        ]

    @property
    def unresolved_count(self) -> int:
        return len(self.escalate_analyst) + len(self.escalate_employee) + len(self.escalate_pl)

    @property
    def exception_count(self) -> int:
        return (self.unresolved_count
                + len(self.hard_rejections)
                + len(self.pl_rejections)
                + len(self.auto_resolved))
