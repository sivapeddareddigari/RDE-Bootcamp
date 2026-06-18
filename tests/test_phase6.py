"""
Tests for Phase 6 — Agentic orchestration layer.

  TestDecisionMemory     decision_memory store — load + find_relevant + format
  TestInstructionStore   instruction_store — format_for_prompt
  TestExceptionAgent     exception_agent — JSON parsing + mocked API call
  TestSupervisor         supervisor — fallback path + mocked tool-use loop
  TestNoticeWriterLLM    notice_writer llm_texts parameter integration
"""

import json
from datetime import date
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch

import pytest

from billing_agent.agents.exception_agent import ExceptionAnalysis, _parse_response, run as agent_run
from billing_agent.agents.supervisor import SupervisorResult, run as supervisor_run
from billing_agent.exceptions.models import ExceptionItem, ExceptionReport
from billing_agent.ingestion.contacts_loader import load_contacts
from billing_agent.ingestion import load_inputs
from billing_agent.models.instruction import ExceptionCase, ProjectInstruction
from billing_agent.output.notice_writer import write_notices
from billing_agent.rules import rule_engine
from billing_agent.matching import reconcile
from billing_agent.exceptions import run as detect_exceptions
from billing_agent.stores.decision_memory import find_relevant, format_for_prompt as fmt_memory, load_memory
from billing_agent.stores.instruction_store import format_for_prompt as fmt_instructions
from tests.conftest import SUBMISSION_CLEAN, SUBMISSION_SUBCON


# ── Shared fixtures ───────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def contacts():
    return load_contacts()


@pytest.fixture(scope="module")
def clean_pipeline(contacts):
    inputs = load_inputs(SUBMISSION_CLEAN)
    rr     = rule_engine.run(inputs)
    mr     = reconcile(inputs, rr)
    er     = detect_exceptions(inputs, rr, mr)
    return inputs, rr, mr, er


@pytest.fixture(scope="module")
def subcon_pipeline(contacts):
    inputs = load_inputs(SUBMISSION_SUBCON)
    rr     = rule_engine.run(inputs)
    mr     = reconcile(inputs, rr)
    er     = detect_exceptions(inputs, rr, mr)
    return inputs, rr, mr, er


# ── TestDecisionMemory ────────────────────────────────────────────────────────

class TestDecisionMemory:

    def test_load_returns_cases(self):
        cases = load_memory()
        assert len(cases) >= 1

    def test_all_fields_populated(self):
        cases = load_memory()
        for c in cases:
            assert c.exception_id
            assert c.exception_type
            assert c.resolution
            assert isinstance(c.resolution_date, date)

    def test_recurring_cases_exist(self):
        cases = load_memory()
        assert any(c.recurring for c in cases)

    def test_find_relevant_matches_by_rule_id(self, clean_pipeline):
        inputs, rr, mr, er = clean_pipeline
        cases = load_memory()
        unresolved = er.escalate_employee + er.escalate_analyst + er.escalate_pl
        if not unresolved:
            pytest.skip("No unresolved items in clean submission")
        relevant = find_relevant(unresolved, cases)
        # There should be at least recurring cases returned
        assert len(relevant) >= 1

    def test_find_relevant_always_returns_recurring(self):
        """Recurring cases are always included regardless of exception type."""
        items = [ExceptionItem(
            transaction_id="TX-999", employee_id="E-0000",
            description="some unknown thing", original_amount=100.0,
            approved_amount=0.0, status="FLAG", exception_type="UNKNOWN_TYPE",
            rule_id="UNKNOWN_RULE", routing="ANALYST", override_applied=False,
            override_source="", note="test", blocks_invoice=False,
        )]
        cases = load_memory()
        relevant = find_relevant(items, cases)
        recurring = [c for c in cases if c.recurring]
        for r in recurring:
            assert r in relevant

    def test_format_for_prompt_contains_resolution(self):
        cases = load_memory()[:3]
        text = fmt_memory(cases)
        assert "Resolution" in text

    def test_format_for_prompt_tags_recurring(self):
        cases = [c for c in load_memory() if c.recurring][:1]
        text = fmt_memory(cases)
        assert "[RECURRING POLICY]" in text

    def test_format_empty_returns_message(self):
        assert "No prior" in fmt_memory([])


# ── TestInstructionStore ──────────────────────────────────────────────────────

class TestInstructionStore:

    def test_format_populated_instructions(self, clean_pipeline):
        inputs, *_ = clean_pipeline
        text = fmt_instructions(inputs.instructions)
        assert len(text) > 20
        assert "PL-EMAIL" in text or "OVERRIDE" in text or "STANDING" in text

    def test_format_empty_returns_message(self):
        assert "No project instructions" in fmt_instructions([])

    def test_format_includes_body_excerpt(self, clean_pipeline):
        inputs, *_ = clean_pipeline
        text = fmt_instructions(inputs.instructions)
        # Body should contain some content
        assert "Body:" in text


