"""
Decision Memory Store — prior exception resolution patterns from resolutions.csv.

Provides context retrieval for the Exception Reasoning Agent: given the
unresolved exceptions in a submission, returns the most relevant prior
resolutions to include in the LLM prompt so Claude can check whether a
standing policy already covers the case.
"""

from pathlib import Path
from typing import List

from billing_agent.exceptions.models import ExceptionItem
from billing_agent.ingestion.exception_loader import load_exceptions
from billing_agent.models.instruction import ExceptionCase

_RESOLUTIONS_PATH = (
    Path(__file__).parent.parent.parent
    / "test-data" / "sample-inputs" / "prior-exceptions" / "resolutions.csv"
)

# Maps rule_id values (from rule engine) to exception_type values (in resolutions.csv)
_RULE_TO_TYPE: dict = {
    "NO_RECEIPT":     "MISSING_RECEIPT",
    "CURRENCY":       "FOREIGN_CURRENCY",
    "MARKUP_MISSING": "SUBCONTRACTOR_MARKUP",
    "MISCODED":       "MISCODED_TIME",
    "HOLD_ITEM":      "HOLD_RELEASE",
    "COMPOSITE_DOC":  "COMPOSITE_DOC",
    "LODGING_CAP":    "EXPENSE_OVERLIMIT",
    "MEAL_CAP":       "EXPENSE_OVERLIMIT",
    "PER_DIEM_CAP":   "EXPENSE_OVERLIMIT",
    "AMOUNT_MISMATCH":"EXPENSE_OVERLIMIT",
    "RATE_MISMATCH":  "RATE_OVERRIDE",
}


def load_memory(path: Path = _RESOLUTIONS_PATH) -> List[ExceptionCase]:
    return load_exceptions(path)


def find_relevant(
    items: List[ExceptionItem],
    all_cases: List[ExceptionCase],
) -> List[ExceptionCase]:
    """Return prior cases relevant to the given exception items (by type or recurring flag)."""
    mapped = {_RULE_TO_TYPE.get(i.rule_id) for i in items} - {None}
    direct = {i.exception_type for i in items if i.exception_type}
    relevant_types = mapped | direct
    return [c for c in all_cases if c.exception_type in relevant_types or c.recurring]


def format_for_prompt(cases: List[ExceptionCase]) -> str:
    if not cases:
        return "No prior resolutions on record."
    lines = []
    for c in cases:
        tag = " [RECURRING POLICY]" if c.recurring else ""
        lines.append(
            f"- [{c.exception_id}] {c.exception_type}{tag}: {c.description}\n"
            f"  Resolution ({c.resolution_date}, by {c.resolved_by}): {c.resolution}"
        )
    return "\n".join(lines)
