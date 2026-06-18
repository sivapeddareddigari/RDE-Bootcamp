"""
Project-level invoice builder — month-end activity.

Triggered by billing_agent/invoice_run.py, NOT by the per-submission watcher.

Reads every completed submission CSV for a given project + billing month,
re-runs the full pipeline (Phases 1–4) for each, then aggregates all
approved transactions into ONE draft invoice and ONE exception report.

Output files (written to output/):
  draft-invoice-{project_id}-{month}.md
  exceptions-report-{project_id}-{month}.md
  audit-trail-{project_id}-{month}.csv
"""

import csv
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from billing_agent import run_logger
from billing_agent.config import COMPLETED_DIR, OUTPUT_DIR
from billing_agent.exceptions.models import ExceptionItem, ExceptionReport
from billing_agent.ingestion.contacts_loader import ContactDirectory
from billing_agent.ingestion.loader import IngestionResult, load_inputs
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

_AUDIT_FIELDS = [
    "submission", "transaction_id", "employee_id", "employee_name",
    "type", "description", "original_amount", "approved_amount", "status",
    "rule_id", "exception_type", "override_applied", "override_source",
    "matched_doc_id", "match_confidence", "amount_delta", "note",
]


# ── Public return type ────────────────────────────────────────────────────────

@dataclass
class BuildResult:
    project_id:      str
    billing_month:   str
    invoice_path:    Path
    audit_path:      Path
    exceptions_path: Path
    labour_total:    float
    expense_total:   float
    grand_total:     float
    blocked_count:   int
    submission_count: int


# ── Public entry point ────────────────────────────────────────────────────────

