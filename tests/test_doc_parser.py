"""Unit tests for billing_agent.ingestion.doc_parser."""

import pytest
from billing_agent.ingestion.doc_parser import load_documents, _file_doc_id
from tests.conftest import DOCS_DIR


# ── _file_doc_id helper ───────────────────────────────────────────────────────

class TestFileDocId:

    def test_rc_prefix_extracted(self):
        assert _file_doc_id("RC-001-flight-outbound") == "RC-001"

    def test_ml_prefix_extracted(self):
        assert _file_doc_id("ML-001-mileage-log") == "ML-001"

    def test_vi_prefix_extracted(self):
        assert _file_doc_id("VI-002-subcontractor") == "VI-002"

    def test_three_digit_number_preserved(self):
        assert _file_doc_id("RC-016-composite") == "RC-016"

    def test_bare_stem_with_no_extra_parts(self):
        assert _file_doc_id("RC-001") == "RC-001"


# ── load_documents — filtering ────────────────────────────────────────────────

class TestLoadDocumentsFiltering:

    def test_unfiltered_loads_all(self):
        docs = load_documents(DOCS_DIR)
        assert len(docs) >= 14   # at least all known test documents

    def test_filter_returns_only_requested_ids(self):
        docs = load_documents(DOCS_DIR, doc_ids={"RC-001", "RC-003"})
        assert {d.doc_id for d in docs} == {"RC-001", "RC-003"}

    def test_filter_single_doc(self):
        docs = load_documents(DOCS_DIR, doc_ids={"RC-012"})
        assert len(docs) == 1
        assert docs[0].doc_id == "RC-012"

    def test_filter_empty_set_returns_empty_list(self):
        docs = load_documents(DOCS_DIR, doc_ids=set())
        assert docs == []

    def test_filter_nonexistent_id_returns_empty(self):
        docs = load_documents(DOCS_DIR, doc_ids={"RC-999"})
        assert docs == []

    def test_filter_mixed_valid_invalid(self):
        docs = load_documents(DOCS_DIR, doc_ids={"RC-001", "RC-999"})
        assert len(docs) == 1
        assert docs[0].doc_id == "RC-001"

    def test_none_filter_is_same_as_unfiltered(self):
        docs_none = load_documents(DOCS_DIR, doc_ids=None)
        docs_all  = load_documents(DOCS_DIR)
        assert len(docs_none) == len(docs_all)


# ── load_documents — document properties ─────────────────────────────────────

class TestDocumentProperties:

    def test_composite_flag_set(self):
        docs = load_documents(DOCS_DIR, doc_ids={"RC-016"})
        assert len(docs) == 1
        assert docs[0].is_composite is True

    def test_non_composite_flag_clear(self):
        docs = load_documents(DOCS_DIR, doc_ids={"RC-001"})
        assert docs[0].is_composite is False

    def test_unreadable_flag_set(self):
        docs = load_documents(DOCS_DIR, doc_ids={"RC-018"})
        assert len(docs) == 1
        assert docs[0].is_unreadable is True

    def test_unreadable_total_is_zero(self):
        docs = load_documents(DOCS_DIR, doc_ids={"RC-018"})
        assert docs[0].total_amount == 0.0

    def test_alcohol_flag_set(self):
        # RC-003 (hotel folio) and RC-007 (team dinner) contain alcohol
        docs = load_documents(DOCS_DIR, doc_ids={"RC-003", "RC-007"})
        assert all(d.has_alcohol for d in docs)

    def test_alcohol_flag_clear_on_clean_doc(self):
        docs = load_documents(DOCS_DIR, doc_ids={"RC-001"})
        assert docs[0].has_alcohol is False

    def test_foreign_currency_extracted(self):
        docs = load_documents(DOCS_DIR, doc_ids={"RC-015"})
        assert docs[0].currency == "CAD"

    def test_default_currency_is_usd(self):
        docs = load_documents(DOCS_DIR, doc_ids={"RC-001"})
        assert docs[0].currency == "USD"

    def test_vendor_name_extracted(self):
        docs = load_documents(DOCS_DIR, doc_ids={"RC-001"})
        assert docs[0].vendor not in ("", "UNKNOWN")

    def test_total_amount_positive(self):
        docs = load_documents(DOCS_DIR, doc_ids={"RC-012"})
        assert docs[0].total_amount == pytest.approx(310.00)

    def test_over_cap_hotel_amount(self):
        docs = load_documents(DOCS_DIR, doc_ids={"RC-012"})
        assert docs[0].total_amount > 275.00   # over metro cap

    def test_mileage_log_type(self):
        docs = load_documents(DOCS_DIR, doc_ids={"ML-001"})
        assert docs[0].doc_type == "MILEAGE_LOG"

    def test_vendor_invoice_type(self):
        docs = load_documents(DOCS_DIR, doc_ids={"VI-002"})
        assert docs[0].doc_type == "VENDOR_INVOICE"

    def test_receipt_type(self):
        docs = load_documents(DOCS_DIR, doc_ids={"RC-001"})
        assert docs[0].doc_type == "RECEIPT"

    def test_composite_doc_type(self):
        docs = load_documents(DOCS_DIR, doc_ids={"RC-016"})
        assert docs[0].doc_type == "COMPOSITE"

    def test_line_items_extracted_for_normal_doc(self):
        docs = load_documents(DOCS_DIR, doc_ids={"RC-013"})
        assert len(docs[0].line_items) > 0

    def test_line_items_empty_for_unreadable(self):
        docs = load_documents(DOCS_DIR, doc_ids={"RC-018"})
        assert docs[0].line_items == []

    def test_subcontractor_invoice_amount(self):
        docs = load_documents(DOCS_DIR, doc_ids={"VI-002"})
        assert docs[0].total_amount == pytest.approx(2400.00)
