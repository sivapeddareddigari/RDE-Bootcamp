"""Unit tests for billing_agent.output.invoice_builder — Phase 5."""

import csv
from datetime import date
from pathlib import Path
from typing import Optional

import pytest

import billing_agent.config as _cfg
from billing_agent.exceptions import run as detect_exceptions
from billing_agent.ingestion import load_inputs
from billing_agent.matching import reconcile
from billing_agent.models.transaction import Transaction
from billing_agent.output import build as build_invoice
from billing_agent.output.invoice_builder import _categorize, _has_markup, _infer_cycle
from billing_agent.rules import rule_engine
from billing_agent.rules.models import RuleResult
from tests.conftest import (
    SUBMISSION_CLEAN,
    SUBMISSION_CURRENCY,
    SUBMISSION_HOLD_MISCODED,
    SUBMISSION_OVER_CAP,
    SUBMISSION_PRINCIPAL_CAP,
    SUBMISSION_SUBCON,
)

_AUDIT_HEADERS = [
    "transaction_id", "employee_id", "type", "description",
    "original_amount", "approved_amount", "status",
    "rule_id", "exception_type", "override_applied", "override_source",
    "matched_doc_id", "match_confidence", "amount_delta", "note",
]


# ── Output directory patch ─────────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def _tmp_output(tmp_path_factory):
    """Redirect OUTPUT_DIR to a temp directory so tests don't pollute output/."""
    tmp = tmp_path_factory.mktemp("invoice_builder")
    _cfg.OUTPUT_DIR = tmp
    yield tmp


# ── Full pipeline runner ───────────────────────────────────────────────────────

def _run(sub: Path):
    """Run Phases 1–5 for one submission and return a 5-tuple."""
    inputs = load_inputs(sub)
    rr     = rule_engine.run(inputs)
    mr     = reconcile(inputs, rr)
    er     = detect_exceptions(inputs, rr, mr)
    br     = build_invoice(inputs, rr, mr, er)
    return inputs, rr, mr, er, br


@pytest.fixture(scope="module")
def clean(_tmp_output):
    return _run(SUBMISSION_CLEAN)


@pytest.fixture(scope="module")
def over_cap(_tmp_output):
    return _run(SUBMISSION_OVER_CAP)


@pytest.fixture(scope="module")
def hold_miscoded(_tmp_output):
    return _run(SUBMISSION_HOLD_MISCODED)


@pytest.fixture(scope="module")
def principal_cap(_tmp_output):
    return _run(SUBMISSION_PRINCIPAL_CAP)


@pytest.fixture(scope="module")
def subcon(_tmp_output):
    return _run(SUBMISSION_SUBCON)


@pytest.fixture(scope="module")
def currency(_tmp_output):
    return _run(SUBMISSION_CURRENCY)


# ── Invariant: grand_total == labour_total + expense_total ────────────────────

class TestTotalInvariant:

    def test_clean_totals_balance(self, clean):
        _, _, _, _, br = clean
        assert br.grand_total == round(br.labour_total + br.expense_total, 2)

    def test_over_cap_totals_balance(self, over_cap):
        _, _, _, _, br = over_cap
        assert br.grand_total == round(br.labour_total + br.expense_total, 2)

    def test_hold_miscoded_totals_balance(self, hold_miscoded):
        _, _, _, _, br = hold_miscoded
        assert br.grand_total == round(br.labour_total + br.expense_total, 2)

    def test_principal_cap_totals_balance(self, principal_cap):
        _, _, _, _, br = principal_cap
        assert br.grand_total == round(br.labour_total + br.expense_total, 2)

    def test_subcon_totals_balance(self, subcon):
        _, _, _, _, br = subcon
        assert br.grand_total == round(br.labour_total + br.expense_total, 2)

    def test_currency_totals_balance(self, currency):
        _, _, _, _, br = currency
        assert br.grand_total == round(br.labour_total + br.expense_total, 2)


# ── Per-submission dollar amounts ──────────────────────────────────────────────

