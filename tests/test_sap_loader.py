"""Unit tests for billing_agent.ingestion.sap_loader."""

import pytest
from billing_agent.ingestion.sap_loader import load_transactions, load_timecards
from tests.conftest import (
    TIMECARD_PATH,
    SUBMISSION_CLEAN,
    SUBMISSION_OVER_CAP,
    SUBMISSION_HOLD_MISCODED,
    SUBMISSION_PRINCIPAL_CAP,
    SUBMISSION_CURRENCY,
    SUBMISSION_SUBCON,
)


# ── load_transactions ─────────────────────────────────────────────────────────

class TestLoadTransactions:

    def test_clean_submission_row_count(self):
        txs = load_transactions(SUBMISSION_CLEAN)
        assert len(txs) == 7

    def test_labour_expense_split(self):
        txs = load_transactions(SUBMISSION_CLEAN)
        assert sum(t.is_labor   for t in txs) == 2
        assert sum(t.is_expense for t in txs) == 5

    def test_all_transactions_same_project(self):
        txs = load_transactions(SUBMISSION_CLEAN)
        assert all(t.project_id == "PRJ-NS-7421" for t in txs)

    def test_single_employee_per_submission(self):
        txs = load_transactions(SUBMISSION_CLEAN)
        assert {t.employee_id for t in txs} == {"E-1041"}

    def test_mileage_amount_equals_quantity_times_rate(self):
        txs = load_transactions(SUBMISSION_CLEAN)
        mileage = next(t for t in txs if t.unit == "MI")
        assert abs(mileage.amount - mileage.quantity * mileage.rate) < 0.01

    def test_hold_flag_parsed_true(self):
        txs = load_transactions(SUBMISSION_HOLD_MISCODED)
        held = [t for t in txs if t.is_on_hold]
        assert len(held) == 1
        assert held[0].transaction_id == "TX-2026-04-3002"

    def test_hold_flag_absent_is_false(self):
        txs = load_transactions(SUBMISSION_CLEAN)
        assert all(not t.is_on_hold for t in txs)

    def test_hold_reason_populated(self):
        txs = load_transactions(SUBMISSION_HOLD_MISCODED)
        held = next(t for t in txs if t.is_on_hold)
        assert "PRINCIPAL_APPROVAL" in held.hold_reason

    def test_principal_cap_hold_flag(self):
        txs = load_transactions(SUBMISSION_PRINCIPAL_CAP)
        held = [t for t in txs if t.is_on_hold]
        assert len(held) == 1
        assert "PRINCIPAL_CAP" in held[0].hold_reason

    def test_foreign_currency_preserved(self):
        txs = load_transactions(SUBMISSION_CURRENCY)
        cad = [t for t in txs if t.currency == "CAD"]
        assert len(cad) == 1

    def test_usd_is_default_currency(self):
        txs = load_transactions(SUBMISSION_CLEAN)
        assert all(t.currency == "USD" for t in txs)

    def test_note_field_populated(self):
        txs = load_transactions(SUBMISSION_CLEAN)
        noted = [t for t in txs if t.note]
        assert len(noted) >= 4   # most lines have receipt references

    def test_labour_rate_correct(self):
        txs = load_transactions(SUBMISSION_CLEAN)
        labour = [t for t in txs if t.is_labor]
        assert all(t.rate == 175.00 for t in labour)

    def test_over_cap_submission_row_count(self):
        txs = load_transactions(SUBMISSION_OVER_CAP)
        assert len(txs) == 7

    def test_subcontractor_submission_no_hold(self):
        txs = load_transactions(SUBMISSION_SUBCON)
        assert all(not t.is_on_hold for t in txs)


# ── load_timecards ────────────────────────────────────────────────────────────

class TestLoadTimecards:

    def test_unfiltered_loads_multiple_employees(self):
        tcs = load_timecards(TIMECARD_PATH)
        emp_ids = {tc.employee_id for tc in tcs}
        assert len(emp_ids) > 1

    def test_filter_single_employee(self):
        tcs = load_timecards(TIMECARD_PATH, employee_ids={"E-1041"})
        assert all(tc.employee_id == "E-1041" for tc in tcs)
        assert len(tcs) > 0

    def test_filter_excludes_others(self):
        tcs_all      = load_timecards(TIMECARD_PATH)
        tcs_filtered = load_timecards(TIMECARD_PATH, employee_ids={"E-1041"})
        assert len(tcs_filtered) < len(tcs_all)

    def test_filter_unknown_employee_returns_empty(self):
        tcs = load_timecards(TIMECARD_PATH, employee_ids={"E-XXXX"})
        assert tcs == []

    def test_filter_multiple_employees(self):
        tcs = load_timecards(TIMECARD_PATH, employee_ids={"E-1041", "E-2210"})
        emp_ids = {tc.employee_id for tc in tcs}
        assert emp_ids.issubset({"E-1041", "E-2210"})

    def test_none_filter_loads_all(self):
        tcs_none = load_timecards(TIMECARD_PATH, employee_ids=None)
        tcs_all  = load_timecards(TIMECARD_PATH)
        assert len(tcs_none) == len(tcs_all)

    def test_timecard_hours_positive(self):
        tcs = load_timecards(TIMECARD_PATH)
        assert all(tc.hours > 0 for tc in tcs)
