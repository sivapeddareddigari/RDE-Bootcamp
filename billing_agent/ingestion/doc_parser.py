"""
Parses backup document markdown files (RC-*, ML-*, VI-*) into ReceiptDocument objects.

Each file has three sections:
  1. Fenced code block — formatted receipt / invoice / log text
  2. # Notes for Analyst — analyst-facing commentary, contains linked TX IDs
  3. # Raw OCR — machine-readable version of the same content

Extraction strategy:
  - doc_id / doc_type from filename prefix
  - vendor: first non-blank line inside the code block
  - date: first ISO-ish date (YYYY-MM-DD) found in the code block
  - total_amount: last "TOTAL ... USD NNN.NN" line in the code block
  - linked_tx_ids: all TX-XXXX-XX-XXXX patterns in the Notes section
  - line_items: amount-bearing lines inside the code block (best-effort)
  - alcohol flag: presence of "alcohol", "house red", "wine", "beer", "spirits"
  - composite flag: filename RC-016 or "Composite scan" in the preamble
  - unreadable flag: "unreadable" or "illegible" in the preamble
"""

import logging
import re
from datetime import date
from pathlib import Path
from typing import List, Optional, Set

from billing_agent.models.document import LineItem, ReceiptDocument

log = logging.getLogger(__name__)

_ALCOHOL_WORDS = re.compile(
    r"\b(alcohol|alcoholic|house red|house white|wine|beer|lager|spirits|whisky|whiskey|champagne|prosecco|cider)\b",
    re.IGNORECASE,
)
_TX_REF   = re.compile(r"TX-\d{4}-\d{2}-\d{4}")
_AMOUNT   = re.compile(r"(?:USD\s*)?([\d,]+\.\d{2})")
_DATE     = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_TOTAL    = re.compile(r"TOTAL[^\d]*([\d,]+\.\d{2})", re.IGNORECASE)
_CURRENCY = re.compile(r"\b(CAD|EUR|GBP|AUD|JPY|CHF|MXN|SEK|NOK|DKK)\b")


def load_documents(docs_dir: Path, doc_ids: Optional[Set[str]] = None) -> List[ReceiptDocument]:
    docs: List[ReceiptDocument] = []
    skipped = 0
    for md_file in sorted(docs_dir.glob("*.md")):
        if md_file.name.lower() == "readme.md":
            continue
        if doc_ids is not None and _file_doc_id(md_file.stem) not in doc_ids:
            skipped += 1
            continue
        doc = _parse_document(md_file)
        if doc:
            docs.append(doc)
    if skipped:
        log.info("Skipped %d documents not referenced by this submission", skipped)
    log.info("Loaded %d documents from %s", len(docs), docs_dir)
    return docs


def _file_doc_id(stem: str) -> str:
    """Extract the doc ID prefix from a filename stem (e.g. 'RC-003-hotel-folio' → 'RC-003')."""
    parts = stem.split("-")
    return f"{parts[0]}-{parts[1]}" if len(parts) >= 2 else stem


def _parse_document(md_path: Path) -> Optional[ReceiptDocument]:
    text = md_path.read_text(encoding="utf-8")
    doc_id, doc_type = _classify(md_path.stem)

    code_block = _extract_code_block(text)
    notes_text = _extract_section(text, "Notes for Analyst")
    preamble   = text.split("```")[0] if "```" in text else text[:200]

    is_unreadable = bool(re.search(r"\b(unreadable|illegible)\b", preamble + notes_text, re.IGNORECASE))
    is_composite  = bool(re.search(r"\bcomposite\b", preamble, re.IGNORECASE)) or "RC-016" in md_path.stem

    vendor         = _extract_vendor(code_block)
    doc_date       = _extract_date(code_block)
    total_amount   = _extract_total(code_block)
    currency       = _extract_currency(code_block)
    line_items     = _extract_line_items(code_block) if not is_unreadable else []
    linked_txids   = _TX_REF.findall(notes_text)
    has_alcohol    = bool(_ALCOHOL_WORDS.search(code_block + " " + notes_text))
    alcohol_amount = sum(
        item.amount for item in line_items if _ALCOHOL_WORDS.search(item.description)
    )

    return ReceiptDocument(
        doc_id         = doc_id,
        doc_type       = doc_type,
        filename       = md_path.name,
        vendor         = vendor,
        doc_date       = doc_date,
        total_amount   = total_amount,
        currency       = currency,
        line_items     = line_items,
        linked_tx_ids  = linked_txids,
        notes          = notes_text.strip(),
        raw_text       = code_block,
        is_composite   = is_composite,
        has_alcohol    = has_alcohol,
        alcohol_amount = alcohol_amount,
        is_unreadable  = is_unreadable,
    )


# ── Classification ────────────────────────────────────────────────────────────

def _classify(stem: str) -> tuple[str, str]:
    upper = stem.upper()
    if upper.startswith("ML-"):
        return stem.split("-")[0] + "-" + stem.split("-")[1], "MILEAGE_LOG"
    if upper.startswith("VI-"):
        return stem.split("-")[0] + "-" + stem.split("-")[1], "VENDOR_INVOICE"
    if upper.startswith("RC-016"):
        return "RC-016", "COMPOSITE"
    if upper.startswith("RC-018"):
        return "RC-018", "UNREADABLE"
    if upper.startswith("RC-"):
        return stem.split("-")[0] + "-" + stem.split("-")[1], "RECEIPT"
    return stem, "UNKNOWN"


# ── Extraction helpers ────────────────────────────────────────────────────────

def _extract_code_block(text: str) -> str:
    match = re.search(r"```\n(.*?)```", text, re.DOTALL)
    return match.group(1) if match else ""


def _extract_section(text: str, heading: str) -> str:
    pattern = re.compile(rf"#+ {re.escape(heading)}\s*\n(.*?)(?:\n#|\Z)", re.DOTALL | re.IGNORECASE)
    match = pattern.search(text)
    return match.group(1) if match else ""


def _extract_vendor(block: str) -> str:
    for line in block.splitlines():
        line = line.strip()
        if line and not _DATE.match(line) and not line.startswith("-"):
            return line[:60]
    return "UNKNOWN"


def _extract_date(block: str) -> Optional[date]:
    for m in _DATE.finditer(block):
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            pass
    return None


def _extract_total(block: str) -> float:
    # Take the last TOTAL line — handles subtotal vs grand total
    totals = _TOTAL.findall(block)
    if totals:
        return float(totals[-1].replace(",", ""))
    # Fallback: last USD amount in block
    amounts = _AMOUNT.findall(block)
    if amounts:
        return float(amounts[-1].replace(",", ""))
    return 0.0


def _extract_currency(block: str) -> str:
    m = _CURRENCY.search(block)
    return m.group(1) if m else "USD"


def _extract_line_items(block: str) -> List[LineItem]:
    items: List[LineItem] = []
    for line in block.splitlines():
        line = line.strip()
        # Skip headers, totals, separators
        if not line or re.match(r"^[-=─]+$", line):
            continue
        if re.search(r"\b(TOTAL|Subtotal|Service charge|Tax|PAID|Charged)\b", line, re.IGNORECASE):
            continue
        m = re.search(r"([\d,]+\.\d{2})\s*$", line)
        if m:
            amount      = float(m.group(1).replace(",", ""))
            description = line[:line.rfind(m.group(1))].strip(" .")
            if description and amount > 0:
                items.append(LineItem(description=description, amount=amount))
    return items
