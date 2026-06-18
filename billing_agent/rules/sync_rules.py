"""
Reads contract-001.md and generates rule JSON files.

Source  : test-data/sample-inputs/contracts/contract-001.md
Outputs : billing_agent/rules/data/expense_caps.json
          billing_agent/rules/data/labour_rules.json
          billing_agent/rules/data/policy_rules.json

keyword_lists.json is NOT generated here — it contains operational
detection keywords maintained independently of the contract text.

Run directly:
    python3 billing_agent/rules/sync_rules.py

Or triggered automatically by:
  - git pre-commit hook  (when contract-001.md is staged)
  - Claude Code PostToolUse hook  (when contract-001.md is edited in-session)
"""

import json
import re
import sys
from pathlib import Path

_REPO_ROOT     = Path(__file__).parent.parent.parent
CONTRACT_PATH  = _REPO_ROOT / "test-data" / "sample-inputs" / "contracts" / "contract-001.md"
RULES_DATA_DIR = Path(__file__).parent / "data"


# ── Public entry point ────────────────────────────────────────────────────────

def sync(contract_path: Path = CONTRACT_PATH) -> bool:
    """Parse contract and write JSON rule files. Returns True if any file changed."""
    if not contract_path.exists():
        print(f"ERROR: contract not found at {contract_path}", file=sys.stderr)
        return False

    text = contract_path.read_text(encoding="utf-8")
    RULES_DATA_DIR.mkdir(parents=True, exist_ok=True)

    changed  = _write_if_changed("expense_caps.json",  _build_expense_caps(text))
    changed |= _write_if_changed("labour_rules.json",  _build_labour_rules(text))
    changed |= _write_if_changed("policy_rules.json",  _build_policy_rules(text))
    return changed


# ── Builders ──────────────────────────────────────────────────────────────────

def _build_expense_caps(text: str) -> dict:
    """Extract monetary caps and rates from §3 and §4."""

    # Lodging: "Up to USD 275/night in major metros, USD 195/night elsewhere"
    lodging = re.findall(r"USD\s*([\d,]+(?:\.\d+)?)/night", text)
    metro_cap = float(lodging[0].replace(",", "")) if len(lodging) >= 1 else None
    other_cap = float(lodging[1].replace(",", "")) if len(lodging) >= 2 else None

    # Meals with receipt: "Up to USD 90/day per traveller"
    m = re.search(r"Meals \(with receipt\)[^\|]*\|[^\|]*USD\s*([\d]+(?:\.\d+)?)/day", text, re.IGNORECASE)
    meal_cap = float(m.group(1)) if m else None

    # Per diem: "USD 65/day in lieu of meal receipts"
    m = re.search(r"Per diem[^\|]*\|[^\|]*USD\s*([\d]+(?:\.\d+)?)/day", text, re.IGNORECASE)
    perdiem_cap = float(m.group(1)) if m else None

    # Stackable? "not stackable with category above"
    per_diem_stackable = "not stackable" not in text.lower()

    # Air travel hours: "flights under 6 hours"
    m = re.search(r"flights? under (\d+) hours?", text, re.IGNORECASE)
    air_economy_max_hrs = int(m.group(1)) if m else None

    # Subcontractor markup: "cost plus 8%"
    m = re.search(r"cost plus (\d+)%", text, re.IGNORECASE)
    markup_pct = float(m.group(1)) / 100.0 if m else None

    # Receipt threshold: "expenses above USD 25"
    m = re.search(r"above USD\s*([\d]+(?:\.\d+)?)", text, re.IGNORECASE)
    receipt_threshold = float(m.group(1)) if m else None

    # Mileage rate — contract says "prevailing federal rate" without a number.
    # Value must be set manually in the _manual_overrides section below.
    mileage_rate = _extract_manual_override("mileage.rate_usd_per_mile", 0.67)

    return {
        "_source":   _contract_id(text),
        "_section":  "§3 subcontractor, §4 expense rules",
        "_generated": "auto — do not edit; update contract-001.md and re-run sync_rules.py",
        "_manual_overrides": {
            "mileage.rate_usd_per_mile": {
                "value": mileage_rate,
                "reason": "Contract says 'prevailing federal rate' with no explicit amount; set to current IRS rate."
            }
        },
        "lodging": {
            "metro_usd_per_night": metro_cap,
            "other_usd_per_night": other_cap
        },
        "meals": {
            "receipt_usd_per_day": meal_cap,
            "per_diem_usd_per_day": perdiem_cap,
            "per_diem_stackable_with_receipt": per_diem_stackable
        },
        "air_travel": {
            "economy_max_hours": air_economy_max_hrs,
            "premium_economy_min_hours": air_economy_max_hrs
        },
        "mileage": {
            "rate_usd_per_mile": mileage_rate
        },
        "receipt_required_above_usd": receipt_threshold,
        "subcontractor_markup_pct":   markup_pct
    }


