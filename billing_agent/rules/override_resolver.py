"""
Applies PL instruction overrides and recurring exception patterns to
a list of RuleResults after the rule engine has run.

Matching strategy (checked in order for each result):
  - RELEASE_HOLD      detected from body text (handles mixed-instruction emails)
  - CONFIRM_MARKUP    instruction_type == CONFIRM_MARKUP, amount proximity
  - Approval intent   body contains explicit approval language + amount + scope
  - OVERRIDE_APPROVE  instruction_type, amount + scope
  - Rejection intent  body contains explicit rejection language + scope (APPROVE/FLAG only)

Alcohol and personal items (AIRPORT_LOUNGE, PERSONAL_ITEM, ALCOHOL rule_ids)
are never overrideable regardless of PL instruction.

After instructions, recurring ExceptionCase patterns are applied to any
remaining FLAG/REJECT results.
"""

import json
import logging
import re
from pathlib import Path
from typing import List

from billing_agent.models.instruction import ExceptionCase, ProjectInstruction
from billing_agent.models.transaction import Transaction
from billing_agent.rules.models import RuleResult

_DATA_DIR = Path(__file__).parent / "data"
_MEAL_KWS: tuple = tuple(
    json.loads((_DATA_DIR / "keyword_lists.json").read_text(encoding="utf-8"))["meals"]
)

log = logging.getLogger(__name__)

# rule_ids that can never be overridden (hard contract prohibitions)
_NO_OVERRIDE_RULES = {"ALCOHOL", "AIRPORT_LOUNGE", "PERSONAL_ITEM"}

_HOLD_RELEASE_RE = re.compile(
    r"release\s+(?:the\s+)?hold|release.*principal.*hold|release.*hold\s+and\s+bill",
    re.IGNORECASE,
)
_MARKUP_CONFIRM_RE = re.compile(
    r"apply\s+the\s+\d+%\s+markup|apply.*markup|confirm.*markup",
    re.IGNORECASE,
)
_APPROVAL_RE = re.compile(
    r"go ahead and bill|please\s+(?:go ahead and\s+)?bill\s+(?:it|the)|bill\s+it\b|"
    r"bill the actual|approved\b|please bill",
    re.IGNORECASE,
)
_REJECTION_RE = re.compile(
    r"please drop\b|not appropriate for billing|do not bill|remove from (?:this )?invoice|"
    r"not billable|please remove",
    re.IGNORECASE,
)

# Scope keyword → description terms that indicate the same expense category
_SCOPE_MAP = {
    # Use "hotel —" (with dash) to avoid matching "rideshare — hotel to site" etc.
    "lodging":         ("hotel —", "hotel stay", "lodging", "accommodation"),
    "meals":           _MEAL_KWS,
    "airport lounge":  ("lounge", "priority pass", "club access"),
    "subcontractor":   ("subcontractor", "drone", "vendor invoice"),
    "hold":            (),
    "miscoded labour": ("training", "pmo", "admin", "internal meeting", "internal training"),
}


def apply_overrides(
    results: List[RuleResult],
    instructions: List[ProjectInstruction],
    exceptions: List[ExceptionCase],
    transactions: List[Transaction],
) -> List[RuleResult]:
    tx_by_id = {t.transaction_id: t for t in transactions}

    for result in results:
        if result.override_applied:
            continue
        tx = tx_by_id.get(result.transaction_id)
        if tx is None:
            continue
        for inst in instructions:
            if _applies(inst, result, tx):
                _mutate(inst, result)
                log.info(
                    "  %s: %s applied from %s",
                    result.transaction_id,
                    inst.instruction_type,
                    inst.instruction_id,
                )
                break

    # Second pass: recurring exception patterns for still-unresolved items
    for result in results:
        if result.override_applied or result.status not in ("FLAG", "REJECT"):
            continue
        for exc in exceptions:
            if exc.recurring and exc.exception_type == result.exception_type:
                result.override_applied = True
                result.override_source  = f"prior-exception:{exc.exception_id}"
                result.status           = "APPROVE"
                result.note            += f" | Auto-resolved via {exc.exception_id}: {exc.resolution}"
                log.info(
                    "  %s: auto-resolved via recurring exception %s",
                    result.transaction_id, exc.exception_id,
                )
                break

    return results