class TestBuildResultAmounts:

    def test_clean_amounts(self, clean):
        _, _, _, _, br = clean
        assert br.labour_total  == 2800.00
        assert br.expense_total == 365.54
        assert br.grand_total   == 3165.54
        assert br.blocked_count == 1

    def test_over_cap_amounts(self, over_cap):
        _, _, _, _, br = over_cap
        assert br.labour_total  == 3335.00
        assert br.expense_total ==  493.00
        assert br.grand_total   == 3828.00
        assert br.blocked_count == 0

    def test_hold_miscoded_amounts(self, hold_miscoded):
        _, _, _, _, br = hold_miscoded
        assert br.labour_total  ==  430.00
        assert br.expense_total ==   18.00
        assert br.grand_total   ==  448.00
        assert br.blocked_count == 2

    def test_principal_cap_amounts(self, principal_cap):
        _, _, _, _, br = principal_cap
        assert br.labour_total  == 1920.00
        assert br.expense_total ==  485.00
        assert br.grand_total   == 2405.00
        assert br.blocked_count == 1

    def test_subcon_amounts(self, subcon):
        _, _, _, _, br = subcon
        assert br.labour_total  == 2030.00
        assert br.expense_total == 2400.00
        assert br.grand_total   == 4430.00
        assert br.blocked_count == 3

    def test_currency_amounts(self, currency):
        _, _, _, _, br = currency
        assert br.labour_total  ==    0.00
        assert br.expense_total ==  375.00
        assert br.grand_total   ==  375.00
        assert br.blocked_count == 2


# ── Output files created ───────────────────────────────────────────────────────

class TestOutputFilesCreated:

    def test_clean_three_files_exist(self, clean):
        _, _, _, _, br = clean
        assert br.invoice_path.exists()
        assert br.audit_path.exists()
        assert br.exceptions_path.exists()

    def test_over_cap_three_files_exist(self, over_cap):
        _, _, _, _, br = over_cap
        assert br.invoice_path.exists()
        assert br.audit_path.exists()
        assert br.exceptions_path.exists()

    def test_hold_miscoded_three_files_exist(self, hold_miscoded):
        _, _, _, _, br = hold_miscoded
        assert br.invoice_path.exists()
        assert br.audit_path.exists()
        assert br.exceptions_path.exists()

    def test_clean_file_name_prefixes(self, clean):
        inputs, _, _, _, br = clean
        stem = inputs.submission_file.stem
        assert br.invoice_path.name.startswith(f"draft-invoice-{stem}__")
        assert br.audit_path.name.startswith(f"audit-trail-{stem}__")
        assert br.exceptions_path.name.startswith(f"exceptions-report-{stem}__")

    def test_invoice_has_md_suffix(self, clean):
        _, _, _, _, br = clean
        assert br.invoice_path.suffix == ".md"
        assert br.exceptions_path.suffix == ".md"

    def test_audit_has_csv_suffix(self, clean):
        _, _, _, _, br = clean
        assert br.audit_path.suffix == ".csv"


# ── Audit trail ────────────────────────────────────────────────────────────────

