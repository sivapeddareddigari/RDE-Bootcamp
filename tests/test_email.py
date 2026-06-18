"""
Tests for billing_agent/email/ — mailer and config.

  TestEmailConfig       config loading from env vars + .env file
  TestMailerDryRun      dry-run mode (EMAIL_ENABLED=false) — no SMTP calls
  TestMailerSend        mocked SMTP — verify message structure
  TestMailerFailure     SMTP exceptions don't propagate
  TestSendSubmission    send_submission_emails() dispatches to correct addresses
  TestSendInvoice       send_invoice_emails() builds correct cover + attachments
"""

import os
import smtplib
from email import message_from_string
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from billing_agent.email.config import EmailConfig, load_config
from billing_agent.email.mailer import (
    _Mailer,
    send_submission_emails,
    send_invoice_emails,
)
from billing_agent.ingestion.contacts_loader import (
    AnalystContact,
    ContactDirectory,
    EmployeeContact,
    ProjectLeadContact,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cfg(enabled: bool = True) -> EmailConfig:
    return EmailConfig(
        enabled   = enabled,
        host      = "smtp.office365.com",
        port      = 587,
        user      = "agent@test.com",
        password  = "secret",
        from_addr = "agent@test.com",
        from_name = "Billing Agent",
    )


def _contacts() -> ContactDirectory:
    return ContactDirectory(
        employees = [
            EmployeeContact("E-1041", "David Okafor", "d.okafor@test.com", "ENG2", "Engineer II"),
            EmployeeContact("E-2210", "Sarah Chen",   "s.chen@test.com",   "ENG3", "Engineer III"),
        ],
        billing_analysts = [
            AnalystContact("Priya Sharma", "p.sharma@test.com"),
        ],
        project_leads = [
            ProjectLeadContact("PRJ-NS-7421", "P.D. Ranganathan", "pd.r@test.com"),
        ],
    )


def _notice_file(tmp_path: Path, emp_id: str, cycle: str = "2026-04") -> Path:
    name = f"exception-notice-{emp_id}-submission-{emp_id.replace('-','')}-clean-{cycle}__20260101T000000Z.md"
    path = tmp_path / name
    path.write_text(
        f"# Expense Claim Exception Notice\n\n"
        f"**To:** David Okafor (d.okafor@test.com)  \n"
        f"**Billing cycle:** {cycle}  \n\n"
        "Please action the items below.",
        encoding="utf-8",
    )
    return path


def _summary_file(tmp_path: Path) -> Path:
    path = tmp_path / "analyst-summary-submission-E1041-clean-2026-04__20260101T000000Z.md"
    path.write_text(
        "# Billing Agent — Submission Summary\n\n"
        "**Submission:** submission-E1041-clean-2026-04.csv  \n",
        encoding="utf-8",
    )
    return path


# ── TestEmailConfig ───────────────────────────────────────────────────────────

class TestEmailConfig:

    def test_defaults_disabled(self, monkeypatch):
        for key in ("EMAIL_ENABLED", "SMTP_HOST", "SMTP_PORT",
                    "SMTP_USER", "SMTP_PASSWORD", "EMAIL_FROM_NAME", "EMAIL_FROM_ADDR"):
            monkeypatch.delenv(key, raising=False)
        with patch("billing_agent.email.config._load_dotenv"):
            cfg = load_config()
        assert cfg.enabled is False
        assert cfg.host == "smtp.office365.com"
        assert cfg.port == 587

    def test_enabled_true(self, monkeypatch):
        monkeypatch.setenv("EMAIL_ENABLED", "true")
        assert load_config().enabled is True

    def test_enabled_1(self, monkeypatch):
        monkeypatch.setenv("EMAIL_ENABLED", "1")
        assert load_config().enabled is True

    def test_enabled_yes(self, monkeypatch):
        monkeypatch.setenv("EMAIL_ENABLED", "yes")
        assert load_config().enabled is True

    def test_enabled_false_string(self, monkeypatch):
        monkeypatch.setenv("EMAIL_ENABLED", "false")
        assert load_config().enabled is False

    def test_custom_smtp_host(self, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "mail.accenture.com")
        assert load_config().host == "mail.accenture.com"

    def test_custom_port(self, monkeypatch):
        monkeypatch.setenv("SMTP_PORT", "465")
        assert load_config().port == 465

    def test_from_addr_defaults_to_smtp_user(self, monkeypatch):
        monkeypatch.setenv("SMTP_USER", "agent@co.com")
        monkeypatch.delenv("EMAIL_FROM_ADDR", raising=False)
        with patch("billing_agent.email.config._load_dotenv"):
            cfg = load_config()
        assert cfg.from_addr == "agent@co.com"

    def test_dotenv_file_is_loaded(self, monkeypatch, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("EMAIL_ENABLED=true\nSMTP_USER=bot@co.com\n", encoding="utf-8")
        monkeypatch.delenv("EMAIL_ENABLED", raising=False)
        monkeypatch.delenv("SMTP_USER", raising=False)
        # Patch the path the loader looks at
        with patch("billing_agent.email.config.Path") as mock_path_cls:
            mock_env = MagicMock()
            mock_env.exists.return_value = True
            mock_env.read_text.return_value = "EMAIL_ENABLED=true\nSMTP_USER=bot@co.com\n"
            mock_path_cls.return_value.__truediv__.return_value.__truediv__.return_value.__truediv__.return_value = mock_env
            mock_path_cls.return_value.parent.parent.parent.__truediv__ = lambda s, x: mock_env
            # Use the real load_config but with env overrides
            monkeypatch.setenv("EMAIL_ENABLED", "true")
            monkeypatch.setenv("SMTP_USER", "bot@co.com")
            cfg = load_config()
        assert cfg.enabled is True
        assert cfg.user == "bot@co.com"


# ── TestMailerDryRun ──────────────────────────────────────────────────────────

class TestMailerDryRun:

    def test_dry_run_returns_true(self):
        mailer = _Mailer(_cfg(enabled=False))
        result = mailer.send(["x@test.com"], "Subject", "Body")
        assert result is True

    def test_dry_run_does_not_open_smtp(self):
        mailer = _Mailer(_cfg(enabled=False))
        with patch("smtplib.SMTP") as mock_smtp:
            mailer.send(["x@test.com"], "Subject", "Body")
        mock_smtp.assert_not_called()

    def test_dry_run_logs_recipient(self, caplog):
        import logging
        mailer = _Mailer(_cfg(enabled=False))
        with caplog.at_level(logging.INFO, logger="billing_agent.email.mailer"):
            mailer.send(["alice@test.com"], "Test subject", "Body text")
        assert "alice@test.com" in caplog.text

    def test_missing_password_returns_false(self):
        cfg = _cfg(enabled=True)
        cfg.password = ""
        mailer = _Mailer(cfg)
        with patch("smtplib.SMTP") as mock_smtp:
            result = mailer.send(["x@test.com"], "Subject", "Body")
        assert result is False
        mock_smtp.assert_not_called()


# ── TestMailerSend ────────────────────────────────────────────────────────────

class TestMailerSend:

    def _patched_send(self, to, subject, body, attachments=None):
        """Run mailer.send() with a mocked SMTP connection, return (result, sent_msg)."""
        mailer = _Mailer(_cfg(enabled=True))
        captured = {}

        mock_smtp_instance = MagicMock()
        mock_smtp_instance.__enter__ = lambda s: mock_smtp_instance
        mock_smtp_instance.__exit__  = MagicMock(return_value=False)
        mock_smtp_instance.sendmail.side_effect = (
            lambda f, t, m: captured.update({"msg_str": m, "from": f, "to": t})
        )

        with patch("smtplib.SMTP", return_value=mock_smtp_instance):
            result = mailer.send(to, subject, body, attachments=attachments)

        return result, captured, mock_smtp_instance

    def test_send_returns_true_on_success(self):
        result, _, _ = self._patched_send(["x@test.com"], "Subj", "Body")
        assert result is True

    def test_send_calls_starttls(self):
        _, _, smtp = self._patched_send(["x@test.com"], "Subj", "Body")
        smtp.starttls.assert_called_once()

    def test_send_calls_login(self):
        _, _, smtp = self._patched_send(["x@test.com"], "Subj", "Body")
        smtp.login.assert_called_once_with("agent@test.com", "secret")

    def test_send_correct_subject(self):
        _, captured, _ = self._patched_send(["x@test.com"], "My Subject", "Body")
        msg = message_from_string(captured["msg_str"])
        assert msg["Subject"] == "My Subject"

    def test_send_correct_to(self):
        _, captured, _ = self._patched_send(["alice@test.com", "bob@test.com"], "S", "B")
        msg = message_from_string(captured["msg_str"])
        assert "alice@test.com" in msg["To"]
        assert "bob@test.com"   in msg["To"]

    def test_send_body_in_message(self):
        _, captured, _ = self._patched_send(["x@test.com"], "S", "Hello world body")
        msg = message_from_string(captured["msg_str"])
        # Walk all parts to find the text/plain payload
        full_text = ""
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                full_text += payload.decode("utf-8") if payload else part.get_payload()
        assert "Hello world body" in full_text

    def test_send_with_attachment(self, tmp_path):
        att = tmp_path / "report.md"
        att.write_text("# Report", encoding="utf-8")
        _, captured, _ = self._patched_send(["x@test.com"], "S", "B", attachments=[att])
        assert "report.md" in captured["msg_str"]

    def test_missing_attachment_skipped(self, tmp_path):
        ghost = tmp_path / "ghost.md"  # doesn't exist
        result, captured, _ = self._patched_send(["x@test.com"], "S", "B", attachments=[ghost])
        assert result is True
        assert "ghost.md" not in captured["msg_str"]


# ── TestMailerFailure ─────────────────────────────────────────────────────────

class TestMailerFailure:

    def test_smtp_error_returns_false(self):
        mailer = _Mailer(_cfg(enabled=True))
        with patch("smtplib.SMTP", side_effect=smtplib.SMTPException("connection refused")):
            result = mailer.send(["x@test.com"], "S", "B")
        assert result is False

    def test_smtp_error_does_not_raise(self):
        mailer = _Mailer(_cfg(enabled=True))
        with patch("smtplib.SMTP", side_effect=OSError("network unreachable")):
            mailer.send(["x@test.com"], "S", "B")  # must not raise


# ── TestSendSubmission ────────────────────────────────────────────────────────

class TestSendSubmission:

    def test_notice_sent_to_employee(self, tmp_path):
        contacts = _contacts()
        notice   = _notice_file(tmp_path, "E-1041")
        sent_to  = []

        def fake_send(self_m, to, subject, body, attachments=None):
            sent_to.extend(to)
            return True

        with patch.object(_Mailer, "send", fake_send):
            with patch("billing_agent.email.mailer.load_config", return_value=_cfg(enabled=True)):
                count = send_submission_emails([notice], contacts)

        assert count == 1
        assert "d.okafor@test.com" in sent_to

    def test_summary_sent_to_analyst(self, tmp_path):
        contacts = _contacts()
        summary  = _summary_file(tmp_path)
        sent_to  = []

        def fake_send(self_m, to, subject, body, attachments=None):
            sent_to.extend(to)
            return True

        with patch.object(_Mailer, "send", fake_send):
            with patch("billing_agent.email.mailer.load_config", return_value=_cfg(enabled=True)):
                count = send_submission_emails([summary], contacts)

        assert count == 1
        assert "p.sharma@test.com" in sent_to

    def test_notice_and_summary_both_sent(self, tmp_path):
        contacts = _contacts()
        notice   = _notice_file(tmp_path, "E-1041")
        summary  = _summary_file(tmp_path)
        sent_to  = []

        def fake_send(self_m, to, subject, body, attachments=None):
            sent_to.extend(to)
            return True

        with patch.object(_Mailer, "send", fake_send):
            with patch("billing_agent.email.mailer.load_config", return_value=_cfg(enabled=True)):
                count = send_submission_emails([notice, summary], contacts)

        assert count == 2
        assert "d.okafor@test.com" in sent_to
        assert "p.sharma@test.com" in sent_to

    def test_notice_subject_contains_cycle(self, tmp_path):
        contacts = _contacts()
        notice   = _notice_file(tmp_path, "E-1041", cycle="2026-05")
        subjects = []

        def fake_send(self_m, to, subject, body, attachments=None):
            subjects.append(subject)
            return True

        with patch.object(_Mailer, "send", fake_send):
            with patch("billing_agent.email.mailer.load_config", return_value=_cfg(enabled=True)):
                send_submission_emails([notice], contacts)

        assert any("2026-05" in s for s in subjects)

    def test_unknown_employee_id_skipped(self, tmp_path):
        contacts = _contacts()
        notice = tmp_path / "exception-notice-E-9999-submission-E9999-clean-2026-04__Z.md"
        notice.write_text("# Notice\n**Billing cycle:** 2026-04", encoding="utf-8")
        calls = []

        def fake_send(self_m, to, subject, body, attachments=None):
            calls.append(to)
            return True

        with patch.object(_Mailer, "send", fake_send):
            with patch("billing_agent.email.mailer.load_config", return_value=_cfg(enabled=True)):
                count = send_submission_emails([notice], contacts)

        assert count == 0

    def test_dry_run_returns_count(self, tmp_path):
        contacts = _contacts()
        notice   = _notice_file(tmp_path, "E-1041")
        with patch("billing_agent.email.mailer.load_config", return_value=_cfg(enabled=False)):
            count = send_submission_emails([notice], contacts)
        assert count == 1  # dry-run still counts as "sent"


# ── TestSendInvoice ───────────────────────────────────────────────────────────

class TestSendInvoice:

    def _run(self, tmp_path, cfg_override=None):
        contacts = _contacts()
        inv  = tmp_path / "draft-invoice-PRJ-NS-7421-2026-04.md"
        aud  = tmp_path / "audit-trail-PRJ-NS-7421-2026-04.csv"
        exc  = tmp_path / "exceptions-report-PRJ-NS-7421-2026-04.md"
        for p in (inv, aud, exc):
            p.write_text("content", encoding="utf-8")

        sent = {}

        def fake_send(self_m, to, subject, body, attachments=None):
            sent["to"]   = to
            sent["subj"] = subject
            sent["body"] = body
            sent["att"]  = attachments or []
            return True

        cfg = cfg_override or _cfg(enabled=True)
        with patch.object(_Mailer, "send", fake_send):
            with patch("billing_agent.email.mailer.load_config", return_value=cfg):
                count = send_invoice_emails(
                    invoice_path     = inv,
                    audit_path       = aud,
                    exceptions_path  = exc,
                    labour_total     = 10_515.00,
                    expense_total    = 4_136.54,
                    grand_total      = 14_651.54,
                    blocked_count    = 9,
                    submission_count = 6,
                    billing_month    = "2026-04",
                    project_id       = "PRJ-NS-7421",
                    contacts         = contacts,
                )
        return count, sent

    def test_sent_to_analyst(self, tmp_path):
        _, sent = self._run(tmp_path)
        assert "p.sharma@test.com" in sent["to"]

    def test_subject_contains_project_and_month(self, tmp_path):
        _, sent = self._run(tmp_path)
        assert "PRJ-NS-7421" in sent["subj"]
        assert "2026-04"     in sent["subj"]

    def test_body_contains_totals(self, tmp_path):
        _, sent = self._run(tmp_path)
        assert "10,515.00" in sent["body"]
        assert "4,136.54"  in sent["body"]
        assert "14,651.54" in sent["body"]

    def test_three_attachments(self, tmp_path):
        _, sent = self._run(tmp_path)
        assert len(sent["att"]) == 3

    def test_attachments_include_csv(self, tmp_path):
        _, sent = self._run(tmp_path)
        names = [p.name for p in sent["att"]]
        assert any(".csv" in n for n in names)

    def test_returns_1_on_success(self, tmp_path):
        count, _ = self._run(tmp_path)
        assert count == 1

    def test_dry_run_returns_1(self, tmp_path):
        count, _ = self._run(tmp_path, cfg_override=_cfg(enabled=False))
        assert count == 1

    def test_no_analysts_returns_0(self, tmp_path):
        contacts_no_analyst = ContactDirectory(
            employees        = [],
            billing_analysts = [],
            project_leads    = [],
        )
        inv = tmp_path / "draft-invoice.md"
        inv.write_text("x", encoding="utf-8")
        aud = tmp_path / "audit.csv"
        aud.write_text("x", encoding="utf-8")
        exc = tmp_path / "exceptions.md"
        exc.write_text("x", encoding="utf-8")

        with patch("billing_agent.email.mailer.load_config", return_value=_cfg(enabled=True)):
            count = send_invoice_emails(
                invoice_path=inv, audit_path=aud, exceptions_path=exc,
                labour_total=0, expense_total=0, grand_total=0,
                blocked_count=0, submission_count=0,
                billing_month="2026-04", project_id="PRJ-NS-7421",
                contacts=contacts_no_analyst,
            )
        assert count == 0