def _build_labour_rules(text: str) -> dict:
    """Extract role rates and labour constraints from §3 and SAP notes."""

    # Role rate table
    rates = {}
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
                    rates[cols[0]] = float(cols[2].replace(",", ""))
                except ValueError:
                    pass

    # Travel time: "50% of the role rate"
    m = re.search(r"(\d+)% of the role rate", text, re.IGNORECASE)
    travel_rate_pct = float(m.group(1)) / 100.0 if m else None

    # Travel time cap: "capped at 8 hours per traveller per direction"
    m = re.search(r"capped at (\d+) hours per traveller per direction", text, re.IGNORECASE)
    travel_cap_hrs = int(m.group(1)) if m else None

    # Principal cap from SAP notes: "capped at 5% of monthly hours"
    m = re.search(r"PRIN time.*?capped at (\d+)%\s+of monthly hours", text, re.IGNORECASE)
    prin_cap_pct = float(m.group(1)) / 100.0 if m else None

    return {
        "_source":    _contract_id(text),
        "_section":   "§3 fees and rate table, project SAP notes",
        "_generated": "auto — do not edit; update contract-001.md and re-run sync_rules.py",
        "role_rates_usd_per_hour":             rates,
        "travel_time_billing_rate_pct":        travel_rate_pct,
        "travel_time_max_hours_per_direction": travel_cap_hrs,
        "principal_cap_pct_of_monthly_hours":  prin_cap_pct
    }


def _build_policy_rules(text: str) -> dict:
    """Extract hard yes/no policy flags from the §4 expense rules table."""

    policies = {}
    in_table = False
    for line in text.splitlines():
        if "Category" in line and "Limit" in line:
            in_table = True
            continue
        if in_table:
            if not line.strip() or line.startswith("#"):
                break
            cols = [c.strip() for c in line.split("|") if c.strip()]
            if len(cols) >= 2 and "---" not in cols[0]:
                cat_raw   = cols[0].lower()
                rule_text = cols[1].lower()
                if "not reimbursable" in rule_text:
                    key = re.sub(r"\s*\(.*?\)", "", cat_raw).strip().replace(" ", "_")
                    override_allowed = "without prior written approval" in rule_text
                    policies[key] = {
                        "reimbursable":    False,
                        "override_allowed": override_allowed,
                        "override_requires": (
                            "prior_written_pl_approval" if override_allowed else None
                        )
                    }

    return {
        "_source":    _contract_id(text),
        "_section":   "§4 reimbursable expenses",
        "_generated": "auto — do not edit; update contract-001.md and re-run sync_rules.py",
        "policies":   policies
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _contract_id(text: str) -> str:
    m = re.search(r"Contract ID[^`]*`([^`]+)`", text)
    return m.group(1) if m else "unknown"


def _extract_manual_override(key: str, default: float) -> float:
    """Read override value from the existing JSON if the field is manually set."""
    path = RULES_DATA_DIR / "expense_caps.json"
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            overrides = existing.get("_manual_overrides", {})
            if key in overrides:
                return overrides[key]["value"]
        except (json.JSONDecodeError, KeyError):
            pass
    return default


def _write_if_changed(filename: str, data: dict) -> bool:
    path = RULES_DATA_DIR / filename
    new_content = json.dumps(data, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == new_content:
        print(f"  unchanged : {path.relative_to(_REPO_ROOT)}")
        return False
    path.write_text(new_content, encoding="utf-8")
    print(f"  updated   : {path.relative_to(_REPO_ROOT)}")
    return True


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"Syncing rules from: {CONTRACT_PATH.relative_to(_REPO_ROOT)}")
    changed = sync()
    print("Done —", "rule files updated." if changed else "no changes detected.")
    sys.exit(0)
