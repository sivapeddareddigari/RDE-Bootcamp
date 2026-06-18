"""
Parses sample-emails.md into ProjectInstruction objects.

Each email block is delimited by "## Email N" headings and contains:
  From / To / Date / Subject header lines, then a free-text body.

Instruction types are inferred from subject + body keywords:
  STANDING          — general standing preference (no specific amount/tx)
  OVERRIDE_APPROVE  — PL explicitly approves an over-cap or flagged item
  OVERRIDE_REJECT   — PL explicitly rejects a charge
  RELEASE_HOLD      — PL releases a hold on a specific transaction
  CONFIRM_MARKUP    — PL confirms a pass-through markup
"""

import logging
import re
from datetime import date
from pathlib import Path
from typing import List, Optional

from billing_agent.models.instruction import ProjectInstruction

log = logging.getLogger(__name__)

_EMAIL_SECTION = re.compile(r"^## Email \d+", re.MULTILINE)
_HEADER_FIELD  = re.compile(r"^\*\*(From|To|Date|Subject):\*\*\s*(.+)$", re.MULTILINE)
_AMOUNT        = re.compile(r"USD\s*([\d,]+(?:\.\d{2})?)|(\d+(?:\.\d{2})?)\s*(?:USD|dollar)", re.IGNORECASE)
_DATE_RE       = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def load_instructions(md_path: Path) -> List[ProjectInstruction]:
    text = md_path.read_text(encoding="utf-8")
    blocks = _split_emails(text)
    instructions: List[ProjectInstruction] = []
    for idx, block in enumerate(blocks, start=1):
        inst = _parse_email(block, idx)
        if inst:
            instructions.append(inst)
    log.info("Loaded %d PL instructions from %s", len(instructions), md_path.name)
    return instructions


def _split_emails(text: str) -> List[str]:
    positions = [m.start() for m in _EMAIL_SECTION.finditer(text)]
    if not positions:
        return []
    blocks = []
    for i, pos in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(text)
        blocks.append(text[pos:end])
    return blocks


def _parse_email(block: str, idx: int) -> Optional[ProjectInstruction]:
    headers = {m.group(1): m.group(2).strip() for m in _HEADER_FIELD.finditer(block)}
    if not headers:
        return None

    from_name = headers.get("From", "Unknown")
    subject   = headers.get("Subject", "")
    date_str  = headers.get("Date", "")
    body      = _strip_headers(block)

    instruction_date = _parse_date(date_str)
    itype            = _infer_type(subject, body)
    amount           = _extract_amount(body)
    recurring        = bool(re.search(r"\bstanding\b|\bgoing forward\b|\bevery cycle\b|\balways\b", body, re.IGNORECASE))

    return ProjectInstruction(
        instruction_id   = f"PL-EMAIL-{idx:02d}",
        instruction_date = instruction_date,
        from_name        = from_name.split("(")[0].strip(),
        subject          = subject,
        instruction_type = itype,
        scope            = _infer_scope(subject, body),
        amount           = amount,
        body             = body.strip(),
        recurring        = recurring,
    )


def _infer_type(subject: str, body: str) -> str:
    text = (subject + " " + body).lower()
    if re.search(r"\bplease drop\b(?! a comment)|\bdo not bill\b|\breject\b|\bnot appropriate\b|\bdrop that\b", text):
        return "OVERRIDE_REJECT"
    if re.search(r"\brelease the hold\b|\brelease.*hold\b|\bbill.*hold\b", text):
        return "RELEASE_HOLD"
    if re.search(r"\bapply.*markup\b|\bconfirm.*markup\b|\bmarkup\b", text):
        return "CONFIRM_MARKUP"
    if re.search(r"\bplease.*bill\b|\bgo ahead and bill\b|\bapproved\b|\bbill it\b", text):
        return "OVERRIDE_APPROVE"
    return "STANDING"


def _infer_scope(subject: str, body: str) -> str:
    scope_parts = []
    if re.search(r"hotel|lodging|room", subject + body, re.IGNORECASE):
        scope_parts.append("lodging")
    if re.search(r"dinner|meal|lunch|breakfast", subject + body, re.IGNORECASE):
        scope_parts.append("meals")
    if re.search(r"lounge", subject + body, re.IGNORECASE):
        scope_parts.append("airport lounge")
    if re.search(r"subcontractor|drone|markup", subject + body, re.IGNORECASE):
        scope_parts.append("subcontractor")
    if re.search(r"hold|principal", subject + body, re.IGNORECASE):
        scope_parts.append("hold")
    if re.search(r"training|miscoded|non-billable|pmo", subject + body, re.IGNORECASE):
        scope_parts.append("miscoded labour")
    return ", ".join(scope_parts) if scope_parts else "general"


def _extract_amount(body: str) -> Optional[float]:
    m = _AMOUNT.search(body)
    if m:
        raw = m.group(1) or m.group(2)
        return float(raw.replace(",", ""))
    return None


def _parse_date(date_str: str) -> date:
    m = _DATE_RE.search(date_str)
    if m:
        return date.fromisoformat(m.group(1))
    return date(2026, 4, 1)


def _strip_headers(block: str) -> str:
    lines = block.splitlines()
    body_lines = []
    past_headers = False
    for line in lines:
        if line.startswith("## Email"):
            continue
        if re.match(r"^\*\*(From|To|Date|Subject):\*\*", line):
            past_headers = True
            continue
        if past_headers:
            body_lines.append(line)
    return "\n".join(body_lines)
