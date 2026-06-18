"""
Tests for Phase 5 — split into two concerns:

  TestNoticeWriter      per-submission employee notices + analyst summary
  TestInvoiceBuild      project-level month-end invoice (aggregates all submissions)
  TestContactsLoader    contacts.json loading and lookup helpers
  TestCategorize        _categorize() helper
  TestHasMarkup         _has_markup() helper
"""

import csv
import shutil
from datetime import date
from pathlib import Path
from typing import Optional

import pytest

from billing_agent.exceptions import run as detect_exceptions
from billing_agent.ingestion import load_inputs
from billing_agent.ingestion.contacts_loader import load_contacts
from billing_agent.matching import reconcile
from billing_agent.models.transaction import Transaction
from billing_agent.output.invoice_builder import _categorize, _has_markup, build
from billing_agent.output.notice_writer import _infer_cycle, write_notices
from billing_agent.rules import rule_engine
from billing_agent.rules.models import RuleResult
from tests.conftest import (
    SUBMISSION_CLEAN,
    SUBMISSION_CURRENCY,
    SUBMISSION_HOLD_MISCODED,
    SUBMISSION_OVER_CAP,
    SUBMISSION_PRINCIPAL_CAP,
    SUBMISSION_SUBCON,
    SUBMISSIONS_DIR,
)

_ALL_SUBMISSIONS = [
    SUBMISSION_CLEAN,
    SUBMISSION_OVER_CAP,
    SUBMISSION_HOLD_MISCODED,
    SUBMISSION_PRINCIPAL_CAP,
    SUBMISSION_SUBCON,
    SUBMISSION_CURRENCY,
]


# ── Contacts fixture ───────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def contacts():
    return load_contacts()


# ── Per-submission pipeline runner ────────────────────────────────────────────

def _run(sub: Path):
    inputs = load_inputs(sub)
    rr     = rule_engine.run(inputs)
    mr     = reconcile(inputs, rr)
    er     = detect_exceptions(inputs, rr, mr)
    return inputs, rr, mr, er


@pytest.fixture(scope="module")
def clean(contacts):
    inputs, rr, mr, er = _run(SUBMISSION_CLEAN)
    written = write_notices(inputs, rr, er, contacts)
    return inputs, rr, mr, er, written


@pytest.fixture(scope="module")
def over_cap(contacts):
    inputs, rr, mr, er = _run(SUBMISSION_OVER_CAP)
    written = write_notices(inputs, rr, er, contacts)
    return inputs, rr, mr, er, written


@pytest.fixture(scope="module")
def hold_miscoded(contacts):
    inputs, rr, mr, er = _run(SUBMISSION_HOLD_MISCODED)
    written = write_notices(inputs, rr, er, contacts)
    return inputs, rr, mr, er, written


@pytest.fixture(scope="module")
def subcon(contacts):
    inputs, rr, mr, er = _run(SUBMISSION_SUBCON)
    written = write_notices(inputs, rr, er, contacts)
    return inputs, rr, mr, er, written


@pytest.fixture(scope="module")
def currency(contacts):
    inputs, rr, mr, er = _run(SUBMISSION_CURRENCY)
    written = write_notices(inputs, rr, er, contacts)
    return inputs, rr, mr, er, written


# ── Project-level invoice fixture (all 6 submissions) ─────────────────────────

@pytest.fixture(scope="module")
def project_invoice(contacts, tmp_path_factory):
    """Copy all submission CSVs into a temp 'completed' dir and run build()."""
    completed = tmp_path_factory.mktemp("completed")
    for sub in _ALL_SUBMISSIONS:
        shutil.copy(sub, completed / sub.name)
    result = build("PRJ-NS-7421", "2026-04", contacts, completed)
    return result


# ── TestContactsLoader ────────────────────────────────────────────────────────

class TestContactsLoader:

    def test_all_employees_loaded(self, contacts):
        emp_ids = {e.employee_id for e in contacts.employees}
        for eid in ("E-1041", "E-2210", "E-3055", "E-4501", "E-5102", "E-7702"):
            assert eid in emp_ids

    def test_employee_lookup_returns_correct_record(self, contacts):
        emp = contacts.employee("E-1041")
        assert emp is not None
        assert emp.name == "David Okafor"
        assert "@" in emp.email

    def test_missing_employee_returns_none(self, contacts):
        assert contacts.employee("E-9999") is None

    def test_billing_analysts_loaded(self, contacts):
        assert len(contacts.billing_analysts) >= 1
        assert "@" in contacts.billing_analysts[0].email

    def test_project_lead_lookup(self, contacts):
        pl = contacts.project_lead("PRJ-NS-7421")
        assert pl is not None
        assert "Lange" in pl.name

    def test_missing_project_lead_returns_none(self, contacts):
        assert contacts.project_lead("PRJ-UNKNOWN") is None


