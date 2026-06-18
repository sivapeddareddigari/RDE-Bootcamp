"""
Parses contract-001.md into ContractClause and RateEntry objects.

Extracts:
  - Rate table (§3)
  - Expense rules table (§4)
  - Project-specific SAP notes at the bottom
"""

import logging
import re
from pathlib import Path
from typing import List, Optional, Tuple

from billing_agent.models.contract import ContractClause, RateEntry

log = logging.getLogger(__name__)

# Maps markdown table category keywords → canonical category names
_CATEGORY_MAP = {
    "air travel":       "TRAVEL_AIR",
    "lodging":          "LODGING",
    "ground transport": "TRAVEL_GROUND",
    "meals (with receipt)": "MEAL_RECEIPT",
    "per diem":         "MEAL_PER_DIEM",
    "mileage":          "MILEAGE",
    "telephony":        "TELEPHONY",
    "alcohol":          "ALCOHOL",
    "personal items":   "PERSONAL",
    "entertainment":    "ENTERTAINMENT",
    "subcontractor":    "SUBCONTRACTOR",
}


def load_contract(md_path: Path) -> Tuple[List[RateEntry], List[ContractClause], str]:
    text = md_path.read_text(encoding="utf-8")
    rates          = _parse_rates(text)
    clauses        = _parse_expense_rules(text) + _parse_sap_notes(text)
    project_sap    = _parse_project_sap_code(text)
    log.info("Loaded %d rate entries and %d contract clauses from %s (project SAP: %s)",
             len(rates), len(clauses), md_path.name, project_sap)
    return rates, clauses, project_sap


# ── Rate table ────────────────────────────────────────────────────────────────

def _parse_rates(text: str) -> List[RateEntry]:
    rates: List[RateEntry] = []
    in_table = False
    for line in text.splitlines():
        if "Role code" in line and "Rate" in line:
            in_table = True
            continue
        if in_table:
            if not line.strip() or line.startswith("#"):
                break
            cols = [c.strip() for c in line.split("|") if c.strip()]
            if len(cols) >= 3 and cols[0] not in ("---", "Role code"):
                try:
                    rates.append(RateEntry(
                        role_code = cols[0],
                        role_name = cols[1],
                        rate_usd  = float(cols[2].replace(",", "")),
                    ))
                except ValueError:
                    pass
    return rates


# ── Expense rules ─────────────────────────────────────────────────────────────

def _parse_expense_rules(text: str) -> List[ContractClause]:
    clauses: List[ContractClause] = []
    in_table = False
    idx = 0
    for line in text.splitlines():
        if "Category" in line and "Limit" in line:
            in_table = True
            continue
        if in_table:
            if not line.strip() or line.startswith("#"):
                break
            cols = [c.strip() for c in line.split("|") if c.strip()]
            if len(cols) >= 2 and "---" not in cols[0]:
                idx += 1
                category_raw = cols[0].lower()
                rule_text    = cols[1]
                category     = _resolve_category(category_raw)
                cap          = _extract_cap(rule_text)
                clauses.append(ContractClause(
                    clause_id  = f"CONTRACT-§4-{idx:02d}",
                    category   = category,
                    rule_text  = rule_text,
                    cap_amount = cap,
                    condition  = category_raw,
                ))
    # Subcontractor markup (§3)
    if re.search(r"cost plus 8", text, re.IGNORECASE):
        clauses.append(ContractClause(
            clause_id  = "CONTRACT-§3-SUB",
            category   = "SUBCONTRACTOR",
            rule_text  = "Subcontractor pass-through at cost plus 8%.",
            cap_amount = None,
            condition  = "subcontractor",
        ))
    return clauses


def _parse_sap_notes(text: str) -> List[ContractClause]:
    clauses: List[ContractClause] = []
    in_notes = False
    idx = 0
    for line in text.splitlines():
        if "Project-specific instructions" in line:
            in_notes = True
            continue
        if in_notes and line.strip().startswith("-"):
            idx += 1
            note_text = line.strip().lstrip("- ").strip('"')
            clauses.append(ContractClause(
                clause_id  = f"SAP-NOTE-{idx:02d}",
                category   = "PROJECT_PREF",
                rule_text  = note_text,
                cap_amount = None,
                condition  = "project preference",
            ))
    return clauses


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_project_sap_code(text: str) -> str:
    match = re.search(r"\*\*Project \(SAP\):\*\*\s*`([^`]+)`", text)
    return match.group(1) if match else ""


def _resolve_category(raw: str) -> str:
    for key, cat in _CATEGORY_MAP.items():
        if key in raw:
            return cat
    return "OTHER"


def _extract_cap(rule_text: str) -> Optional[float]:
    match = re.search(r"USD\s*([\d,]+(?:\.\d+)?)", rule_text)
    if match:
        return float(match.group(1).replace(",", ""))
    return None
