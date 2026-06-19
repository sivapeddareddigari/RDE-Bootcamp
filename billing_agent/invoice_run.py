"""
Month-end invoice generation — project-level CLI trigger.

Usage:
    python3 -m billing_agent.invoice_run --project PRJ-NS-7421 --month 2026-04

Reads all completed submission CSVs for the given project + billing month,
re-runs Phases 1–4 for each, then produces:
    output/draft-invoice-{project}-{month}.md
    output/exceptions-report-{project}-{month}.md
    output/audit-trail-{project}-{month}.csv

Run this once at month-end after all employee submissions have been processed
and employees have had time to correct their SAP entries.
"""

import argparse
import logging
import sys
from pathlib import Path

from billing_agent.config import COMPLETED_DIR, OUTPUT_DIR
from billing_agent.email.mailer import send_invoice_emails
from billing_agent.ingestion.contacts_loader import load_contacts
from billing_agent.output.invoice_builder import build

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate month-end project invoice from completed submissions."
    )
    parser.add_argument(
        "--project", required=True,
        help="Project ID to invoice (e.g. PRJ-NS-7421)",
    )
    parser.add_argument(
        "--month", required=True,
        help="Billing month in YYYY-MM format (e.g. 2026-04)",
    )
    parser.add_argument(
        "--submissions-dir", default=str(COMPLETED_DIR),
        help=f"Directory of completed submission CSVs (default: {COMPLETED_DIR})",
    )
    args = parser.parse_args()

    import re
    if not re.fullmatch(r"\d{4}-\d{2}", args.month):
        log.error("--month must be in YYYY-MM format (e.g. 2026-04)")
        sys.exit(1)

    contacts = load_contacts()
    submissions_dir = Path(args.submissions_dir)

    log.info("=" * 60)
    log.info("Invoice Run — %s / %s", args.project, args.month)
    log.info("  Reading from : %s", submissions_dir)
    log.info("  Writing to   : %s", OUTPUT_DIR)
    log.info("=" * 60)

    try:
        result = build(
            project_id      = args.project,
            billing_month   = args.month,
            contacts        = contacts,
            submissions_dir = submissions_dir,
        )
    except FileNotFoundError as e:
        log.error("%s", e)
        sys.exit(1)

    log.info("")
    log.info("Invoice generation complete")
    log.info("  Submissions processed : %d", result.submission_count)
    log.info("  Labour total          : $%s", f"{result.labour_total:,.2f}")
    log.info("  Expense total         : $%s", f"{result.expense_total:,.2f}")
    log.info("  Grand total           : $%s", f"{result.grand_total:,.2f}")
    log.info("  Blocked items         : %d", result.blocked_count)
    log.info("")
    log.info("  Draft invoice   → %s", result.invoice_path)
    log.info("  Audit trail     → %s", result.audit_path)
    log.info("  Exceptions      → %s", result.exceptions_path)

    send_invoice_emails(
        invoice_path    = result.invoice_path,
        audit_path      = result.audit_path,
        exceptions_path = result.exceptions_path,
        labour_total    = result.labour_total,
        expense_total   = result.expense_total,
        grand_total     = result.grand_total,
        blocked_count   = result.blocked_count,
        submission_count= result.submission_count,
        billing_month   = args.month,
        project_id      = args.project,
        contacts        = contacts,
    )

    if result.blocked_count > 0:
        log.warning(
            "%d item(s) are blocking the invoice — see exceptions report.",
            result.blocked_count,
        )
        sys.exit(2)


if __name__ == "__main__":
    main()