def build(
    project_id:    str,
    billing_month: str,
    contacts:      ContactDirectory,
    submissions_dir: Path = COMPLETED_DIR,
) -> BuildResult:
    """
    Aggregate all completed submissions for project_id + billing_month and
    produce a single draft invoice, audit trail, and exceptions report.

    billing_month format: "YYYY-MM"
    """
    log.info("Invoice build — project=%s  month=%s", project_id, billing_month)

    # Discover all completed submission CSVs for this project + month
    csv_files = _find_submissions(submissions_dir, billing_month)
    if not csv_files:
        raise FileNotFoundError(
            f"No completed submissions found in {submissions_dir} "
            f"for billing month {billing_month}"
        )
    log.info("  found %d submission(s)", len(csv_files))

    # Re-run Phases 1–4 for each submission and aggregate results
    all_inputs:    List[IngestionResult] = []
    all_rr:        List[RuleResult]      = []
    all_mr:        List[MatchResult]     = []
    all_er_items:  List[ExceptionItem]   = []

    total_tx = 0
    total_clean = 0
    total_auto_resolved: List[ExceptionItem] = []
    total_pl_rejections: List[ExceptionItem] = []
    total_hard_rejections: List[ExceptionItem] = []
    total_escalate_analyst: List[ExceptionItem] = []
    total_escalate_employee: List[ExceptionItem] = []
    total_escalate_pl: List[ExceptionItem] = []

    for csv_path in csv_files:
        from billing_agent.rules import rule_engine
        from billing_agent.matching import reconcile
        from billing_agent.exceptions import run as detect_exceptions

        inputs = load_inputs(csv_path)
        rr     = rule_engine.run(inputs)
        mr     = reconcile(inputs, rr)
        er     = detect_exceptions(inputs, rr, mr)

        all_inputs.append(inputs)
        all_rr.extend(rr)
        all_mr.extend(mr)

        total_tx            += er.total_transactions
        total_clean         += er.clean_count
        total_auto_resolved  += er.auto_resolved
        total_pl_rejections  += er.pl_rejections
        total_hard_rejections += er.hard_rejections
        total_escalate_analyst  += er.escalate_analyst
        total_escalate_employee += er.escalate_employee
        total_escalate_pl       += er.escalate_pl

    # Build a combined tx lookup (all submissions)
    tx_by_id: Dict[str, Transaction] = {}
    for inputs in all_inputs:
        for tx in inputs.transactions:
            tx_by_id[tx.transaction_id] = tx

    mr_by_id: Dict[str, MatchResult] = {m.transaction_id: m for m in all_mr}

    # Combined exception report (project-wide)
    combined_er = ExceptionReport(
        submission_file   = f"{project_id}/{billing_month}",
        generated_at      = datetime.now(timezone.utc).isoformat(timespec="seconds"),
        total_transactions = total_tx,
        clean_count       = total_clean,
        auto_resolved     = total_auto_resolved,
        pl_rejections     = total_pl_rejections,
        hard_rejections   = total_hard_rejections,
        escalate_analyst  = total_escalate_analyst,
        escalate_employee = total_escalate_employee,
        escalate_pl       = total_escalate_pl,
    )

    # Partition billable vs non-billable across all submissions
    billable  = [r for r in all_rr if r.status == "APPROVE"]
    labour    = [r for r in billable if tx_by_id[r.transaction_id].is_labor]
    expenses  = [r for r in billable if tx_by_id[r.transaction_id].is_expense]
    non_bill  = [r for r in all_rr   if r.status != "APPROVE"]

    labour_total  = round(sum(r.approved_amount for r in labour),  2)
    expense_total = round(sum(r.approved_amount for r in expenses), 2)
    grand_total   = round(labour_total + expense_total, 2)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    invoice_path    = OUTPUT_DIR / f"draft-invoice-{project_id}-{billing_month}.md"
    audit_path      = OUTPUT_DIR / f"audit-trail-{project_id}-{billing_month}.csv"
    exceptions_path = OUTPUT_DIR / f"exceptions-report-{project_id}-{billing_month}.md"

    _write_invoice(
        invoice_path, project_id, billing_month, labour, expenses, non_bill,
        tx_by_id, mr_by_id, contacts, labour_total, expense_total, grand_total, ts,
    )
    _write_audit(audit_path, all_rr, tx_by_id, mr_by_id, contacts, all_inputs)
    _write_exceptions(exceptions_path, combined_er, contacts, ts)

    result = BuildResult(
        project_id       = project_id,
        billing_month    = billing_month,
        invoice_path     = invoice_path,
        audit_path       = audit_path,
        exceptions_path  = exceptions_path,
        labour_total     = labour_total,
        expense_total    = expense_total,
        grand_total      = grand_total,
        blocked_count    = len(combined_er.blocking),
        submission_count = len(csv_files),
    )

    _validate(result, all_rr, tx_by_id)

    run_logger.step(
        f"Project invoice built — {len(csv_files)} submission(s) | "
        f"labour ${labour_total:,.2f}  expenses ${expense_total:,.2f}  "
        f"total ${grand_total:,.2f}  ({len(combined_er.blocking)} blocked)",
        "ok" if len(combined_er.blocking) == 0 else "warn",
    )
    return result


# ── Submission discovery ───────────────────────────────────────────────────────

def _find_submissions(completed_dir: Path, billing_month: str) -> List[Path]:
    """Return all CSVs in completed_dir whose name contains the billing_month."""
    if not completed_dir.exists():
        return []
    return sorted(
        f for f in completed_dir.iterdir()
        if f.suffix.lower() == ".csv" and billing_month in f.name
    )


# ── Draft invoice (Markdown) ──────────────────────────────────────────────────

def _write_invoice(
    path: Path,
    project_id: str,
    billing_month: str,
    labour: List[RuleResult],
    expenses: List[RuleResult],
    non_bill: List[RuleResult],
    tx_by_id: Dict[str, Transaction],
    mr_by_id: Dict[str, MatchResult],
    contacts: ContactDirectory,
    labour_total: float,
    expense_total: float,
    grand_total: float,
    ts: str,
) -> None:
    lines: List[str] = []
    w = lines.append

<<<<<<< Updated upstream
    w("# Draft Invoice — Coastal Greenway Feasibility Study (PRJ-NS-7421)")
    w("")
    w(f"**Invoice for:** Northstar Civic Group, Inc.  ")
    w(f"**Project:** Coastal Greenway Feasibility Study — Phase 2 ({project_id})  ")
    w(f"**Billing cycle:** {billing_month}  ")
    w(f"**Generated:** {ts}  ")
    w(f"**Currency:** USD  ")
    w(f"**Status:** DRAFT — subject to final SAP reconciliation  ")
    w("")
    w("---")
    w("")
