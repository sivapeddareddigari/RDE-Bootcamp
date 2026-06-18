"""
Unit tests for billing_agent/rules/sync_rules.py and the generated rule JSON files.

Covers:
  - sync_rules._build_expense_caps()   — parses monetary caps from contract text
  - sync_rules._build_labour_rules()   — parses role rates and labour constraints
  - sync_rules._build_policy_rules()   — parses hard yes/no policy flags
  - sync_rules.sync()                  — idempotency and change detection
  - billing_agent/rules/data/*.json    — generated files have correct values
  - keyword_lists.json                 — manually maintained detection words
"""

import json
import pytest
from pathlib import Path

from billing_agent.rules.sync_rules import (
    CONTRACT_PATH,
    RULES_DATA_DIR,
    sync,
    _build_expense_caps,
    _build_labour_rules,
    _build_policy_rules,
    _contract_id,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def contract_text() -> str:
    return CONTRACT_PATH.read_text(encoding="utf-8")

@pytest.fixture(scope="module")
def expense_caps() -> dict:
    return json.loads((RULES_DATA_DIR / "expense_caps.json").read_text())

@pytest.fixture(scope="module")
def labour_rules() -> dict:
    return json.loads((RULES_DATA_DIR / "labour_rules.json").read_text())

@pytest.fixture(scope="module")
def policy_rules() -> dict:
    return json.loads((RULES_DATA_DIR / "policy_rules.json").read_text())

@pytest.fixture(scope="module")
def keyword_lists() -> dict:
    return json.loads((RULES_DATA_DIR / "keyword_lists.json").read_text())


# ── Generated JSON files exist ────────────────────────────────────────────────

class TestRuleFilesExist:

    def test_expense_caps_file_exists(self):
        assert (RULES_DATA_DIR / "expense_caps.json").exists()

    def test_labour_rules_file_exists(self):
        assert (RULES_DATA_DIR / "labour_rules.json").exists()

    def test_policy_rules_file_exists(self):
        assert (RULES_DATA_DIR / "policy_rules.json").exists()

    def test_keyword_lists_file_exists(self):
        assert (RULES_DATA_DIR / "keyword_lists.json").exists()

    def test_all_generated_files_are_valid_json(self):
        for fname in ("expense_caps.json", "labour_rules.json", "policy_rules.json"):
            content = (RULES_DATA_DIR / fname).read_text()
            json.loads(content)   # raises if invalid

    def test_generated_files_carry_source_annotation(self, expense_caps):
        assert expense_caps["_source"] == "MSA-NS-2024-0418"

    def test_generated_files_carry_do_not_edit_warning(self, expense_caps):
        assert "do not edit" in expense_caps["_generated"]


# ── expense_caps.json values ──────────────────────────────────────────────────

class TestExpenseCaps:

    def test_lodging_metro_cap(self, expense_caps):
        assert expense_caps["lodging"]["metro_usd_per_night"] == pytest.approx(275.0)

    def test_lodging_other_cap(self, expense_caps):
        assert expense_caps["lodging"]["other_usd_per_night"] == pytest.approx(195.0)

    def test_metro_cap_higher_than_other_cap(self, expense_caps):
        assert expense_caps["lodging"]["metro_usd_per_night"] > \
               expense_caps["lodging"]["other_usd_per_night"]

    def test_meal_receipt_cap(self, expense_caps):
        assert expense_caps["meals"]["receipt_usd_per_day"] == pytest.approx(90.0)

    def test_per_diem_cap(self, expense_caps):
        assert expense_caps["meals"]["per_diem_usd_per_day"] == pytest.approx(65.0)

    def test_per_diem_not_stackable_with_receipt(self, expense_caps):
        assert expense_caps["meals"]["per_diem_stackable_with_receipt"] is False

    def test_air_economy_max_hours(self, expense_caps):
        assert expense_caps["air_travel"]["economy_max_hours"] == 6

    def test_premium_economy_threshold_matches_economy_cutoff(self, expense_caps):
        assert expense_caps["air_travel"]["premium_economy_min_hours"] == \
               expense_caps["air_travel"]["economy_max_hours"]

    def test_mileage_rate(self, expense_caps):
        assert expense_caps["mileage"]["rate_usd_per_mile"] == pytest.approx(0.67)

    def test_mileage_is_manual_override(self, expense_caps):
        assert "mileage.rate_usd_per_mile" in expense_caps["_manual_overrides"]

    def test_receipt_threshold(self, expense_caps):
        assert expense_caps["receipt_required_above_usd"] == pytest.approx(25.0)

    def test_subcontractor_markup_pct(self, expense_caps):
        assert expense_caps["subcontractor_markup_pct"] == pytest.approx(0.08)

    def test_subcontractor_markup_as_multiplier(self, expense_caps):
        invoice_amount = 2400.00
        expected_billed = invoice_amount * (1 + expense_caps["subcontractor_markup_pct"])
        assert expected_billed == pytest.approx(2592.0)


# ── labour_rules.json values ──────────────────────────────────────────────────

class TestLabourRules:

    def test_six_roles_loaded(self, labour_rules):
        assert len(labour_rules["role_rates_usd_per_hour"]) == 6

    def test_eng1_rate(self, labour_rules):
        assert labour_rules["role_rates_usd_per_hour"]["ENG1"] == pytest.approx(145.0)

    def test_eng2_rate(self, labour_rules):
        assert labour_rules["role_rates_usd_per_hour"]["ENG2"] == pytest.approx(175.0)

    def test_eng3_rate(self, labour_rules):
        assert labour_rules["role_rates_usd_per_hour"]["ENG3"] == pytest.approx(230.0)

    def test_pm1_rate(self, labour_rules):
        assert labour_rules["role_rates_usd_per_hour"]["PM1"] == pytest.approx(215.0)

    def test_prin_rate(self, labour_rules):
        assert labour_rules["role_rates_usd_per_hour"]["PRIN"] == pytest.approx(320.0)

    def test_admin_rate(self, labour_rules):
        assert labour_rules["role_rates_usd_per_hour"]["ADMIN"] == pytest.approx(95.0)

    def test_prin_is_highest_rate(self, labour_rules):
        rates = labour_rules["role_rates_usd_per_hour"].values()
        assert labour_rules["role_rates_usd_per_hour"]["PRIN"] == max(rates)

    def test_admin_is_lowest_rate(self, labour_rules):
        rates = labour_rules["role_rates_usd_per_hour"].values()
        assert labour_rules["role_rates_usd_per_hour"]["ADMIN"] == min(rates)

    def test_principal_cap_pct(self, labour_rules):
        assert labour_rules["principal_cap_pct_of_monthly_hours"] == pytest.approx(0.05)

    def test_travel_time_billing_rate(self, labour_rules):
        assert labour_rules["travel_time_billing_rate_pct"] == pytest.approx(0.50)

    def test_travel_time_max_hours(self, labour_rules):
        assert labour_rules["travel_time_max_hours_per_direction"] == 8

    def test_travel_time_rate_is_half(self, labour_rules):
        assert labour_rules["travel_time_billing_rate_pct"] == pytest.approx(0.5)


# ── policy_rules.json values ──────────────────────────────────────────────────

class TestPolicyRules:

    def test_three_policies_present(self, policy_rules):
        assert len(policy_rules["policies"]) == 3

    def test_alcohol_not_reimbursable(self, policy_rules):
        assert policy_rules["policies"]["alcohol"]["reimbursable"] is False

    def test_alcohol_override_not_allowed(self, policy_rules):
        assert policy_rules["policies"]["alcohol"]["override_allowed"] is False

    def test_alcohol_override_requires_is_null(self, policy_rules):
        assert policy_rules["policies"]["alcohol"]["override_requires"] is None

    def test_personal_items_not_reimbursable(self, policy_rules):
        assert policy_rules["policies"]["personal_items"]["reimbursable"] is False

    def test_personal_items_override_not_allowed(self, policy_rules):
        assert policy_rules["policies"]["personal_items"]["override_allowed"] is False

    def test_entertainment_not_reimbursable(self, policy_rules):
        assert policy_rules["policies"]["entertainment"]["reimbursable"] is False

    def test_entertainment_override_allowed(self, policy_rules):
        assert policy_rules["policies"]["entertainment"]["override_allowed"] is True

    def test_entertainment_requires_pl_approval(self, policy_rules):
        assert policy_rules["policies"]["entertainment"]["override_requires"] == \
               "prior_written_pl_approval"


# ── keyword_lists.json content ────────────────────────────────────────────────

class TestKeywordLists:

    def test_five_keyword_categories(self, keyword_lists):
        expected = {"alcohol", "personal_items", "miscoded_labour", "airport_lounge", "entertainment"}
        assert expected.issubset(keyword_lists.keys())

    def test_alcohol_list_nonempty(self, keyword_lists):
        assert len(keyword_lists["alcohol"]) > 0

    def test_alcohol_contains_wine(self, keyword_lists):
        assert "wine" in keyword_lists["alcohol"]

    def test_alcohol_contains_beer(self, keyword_lists):
        assert "beer" in keyword_lists["alcohol"]

    def test_alcohol_contains_spirits(self, keyword_lists):
        assert "spirits" in keyword_lists["alcohol"]

    def test_personal_contains_laundry(self, keyword_lists):
        assert "laundry" in keyword_lists["personal_items"]

    def test_personal_contains_dry_cleaning(self, keyword_lists):
        assert "dry cleaning" in keyword_lists["personal_items"]

    def test_miscoded_contains_training(self, keyword_lists):
        assert "training" in keyword_lists["miscoded_labour"]

    def test_miscoded_contains_pmo_admin(self, keyword_lists):
        assert "pmo admin" in keyword_lists["miscoded_labour"]

    def test_miscoded_contains_internal_meeting(self, keyword_lists):
        assert "internal meeting" in keyword_lists["miscoded_labour"]

    def test_lounge_contains_lounge(self, keyword_lists):
        assert "lounge" in keyword_lists["airport_lounge"]

    def test_lounge_contains_priority_pass(self, keyword_lists):
        assert "priority pass" in keyword_lists["airport_lounge"]

    def test_all_keywords_are_lowercase(self, keyword_lists):
        for category, words in keyword_lists.items():
            if category.startswith("_"):
                continue
            for word in words:
                assert word == word.lower(), f"Keyword '{word}' in {category} is not lowercase"


# ── Builder functions (unit tests on parser logic) ────────────────────────────

class TestBuildExpenseCaps:

    def test_parses_metro_lodging_cap(self, contract_text):
        result = _build_expense_caps(contract_text)
        assert result["lodging"]["metro_usd_per_night"] == pytest.approx(275.0)

    def test_parses_other_lodging_cap(self, contract_text):
        result = _build_expense_caps(contract_text)
        assert result["lodging"]["other_usd_per_night"] == pytest.approx(195.0)

    def test_parses_meal_cap(self, contract_text):
        result = _build_expense_caps(contract_text)
        assert result["meals"]["receipt_usd_per_day"] == pytest.approx(90.0)

    def test_parses_per_diem_cap(self, contract_text):
        result = _build_expense_caps(contract_text)
        assert result["meals"]["per_diem_usd_per_day"] == pytest.approx(65.0)

    def test_parses_subcontractor_markup(self, contract_text):
        result = _build_expense_caps(contract_text)
        assert result["subcontractor_markup_pct"] == pytest.approx(0.08)

    def test_parses_air_travel_threshold(self, contract_text):
        result = _build_expense_caps(contract_text)
        assert result["air_travel"]["economy_max_hours"] == 6

    def test_parses_receipt_threshold(self, contract_text):
        result = _build_expense_caps(contract_text)
        assert result["receipt_required_above_usd"] == pytest.approx(25.0)

    def test_detects_per_diem_not_stackable(self, contract_text):
        result = _build_expense_caps(contract_text)
        assert result["meals"]["per_diem_stackable_with_receipt"] is False

    def test_cap_change_reflected_in_output(self):
        modified = "Up to USD 300/night in major metros, USD 210/night elsewhere."
        result = _build_expense_caps(modified)
        assert result["lodging"]["metro_usd_per_night"] == pytest.approx(300.0)
        assert result["lodging"]["other_usd_per_night"] == pytest.approx(210.0)

    def test_markup_change_reflected_in_output(self):
        modified = "Subcontractor pass-through is permitted at cost plus 10%."
        result = _build_expense_caps(modified)
        assert result["subcontractor_markup_pct"] == pytest.approx(0.10)


class TestBuildLabourRules:

    def test_parses_all_six_roles(self, contract_text):
        result = _build_labour_rules(contract_text)
        assert set(result["role_rates_usd_per_hour"].keys()) == {
            "ENG1", "ENG2", "ENG3", "PM1", "PRIN", "ADMIN"
        }

    def test_parses_principal_cap(self, contract_text):
        result = _build_labour_rules(contract_text)
        assert result["principal_cap_pct_of_monthly_hours"] == pytest.approx(0.05)

    def test_parses_travel_time_rate(self, contract_text):
        result = _build_labour_rules(contract_text)
        assert result["travel_time_billing_rate_pct"] == pytest.approx(0.50)

    def test_parses_travel_time_cap(self, contract_text):
        result = _build_labour_rules(contract_text)
        assert result["travel_time_max_hours_per_direction"] == 8

    def test_rate_change_reflected_in_output(self):
        modified = (
            "| Role code | Role | Rate (USD/hr) |\n"
            "|---|---|---|\n"
            "| ENG2 | Engineer II | 200 |"
        )
        result = _build_labour_rules(modified)
        assert result["role_rates_usd_per_hour"].get("ENG2") == pytest.approx(200.0)

    def test_principal_cap_change_reflected(self):
        modified = "PRIN time on this project capped at 10% of monthly hours"
        result = _build_labour_rules(modified)
        assert result["principal_cap_pct_of_monthly_hours"] == pytest.approx(0.10)


class TestBuildPolicyRules:

    def test_alcohol_marked_not_reimbursable(self, contract_text):
        result = _build_policy_rules(contract_text)
        assert result["policies"]["alcohol"]["reimbursable"] is False

    def test_alcohol_override_not_allowed(self, contract_text):
        result = _build_policy_rules(contract_text)
        assert result["policies"]["alcohol"]["override_allowed"] is False

    def test_entertainment_override_allowed(self, contract_text):
        result = _build_policy_rules(contract_text)
        assert result["policies"]["entertainment"]["override_allowed"] is True

    def test_policy_count(self, contract_text):
        result = _build_policy_rules(contract_text)
        assert len(result["policies"]) == 3


class TestContractId:

    def test_extracts_contract_id(self, contract_text):
        assert _contract_id(contract_text) == "MSA-NS-2024-0418"

    def test_unknown_when_no_id(self):
        assert _contract_id("No contract ID here") == "unknown"


# ── Sync idempotency ──────────────────────────────────────────────────────────

class TestSyncIdempotency:

    def test_sync_reports_no_changes_on_second_run(self):
        sync()                    # ensure files are current
        changed = sync()          # second run should detect no diff
        assert changed is False

    def test_sync_files_match_builder_output(self, contract_text):
        """Files on disk must equal what the builders would produce right now."""
        sync()
        on_disk = json.loads((RULES_DATA_DIR / "expense_caps.json").read_text())
        built   = _build_expense_caps(contract_text)
        assert on_disk["lodging"] == built["lodging"]
        assert on_disk["meals"]   == built["meals"]
        assert on_disk["subcontractor_markup_pct"] == built["subcontractor_markup_pct"]
