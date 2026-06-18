"""
Billing Supervisor Agent — LLM-orchestrated per-submission pipeline.

Uses a Claude tool-use loop to orchestrate the deterministic pipeline
(Phases 1–4), call the Exception Reasoning Agent for unresolved items,
and write enhanced employee notices.

Entry point:
    from billing_agent.agents.supervisor import run
    result = run(submission_path, contacts)

Graceful fallback: if the Anthropic API is unavailable the pipeline runs
directly (same behaviour as Phase 5a — template notices, no LLM reasoning).
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import anthropic

from billing_agent import run_logger
from billing_agent.agents.exception_agent import ExceptionAnalysis
from billing_agent.agents.exception_agent import run as _exception_agent_run
from billing_agent.email.mailer import send_submission_emails
from billing_agent.exceptions import run as detect_exceptions
from billing_agent.exceptions.models import ExceptionReport
from billing_agent.ingestion import load_inputs
from billing_agent.ingestion.contacts_loader import ContactDirectory
from billing_agent.ingestion.loader import IngestionResult
from billing_agent.matching import reconcile
from billing_agent.output.notice_writer import write_notices
from billing_agent.rules import rule_engine
from billing_agent.rules.models import RuleResult

log = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5-20251001"
_MAX_ITERATIONS = 10


# ── Public return type ────────────────────────────────────────────────────────

@dataclass
class SupervisorResult:
    inputs:              IngestionResult
    rule_results:        List[RuleResult]
    exception_report:    ExceptionReport
    analyses:            List[ExceptionAnalysis]
    notices_written:     List[Path]
    auto_resolved_by_llm: int


# ── Tool schemas (Claude-facing) ──────────────────────────────────────────────

_TOOLS = [
    {
        "name": "run_pipeline_phases_1_to_4",
        "description": (
            "Run the deterministic billing pipeline (Phases 1–4) on a submission CSV. "
            "Ingests all inputs, applies contract rules, matches receipt documents, and "
            "detects exceptions. Returns a structured summary of all exception counts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "submission_path": {
                    "type": "string",
                    "description": "Absolute path to the submission CSV file.",
                }
            },
            "required": ["submission_path"],
        },
    },
    {
        "name": "analyse_unresolved_exceptions",
        "description": (
            "Call the Exception Reasoning Agent to analyse unresolved exceptions using "
            "prior resolution patterns and PL instructions. Generates contextual, "
            "transaction-specific employee notice text. "
            "Only call this tool when the pipeline reports unresolved exceptions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "description": "The run_id returned by run_pipeline_phases_1_to_4.",
                }
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "write_notices_and_summary",
        "description": (
            "Write per-employee exception notices and an analyst summary to the output/ "
            "directory. Uses LLM-generated notice text when available, falls back to "
            "templates otherwise. Must be called after run_pipeline_phases_1_to_4."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "description": "The run_id returned by run_pipeline_phases_1_to_4.",
                },
                "use_llm_text": {
                    "type": "boolean",
                    "description": (
                        "If true, use LLM-generated notice text for each exception item. "
                        "If false, use the built-in template text."
                    ),
                },
            },
            "required": ["run_id"],
        },
    },
]


# ── Public entry point ────────────────────────────────────────────────────────

def run(submission_path: Path, contacts: ContactDirectory) -> SupervisorResult:
    """
    Orchestrate the per-submission billing review pipeline via a Claude tool-use loop.
    Falls back to a direct deterministic run if the Anthropic API is unavailable.
    """
    state: Dict[str, Any] = {}

    def handle(name: str, tool_input: Dict) -> str:
        if name == "run_pipeline_phases_1_to_4":
            return _tool_run_pipeline(tool_input, state, contacts)
        if name == "analyse_unresolved_exceptions":
            return _tool_analyse_exceptions(tool_input, state, contacts)
        if name == "write_notices_and_summary":
            return _tool_write_notices(tool_input, state, contacts)
        return json.dumps({"error": f"Unknown tool: {name}"})

    messages = [
        {
            "role": "user",
            "content": (
                f"Process the billing submission at: {submission_path}\n\n"
                "Steps:\n"
                "1. Run the pipeline (Phases 1–4) to evaluate all transactions.\n"
                "2. If there are unresolved exceptions, call the exception reasoning agent.\n"
                "3. Write employee notices and the analyst summary.\n"
                "Respond with a brief summary of findings once all three steps are done."
            ),
        }
    ]

    try:
        api_key = _resolve_api_key()
        client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        for _ in range(_MAX_ITERATIONS):
            response = client.messages.create(
                model=_MODEL,
                max_tokens=2048,
                system=_SYSTEM_PROMPT,
                tools=_TOOLS,
                messages=messages,
            )

            tool_uses = [b for b in response.content if b.type == "tool_use"]

            for tb in response.content:
                if tb.type == "text" and tb.text.strip():
                    run_logger.step(f"Supervisor: {tb.text[:120]}", "info")

            if response.stop_reason == "end_turn" or not tool_uses:
                break

            results = []
            for tu in tool_uses:
                outcome = handle(tu.name, tu.input)
                log.info("  [supervisor] %s → %s", tu.name, outcome[:120])
                run_logger.step(f"Supervisor → {tu.name}", "info")
                results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": outcome,
                })

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": results})

        run_logger.step("Supervisor agent — pipeline complete", "ok")

    except Exception as exc:
        log.warning("Supervisor unavailable (%s) — running deterministic fallback", exc)
        run_logger.step("Supervisor — falling back to deterministic pipeline", "warn")
        _direct_fallback(submission_path, contacts, state)

    result = _build_result(state)
    _dispatch_emails(result, contacts)
    return result


# ── Tool implementations ──────────────────────────────────────────────────────

def _tool_run_pipeline(
    tool_input: Dict, state: Dict, contacts: ContactDirectory
) -> str:
    path = Path(tool_input["submission_path"])
    try:
        inputs       = load_inputs(path)
        rr           = rule_engine.run(inputs)
        mr           = reconcile(inputs, rr)
        er           = detect_exceptions(inputs, rr, mr)
        run_id       = path.stem
        state[run_id] = {
            "inputs": inputs, "rule_results": rr,
            "match_results": mr, "exception_report": er,
            "analyses": [], "notices_written": [],
        }
        return json.dumps({
            "run_id":           run_id,
            "total_transactions": er.total_transactions,
            "clean_count":      er.clean_count,
            "auto_resolved":    len(er.auto_resolved),
            "hard_rejections":  len(er.hard_rejections),
            "unresolved_count": er.unresolved_count,
            "blocking_count":   len(er.blocking),
            "has_unresolved":   er.unresolved_count > 0,
        })
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def _tool_analyse_exceptions(
    tool_input: Dict, state: Dict, contacts: ContactDirectory
) -> str:
    run_id = tool_input.get("run_id", "")
    s = state.get(run_id)
    if not s:
        return json.dumps({"error": f"No pipeline state for run_id={run_id!r}"})
    analyses = _exception_agent_run(s["exception_report"], s["inputs"], contacts)
    s["analyses"] = analyses
    auto = sum(1 for a in analyses if a.recommendation == "AUTO_RESOLVE")
    return json.dumps({
        "run_id":             run_id,
        "total_analysed":     len(analyses),
        "auto_resolved_by_llm": auto,
        "still_escalating":   len(analyses) - auto,
    })


def _tool_write_notices(
    tool_input: Dict, state: Dict, contacts: ContactDirectory
) -> str:
    run_id    = tool_input.get("run_id", "")
    use_llm   = tool_input.get("use_llm_text", True)
    s = state.get(run_id)
    if not s:
        return json.dumps({"error": f"No pipeline state for run_id={run_id!r}"})

    llm_texts = (
        {a.transaction_id: a.employee_notice_text for a in s["analyses"] if a.employee_notice_text}
        if use_llm else {}
    )
    llm_auto_resolved = (
        {a.transaction_id for a in s["analyses"] if a.recommendation == "AUTO_RESOLVE"}
        if use_llm else set()
    )
    written = write_notices(
        s["inputs"], s["rule_results"], s["exception_report"], contacts,
        llm_texts=llm_texts,
        llm_auto_resolved=llm_auto_resolved,
    )
    s["notices_written"] = written
    return json.dumps({
        "run_id":        run_id,
        "files_written": len(written),
        "paths":         [str(p) for p in written],
    })


# ── Deterministic fallback (API unavailable) ──────────────────────────────────

def _direct_fallback(
    submission_path: Path, contacts: ContactDirectory, state: Dict
) -> None:
    inputs = load_inputs(submission_path)
    rr     = rule_engine.run(inputs)
    mr     = reconcile(inputs, rr)
    er     = detect_exceptions(inputs, rr, mr)
    written = write_notices(inputs, rr, er, contacts)
    run_id = submission_path.stem
    state[run_id] = {
        "inputs": inputs, "rule_results": rr,
        "match_results": mr, "exception_report": er,
        "analyses": [], "notices_written": written,
    }


# ── Result assembly ───────────────────────────────────────────────────────────

def _resolve_api_key() -> str:
    """
    Find the Anthropic API key from the environment, .env file, or Claude Code
    settings files. Returns the key string, or "" if not found.
    """
    import os, json as _json

    # 1. Standard environment variable
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        return key

    # 2. Project .env file (same format as email config)
    env_path = Path(__file__).parent.parent.parent / ".env"
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == "ANTHROPIC_API_KEY":
                key = v.strip().strip('"').strip("'")
                if key:
                    return key

    # 3. Claude Code local project settings (.claude/settings.local.json)
    for settings_path in [
        Path(".claude/settings.local.json"),
        Path.home() / ".claude" / "settings.local.json",
        Path.home() / ".claude" / "settings.json",
    ]:
        try:
            data = _json.loads(settings_path.read_text(encoding="utf-8"))
            key = data.get("env", {}).get("ANTHROPIC_API_KEY", "")
            if key:
                return key
        except (FileNotFoundError, _json.JSONDecodeError, PermissionError):
            continue

    return ""


def _build_result(state: Dict) -> SupervisorResult:
    if not state:
        raise RuntimeError("Supervisor produced no state — pipeline never ran")
    s = state[next(iter(state))]
    return SupervisorResult(
        inputs               = s["inputs"],
        rule_results         = s["rule_results"],
        exception_report     = s["exception_report"],
        analyses             = s["analyses"],
        notices_written      = s["notices_written"],
        auto_resolved_by_llm = sum(
            1 for a in s["analyses"] if a.recommendation == "AUTO_RESOLVE"
        ),
    )


def _dispatch_emails(result: SupervisorResult, contacts: ContactDirectory) -> None:
    try:
        send_submission_emails(result.notices_written, contacts)
    except Exception as exc:
        log.warning("Email dispatch failed (%s) — pipeline result unaffected", exc)


# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are the Billing Supervisor Agent for Meridian Atlas Partners. "
    "Your job is to orchestrate the billing review pipeline for a single employee "
    "expense submission using the three tools available to you.\n\n"
    "Tool usage rules:\n"
    "- Always call run_pipeline_phases_1_to_4 first.\n"
    "- Call analyse_unresolved_exceptions only if has_unresolved is true in the pipeline result.\n"
    "- Always call write_notices_and_summary last, setting use_llm_text=true when "
    "LLM analyses are available.\n"
    "- Do not call any tool more than once.\n"
    "- After all tools complete, respond with a one-sentence summary of what was found."
)