# ── TestNoticeWriter ──────────────────────────────────────────────────────────

class TestNoticeWriter:

    def test_clean_employee_notice_written(self, clean):
        _, _, _, _, written = clean
        notice_files = [p for p in written if "exception-notice" in p.name]
        # E-1041 has 2 unresolved exceptions → notice written
        assert len(notice_files) == 1
        assert "E-1041" in notice_files[0].name

    def test_analyst_summary_always_written(self, clean):
        _, _, _, _, written = clean
        summary = [p for p in written if "analyst-summary" in p.name]
        assert len(summary) == 1

    def test_notice_contains_employee_name(self, clean):
        _, _, _, _, written = clean
        notice = next(p for p in written if "exception-notice" in p.name)
        assert "David Okafor" in notice.read_text()

    def test_notice_contains_employee_email(self, clean, contacts):
        _, _, _, _, written = clean
        notice = next(p for p in written if "exception-notice" in p.name)
        emp = contacts.employee("E-1041")
        assert emp.email in notice.read_text()

    def test_notice_contains_action_instructions(self, clean):
        _, _, _, _, written = clean
        notice = next(p for p in written if "exception-notice" in p.name)
        text = notice.read_text()
        assert "SAP" in text           # corrective action points to SAP
        assert "resubmit" in text.lower() or "correct" in text.lower()

    def test_notice_shows_blocking_section(self, clean):
        _, _, _, _, written = clean
        notice = next(p for p in written if "exception-notice" in p.name)
        # TX-1006 (NO_RECEIPT) is blocking
        assert "Blocking" in notice.read_text()

    def test_over_cap_two_notices_written(self, over_cap):
        # E-2210 has hard rejections (ALCOHOL, MISCODED) → notice
        _, _, _, _, written = over_cap
        notice_files = [p for p in written if "exception-notice" in p.name]
        assert len(notice_files) == 1
        assert "E-2210" in notice_files[0].name

    def test_analyst_summary_addressed_to_maya(self, clean, contacts):
        _, _, _, _, written = clean
        summary = next(p for p in written if "analyst-summary" in p.name)
        text = summary.read_text()
        assert "Maya Okonkwo" in text
        assert contacts.billing_analysts[0].email in text

    def test_analyst_summary_contains_blocking_count(self, clean):
        _, _, _, er, written = clean
        summary = next(p for p in written if "analyst-summary" in p.name)
        text = summary.read_text()
        assert str(len(er.blocking)) in text

    def test_analyst_summary_contains_submission_name(self, clean):
        inputs, _, _, _, written = clean
        summary = next(p for p in written if "analyst-summary" in p.name)
        assert inputs.submission_file.name in summary.read_text()

    def test_clean_employee_no_exceptions_no_notice(self, over_cap):
        # E-2210 is the only employee — only one notice file
        _, _, _, _, written = over_cap
        notice_files = [p for p in written if "exception-notice" in p.name]
        assert all("E-2210" in p.name for p in notice_files)

    def test_subcon_blocking_notice_written(self, subcon):
        _, _, _, _, written = subcon
        notice_files = [p for p in written if "exception-notice" in p.name]
        assert len(notice_files) == 1   # E-5102 has 3 NO_RECEIPT items

    def test_notice_file_is_markdown(self, clean):
        _, _, _, _, written = clean
        for p in written:
            assert p.suffix == ".md"


# ── TestInvoiceBuild (project-level) ─────────────────────────────────────────

