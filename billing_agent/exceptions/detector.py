"""
Phase 4 — exception detection and triage.

Classifies every non-clean RuleResult by routing (who must act to resolve it)
and produces a structured ExceptionReport for Phase 5 and analyst review.

Routing logic (rule_id → actor):
  ANALYST  — amount or rate judgment needed; analyst reviews and approves correct figure
  EMPLOYEE — employee must correct in SAP (wrong time code, missing receipt, personal item)
  PL       — Project Lead approval needed (over-cap, unreleased hold)

Items with blocks_invoice=True cannot appear on the draft invoice without resolution.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List

from billing_agent import run_logger
from billing_agent.exceptions.models import ExceptionItem, ExceptionReport
from billing_agent.ingestion.loader import IngestionResult
from billing_agent.matching.matcher import MatchResult
from billing_agent.models.transaction import Transaction
from billing_agent.rules.models import RuleResult

log = logging.getLogger(__name__)

# ── Routing and blocking tables ───────────────────────────────────────────────

_ANALYST_RULES = frozenset({
    "RATE_MISMATCH", "TRAVEL_RATE", "TRAVEL_HRS_CAP", "MILEAGE_RATE",
    "CURRENCY",        # FX conversion needed before amount can be confirmed
    "COMPOSITE_DOC",   # analyst must split line items
    "MARKUP_MISSING",  # analyst confirms correct markup amount
    "AMOUNT_MISMATCH", # analyst reviews SAP vs receipt discrepancy
})

_PL_RULES = frozenset({
    "LODGING_CAP",   # over lodging cap — PL written approval required
    "MEAL_CAP",      # over meal cap
    "PER_DIEM_CAP",  # over per diem cap
    "HOLD_ITEM",     # SAP hold not released by any PL instruction
})

# Everything else routes to EMPLOYEE (missing receipts, policy items, miscoding)

# Unresolved items that prevent the line from appearing on the draft invoice
_BLOCKING_RULES = frozenset({
    "PROJECT_MISMATCH",                            # transaction coded to wrong SAP project
    "LODGING_CAP", "MEAL_CAP", "PER_DIEM_CAP",  # can't bill over cap without approval
    "HOLD_ITEM",                                   # SAP hold is still active
    "NO_RECEIPT", "UNREADABLE_DOC",               # no acceptable backup documentation
    "CURRENCY",                                    # SAP amount in wrong currency
    "MARKUP_MISSING",                              # markup not yet applied to vendor amount
})


# ── Public entry point ────────────────────────────────────────────────────────

def run(
    inputs: IngestionResult,
    rule_results: List[RuleResult],
    match_results: List[MatchResult],
) -> ExceptionReport:
    """Classify all exceptions and return a triage report."""
    tx_by_id: Dict[str, Transaction] = {t.transaction_id: t for t in inputs.transactions}

    clean = 0
    auto_resolved: List[ExceptionItem] = []
    pl_rejections: List[ExceptionItem] = []
    hard_rejections: List[ExceptionItem] = []
    escalate_analyst: List[ExceptionItem] = []
    escalate_employee: List[ExceptionItem] = []
    escalate_pl: List[ExceptionItem] = []

    for result in rule_results:
        tx = tx_by_id.get(result.transaction_id)
        if tx is None:
            continue

        # Clean approvals — no exception, no action needed
        if result.status == "APPROVE" and not result.override_applied:
            clean += 1
            continue

        item = _make_item(tx, result)

        if result.status == "APPROVE":
            # Was FLAG or HOLD; resolved by PL instruction or prior-exception pattern
            item.routing = "AUTO_RESOLVED"
            auto_resolved.append(item)

        elif result.status == "REJECT" and result.override_applied:
            # PL explicitly rejected this line (e.g. Email 5 — miscoded training)
            item.routing = "AUTO_RESOLVED"
            pl_rejections.append(item)

        elif result.status == "REJECT":
            # Hard contract rejection (alcohol, lounge, personal, miscoded without PL)
            item.routing = _route(result.rule_id)
            hard_rejections.append(item)

        elif result.status in ("FLAG", "HOLD"):
            item.routing = _route(result.rule_id)
            item.blocks_invoice = result.rule_id in _BLOCKING_RULES
            if item.routing == "ANALYST":
                escalate_analyst.append(item)
            elif item.routing == "PL":
                escalate_pl.append(item)
            else:
                escalate_employee.append(item)

    report = ExceptionReport(
        submission_file=inputs.submission_file.name,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        total_transactions=len(rule_results),
        clean_count=clean,
        auto_resolved=auto_resolved,
        pl_rejections=pl_rejections,
        hard_rejections=hard_rejections,
        escalate_analyst=escalate_analyst,
        escalate_employee=escalate_employee,
        escalate_pl=escalate_pl,
    )

    _log_report(report)
    return report


# ── Helpers ───────────────────────────────────────────────────────────────────

def _route(rule_id: str) -> str:
    if rule_id in _ANALYST_RULES:
        return "ANALYST"
    if rule_id in _PL_RULES:
        return "PL"
    return "EMPLOYEE"


def _make_item(tx: Transaction, result: RuleResult) -> ExceptionItem:
    return ExceptionItem(
        transaction_id=result.transaction_id,
        employee_id=tx.employee_id,
        description=tx.description,
        original_amount=result.original_amount,
        approved_amount=result.approved_amount,
        status=result.status,
        exception_type=result.exception_type,
        rule_id=result.rule_id,
        routing="",           # set by caller after construction
        override_applied=result.override_applied,
        override_source=result.override_source,
        note=result.note,
        blocks_invoice=False,  # set for FLAG/HOLD items by caller
    )


def _log_report(report: ExceptionReport) -> None:
    n_analyst  = len(report.escalate_analyst)
    n_employee = len(report.escalate_employee)
    n_pl       = len(report.escalate_pl)
    n_blocking = len(report.blocking)
    unresolved = report.unresolved_count

    severity = "error" if n_blocking > 0 else ("warn" if unresolved > 0 else "ok")
    run_logger.step(
        f"Exception triage — {report.clean_count} clean, "
        f"{len(report.auto_resolved)} resolved, "
        f"{len(report.hard_rejections)} rejected, "
        f"{unresolved} escalated "
        f"({n_analyst} analyst / {n_employee} employee / {n_pl} PL)"
        + (f", {n_blocking} BLOCKING invoice" if n_blocking else ""),
        severity,
    )

    for item in report.auto_resolved:
        log.info("  ✓ resolved  %s  [%s]  %s", item.transaction_id, item.override_source, item.note)
    for item in report.pl_rejections:
        log.info("  ✗ PL-reject %s  [%s]  %s", item.transaction_id, item.override_source, item.note)
    for item in report.hard_rejections:
        log.info("  ✗ rejected  %s  %s", item.transaction_id, item.note)
    for item in report.escalate_pl:
        pfx = "  ⚠ BLOCKING" if item.blocks_invoice else "  ⚠ PL      "
        log.warning("%s %s  %s", pfx, item.transaction_id, item.note)
    for item in report.escalate_analyst:
        pfx = "  ⚠ BLOCKING" if item.blocks_invoice else "  ⚠ analyst "
        log.warning("%s %s  %s", pfx, item.transaction_id, item.note)
    for item in report.escalate_employee:
        pfx = "  ⚠ BLOCKING" if item.blocks_invoice else "  ⚠ employee"
        log.info("%s %s  %s", pfx, item.transaction_id, item.note)
