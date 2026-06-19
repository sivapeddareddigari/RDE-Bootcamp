"""
SMTP mailer for the billing agent.

Two high-level functions are called from the pipeline:

    send_submission_emails(notices_written, contacts)
        → employee exception notices → each employee's email  (HTML)
        → analyst summary            → billing analyst(s)     (HTML)

    send_invoice_emails(...)
        → cover email with totals + 3 attachments → billing analyst(s) (HTML)

Emails are sent as multipart/alternative (HTML + plain-text fallback).
Both functions are no-ops when EMAIL_ENABLED is false (default).
All send failures are logged — email never crashes the pipeline.
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
    else:
        run_logger.step(
            "Email — 0 messages sent (check EMAIL_ENABLED / SMTP credentials in .env)",
            "warn",
        )
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
    mailer = _Mailer(cfg or load_config())
    to     = [a.email for a in contacts.billing_analysts]
    if not to:
        log.warning("No billing analyst email addresses — invoice email skipped")
        return 0

    subject = f"[Billing Agent] Draft Invoice Ready — {project_id} {billing_month}"
    plain   = (
        f"The month-end billing run for {project_id} ({billing_month}) is complete.\n\n"
        f"  Submissions processed : {submission_count}\n"
        f"  Labour total          : ${labour_total:,.2f}\n"
        f"  Expense total         : ${expense_total:,.2f}\n"
        f"  Grand total           : ${grand_total:,.2f}\n"
        f"  Blocked items         : {blocked_count}\n\n"
        f"Files attached: {invoice_path.name}, {audit_path.name}, {exceptions_path.name}\n\n"
        "This is an automated message from the Billing Agent.\n"
    )
    html = _invoice_cover_html(
        project_id, billing_month, submission_count,
        labour_total, expense_total, grand_total, blocked_count,
        invoice_path, audit_path, exceptions_path,
    )
    attachments = [p for p in (invoice_path, audit_path, exceptions_path) if p.exists()]
    ok = mailer.send(to, subject, plain, html_body=html, attachments=attachments)
    if ok:
        run_logger.step(f"Email — invoice package sent to {len(to)} analyst(s)", "ok")
        return 1
    return 0


# ── Internal dispatch helpers ─────────────────────────────────────────────────

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

    body    = path.read_text(encoding="utf-8")
    cycle   = (_CYCLE_RE.search(body) or type("", (), {"group": lambda self, i: "current"})()).group(1)
    subject = f"[Action Required] Expense Claim Exceptions — {cycle}"
    ok      = mailer.send([emp.email], subject, body, html_body=_notice_to_html(body))
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
    name_m  = re.search(r"Submission:\*\*\s*(\S+)", body)
    sub_name = name_m.group(1) if name_m else path.stem
    subject = f"[Billing Agent] Submission Summary — {sub_name}"
    ok      = mailer.send(to, subject, body, html_body=_notice_to_html(body))
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
        html_body: Optional[str] = None,
        attachments: Optional[List[Path]] = None,
    ) -> bool:
        if not self._cfg.enabled:
            log.info(
                "EMAIL dry-run | To: %s | Subject: %s | Attachments: %s",
                to, subject, [p.name for p in (attachments or [])],
            )
            return True

        if not self._cfg.user or not self._cfg.password:
            log.warning("Email skipped — SMTP_USER / SMTP_PASSWORD not set in .env")
            return False

        try:
            # Outer container: mixed (needed when there are attachments)
            outer = MIMEMultipart("mixed")
            outer["From"]    = f"{self._cfg.from_name} <{self._cfg.from_addr}>"
            outer["To"]      = ", ".join(to)
            outer["Subject"] = subject

            # Inner alternative part: plain + HTML
            alt = MIMEMultipart("alternative")
            alt.attach(MIMEText(body, "plain", "utf-8"))
            if html_body:
                alt.attach(MIMEText(html_body, "html", "utf-8"))
            outer.attach(alt)

            for att in (attachments or []):
                if att.exists():
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(att.read_bytes())
                    encoders.encode_base64(part)
                    part.add_header(
                        "Content-Disposition",
                        f'attachment; filename="{att.name}"',
                    )
                    outer.attach(part)

            with smtplib.SMTP(self._cfg.host, self._cfg.port, timeout=30) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
                smtp.login(self._cfg.user, self._cfg.password)
                smtp.sendmail(self._cfg.from_addr, to, outer.as_string())

            log.info("Email sent → %s | %s", to, subject)
            return True

        except Exception as exc:
            log.warning("Email send failed (%s) — pipeline continues", exc)
            run_logger.step(f"Email SMTP error: {exc}", "error")
            return False


# ── HTML generation ───────────────────────────────────────────────────────────

_ACCENT = {
    "blocking": "#c0392b",
    "review":   "#d35400",
    "default":  "#2c3e50",
}

_BASE_STYLE = (
    "font-family:Arial,Helvetica,sans-serif;"
    "font-size:14px;color:#333333;"
    "max-width:780px;margin:0 auto;padding:20px;"
    "line-height:1.5;"
)


def _notice_to_html(md: str) -> str:
    """Convert a billing-agent Markdown notice to styled HTML."""
    lines   = md.splitlines()
    out     = [
        f'<!DOCTYPE html><html><body style="{_BASE_STYLE}">',
    ]
    i                  = 0
    in_table           = False
    table_header: Optional[List[str]] = None
    table_rows:   List[List[str]]     = []
    section_accent     = _ACCENT["default"]

    def flush_table():
        nonlocal in_table, table_header, table_rows
        if in_table and table_header:
            out.append(_table_html(table_header, table_rows, section_accent))
        in_table     = False
        table_header = None
        table_rows   = []

    while i < len(lines):
        raw     = lines[i]
        stripped = raw.strip()

        # H1 title
        if re.match(r'^# (?!#)', stripped):
            flush_table()
            text = stripped[2:]
            out.append(
                f'<h2 style="color:#1a5276;border-bottom:2px solid #1a5276;'
                f'padding-bottom:6px;margin-top:0">{text}</h2>'
            )

        # Metadata block: **Key:** Value  (collect all consecutive lines)
        elif re.match(r'^\*\*[^*]+:\*\*', stripped):
            flush_table()
            meta = []
            while i < len(lines) and re.match(r'^\*\*[^*]+:\*\*', lines[i].strip()):
                m = re.match(r'^\*\*([^*]+):\*\*\s*(.*)', lines[i].strip().rstrip("  "))
                if m:
                    meta.append((m.group(1), m.group(2)))
                i += 1
            out.append(
                '<table style="background:#eaf2ff;padding:0;margin:12px 0 16px;'
                'border-left:4px solid #2980b9;width:100%;border-collapse:collapse">'
            )
            for k, v in meta:
                out.append(
                    f'<tr>'
                    f'<td style="padding:4px 14px 4px 10px;font-weight:bold;'
                    f'color:#1a5276;white-space:nowrap;width:140px">{k}:</td>'
                    f'<td style="padding:4px 0;color:#333">{v}</td>'
                    f'</tr>'
                )
            out.append("</table>")
            continue  # i already advanced inside the while loop

        # H2 section headers
        elif stripped.startswith("## "):
            flush_table()
            text = stripped[3:]
            if "Blocking" in text or "⚠" in text:
                section_accent = _ACCENT["blocking"]
                bg = "#fdf3f2"
            elif "under review" in text.lower():
                section_accent = _ACCENT["review"]
                bg = "#fef9ec"
            else:
                section_accent = _ACCENT["default"]
                bg = "#f4f6f7"
            out.append(
                f'<h3 style="color:{section_accent};border-left:4px solid {section_accent};'
                f'background:{bg};padding:8px 12px;margin:20px 0 8px;border-radius:0 4px 4px 0">'
                f'{text}</h3>'
            )

        # Table rows
        elif stripped.startswith("|"):
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            if all(set(c) <= set("-: ") for c in cells):
                pass  # alignment separator — skip
            elif table_header is None:
                table_header = cells
                in_table     = True
            else:
                table_rows.append(cells)

        # Horizontal rule
        elif stripped == "---":
            flush_table()
            out.append('<hr style="border:none;border-top:1px solid #ddd;margin:16px 0">')

        # Non-empty text
        elif stripped:
            flush_table()
            converted = _inline_md(stripped)
            if re.match(r"^Hi \w+,?$", stripped):
                out.append(
                    f'<p style="font-size:15px;margin:16px 0 8px">'
                    f'<strong>{converted}</strong></p>'
                )
            else:
                out.append(f'<p style="margin:6px 0">{converted}</p>')

        i += 1

    flush_table()
    out.append(
        '<p style="color:#888;font-size:12px;margin-top:24px;border-top:1px solid #ddd;'
        'padding-top:10px">This is an automated message from the Billing Agent.</p>'
    )
    out.append("</body></html>")
    return "\n".join(out)


def _invoice_cover_html(
    project_id: str,
    billing_month: str,
    submission_count: int,
    labour_total: float,
    expense_total: float,
    grand_total: float,
    blocked_count: int,
    invoice_path: Path,
    audit_path: Path,
    exceptions_path: Path,
) -> str:
    status_color = "#c0392b" if blocked_count > 0 else "#27ae60"
    blocked_note = (
        f'<p style="color:#c0392b;font-weight:bold">⚠ {blocked_count} item(s) are '
        f'blocking the invoice — see the exceptions report.</p>'
        if blocked_count > 0 else ""
    )
    return f"""<!DOCTYPE html>