def _applies(inst: ProjectInstruction, result: RuleResult, tx: Transaction) -> bool:
    body  = inst.body
    scope = inst.scope.lower()
    desc  = tx.description.lower()
    itype = inst.instruction_type

    # 1. RELEASE_HOLD detected from body text (catches mixed emails like Email 4)
    if _HOLD_RELEASE_RE.search(body) and result.status == "HOLD":
        return True

    # 2. Markup confirmation — detect from body text regardless of instruction_type,
    #    since mixed-instruction emails (Email 4) may be typed as RELEASE_HOLD.
    if (result.exception_type == "SUBCONTRACTOR_MARKUP"
            and result.status == "FLAG"
            and (_MARKUP_CONFIRM_RE.search(body) or itype == "CONFIRM_MARKUP")
            and _amount_close(inst, result)):
        return True

    # 3. Approval of flagged items
    if result.status == "FLAG" and result.rule_id not in _NO_OVERRIDE_RULES:
        if not _amount_close(inst, result):
            return False
        if not _scope_matches(scope, desc):
            return False
        if _APPROVAL_RE.search(body) or itype == "OVERRIDE_APPROVE":
            return True

    # 4. Rejection of approved or flagged items
    #    Per diem lines are receipt-exempt; only reject if body explicitly says "per diem".
    if result.status in ("APPROVE", "FLAG") and result.rule_id not in _NO_OVERRIDE_RULES:
        if "per diem" in desc and "per diem" not in body.lower():
            return False
        if _REJECTION_RE.search(body) and _scope_matches(scope, desc):
            # Don't reject when this same email also approves the exact amount
            if _APPROVAL_RE.search(body) and _amount_close(inst, result):
                return False
            # Don't reject a passing item from a mixed-intent email: if the email's
            # primary purpose is approving something and this item is already clean,
            # the incidental rejection language targets a different charge.
            if _APPROVAL_RE.search(body) and result.rule_id == "CLEAN":
                return False
            return True

    return False


def _mutate(inst: ProjectInstruction, result: RuleResult) -> None:
    result.override_applied = True
    result.override_source  = inst.instruction_id
    body  = inst.body
    itype = inst.instruction_type

    if _HOLD_RELEASE_RE.search(body):
        result.status          = "APPROVE"
        result.approved_amount = result.original_amount
        result.note           += f" | Hold released per {inst.instruction_id} ({inst.instruction_date})"

    elif itype == "CONFIRM_MARKUP" or _MARKUP_CONFIRM_RE.search(body):
        result.status  = "APPROVE"
        result.note   += f" | Markup confirmed per {inst.instruction_id} ({inst.instruction_date})"

    elif result.status == "FLAG" and (_APPROVAL_RE.search(body) or itype == "OVERRIDE_APPROVE"):
        result.status          = "APPROVE"
        result.approved_amount = result.original_amount
        result.note           += f" | PL approved per {inst.instruction_id} ({inst.instruction_date})"

    elif _REJECTION_RE.search(body) or itype == "OVERRIDE_REJECT":
        result.status          = "REJECT"
        result.approved_amount = 0.0
        result.note           += f" | PL rejected per {inst.instruction_id} ({inst.instruction_date})"


def _amount_close(inst: ProjectInstruction, result: RuleResult) -> bool:
    """Return True if instruction has no amount or is within $1 of the transaction amount."""
    if inst.amount is None:
        return True
    return abs(inst.amount - result.original_amount) <= 1.0


def _scope_matches(scope: str, desc: str) -> bool:
    """Return True if any scope keyword maps to terms present in the description."""
    for scope_kw, desc_terms in _SCOPE_MAP.items():
        if scope_kw in scope and any(t in desc for t in desc_terms):
            return True
    return False