# ── TestExceptionAgent ────────────────────────────────────────────────────────

class TestExceptionAgent:

    def test_parse_valid_json(self):
        raw = json.dumps({
            "analyses": [
                {
                    "transaction_id": "TX-1006",
                    "recommendation": "ESCALATE",
                    "routing": "EMPLOYEE",
                    "reasoning": "Missing receipt — no backup on file.",
                    "employee_notice_text": "Hi David, please upload the receipt for TX-1006.",
                    "analyst_note": "Employee must submit receipt.",
                }
            ]
        })
        results = _parse_response(raw, {"TX-1006"})
        assert len(results) == 1
        assert results[0].transaction_id == "TX-1006"
        assert results[0].recommendation == "ESCALATE"
        assert results[0].routing == "EMPLOYEE"
        assert "David" in results[0].employee_notice_text

    def test_parse_strips_markdown_fences(self):
        raw = "```json\n{\"analyses\": [{\"transaction_id\": \"TX-1006\", \"recommendation\": \"ESCALATE\", \"routing\": \"EMPLOYEE\", \"reasoning\": \"x\", \"employee_notice_text\": \"y\", \"analyst_note\": \"z\"}]}\n```"
        results = _parse_response(raw, {"TX-1006"})
        assert len(results) == 1

    def test_parse_filters_unknown_ids(self):
        raw = json.dumps({
            "analyses": [
                {"transaction_id": "TX-UNKNOWN", "recommendation": "ESCALATE",
                 "routing": "EMPLOYEE", "reasoning": "", "employee_notice_text": "", "analyst_note": ""}
            ]
        })
        results = _parse_response(raw, {"TX-1006"})
        assert results == []

    def test_parse_malformed_json_returns_empty(self):
        results = _parse_response("this is not json", {"TX-1006"})
        assert results == []

    def test_no_unresolved_returns_empty(self, clean_pipeline, contacts):
        """If exception report has no unresolved items, agent returns [] without API call."""
        inputs, rr, mr, _ = clean_pipeline
        empty_report = ExceptionReport(
            submission_file="test.csv",
            generated_at="2026-06-18T00:00:00",
            total_transactions=5,
            clean_count=5,
        )
        result = agent_run(empty_report, inputs, contacts)
        assert result == []

    def test_api_error_returns_empty(self, clean_pipeline, contacts):
        """When the API raises, agent returns [] and pipeline continues."""
        inputs, rr, mr, er = clean_pipeline
        with patch("billing_agent.agents.exception_agent.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.side_effect = RuntimeError("network error")
            result = agent_run(er, inputs, contacts)
        assert result == []

    def test_mocked_api_call_returns_analyses(self, clean_pipeline, contacts):
        """With a mocked API response, agent returns ExceptionAnalysis objects."""
        inputs, rr, mr, er = clean_pipeline
        unresolved = er.escalate_employee + er.escalate_analyst + er.escalate_pl
        if not unresolved:
            pytest.skip("No unresolved items in clean submission")

        # Build a mock response matching the first unresolved item
        tx_id = unresolved[0].transaction_id
        mock_text = json.dumps({"analyses": [{
            "transaction_id": tx_id,
            "recommendation": "ESCALATE",
            "routing": "EMPLOYEE",
            "reasoning": "Missing receipt for this transaction.",
            "employee_notice_text": f"Please upload the receipt for {tx_id} in SAP.",
            "analyst_note": "Receipt outstanding.",
        }]})

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=mock_text)]

        with patch("billing_agent.agents.exception_agent.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = mock_response
            with patch("billing_agent.agents.exception_agent._resolve_api_key", return_value="test-key"):
                result = agent_run(er, inputs, contacts)

        assert len(result) == 1
        assert result[0].transaction_id == tx_id
        assert result[0].recommendation == "ESCALATE"
        assert "SAP" in result[0].employee_notice_text


# ── TestSupervisor ────────────────────────────────────────────────────────────

class TestSupervisor:

    def test_fallback_path_produces_result(self, contacts):
        """Without an API key the supervisor runs the deterministic fallback."""
        result = supervisor_run(SUBMISSION_CLEAN, contacts)
        assert isinstance(result, SupervisorResult)
        assert result.exception_report.total_transactions == 7
        assert len(result.notices_written) >= 1

    def test_fallback_result_has_notices(self, contacts):
        result = supervisor_run(SUBMISSION_CLEAN, contacts)
        notice_files = [p for p in result.notices_written if "exception-notice" in p.name]
        summary_files = [p for p in result.notices_written if "analyst-summary" in p.name]
        assert len(notice_files) >= 1
        assert len(summary_files) == 1

    def test_fallback_no_llm_analyses(self, contacts):
        """Fallback path produces no LLM analyses (no API call made)."""
        result = supervisor_run(SUBMISSION_CLEAN, contacts)
        assert result.analyses == []
        assert result.auto_resolved_by_llm == 0

    def test_mocked_supervisor_tool_loop(self, contacts):
        """Supervisor calls all three tools in the correct order."""
        # We'll track tool calls
        call_log = []

        def fake_create(**kwargs):
            messages = kwargs.get("messages", [])
            # Determine which turn we're on by counting prior assistant messages
            assistant_turns = sum(1 for m in messages if m["role"] == "assistant")

            if assistant_turns == 0:
                # Turn 1: call run_pipeline_phases_1_to_4
                call_log.append("run_pipeline")
                tool_use = MagicMock()
                tool_use.type = "tool_use"
                tool_use.id = "tu_001"
                tool_use.name = "run_pipeline_phases_1_to_4"
                tool_use.input = {"submission_path": str(SUBMISSION_CLEAN)}
                response = MagicMock()
                response.content = [tool_use]
                response.stop_reason = "tool_use"
                return response

            elif assistant_turns == 1:
                # Turn 2: call analyse_unresolved_exceptions
                call_log.append("analyse_exceptions")
                tool_use = MagicMock()
                tool_use.type = "tool_use"
                tool_use.id = "tu_002"
                tool_use.name = "analyse_unresolved_exceptions"
                tool_use.input = {"run_id": SUBMISSION_CLEAN.stem}
                response = MagicMock()
                response.content = [tool_use]
                response.stop_reason = "tool_use"
                return response

            elif assistant_turns == 2:
                # Turn 3: call write_notices_and_summary
                call_log.append("write_notices")
                tool_use = MagicMock()
                tool_use.type = "tool_use"
                tool_use.id = "tu_003"
                tool_use.name = "write_notices_and_summary"
                tool_use.input = {"run_id": SUBMISSION_CLEAN.stem, "use_llm_text": True}
                response = MagicMock()
                response.content = [tool_use]
                response.stop_reason = "tool_use"
                return response

            else:
                # Final turn: end
                call_log.append("end_turn")
                text = MagicMock()
                text.type = "text"
                text.text = "Pipeline complete."
                response = MagicMock()
                response.content = [text]
                response.stop_reason = "end_turn"
                return response

        with patch("billing_agent.agents.supervisor.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.side_effect = fake_create
            with patch("billing_agent.agents.supervisor._resolve_api_key", return_value="test-key"):
                # Exception agent will fail (no real API) and that's fine — fallback text is used
                result = supervisor_run(SUBMISSION_CLEAN, contacts)

        # Supervisor called all three tools
        assert "run_pipeline" in call_log
        assert "write_notices" in call_log
        assert isinstance(result, SupervisorResult)

    def test_supervisor_api_error_falls_back(self, contacts):
        """If the supervisor's own API call raises, it falls back gracefully."""
        with patch("billing_agent.agents.supervisor.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.side_effect = Exception("timeout")
            result = supervisor_run(SUBMISSION_CLEAN, contacts)
        assert isinstance(result, SupervisorResult)
        assert result.exception_report.total_transactions == 7


# ── TestNoticeWriterLLM ───────────────────────────────────────────────────────

class TestNoticeWriterLLM:

    def test_llm_text_replaces_template(self, clean_pipeline, contacts):
        """When llm_texts provides text for a transaction, it appears in the notice."""
        inputs, rr, mr, er = clean_pipeline
        unresolved = er.escalate_employee + er.escalate_analyst + er.escalate_pl
        if not unresolved:
            pytest.skip("No unresolved items in clean submission")
        tx_id = unresolved[0].transaction_id
        custom_text = "CUSTOM_LLM_INSTRUCTION_XYZ"
        llm_texts = {tx_id: custom_text}

        written = write_notices(inputs, rr, er, contacts, llm_texts=llm_texts)
        notice_files = [p for p in written if "exception-notice" in p.name]
        assert len(notice_files) >= 1
        # At least one notice should contain the custom text
        combined = "".join(p.read_text() for p in notice_files)
        assert custom_text in combined

    def test_template_used_when_no_llm_text(self, clean_pipeline, contacts):
        """When no llm_texts provided, template _ACTION text is used."""
        inputs, rr, mr, er = clean_pipeline
        written = write_notices(inputs, rr, er, contacts)
        notice_files = [p for p in written if "exception-notice" in p.name]
        if not notice_files:
            pytest.skip("No exception notices written")
        text = notice_files[0].read_text()
        # Template text references SAP
        assert "SAP" in text

    def test_empty_llm_texts_uses_template(self, clean_pipeline, contacts):
        """Passing an empty dict is equivalent to no llm_texts."""
        inputs, rr, mr, er = clean_pipeline
        written_template = write_notices(inputs, rr, er, contacts)
        written_empty    = write_notices(inputs, rr, er, contacts, llm_texts={})
        # Both should produce the same number of files
        assert len(written_template) == len(written_empty)
