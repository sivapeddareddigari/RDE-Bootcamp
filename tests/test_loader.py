"""Unit tests for billing_agent.ingestion.loader — scoping and IngestionResult."""

import pytest
from datetime import date
from billing_agent.ingestion.loader import load_inputs, _referenced_doc_ids
from billing_agent.ingestion.sap_loader import load_transactions
from billing_agent.models.transaction import Transaction
from tests.conftest import (
    SUBMISSION_CLEAN,
    SUBMISSION_OVER_CAP,
    SUBMISSION_HOLD_MISCODED,
    SUBMISSION_PRINCIPAL_CAP,
    SUBMISSION_CURRENCY,
    SUBMISSION_SUBCON,
)


# ── _referenced_doc_ids helper ────────────────────────────────────────────────

def _make_tx(note: str) -> Transaction:
    return Transaction(
        "TX-001", "PRJ-001", "T-100", date.today(), "EXPENSE",
        "E-001", "", "Test item", 1.0, "EA", 100.0, 100.0,
        "USD", "STD", False, "", note,
    )


class TestReferencedDocIds:

    def test_rc_id_extracted_from_note(self):
        ids = _referenced_doc_ids([_make_tx("Receipt: RC-001")])
        assert "RC-001" in ids

    def test_ml_id_extracted_from_note(self):
        ids = _referenced_doc_ids([_make_tx("Mileage log: ML-001")])
        assert "ML-001" in ids

    def test_vi_id_extracted_from_note(self):
        ids = _referenced_doc_ids([_make_tx("Vendor invoice VI-002")])
        assert "VI-002" in ids

    def test_multiple_ids_in_one_note(self):
        ids = _referenced_doc_ids([_make_tx("See RC-003 and VI-001 for backup")])
        assert {"RC-003", "VI-001"}.issubset(ids)

    def test_ids_collected_across_transactions(self):
        ids = _referenced_doc_ids([
            _make_tx("Receipt: RC-001"),
            _make_tx("Receipt: RC-003"),
        ])
        assert ids == {"RC-001", "RC-003"}

    def test_empty_note_returns_empty_set(self):
        ids = _referenced_doc_ids([_make_tx("")])
        assert ids == set()

    def test_labour_lines_with_no_note(self):
        txs = load_transactions(SUBMISSION_CLEAN)
        labour_notes = [t.note for t in txs if t.is_labor]
        assert all(n == "" for n in labour_notes)

    def test_clean_submission_references_correct_docs(self):
        txs = load_transactions(SUBMISSION_CLEAN)
        ids = _referenced_doc_ids(txs)
        assert "RC-001" in ids
        assert "RC-003" in ids
        assert "ML-001" in ids


# ── load_inputs — transaction loading ────────────────────────────────────────

class TestIngestionResultTransactions:

    def test_clean_transaction_counts(self):
        result = load_inputs(SUBMISSION_CLEAN)
        assert len(result.transactions)          == 7
        assert len(result.labour_transactions)   == 2
        assert len(result.expense_transactions)  == 5
        assert len(result.held_transactions)     == 0

    def test_hold_miscoded_held_count(self):
        result = load_inputs(SUBMISSION_HOLD_MISCODED)
        assert len(result.held_transactions) == 1

    def test_principal_cap_held_count(self):
        result = load_inputs(SUBMISSION_PRINCIPAL_CAP)
        assert len(result.held_transactions) == 1

    def test_over_cap_submission_has_no_holds(self):
        result = load_inputs(SUBMISSION_OVER_CAP)
        assert len(result.held_transactions) == 0


# ── load_inputs — timecard scoping ────────────────────────────────────────────