=======
    proj = inputs.contract_sap_project
    _hdr(f"# Draft Invoice — Coastal Greenway Feasibility Study ({proj})")
    _hdr("")
    _hdr(f"**Invoice for:** Northstar Civic Group, Inc.  ")
    _hdr(f"**Project:** Coastal Greenway Feasibility Study — Phase 2 ({proj})  ")
    _hdr(f"**Cycle:** {_infer_cycle(inputs)}  ")
    _hdr(f"**Submission:** {inputs.submission_file.name}  ")
    _hdr(f"**Generated:** {ts}  ")
    _hdr(f"**Currency:** USD  ")
    _hdr("")
    _hdr("---")
    _hdr("")
>>>>>>> Stashed changes

    # Section A — Labour
    w("## Section A — Labour")
    w("")
    if labour:
        w("| Employee | Role | Description | Hrs | Rate (USD) | Amount (USD) |")
        w("|----------|------|-------------|----:|-----------:|-------------:|")
        for r in sorted(labour, key=lambda r: tx_by_id[r.transaction_id].employee_id):
            tx   = tx_by_id[r.transaction_id]
            emp  = contacts.employee(tx.employee_id)
            name = emp.name if emp else tx.employee_id
            role = _ROLE_LABEL.get(tx.role_code, tx.role_code)
            note = " ¹" if r.override_applied else ""
            w(f"| {name} | {role} | {tx.description}{note} | {tx.quantity:.1f} "
              f"| {tx.rate:,.2f} | {r.approved_amount:,.2f} |")
    else:
        w("*No billable labour transactions.*")
    w("")
    w(f"**Labour subtotal: USD {labour_total:,.2f}**")
    w("")

    excluded_labour = [r for r in non_bill if tx_by_id[r.transaction_id].is_labor]
    if excluded_labour:
        w("**Excluded labour:**")
        w("")
        for r in excluded_labour:
            tx  = tx_by_id[r.transaction_id]
            emp = contacts.employee(tx.employee_id)
            name = emp.name if emp else tx.employee_id
            w(f"- {r.transaction_id} ({name}) — {tx.description} ({r.rule_id}): {r.note}")
        w("")

    w("---")
    w("")

    # Section B — Expenses
    w("## Section B — Reimbursable Expenses")
    w("")
    if expenses:
        by_cat: Dict[str, List[RuleResult]] = {c: [] for c in _CAT_ORDER}
        for r in expenses:
            by_cat[_categorize(tx_by_id[r.transaction_id])].append(r)

        for cat in _CAT_ORDER:
            rows = by_cat[cat]
            if not rows:
                continue
            w(f"### {_CAT_LABEL[cat]}")
            w("")
            w("| Employee | Description | Amount (USD) | Note |")
            w("|----------|-------------|-------------:|------|")
            for r in rows:
                tx   = tx_by_id[r.transaction_id]
                emp  = contacts.employee(tx.employee_id)
                name = emp.name if emp else tx.employee_id
                note = _expense_note(r, mr_by_id.get(r.transaction_id))
                if cat == "SUBCONTRACTOR" and _has_markup(r):
                    cost   = r.original_amount
                    markup = round(r.approved_amount - cost, 2)
                    w(f"| {name} | {tx.description} (cost) | {cost:,.2f} | {note} |")
                    w(f"| {name} | {tx.description} (markup {_MARKUP_PCT:.0%}) | {markup:,.2f} | |")
                else:
                    w(f"| {name} | {tx.description} | {r.approved_amount:,.2f} | {note} |")
            w("")
    else:
        w("*No billable expense transactions.*")
    w("")
    w(f"**Expense subtotal: USD {expense_total:,.2f}**")
    w("")
    w("---")
    w("")

    # Totals
    w("## Totals")
    w("")
    w("| Section | Amount (USD) |")
    w("|---------|-------------:|")
    w(f"| Labour | {labour_total:,.2f} |")
    w(f"| Reimbursable expenses | {expense_total:,.2f} |")
    w(f"| **Grand total** | **{grand_total:,.2f}** |")
    w("")

    # Excluded / blocked summary
    excluded_exp = [r for r in non_bill if tx_by_id[r.transaction_id].is_expense]
    if excluded_labour or excluded_exp:
        w("---")
        w("")
        w("## Excluded / Blocked items")
        w("")
        w(f"*{len(non_bill)} item(s) not included above. See exceptions report for details.*")
        w("")
        w("| Transaction | Employee | Description | Status | Rule | Note |")
        w("|-------------|----------|-------------|--------|------|------|")
        for r in non_bill:
            tx  = tx_by_id[r.transaction_id]
            emp = contacts.employee(tx.employee_id)
            name = emp.name if emp else tx.employee_id
            w(f"| {r.transaction_id} | {name} | {tx.description[:40]} "
              f"| {r.status} | {r.rule_id} | {r.note[:60]} |")

    if r.override_applied if labour else False:
        w("")
        w("¹ Amount adjusted per PL written approval.")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.debug("  invoice → %s", path.name)