class TestAuditTrail:

    def _read_audit(self, br) -> list:
        with br.audit_path.open(encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    def test_clean_audit_row_count_matches_tx_count(self, clean):
        inputs, _, _, _, br = clean
        rows = self._read_audit(br)
        assert len(rows) == len(inputs.transactions)

    def test_over_cap_audit_row_count(self, over_cap):
        inputs, _, _, _, br = over_cap
        rows = self._read_audit(br)
        assert len(rows) == len(inputs.transactions)

    def test_subcon_audit_row_count(self, subcon):
        inputs, _, _, _, br = subcon
        rows = self._read_audit(br)
        assert len(rows) == len(inputs.transactions)

    def test_audit_headers_are_correct(self, clean):
        _, _, _, _, br = clean
        with br.audit_path.open(encoding="utf-8") as fh:
            reader = csv.reader(fh)
            headers = next(reader)
        assert headers == _AUDIT_HEADERS

    def test_audit_all_transactions_present(self, clean):
        inputs, _, _, _, br = clean
        rows = self._read_audit(br)
        tx_ids_in_audit = {r["transaction_id"] for r in rows}
        tx_ids_expected = {t.transaction_id for t in inputs.transactions}
        assert tx_ids_in_audit == tx_ids_expected

    def test_audit_alcohol_approved_amount_is_zero(self, over_cap):
        _, _, _, _, br = over_cap
        rows = self._read_audit(br)
        alcohol_rows = [r for r in rows if r["rule_id"] == "ALCOHOL"]
        assert len(alcohol_rows) == 1
        assert float(alcohol_rows[0]["approved_amount"]) == 0.0

    def test_audit_personal_item_approved_amount_is_zero(self, currency):
        _, _, _, _, br = currency
        rows = self._read_audit(br)
        personal_rows = [r for r in rows if r["rule_id"] == "PERSONAL_ITEM"]
        assert len(personal_rows) == 1
        assert float(personal_rows[0]["approved_amount"]) == 0.0

    def test_audit_airport_lounge_approved_amount_is_zero(self, currency):
        _, _, _, _, br = currency
        rows = self._read_audit(br)
        lounge_rows = [r for r in rows if r["rule_id"] == "AIRPORT_LOUNGE"]
        assert len(lounge_rows) == 1
        assert float(lounge_rows[0]["approved_amount"]) == 0.0

    def test_audit_override_source_recorded(self, over_cap):
        _, _, _, _, br = over_cap
        rows = self._read_audit(br)
        overridden = [r for r in rows if r["override_applied"] == "True"]
        assert len(overridden) >= 2
        for r in overridden:
            assert r["override_source"] != ""

    def test_audit_status_values_are_valid(self, clean):
        _, _, _, _, br = clean
        rows = self._read_audit(br)
        valid_statuses = {"APPROVE", "FLAG", "REJECT", "HOLD"}
        for r in rows:
            assert r["status"] in valid_statuses


# ── Validation: hard-rejected amounts ─────────────────────────────────────────

class TestValidation:

    def test_alcohol_not_in_expense_total(self, over_cap):
        _, rr, _, _, br = over_cap
        alcohol = next(r for r in rr if r.rule_id == "ALCOHOL")
        assert alcohol.approved_amount == 0.0
        # Expense total excludes rejected items (REJECT status, not in billable)
        assert alcohol.status == "REJECT"

    def test_personal_item_not_in_expense_total(self, currency):
        _, rr, _, _, br = currency
        personal = next(r for r in rr if r.rule_id == "PERSONAL_ITEM")
        assert personal.approved_amount == 0.0
        assert personal.status == "REJECT"

    def test_airport_lounge_not_in_expense_total(self, currency):
        _, rr, _, _, br = currency
        lounge = next(r for r in rr if r.rule_id == "AIRPORT_LOUNGE")
        assert lounge.approved_amount == 0.0
        assert lounge.status == "REJECT"

    def test_miscoded_labour_not_in_totals(self, hold_miscoded):
        _, rr, _, _, br = hold_miscoded
        miscoded = next(r for r in rr if r.rule_id == "MISCODED")
        assert miscoded.approved_amount == 0.0
        assert miscoded.status == "REJECT"


# ── Exceptions report ──────────────────────────────────────────────────────────

class TestExceptionsReport:

    def test_clean_has_two_unresolved_escalations(self, clean):
        _, _, _, er, _ = clean
        # TX-1003 (AMOUNT_MISMATCH) → analyst; TX-1006 (NO_RECEIPT) → employee
        assert er.unresolved_count == 2
        assert len(er.escalate_analyst) == 1
        assert len(er.escalate_employee) == 1

    def test_over_cap_has_zero_blocking(self, over_cap):
        _, _, _, er, _ = over_cap
        assert len(er.blocking) == 0

    def test_over_cap_auto_resolved_count(self, over_cap):
        _, _, _, er, _ = over_cap
        # TX-2003 (LODGING_CAP) and TX-2004 (MEAL_CAP) both resolved via PL emails
        assert len(er.auto_resolved) == 2

    def test_over_cap_hard_rejections_count(self, over_cap):
        _, _, _, er, _ = over_cap
        # ALCOHOL (TX-2005) and MISCODED (TX-2007)
        assert len(er.hard_rejections) == 2

    def test_hold_miscoded_blocking_items(self, hold_miscoded):
        _, _, _, er, _ = hold_miscoded
        # TX-3002 HOLD was released by PL email (auto_resolved); TX-3004/TX-3005 NO_RECEIPT block
        assert len(er.blocking) == 2
        assert all(i.rule_id == "NO_RECEIPT" for i in er.blocking)

    def test_subcon_blocking_items_are_no_receipt(self, subcon):
        _, _, _, er, _ = subcon
        assert len(er.blocking) == 3
        for item in er.blocking:
            assert item.rule_id == "NO_RECEIPT"

    def test_subcon_tx5003_is_auto_resolved(self, subcon):
        _, _, _, er, _ = subcon
        auto_ids = {i.transaction_id for i in er.auto_resolved}
        assert "TX-2026-04-5003" in auto_ids

    def test_currency_blocking_includes_currency_mismatch(self, currency):
        _, _, _, er, _ = currency
        blocking_rules = {i.rule_id for i in er.blocking}
        assert "CURRENCY" in blocking_rules or "NO_RECEIPT" in blocking_rules


# ── Invoice markdown content ───────────────────────────────────────────────────

class TestInvoiceMarkdown:

    def _read_invoice(self, br) -> str:
        return br.invoice_path.read_text(encoding="utf-8")

    def test_invoice_has_section_a_header(self, clean):
        text = self._read_invoice(clean[4])
        assert "## Section A — Labour" in text

    def test_invoice_has_section_b_header(self, clean):
        text = self._read_invoice(clean[4])
        assert "## Section B — Reimbursable Expenses" in text

    def test_invoice_has_totals_section(self, clean):
        text = self._read_invoice(clean[4])
        assert "## Totals" in text

    def test_invoice_grand_total_appears_in_text(self, clean):
        _, _, _, _, br = clean
        text = self._read_invoice(br)
        assert f"{br.grand_total:,.2f}" in text

    def test_invoice_shows_excluded_items_section(self, clean):
        text = self._read_invoice(clean[4])
        assert "Excluded / Blocked items" in text

    def test_invoice_clean_no_excluded_labour(self, over_cap):
        # E2210 has no excluded labour (miscoded TX is EXPENSE type)
        _, _, _, _, br = over_cap
        text = self._read_invoice(br)
        assert "## Section A — Labour" in text

    def test_invoice_submission_name_in_header(self, clean):
        inputs, _, _, _, br = clean
        text = self._read_invoice(br)
        assert inputs.submission_file.name in text


# ── Exceptions report markdown content ────────────────────────────────────────

class TestExceptionsMarkdown:

    def _read_exc(self, br) -> str:
        return br.exceptions_path.read_text(encoding="utf-8")

    def test_exceptions_report_submission_name(self, clean):
        inputs, _, _, _, br = clean
        text = self._read_exc(br)
        assert inputs.submission_file.name in text

    def test_exceptions_report_has_total_transactions(self, clean):
        inputs, _, _, _, br = clean
        text = self._read_exc(br)
        assert "Total transactions" in text

    def test_exceptions_hard_rejections_section(self, over_cap):
        _, _, _, er, br = over_cap
        if er.hard_rejections:
            text = self._read_exc(br)
            assert "Hard rejections" in text

    def test_exceptions_auto_resolved_section(self, over_cap):
        _, _, _, er, br = over_cap
        if er.auto_resolved:
            text = self._read_exc(br)
            assert "Auto-resolved" in text


# ── Helpers: _categorize ───────────────────────────────────────────────────────

def _tx(desc: str, unit: str = "EA", tx_type: str = "EXPENSE") -> Transaction:
    return Transaction(
        "TX-000", "PRJ-000", "", date.today(), tx_type,
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

    def test_vendor_drone_is_subcontractor(self):
        assert _categorize(_tx("Drone survey — aeroview inc")) == "SUBCONTRACTOR"

    def test_unknown_is_other(self):
        assert _categorize(_tx("Office supplies")) == "OTHER"


# ── Helpers: _has_markup ───────────────────────────────────────────────────────

def _rule_result(
    exception_type: Optional[str],
    override_applied: bool,
    original_amount: float,
    approved_amount: float,
) -> RuleResult:
    return RuleResult(
        transaction_id   = "TX-000",
        status           = "APPROVE",
        exception_type   = exception_type,
        rule_id          = "MARKUP_MISSING",
        override_applied = override_applied,
        override_source  = "PL-EMAIL-04" if override_applied else "",
        original_amount  = original_amount,
        approved_amount  = approved_amount,
        note             = "",
    )


class TestHasMarkup:

    def test_markup_applied_returns_true(self):
        r = _rule_result("SUBCONTRACTOR_MARKUP", True, 2400.0, 2592.0)
        assert _has_markup(r) is True

    def test_no_override_returns_false(self):
        r = _rule_result("SUBCONTRACTOR_MARKUP", False, 2400.0, 2592.0)
        assert _has_markup(r) is False

    def test_wrong_exception_type_returns_false(self):
        r = _rule_result("OVER_CAP", True, 310.0, 250.0)
        assert _has_markup(r) is False

    def test_approved_equals_original_returns_false(self):
        r = _rule_result("SUBCONTRACTOR_MARKUP", True, 2400.0, 2400.0)
        assert _has_markup(r) is False

    def test_approved_less_than_original_returns_false(self):
        r = _rule_result("SUBCONTRACTOR_MARKUP", True, 2400.0, 2000.0)
        assert _has_markup(r) is False


# ── Helpers: _infer_cycle ──────────────────────────────────────────────────────

class TestInferCycle:

    def test_extracts_yyyy_mm_from_filename(self, clean):
        inputs, _, _, _, _ = clean
        cycle = _infer_cycle(inputs)
        assert cycle == "2026-04"

    def test_extracts_cycle_from_over_cap(self, over_cap):
        inputs, _, _, _, _ = over_cap
        cycle = _infer_cycle(inputs)
        assert cycle == "2026-04"

    def test_fallback_for_no_date_in_name(self, tmp_path):
        from billing_agent.ingestion.loader import IngestionResult
        from datetime import datetime, timezone

        class _FakeIngestion:
            submission_file = tmp_path / "submission-nodates.csv"

        result = _infer_cycle(_FakeIngestion())
        assert result == "current"
