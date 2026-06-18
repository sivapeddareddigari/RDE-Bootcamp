from dataclasses import dataclass
from typing import Optional


@dataclass
class RuleResult:
    """Outcome of applying all rules to a single transaction."""
    transaction_id: str
    status: str                     # APPROVE | REJECT | FLAG | HOLD
    exception_type: Optional[str]   # None when clean
    rule_id: str                    # which rule fired (e.g. "LODGING_CAP", "CLEAN")
    override_applied: bool
    override_source: str            # e.g. "PL-EMAIL-02" or ""
    original_amount: float
    approved_amount: float          # may differ after exclusions or markup
    note: str
