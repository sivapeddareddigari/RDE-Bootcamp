"""
Exception Reasoning Agent — uses Claude to analyse unresolved billing exceptions.

For each unresolved ExceptionItem the agent:
  1. Checks whether a prior [RECURRING POLICY] resolution or PL instruction
     already covers the case → AUTO_RESOLVE recommendation.
  2. If the case is novel or needs human action → ESCALATE recommendation.
  3. Generates a transaction-specific, plain-English employee notice text
     (replacing the generic _ACTION template strings from notice_writer.py).
  4. Provides a short analyst note for the analyst summary.

This is a single-turn structured-output call — all context is gathered
deterministically before the call; Claude only reasons and writes language.
It does NOT call external tools or fetch additional data.

Falls back gracefully (returns []) if the API call fails or JSON is malformed.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import anthropic

from billing_agent import run_logger
from billing_agent.exceptions.models import ExceptionItem, ExceptionReport
from billing_agent.ingestion.contacts_loader import ContactDirectory
from billing_agent.ingestion.loader import IngestionResult
from billing_agent.stores.decision_memory import find_relevant, format_for_prompt as fmt_memory, load_memory
from billing_agent.stores.instruction_store import format_for_prompt as fmt_instructions

log = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 4096


# ── Public types ──────────────────────────────────────────────────────────────

@dataclass
class ExceptionAnalysis:
    transaction_id:      str
    recommendation:      str   # "AUTO_RESOLVE" | "ESCALATE"
    routing:             str   # "ANALYST" | "EMPLOYEE" | "PL" (may be unchanged)
    reasoning:           str   # why this decision was made
    employee_notice_text: str  # specific, actionable text for the employee notice
    analyst_note:        str   # short note for the analyst summary


# ── Public entry point ────────────────────────────────────────────────────────

def run(
    exception_report: ExceptionReport,
    inputs: IngestionResult,
    contacts: ContactDirectory,
) -> List[ExceptionAnalysis]:
    """
    Analyse all unresolved exceptions using Claude.

    Returns one ExceptionAnalysis per unresolved item.
    Returns [] if there are no unresolved items or if the API call fails.
    """
    unresolved: List[ExceptionItem] = (
        exception_report.escalate_employee
        + exception_report.escalate_analyst
        + exception_report.escalate_pl
    )
    if not unresolved:
        run_logger.step("Exception agent — no unresolved items, skipping", "ok")
        return []

    memory = load_memory()
    relevant_cases = find_relevant(unresolved, memory)

    context  = _build_context(inputs, relevant_cases)
    user_msg = _build_user_message(unresolved, contacts)

    try:
        api_key = _resolve_api_key()
        client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        response = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"{context}\n\n---\n\n{user_msg}"}],
        )
        raw = response.content[0].text
        analyses = _parse_response(raw, {i.transaction_id for i in unresolved})
        auto = sum(1 for a in analyses if a.recommendation == "AUTO_RESOLVE")
        run_logger.step(
            f"Exception agent — {len(analyses)} item(s) analysed  "
            f"({auto} auto-resolved by LLM, {len(analyses) - auto} escalating)",
            "ok",
        )
        return analyses

    except Exception as exc:
        log.warning("Exception agent unavailable (%s) — continuing with template notices", exc)
        run_logger.step("Exception agent — falling back to template notices", "warn")
        return []


def _resolve_api_key() -> str:
    import os, json as _json
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        return key
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


# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a billing exception analyst at Meridian Atlas Partners, a professional services firm. You review unresolved billing exceptions on client engagements and produce two outputs per exception:

1. A RECOMMENDATION: either AUTO_RESOLVE (the exception can be closed without employee action, based on a PL instruction or recurring policy) or ESCALATE (the employee or analyst must act).

2. An EMPLOYEE_NOTICE_TEXT: a clear, specific, friendly message addressed to the employee. It must name the exact vendor/description, date if visible in the note, and the precise action they need to take in SAP. Do NOT use generic language like "review this item" — be concrete.

HARD RULES (never override):
- ALCOHOL, PERSONAL_ITEM, AIRPORT_LOUNGE: always ESCALATE with EMPLOYEE routing.
- If a prior case is marked [RECURRING POLICY] and the exception type matches, you MAY AUTO_RESOLVE.
- If a PL instruction explicitly covers this transaction, you MAY AUTO_RESOLVE.
- Do not change the routing unless you have a specific reason.

Respond ONLY with a JSON object — no prose before or after:
{
  "analyses": [
    {
      "transaction_id": "TX-XXXX",
      "recommendation": "AUTO_RESOLVE" or "ESCALATE",
      "routing": "ANALYST" or "EMPLOYEE" or "PL",
      "reasoning": "One or two sentences citing the specific contract clause, PL instruction ID, or prior case ID that drives this decision.",
      "employee_notice_text": "A specific message to the employee. Start with their first name if available. Reference the exact transaction. State what to do in SAP.",
      "analyst_note": "One sentence for the billing analyst."
    }
  ]
}"""


# ── Prompt construction ───────────────────────────────────────────────────────

def _build_context(inputs: IngestionResult, relevant_cases) -> str:
    instructions_text = fmt_instructions(inputs.instructions)
    memory_text       = fmt_memory(relevant_cases)

    clause_lines = [
        f"  §{c.clause_id}: {c.rule_text[:200]}"
        for c in inputs.contract_clauses
        if c.clause_id
    ][:12]
    contract_text = "\n".join(clause_lines) or "Contract clauses not available."

    return (
        "## Project Instructions (PL emails for this billing cycle)\n"
        f"{instructions_text}\n\n"
        "## Prior Resolution Patterns (decision memory)\n"
        f"{memory_text}\n\n"
        "## Contract Rules (excerpt)\n"
        f"{contract_text}"
    )


def _build_user_message(
    unresolved: List[ExceptionItem],
    contacts: ContactDirectory,
) -> str:
    blocks = ["## Unresolved exceptions — analyse each one\n"]
    for item in unresolved:
        emp = contacts.employee(item.employee_id)
        name = emp.name if emp else item.employee_id
        blocks.append(
            f"Transaction ID : {item.transaction_id}\n"
            f"Employee       : {name} ({item.employee_id})\n"
            f"Description    : {item.description}\n"
            f"Amount (USD)   : ${item.original_amount:,.2f}\n"
            f"Rule fired     : {item.rule_id}\n"
            f"Current routing: {item.routing}\n"
            f"Blocks invoice : {item.blocks_invoice}\n"
            f"Rule note      : {item.note}"
        )
    return "\n\n---\n\n".join(blocks)


# ── Response parsing ──────────────────────────────────────────────────────────

def _parse_response(
    raw: str,
    valid_ids: set,
) -> List[ExceptionAnalysis]:
    try:
        text = raw.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            parts = text.split("```")
            text = parts[1].lstrip("json").strip() if len(parts) > 1 else text
        data = json.loads(text)
        results = []
        for a in data.get("analyses", []):
            tx_id = a.get("transaction_id", "")
            if tx_id not in valid_ids:
                continue
            results.append(ExceptionAnalysis(
                transaction_id       = tx_id,
                recommendation       = a.get("recommendation", "ESCALATE"),
                routing              = a.get("routing", "EMPLOYEE"),
                reasoning            = a.get("reasoning", ""),
                employee_notice_text = a.get("employee_notice_text", ""),
                analyst_note         = a.get("analyst_note", ""),
            ))
        return results
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        log.warning("Failed to parse exception agent response: %s", exc)
        log.debug("Raw response (first 500 chars): %s", raw[:500])
        return []