class TestTimecardScoping:

    def test_clean_timecards_only_e1041(self):
        result = load_inputs(SUBMISSION_CLEAN)
        emp_ids = {tc.employee_id for tc in result.timecards}
        assert emp_ids == {"E-1041"}

    def test_over_cap_timecards_only_e2210(self):
        result = load_inputs(SUBMISSION_OVER_CAP)
        emp_ids = {tc.employee_id for tc in result.timecards}
        assert emp_ids == {"E-2210"}

    def test_hold_miscoded_timecards_only_e3055(self):
        result = load_inputs(SUBMISSION_HOLD_MISCODED)
        emp_ids = {tc.employee_id for tc in result.timecards}
        assert emp_ids == {"E-3055"}

    def test_principal_cap_timecards_only_e4501(self):
        result = load_inputs(SUBMISSION_PRINCIPAL_CAP)
        emp_ids = {tc.employee_id for tc in result.timecards}
        assert emp_ids == {"E-4501"}

    def test_currency_submission_timecards_only_e7702(self):
        result = load_inputs(SUBMISSION_CURRENCY)
        emp_ids = {tc.employee_id for tc in result.timecards}
        # E-7702 has no entries in the timecard file (expense-only employee);
        # scoping is still correct — no other employee's timecards were loaded
        assert emp_ids.issubset({"E-7702"})

    def test_different_submissions_load_different_timecards(self):
        r1 = load_inputs(SUBMISSION_CLEAN)
        r2 = load_inputs(SUBMISSION_OVER_CAP)
        ids1 = {tc.employee_id for tc in r1.timecards}
        ids2 = {tc.employee_id for tc in r2.timecards}
        assert ids1.isdisjoint(ids2)


# ── load_inputs — document scoping ────────────────────────────────────────────

class TestDocumentScoping:

    def test_clean_loads_only_referenced_docs(self):
        result = load_inputs(SUBMISSION_CLEAN)
        doc_ids = {d.doc_id for d in result.documents}
        # RC-004 is referenced in a note but has no file in the docs store
        assert doc_ids == {"RC-001", "RC-003", "ML-001"}

    def test_over_cap_loads_only_rc012_rc013(self):
        result = load_inputs(SUBMISSION_OVER_CAP)
        doc_ids = {d.doc_id for d in result.documents}
        assert doc_ids == {"RC-012", "RC-013"}

    def test_subcon_loads_no_docs_when_none_exist(self):
        # RC-051, RC-052, VI-003 referenced but don't exist in docs store
        result = load_inputs(SUBMISSION_SUBCON)
        assert result.documents == []

    def test_docs_not_in_submission_are_excluded(self):
        result = load_inputs(SUBMISSION_CLEAN)
        doc_ids = {d.doc_id for d in result.documents}
        assert "RC-007" not in doc_ids   # team dinner not referenced by E-1041
        assert "RC-016" not in doc_ids   # composite not referenced by E-1041

    def test_currency_submission_references_correct_docs(self):
        result = load_inputs(SUBMISSION_CURRENCY)
        doc_ids = {d.doc_id for d in result.documents}
        # RC-041, RC-042, RC-043 don't exist in docs store — expect empty or subset
        assert doc_ids.issubset({"RC-041", "RC-042", "RC-043"})


# ── load_inputs — contract and static data ────────────────────────────────────

class TestStaticDataLoading:

    def test_rate_table_loaded(self):
        result = load_inputs(SUBMISSION_CLEAN)
        assert len(result.rate_table) > 0

    def test_contract_clauses_loaded(self):
        result = load_inputs(SUBMISSION_CLEAN)
        assert len(result.contract_clauses) > 0

    def test_instructions_loaded(self):
        result = load_inputs(SUBMISSION_CLEAN)
        assert len(result.instructions) > 0

    def test_exceptions_loaded(self):
        result = load_inputs(SUBMISSION_CLEAN)
        assert len(result.exceptions) > 0

    def test_submission_file_path_recorded(self):
        result = load_inputs(SUBMISSION_CLEAN)
        assert result.submission_file == SUBMISSION_CLEAN

    def test_loaded_at_is_set(self):
        result = load_inputs(SUBMISSION_CLEAN)
        assert result.loaded_at is not None