<html><body style="{_BASE_STYLE}">
<h2 style="color:#1a5276;border-bottom:2px solid #1a5276;padding-bottom:6px;margin-top:0">
  Draft Invoice Ready — {project_id} {billing_month}
</h2>
<p>The month-end billing run for <strong>{project_id}</strong> ({billing_month}) is complete.</p>

<table style="width:100%;border-collapse:collapse;margin:16px 0;font-size:14px">
  <tr style="background:#eaf2ff">
    <td style="padding:9px 14px;font-weight:bold;border-bottom:1px solid #d6e8ff">Submissions processed</td>
    <td style="padding:9px 14px;text-align:right;border-bottom:1px solid #d6e8ff">{submission_count}</td>
  </tr>
  <tr>
    <td style="padding:9px 14px;font-weight:bold;border-bottom:1px solid #eee">Labour total</td>
    <td style="padding:9px 14px;text-align:right;font-family:monospace;border-bottom:1px solid #eee">${labour_total:,.2f}</td>
  </tr>
  <tr style="background:#eaf2ff">
    <td style="padding:9px 14px;font-weight:bold;border-bottom:1px solid #d6e8ff">Expense total</td>
    <td style="padding:9px 14px;text-align:right;font-family:monospace;border-bottom:1px solid #d6e8ff">${expense_total:,.2f}</td>
  </tr>
  <tr style="border-top:2px solid #2c3e50">
    <td style="padding:10px 14px;font-weight:bold;font-size:15px">Grand total</td>
    <td style="padding:10px 14px;text-align:right;font-family:monospace;font-size:15px;font-weight:bold">${grand_total:,.2f}</td>
  </tr>
  <tr>
    <td style="padding:9px 14px;font-weight:bold;color:{status_color}">Blocked items</td>
    <td style="padding:9px 14px;text-align:right;font-weight:bold;color:{status_color}">{blocked_count}</td>
  </tr>