class TestInvoiceBuild:

    def test_all_six_submissions_processed(self, project_invoice):
        assert project_invoice.submission_count == 6

    def test_grand_total_balances(self, project_invoice):
        assert project_invoice.grand_total == round(
            project_invoice.labour_total + project_invoice.expense_total, 2
        )

    def test_labour_total(self, project_invoice):
        assert project_invoice.labour_total == 10_515.00

    def test_expense_total(self, project_invoice):
        assert project_invoice.expense_total == 4_136.54

    def test_grand_total(self, project_invoice):
        assert project_invoice.grand_total == 14_651.54

    def test_blocked_count(self, project_invoice):
        assert project_invoice.blocked_count == 9

    def test_three_output_files_exist(self, project_invoice):
        assert project_invoice.invoice_path.exists()
        assert project_invoice.audit_path.exists()
        assert project_invoice.exceptions_path.exists()

    def test_file_names_contain_project_and_month(self, project_invoice):
        assert "PRJ-NS-7421" in project_invoice.invoice_path.name
        assert "2026-04" in project_invoice.invoice_path.name

    def test_audit_trail_row_count(self, project_invoice):
        with project_invoice.audit_path.open(encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        # 7+7+6+4+6+6 = 36 transactions across all submissions
        assert len(rows) == 36

    def test_audit_trail_has_employee_name_column(self, project_invoice):
        with project_invoice.audit_path.open(encoding="utf-8") as fh:
            reader = csv.reader(fh)
            headers = next(reader)
        assert "employee_name" in headers

    def test_audit_trail_employee_names_populated(self, project_invoice):
        with project_invoice.audit_path.open(encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        named = [r for r in rows if r["employee_name"] != ""]
        # At least the employees in contacts should resolve to names
        assert len(named) > 0

    def test_alcohol_approved_amount_zero_in_audit(self, project_invoice):
        with project_invoice.audit_path.open(encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        alcohol = [r for r in rows if r["rule_id"] == "ALCOHOL"]
        assert len(alcohol) >= 1
        for r in alcohol:
            assert float(r["approved_amount"]) == 0.0

    def test_personal_item_approved_amount_zero_in_audit(self, project_invoice):
        with project_invoice.audit_path.open(encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        personal = [r for r in rows if r["rule_id"] == "PERSONAL_ITEM"]
        assert len(personal) >= 1
        for r in personal:
            assert float(r["approved_amount"]) == 0.0

    def test_invoice_has_section_a_and_b(self, project_invoice):
        text = project_invoice.invoice_path.read_text()
        assert "## Section A — Labour" in text
        assert "## Section B — Reimbursable Expenses" in text

    def test_invoice_contains_employee_names(self, project_invoice):
        text = project_invoice.invoice_path.read_text()
        assert "David Okafor" in text
        assert "Sandra Osei" in text

    def test_invoice_grand_total_appears_in_text(self, project_invoice):
        text = project_invoice.invoice_path.read_text()
        assert f"{project_invoice.grand_total:,.2f}" in text

    def test_exceptions_report_has_blocking_section(self, project_invoice):
        text = project_invoice.exceptions_path.read_text()
        assert "BLOCKS" in text

    def test_exceptions_report_contains_employee_names(self, project_invoice):
        text = project_invoice.exceptions_path.read_text()
        assert "David Okafor" in text or "Sandra Osei" in text

    def test_no_completed_dir_raises_file_not_found(self, contacts, tmp_path):
        empty = tmp_path / "empty_completed"
        empty.mkdir()
        with pytest.raises(FileNotFoundError):
            build("PRJ-NS-7421", "2026-04", contacts, empty)


# ── TestCategorize ────────────────────────────────────────────────────────────

def _tx(desc: str, unit: str = "EA") -> Transaction:
    return Transaction(
        "TX-000", "PRJ-000", "", date.today(), "EXPENSE",
        "E-000", "", desc, 1.0, unit, 100.0, 100.0,
        "USD", "STD", False, "", "",
    )


class TestCategorize:

    def test_hotel_night_is_lodging(self):
        assert _categorize(_tx("Hotel — 1 night downtown", unit="NIGHT")) == "LODGING"

    def test_hotel_keyword_is_lodging(self):
        assert _categorize(_tx("Hotel stay — coastal inn")) == "LODGING"

    def test_mileage_unit_is_mileage(self):
        assert _categorize(_tx("Home to airport", unit="MILE")) == "MILEAGE"

    def test_mileage_keyword_is_mileage(self):
        assert _categorize(_tx("Mileage reimbursement — site visit")) == "MILEAGE"

    def test_flight_is_air(self):
        assert _categorize(_tx("Flight outbound — economy class")) == "AIR"

    def test_rideshare_is_ground(self):
        assert _categorize(_tx("Rideshare — airport to site")) == "GROUND"

    def test_parking_is_ground(self):
        assert _categorize(_tx("Parking — downtown garage")) == "GROUND"

    def test_dinner_is_meals(self):
        assert _categorize(_tx("Client dinner — stakeholder meeting")) == "MEALS"

    def test_per_diem_is_meals(self):
        assert _categorize(_tx("Per diem — meals return day")) == "MEALS"

    def test_subcontractor_is_subcontractor(self):
        assert _categorize(_tx("Subcontractor — drone aerial survey")) == "SUBCONTRACTOR"

    def test_unknown_is_other(self):
        assert _categorize(_tx("Office supplies")) == "OTHER"


# ── TestHasMarkup ─────────────────────────────────────────────────────────────

def _rr(exception_type: Optional[str], override: bool, orig: float, approved: float) -> RuleResult:
    return RuleResult(
        transaction_id   = "TX-000",
        status           = "APPROVE",
        exception_type   = exception_type,
        rule_id          = "MARKUP_MISSING",
        override_applied = override,
        override_source  = "PL-EMAIL-04" if override else "",
        original_amount  = orig,
        approved_amount  = approved,
        note             = "",
    )


class TestHasMarkup:

    def test_markup_applied_returns_true(self):
        assert _has_markup(_rr("SUBCONTRACTOR_MARKUP", True, 2400.0, 2592.0)) is True

    def test_no_override_returns_false(self):
        assert _has_markup(_rr("SUBCONTRACTOR_MARKUP", False, 2400.0, 2592.0)) is False

    def test_wrong_exception_type_returns_false(self):
        assert _has_markup(_rr("OVER_CAP", True, 310.0, 250.0)) is False

    def test_approved_equals_original_returns_false(self):
        assert _has_markup(_rr("SUBCONTRACTOR_MARKUP", True, 2400.0, 2400.0)) is False


# ── TestInferCycle ────────────────────────────────────────────────────────────

class TestInferCycle:

    def test_extracts_yyyy_mm_from_submission(self):
        inputs = load_inputs(SUBMISSION_CLEAN)
        assert _infer_cycle(inputs) == "2026-04"

    def test_fallback_for_no_date_in_name(self, tmp_path):
        class _Fake:
            submission_file = tmp_path / "submission-nodates.csv"
<<<<<<< Updated upstream
        assert _infer_cycle(_Fake()) == "current"
=======

        result = _infer_cycle(_FakeIngestion())
        assert result == "current"


# ── Project ID mismatch rule ───────────────────────────────────────────────────

from billing_agent.ingestion.loader import IngestionResult
from billing_agent.exceptions import run as _detect_exceptions


def _minimal_ingestion(project_id: str, contract_project: str, tmp_path) -> IngestionResult:
    """One EXPENSE transaction; only fields the rule engine needs are set."""
    tx = Transaction(
        "TX-PROJ-001", project_id, "T-100", date.today(), "EXPENSE",
        "E-001", "", "Hotel — 1 night", 1.0, "NIGHT", 200.0, 200.0,
        "USD", "STD-TM-NA-CIVIC", False, "", "",
    )
    return IngestionResult(
        submission_file      = tmp_path / "submission-test.csv",
        transactions         = [tx],
        contract_sap_project = contract_project,
    )


class TestProjectMismatch:

    def test_wrong_project_id_is_rejected(self, tmp_path):
        inputs = _minimal_ingestion("PRJ-NS-9999", "PRJ-NS-7421", tmp_path)
        results = rule_engine.run(inputs)
        assert len(results) == 1
        r = results[0]
        assert r.rule_id       == "PROJECT_MISMATCH"
        assert r.status        == "REJECT"
        assert r.approved_amount == 0.0

    def test_wrong_project_id_note_contains_both_ids(self, tmp_path):
        inputs = _minimal_ingestion("PRJ-NS-9999", "PRJ-NS-7421", tmp_path)
        r = rule_engine.run(inputs)[0]
        assert "PRJ-NS-9999"  in r.note
        assert "PRJ-NS-7421"  in r.note

    def test_matching_project_id_does_not_trigger_mismatch(self, tmp_path):
        inputs = _minimal_ingestion("PRJ-NS-7421", "PRJ-NS-7421", tmp_path)
        results = rule_engine.run(inputs)
        assert all(r.rule_id != "PROJECT_MISMATCH" for r in results)

    def test_mismatch_routes_to_hard_rejections(self, tmp_path):
        inputs  = _minimal_ingestion("PRJ-NS-9999", "PRJ-NS-7421", tmp_path)
        rr      = rule_engine.run(inputs)
        er      = _detect_exceptions(inputs, rr, [])
        assert len(er.hard_rejections) == 1
        assert er.hard_rejections[0].rule_id == "PROJECT_MISMATCH"
        assert len(er.escalate_employee) == 0

    def test_mismatch_exception_type_is_project_mismatch(self, tmp_path):
        inputs = _minimal_ingestion("PRJ-NS-9999", "PRJ-NS-7421", tmp_path)
        rr     = rule_engine.run(inputs)
        er     = _detect_exceptions(inputs, rr, [])
        assert er.hard_rejections[0].exception_type == "PROJECT_MISMATCH"

    def test_no_project_mismatch_in_clean_submission(self, clean):
        _, rr, _, _, _ = clean
        assert all(r.rule_id != "PROJECT_MISMATCH" for r in rr)

    def test_no_project_mismatch_in_over_cap_submission(self, over_cap):
        _, rr, _, _, _ = over_cap
        assert all(r.rule_id != "PROJECT_MISMATCH" for r in rr)

    def test_no_project_mismatch_in_hold_miscoded_submission(self, hold_miscoded):
        _, rr, _, _, _ = hold_miscoded
        assert all(r.rule_id != "PROJECT_MISMATCH" for r in rr)

    def test_no_project_mismatch_in_subcon_submission(self, subcon):
        _, rr, _, _, _ = subcon
        assert all(r.rule_id != "PROJECT_MISMATCH" for r in rr)
>>>>>>> Stashed changes
