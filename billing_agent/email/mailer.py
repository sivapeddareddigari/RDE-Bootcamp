"""
SMTP mailer for the billing agent.

Two high-level functions are called from the pipeline:

    send_submission_emails(notices_written, contacts)
        → called after each submission run (supervisor)
        → employee exception notices → each employee's email
        → analyst summary            → billing analyst(s)

    send_invoice_emails(result, contacts)
        → called after month-end invoice_run
        → cover email with totals + 3 attachments → billing analyst(s)

Both functions are no-ops when EMAIL_ENABLED is false (default).
All send failures are logged and swallowed — email never crashes the pipeline.
"""

import logging
import re
import smtplib
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import List, Optional

from billing_agent import run_logger
from billing_agent.email.config import EmailConfig, load_config
from billing_agent.ingestion.contacts_loader import ContactDirectory

log = logging.getLogger(__name__)

_EMP_ID_RE = re.compile(r"exception-notice-(E-\d+)-")
_CYCLE_RE  = re.compile(r"\*\*Billing cycle:\*\*\s*(\d{4}-\d{2})")


# ── Public pipeline hooks ─────────────────────────────────────────────────────

def send_submission_emails(
    notices_written: List[Path],
    contacts: ContactDirectory,
    cfg: Optional[EmailConfig] = None,
) -> int:
    """
    Dispatch per-submission emails.
    Returns the number of emails successfully sent (or logged in dry-run).
    """
    mailer = _Mailer(cfg or load_config())
    sent   = 0

    for path in notices_written:
        if not path.exists():
            continue

        if "exception-notice-" in path.name:
            sent += _send_employee_notice(mailer, path, contacts)

        elif "analyst-summary-" in path.name:
            sent += _send_analyst_summary(mailer, path, contacts)

    if sent:
        run_logger.step(f"Email — {sent} message(s) dispatched", "ok")
    return sent


def send_invoice_emails(
    invoice_path: Path,
    audit_path: Path,
    exceptions_path: Path,
    labour_total: float,
    expense_total: float,
    grand_total: float,
    blocked_count: int,
    submission_count: int,
    billing_month: str,
    project_id: str,
    contacts: ContactDirectory,
    cfg: Optional[EmailConfig] = None,
) -> int:
    """
    Send the month-end invoice package to billing analysts.
    Attaches draft invoice (MD), audit trail (CSV), and exceptions report (MD).
    Returns 1 on success, 0 on failure/dry-run-only.
    """
    mailer  = _Mailer(cfg or load_config())
    to      = [a.email for a in contacts.billing_analysts]
    if not to:
        log.warning("No billing analyst email addresses — invoice email skipped")
        return 0

    subject = f"[Billing Agent] Draft Invoice Ready — {project_id} {billing_month}"
    body    = (
        f"The month-end billing run for {project_id} ({billing_month}) is complete.\n\n"
        f"  Submissions processed : {submission_count}\n"
        f"  Labour total          : ${labour_total:,.2f}\n"
        f"  Expense total         : ${expense_total:,.2f}\n"
        f"  Grand total           : ${grand_total:,.2f}\n"
        f"  Blocked items         : {blocked_count}\n\n"
        "Files attached:\n"
        f"  • {invoice_path.name}    — draft invoice\n"
        f"  • {audit_path.name}     — full audit trail\n"
        f"  • {exceptions_path.name} — exception summary\n\n"
        "This is an automated message from the Billing Agent.\n"
    )
    attachments = [p for p in (invoice_path, audit_path, exceptions_path) if p.exists()]
    ok = mailer.send(to, subject, body, attachments=attachments)
    if ok:
        run_logger.step(f"Email — invoice package sent to {len(to)} analyst(s)", "ok")
        return 1
    return 0


# ── Internal helpers ──────────────────────────────────────────────────────────

def _send_employee_notice(
    mailer: "_Mailer",
    path: Path,
    contacts: ContactDirectory,
) -> int:
    m = _EMP_ID_RE.search(path.name)
    if not m:
        log.warning("Could not extract employee_id from %s — skipping", path.name)
        return 0
    emp_id = m.group(1)
    emp    = contacts.employee(emp_id)
    if not emp:
        log.warning("No contact record for %s — skipping notice email", emp_id)
        return 0

    body  = path.read_text(encoding="utf-8")
    cycle = (_CYCLE_RE.search(body) or type("", (), {"group": lambda self, i: "current"})()).group(1)
    subject = f"[Action Required] Expense Claim Exceptions — {cycle}"

    ok = mailer.send([emp.email], subject, body)
    return 1 if ok else 0


def _send_analyst_summary(
    mailer: "_Mailer",
    path: Path,
    contacts: ContactDirectory,
) -> int:
    analysts = contacts.billing_analysts
    if not analysts:
        return 0
    to      = [a.email for a in analysts]
    body    = path.read_text(encoding="utf-8")
    # Extract submission name from first H1 or filename
    name_m  = re.search(r"Submission:\*\*\s*(\S+)", body)
    sub_name = name_m.group(1) if name_m else path.stem
    subject = f"[Billing Agent] Submission Summary — {sub_name}"

    ok = mailer.send(to, subject, body)
    return 1 if ok else 0


# ── Core SMTP sender ──────────────────────────────────────────────────────────

class _Mailer:
    def __init__(self, cfg: EmailConfig):
        self._cfg = cfg

    def send(
        self,
        to: List[str],
        subject: str,
        body: str,
        attachments: Optional[List[Path]] = None,
    ) -> bool:
        """
        Send a plain-text email with optional file attachments.
        In dry-run mode (EMAIL_ENABLED=false) logs the intent and returns True.
        Returns True on success, False on any failure.
        """
        if not self._cfg.enabled:
            log.info(
                "EMAIL dry-run | To: %s | Subject: %s | Attachments: %s",
                to, subject, [p.name for p in (attachments or [])],
            )
            return True

        if not self._cfg.user or not self._cfg.password:
            log.warning(
                "Email skipped — SMTP_USER / SMTP_PASSWORD not set. "
                "Add them to .env or export as environment variables."
            )
            return False

        try:
            msg            = MIMEMultipart("mixed")
            msg["From"]    = f"{self._cfg.from_name} <{self._cfg.from_addr}>"
            msg["To"]      = ", ".join(to)
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain", "utf-8"))

            for att in (attachments or []):
                if att.exists():
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(att.read_bytes())
                    encoders.encode_base64(part)
                    part.add_header(
                        "Content-Disposition",
                        f'attachment; filename="{att.name}"',
                    )
                    msg.attach(part)

            with smtplib.SMTP(self._cfg.host, self._cfg.port, timeout=30) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
                smtp.login(self._cfg.user, self._cfg.password)
                smtp.sendmail(self._cfg.from_addr, to, msg.as_string())

            log.info("Email sent → %s | %s", to, subject)
            return True

        except Exception as exc:
            log.warning("Email send failed (%s) — pipeline continues", exc)
            return False