# ── Audit trail (CSV) ─────────────────────────────────────────────────────────

def _write_audit(
    path: Path,
    rule_results: List[RuleResult],
    tx_by_id: Dict[str, Transaction],
    mr_by_id: Dict[str, MatchResult],
    contacts: ContactDirectory,
    all_inputs: List[IngestionResult],
) -> None:
    stem_by_tx: Dict[str, str] = {}
    for inputs in all_inputs:
        for tx in inputs.transactions:
            stem_by_tx[tx.transaction_id] = inputs.submission_file.stem

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_AUDIT_FIELDS)
        writer.writeheader()
        for r in rule_results:
            tx  = tx_by_id[r.transaction_id]
            mr  = mr_by_id.get(r.transaction_id)
            emp = contacts.employee(tx.employee_id)
            writer.writerow({
                "submission":       stem_by_tx.get(r.transaction_id, ""),
                "transaction_id":   r.transaction_id,
                "employee_id":      tx.employee_id,
                "employee_name":    emp.name if emp else "",
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
    log.debug("  audit → %s", path.name)


# ── Exceptions report (Markdown) ──────────────────────────────────────────────

def _write_exceptions(
    path: Path,
    report: ExceptionReport,
    contacts: ContactDirectory,
    ts: str,
) -> None:
    lines: List[str] = []
    w = lines.append

    w(f"# Project Exceptions Report — {report.submission_file}")
    w("")
    w(f"**Generated:** {ts}  ")
    w(f"**Total transactions:** {report.total_transactions}  ")
    w(f"**Clean (no exception):** {report.clean_count}  ")
    w(f"**Unresolved escalations:** {report.unresolved_count}  ")
    w(f"**Blocking invoice:** {len(report.blocking)}  ")
    w("")
    w("---")
    w("")

    def _section(title: str, items: List[ExceptionItem], desc: str) -> None:
        if not items:
            return
        w(f"## {title} ({len(items)})")
        w("")
        w(f"*{desc}*")
        w("")
        w("| Transaction | Employee | Description | Amount | Rule | Override | Note |")
        w("|-------------|----------|-------------|-------:|------|----------|------|")
        for item in items:
            emp  = contacts.employee(item.employee_id)
            name = emp.name if emp else item.employee_id
            blk  = " ⚠ BLOCKS" if item.blocks_invoice else ""
            src  = item.override_source if item.override_applied else "—"
            w(f"| {item.transaction_id} | {name} | {item.description[:35]} "
              f"| {item.original_amount:,.2f} | {item.rule_id}{blk} | {src} | {item.note[:55]} |")
        w("")

    _section("Auto-resolved", report.auto_resolved,
             "Resolved by PL instruction or prior-exception pattern.")
    _section("PL rejections", report.pl_rejections,
             "Explicitly rejected by PL instruction.")
    _section("Hard rejections (contract)", report.hard_rejections,
             "Contract violation — employee must remove from SAP.")
    _section("Escalate → PL", report.escalate_pl,
             "Require PL written approval before invoicing.")
    _section("Escalate → Analyst", report.escalate_analyst,
             "Require analyst review (amount, rate, or FX discrepancy).")
    _section("Escalate → Employee", report.escalate_employee,
             "Employee must correct in SAP. Exception notices sent.")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.debug("  exceptions → %s", path.name)


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
    return (r.exception_type == "SUBCONTRACTOR_MARKUP"
            and r.override_applied
            and r.approved_amount > r.original_amount)


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
