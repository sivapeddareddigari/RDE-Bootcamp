"""
Phase 5 (per-submission) — employee exception notices and analyst summary.

For each submission run this module writes:
  output/notices/exception-notice-{employee_id}-{stem}__{ts}.md
      → addressed to the employee; lists every exception on their claim
        with plain-English corrective action instructions

  output/analyst-summary-{stem}__{ts}.md
      → addressed to the billing analyst; aggregate view of the submission
        with routing, blocking status, and override citations

The draft invoice is NOT produced here — that is a project-level month-end
activity triggered separately via billing_agent/invoice_run.py.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from billing_agent import run_logger
from billing_agent.config import OUTPUT_DIR
from billing_agent.exceptions.models import ExceptionItem, ExceptionReport
from billing_agent.ingestion.contacts_loader import ContactDirectory, EmployeeContact
from billing_agent.ingestion.loader import IngestionResult
from billing_agent.matching.matcher import MatchResult
from billing_agent.rules.models import RuleResult

log = logging.getLogger(__name__)

_NOTICES_DIR = OUTPUT_DIR.parent / "output" / "notices"


# ── Corrective action instructions per rule_id ────────────────────────────────

_ACTION = {
    "NO_RECEIPT":       "Upload the missing receipt to your SAP expense entry and resubmit.",
    "UNREADABLE_DOC":   "Replace the unreadable scan with a legible copy and resubmit.",
    "ALCOHOL":          "Remove this charge from your SAP expense entry — alcohol is not reimbursable under the contract (§4).",
    "AIRPORT_LOUNGE":   "Remove this charge — airport lounge access is not reimbursable on this engagement.",
    "PERSONAL_ITEM":    "Remove this charge — personal items are not reimbursable under the contract (§4).",
    "MISCODED":         "Correct the task code in SAP. This activity is not billable to this project.",
    "LODGING_CAP":      "Your lodging charge exceeds the contract cap. Obtain written PL approval or correct the SAP entry to the cap amount.",
    "MEAL_CAP":         "Your meal charge exceeds the $90/day cap. Obtain written PL approval or correct the SAP entry.",
    "PER_DIEM_CAP":     "Your per diem claim exceeds the $65/day rate. Correct the SAP entry.",
    "RATE_MISMATCH":    "The billed rate does not match your contracted role rate. Correct the rate in SAP.",
    "TRAVEL_RATE":      "Travel time must be billed at 50% of your role rate. Correct the rate in SAP.",
    "TRAVEL_HRS_CAP":   "Travel hours exceed the 8-hour per-direction cap. Correct the quantity in SAP.",
    "MILEAGE_RATE":     "Mileage must be billed at $0.67/mile. Correct the amount in SAP.",
    "CURRENCY":         "Your receipt is in a foreign currency. Resubmit with the USD-converted amount and note the exchange rate used.",
    "AMOUNT_MISMATCH":  "The SAP amount does not match your receipt. Correct the amount in SAP to match the receipt.",
    "COMPOSITE_DOC":    "Your backup document covers multiple receipts. Please split into individual line items in SAP.",
    "MARKUP_MISSING":   "Subcontractor invoices must include the 8% contract markup. Correct the amount in SAP.",
    "HOLD_ITEM":        "This transaction is on SAP hold. Contact your billing analyst for resolution.",
}

_DEFAULT_ACTION = "Review this item and contact your billing analyst if you have questions."


def _action(rule_id: str, note: str) -> str:
    return _ACTION.get(rule_id, _DEFAULT_ACTION)


# ── Public entry point ────────────────────────────────────────────────────────

def write_notices(
    inputs: IngestionResult,
    rule_results: List[RuleResult],
    exception_report: ExceptionReport,
    contacts: ContactDirectory,
    llm_texts: Optional[Dict[str, str]] = None,
) -> List[Path]:
    """
    Write per-employee exception notices and an analyst summary.

    llm_texts: optional dict mapping transaction_id → LLM-generated employee notice
    text. When present, the LLM text replaces the generic _ACTION template for that
    transaction. Provided by the Phase 6 exception agent.

    Returns paths to all files written (notices + summary).
    """
    ts   = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = inputs.submission_file.stem
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    notices_dir = OUTPUT_DIR / "notices"
    notices_dir.mkdir(parents=True, exist_ok=True)

    llm = llm_texts or {}
    written: List[Path] = []

    # ── Per-employee exception notices ────────────────────────────────────────
    employee_ids = {t.employee_id for t in inputs.transactions}
    for emp_id in sorted(employee_ids):
        emp = contacts.employee(emp_id)
        items = _items_for_employee(exception_report, emp_id)
        if not items:
            continue                      # clean employee — no notice needed
        path = notices_dir / f"exception-notice-{emp_id}-{stem}__{ts}.md"
        _write_employee_notice(path, emp_id, emp, items, inputs, ts, llm)
        written.append(path)
        log.info("  notice → %s", path.name)

    # ── Analyst summary ───────────────────────────────────────────────────────
    analyst_path = OUTPUT_DIR / f"analyst-summary-{stem}__{ts}.md"
    _write_analyst_summary(analyst_path, inputs, rule_results, exception_report, contacts, ts)
    written.append(analyst_path)
    log.info("  analyst summary → %s", analyst_path.name)

    n_notices = len(written) - 1
    run_logger.step(
        f"Notices — {n_notices} employee notice(s) written, analyst summary written",
        "ok" if exception_report.unresolved_count == 0 else "warn",
    )
    return written


# ── Employee notice ───────────────────────────────────────────────────────────

def _items_for_employee(report: ExceptionReport, employee_id: str) -> List[ExceptionItem]:
    all_items = (
        report.escalate_employee
        + report.escalate_analyst
        + report.escalate_pl
        + report.hard_rejections
    )
    return [i for i in all_items if i.employee_id == employee_id]


def _write_employee_notice(
    path: Path,
    employee_id: str,
    emp: Optional[EmployeeContact],
    items: List[ExceptionItem],
    inputs: IngestionResult,
    ts: str,
    llm_texts: Optional[Dict[str, str]] = None,
) -> None:
    name  = emp.name if emp else employee_id
    email = emp.email if emp else "—"

    lines = []
    w = lines.append

    w(f"# Expense Claim Exception Notice")
    w("")
    w(f"**To:** {name} ({email})  ")
    w(f"**Project:** Coastal Greenway Feasibility Study (PRJ-NS-7421)  ")
    w(f"**Billing cycle:** {_infer_cycle(inputs)}  ")
    w(f"**Submission:** {inputs.submission_file.name}  ")
    w(f"**Generated:** {ts}  ")
    w("")
    w("---")
    w("")
    w(f"Hi {name.split()[0]},")
    w("")
    w("Your expense claim for the above billing cycle has been reviewed by the billing agent. "
      f"**{len(items)} item(s) require your attention** before they can be included in the "
      "month-end invoice to the client. Please action the items below in SAP and resubmit.")
    w("")

    blocking = [i for i in items if i.blocks_invoice]
    non_blocking = [i for i in items if not i.blocks_invoice]

    if blocking:
        w(f"## ⚠ Blocking items ({len(blocking)}) — must be resolved before invoicing")
        w("")
        w("These charges **cannot appear on the client invoice** until resolved.")
        w("")
        _item_table(lines, blocking, llm_texts or {})
        w("")

    if non_blocking:
        w(f"## Items under review ({len(non_blocking)}) — analyst or PL action in progress")
        w("")
        w("These items are being reviewed by the billing analyst or Project Lead. "
          "No action required from you unless contacted separately.")
        w("")
        _item_table(lines, non_blocking, llm_texts or {})
        w("")

    w("---")
    w("")
    w("If you have questions, reply to this notice or contact the billing analyst directly.")
    w("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _item_table(
    lines: List[str],
    items: List[ExceptionItem],
    llm_texts: Dict[str, str],
) -> None:
    w = lines.append
    w("| Transaction | Description | Amount (USD) | Issue | Action required |")
    w("|-------------|-------------|-------------:|-------|-----------------|")
    for item in items:
        # LLM-generated text takes precedence over the generic template
        action = llm_texts.get(item.transaction_id) or _action(item.rule_id, item.note)
        w(f"| {item.transaction_id} | {item.description[:45]} "
          f"| {item.original_amount:,.2f} | {item.note[:60]} | {action} |")


# ── Analyst summary ───────────────────────────────────────────────────────────

def _write_analyst_summary(
    path: Path,
    inputs: IngestionResult,
    rule_results: List[RuleResult],
    report: ExceptionReport,
    contacts: ContactDirectory,
    ts: str,
) -> None:
    analysts = contacts.billing_analysts
    to_line  = ", ".join(f"{a.name} ({a.email})" for a in analysts) or "Billing Analyst"

    lines = []
    w = lines.append

    w("# Billing Agent — Submission Summary")
    w("")
    w(f"**To:** {to_line}  ")
    w(f"**Project:** Coastal Greenway Feasibility Study (PRJ-NS-7421)  ")
    w(f"**Submission:** {inputs.submission_file.name}  ")
    w(f"**Billing cycle:** {_infer_cycle(inputs)}  ")
    w(f"**Generated:** {ts}  ")
    w("")
    w("---")
    w("")

    # Headline numbers
    w("## Summary")
    w("")
    w(f"| | Count |")
    w(f"|--|------:|")
    w(f"| Total transactions | {report.total_transactions} |")
    w(f"| Clean (no exception) | {report.clean_count} |")
    w(f"| Auto-resolved (PL override / prior pattern) | {len(report.auto_resolved)} |")
    w(f"| Hard rejections (contract) | {len(report.hard_rejections)} |")
    w(f"| PL rejections | {len(report.pl_rejections)} |")
    w(f"| Escalate → Employee | {len(report.escalate_employee)} |")
    w(f"| Escalate → Analyst | {len(report.escalate_analyst)} |")
    w(f"| Escalate → PL | {len(report.escalate_pl)} |")
    w(f"| **Blocking invoice** | **{len(report.blocking)}** |")
    w("")

    if report.blocking:
        w("## ⚠ Blocking items — invoice cannot be finalised until resolved")
        w("")
        w("| Transaction | Employee | Rule | Note |")
        w("|-------------|----------|------|------|")
        for item in report.blocking:
            emp = contacts.employee(item.employee_id)
            name = emp.name if emp else item.employee_id
            w(f"| {item.transaction_id} | {name} | {item.rule_id} | {item.note[:70]} |")
        w("")

    if report.escalate_analyst:
        w("## Requires analyst review")
        w("")
        w("| Transaction | Employee | Rule | Note |")
        w("|-------------|----------|------|------|")
        for item in report.escalate_analyst:
            emp = contacts.employee(item.employee_id)
            name = emp.name if emp else item.employee_id
            w(f"| {item.transaction_id} | {name} | {item.rule_id} | {item.note[:70]} |")
        w("")

    if report.escalate_pl:
        w("## Requires PL approval")
        w("")
        w("| Transaction | Employee | Rule | Note |")
        w("|-------------|----------|------|------|")
        for item in report.escalate_pl:
            emp = contacts.employee(item.employee_id)
            name = emp.name if emp else item.employee_id
            w(f"| {item.transaction_id} | {name} | {item.rule_id} | {item.note[:70]} |")
        w("")

    if report.auto_resolved:
        w("## Auto-resolved")
        w("")
        w("| Transaction | Employee | Rule | Override source |")
        w("|-------------|----------|------|----------------|")
        for item in report.auto_resolved:
            emp = contacts.employee(item.employee_id)
            name = emp.name if emp else item.employee_id
            w(f"| {item.transaction_id} | {name} | {item.rule_id} | {item.override_source} |")
        w("")

    if report.hard_rejections or report.pl_rejections:
        w("## Rejected items")
        w("")
        w("| Transaction | Employee | Rule | Note |")
        w("|-------------|----------|------|------|")
        for item in report.hard_rejections + report.pl_rejections:
            emp = contacts.employee(item.employee_id)
            name = emp.name if emp else item.employee_id
            w(f"| {item.transaction_id} | {name} | {item.rule_id} | {item.note[:70]} |")
        w("")

    w("---")
    w("")
    w("*Employee notices have been written to `output/notices/`. "
      "Run `python3 -m billing_agent.invoice_run` at month-end to generate the project invoice.*")
    w("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _infer_cycle(inputs: IngestionResult) -> str:
    import re
    m = re.search(r"(\d{4}-\d{2})", inputs.submission_file.name)
    return m.group(1) if m else "current"