</table>

{blocked_note}

<p style="margin:16px 0 6px;font-weight:bold">Files attached:</p>
<table style="font-size:13px;border-collapse:collapse">
  <tr>
    <td style="padding:4px 12px 4px 0;font-family:monospace;color:#1a5276">{invoice_path.name}</td>
    <td style="padding:4px 0;color:#555">Draft invoice</td>
  </tr>
  <tr>
    <td style="padding:4px 12px 4px 0;font-family:monospace;color:#1a5276">{audit_path.name}</td>
    <td style="padding:4px 0;color:#555">Full audit trail (CSV)</td>
  </tr>
  <tr>
    <td style="padding:4px 12px 4px 0;font-family:monospace;color:#1a5276">{exceptions_path.name}</td>
    <td style="padding:4px 0;color:#555">Exception summary</td>
  </tr>
</table>

<p style="color:#888;font-size:12px;margin-top:24px;border-top:1px solid #ddd;padding-top:10px">
  This is an automated message from the Billing Agent.
</p>
</body></html>"""


def _table_html(
    headers: List[str],
    rows: List[List[str]],
    accent: str,
) -> str:
    if not headers:
        return ""
    parts = [
        f'<table style="width:100%;border-collapse:collapse;font-size:13px;margin:8px 0 16px">',
        "<thead><tr>",
    ]
    for h in headers:
        align = "right" if "Amount" in h else "left"
        parts.append(
            f'<th style="background:{accent};color:white;padding:8px 11px;'
            f'text-align:{align};font-weight:600">{h}</th>'
        )
    parts.append("</tr></thead><tbody>")

    for idx, row in enumerate(rows):
        bg = "#f9f9f9" if idx % 2 == 0 else "white"
        parts.append(f'<tr style="background:{bg}">')
        for j, cell in enumerate(row):
            header = headers[j] if j < len(headers) else ""
            if "Amount" in header:
                parts.append(
                    f'<td style="padding:7px 11px;border-bottom:1px solid #eee;'
                    f'text-align:right;font-family:monospace;white-space:nowrap">{cell}</td>'
                )
            elif j == 0:  # Transaction ID / first col
                parts.append(
                    f'<td style="padding:7px 11px;border-bottom:1px solid #eee;'
                    f'font-family:monospace;white-space:nowrap;color:#1a5276">{cell}</td>'
                )
            else:
                parts.append(
                    f'<td style="padding:7px 11px;border-bottom:1px solid #eee">{cell}</td>'
                )
        parts.append("</tr>")

    parts.append("</tbody></table>")
    return "\n".join(parts)


def _inline_md(text: str) -> str:
    """Convert inline Markdown (**bold**, *italic*) to HTML."""
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'\*([^*]+?)\*',  r'<em>\1</em>',        text)
    return text
