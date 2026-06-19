# Implementation Plan: Agentic Billing Review System

**Project:** Meridian Atlas Partners — RDE Bootcamp  
**Client:** Northstar Civic Group  
**Engagement:** Coastal Greenway Feasibility Study (PRJ-NS-7421)  
**Target invoice cycle:** 2026-04  

---

## Progress Tracker

| Phase | Description | Status | Commit(s) |
|-------|-------------|--------|-----------|
| Trigger | Drop folder watcher | ✅ Done | `e078864` |
| Phase 1 | Scaffolding & ingestion | ✅ Done | `e078864` → `624b1d5` |
| Phase 2 | Rule infrastructure & contract sync | ✅ Done | `6ea7ba5` |
| Phase 3 | Rule engine evaluation, document matching & override resolution | ✅ Done | `1ca6104` |
| Phase 4 | Exception detection & triage | ✅ Done | `HEAD` |
| Phase 5 | Invoice builder & outputs | ✅ Done | `HEAD` |
| Phase 6 | Agentic orchestration (Claude API) | ✅ Done | HEAD |
| Phase 7 | Testing | 🔄 In Progress | `624b1d5` → latest |

---

## Phase 1 — Completed Work

### What was built

| Component | Description | Commit |
|-----------|-------------|--------|
| Data models | `Transaction`, `TimecardEntry`, `ReceiptDocument`, `ContractClause`, `ProjectInstruction`, `ExceptionCase` | `e078864` |
| Drop-folder watcher | Polls `submissions/incoming/` every 5s; state machine `incoming → processing → completed/failed` | `e078864` |
| Ingestion pipeline | `load_inputs()` returns `IngestionResult` with all 6 data sources | `e078864` |
| Crash recovery | Retry counter baked into filename (`__r1`, `__r2`, `__r3`); quarantine after `MAX_RETRIES=3` | `fe14a25` |
| Run logger | Single append-mode `output/billing_agent.log`; per-line filename tag; run header + footer | `e877433` |
| Foreign currency | Regex extracts CAD/EUR/GBP etc. from receipt code blocks; `FX_RATES` in config | `fe14a25` |
| Scoped loading | Timecards filtered to employees in submission; documents filtered to IDs in note fields | `624b1d5` |
| 9 defect fixes | Word-boundary regex, alcohol amount extraction, SAP whitespace stripping, timecard path derivation | `fe14a25` |
| Test data | 6 per-employee submission CSVs covering all exception scenarios | `a537867` |

### Ingestion results (per-employee scoped loads)

| Submission | Transactions | Timecards loaded | Documents loaded |
|------------|-------------|-----------------|-----------------|
| E-1041 clean | 7 (2 labour, 5 expense) | 6 (E-1041 only) | 3 (RC-001, RC-003, ML-001) |
| E-2210 over-cap/alcohol | 7 (2 labour, 5 expense) | 6 (E-2210 only) | 2 (RC-012, RC-013) |
| E-3055 hold/miscoded | 6 (3 labour, 2 expense, 1 parking) | varies (E-3055 only) | 1 (RC-021) |
| E-4501 principal cap | 4 (2 labour, 2 expense) | varies (E-4501 only) | 0 (RC-031 not in store) |
| E-7702 currency/personal | 6 (0 labour, 6 expense) | 0 (no timecards for E-7702) | 0 (RC-04x not in store) |
| E-5102 subcon/composite | 6 (2 labour, 4 expense) | varies (E-5102 only) | 0 (RC-051/052, VI-003 not in store) |

### Contract constants loaded

- 6 role rate entries · 18 contract clauses · 5 PL email instructions · 10 prior exception cases
- **SAP project code** extracted from contract header (`**Project (SAP):**` field) and stored as `IngestionResult.contract_sap_project` — used to validate every incoming transaction's `project_id`

---

## Phase 2 — Completed

### What was built

| Component | Description | Commit |
|-----------|-------------|--------|
| `rules/data/expense_caps.json` | Lodging caps, meal caps, air class threshold, mileage rate, receipt threshold, subcontractor markup — auto-generated from contract | `6ea7ba5` |
| `rules/data/labour_rules.json` | Role rates, principal cap %, travel-time billing rate and hour cap — auto-generated from contract | `6ea7ba5` |
| `rules/data/policy_rules.json` | Hard yes/no policy flags (alcohol, personal items, entertainment) with override_allowed flag — auto-generated from contract | `6ea7ba5` |
| `rules/data/keyword_lists.json` | Operational detection word lists (alcohol, personal items, miscoded labour, lounge, entertainment) — manually maintained | `6ea7ba5` |
| `rules/sync_rules.py` | Parses `contract-001.md` and regenerates the three auto-generated JSON files; idempotent; preserves `_manual_overrides` | `6ea7ba5` |
| `.githooks/pre-commit` | Runs `sync_rules.py` and stages updated JSON whenever `contract-001.md` or `resolutions.csv` is committed | `6ea7ba5` |
| `.claude/settings.json` | PostToolUse hook fires `sync_rules.py` immediately when `contract-001.md` is edited in a Claude Code session | `6ea7ba5` |

### Rule values in force

| Rule | Value | JSON file | Source |
|------|-------|-----------|--------|
| Lodging — metro | $275/night | `expense_caps.json` | Contract §4 |
| Lodging — elsewhere | $195/night | `expense_caps.json` | Contract §4 |
| Meals — with receipt | $90/day | `expense_caps.json` | Contract §4 |
| Per diem | $65/day (not stackable) | `expense_caps.json` | Contract §4 |
| Air travel | Economy <6 hr; premium economy ≥6 hr | `expense_caps.json` | Contract §4 |
| Mileage rate | $0.67/mile (manual override) | `expense_caps.json` | IRS prevailing rate |
| Receipt threshold | >$25 requires backup | `expense_caps.json` | Contract §4 |
| Subcontractor markup | Cost + 8% | `expense_caps.json` | Contract §3 |
| Role rates | ENG1 $145 · ENG2 $175 · ENG3 $230 · PM1 $215 · PRIN $320 · ADMIN $95 | `labour_rules.json` | Contract §3 |
| Principal cap | 5% of monthly hours | `labour_rules.json` | SAP note 2025-02 |
| Travel time | 50% of role rate, max 8 hr/direction | `labour_rules.json` | Contract §3 |
| Alcohol | Never reimbursable, no override | `policy_rules.json` | Contract §4 |
| Personal items | Never reimbursable, no override | `policy_rules.json` | Contract §4 |
| Entertainment | Not reimbursable; PL written approval required | `policy_rules.json` | Contract §4 |

### Sync chain

```
contract-001.md  ──[any change]──►  sync_rules.py  ──►  expense_caps.json   (auto)
                                                    ──►  labour_rules.json   (auto)
                                                    ──►  policy_rules.json   (auto)
keyword_lists.json  ◄── manually maintained (detection keywords — no contractual source)
```

**Auto-sync triggers:**
- `.githooks/pre-commit` — fires on `git commit` whenever `contract-001.md` is staged
- `.claude/settings.json` PostToolUse hook — fires immediately when `contract-001.md` is edited in a Claude Code session

---

## Phase 3 — Completed

### What was built

| Module | Description | Commit |
|--------|-------------|--------|
| `rules/models.py` | `RuleResult` dataclass — status (APPROVE/FLAG/REJECT/HOLD), exception_type, rule_id, override info, original/approved amounts, note | `1ca6104` |
| `rules/rule_engine.py` | Per-transaction rule orchestrator; reads all JSON files at import; routes labour vs expense; calls `apply_overrides()` after evaluation | `1ca6104` |
| `rules/override_resolver.py` | Applies PL instructions (body-text regex for mixed-intent emails) and prior-exception patterns; handles Email 4's hold-release + markup-confirm in one message; guards clean items from incidental rejections | `1ca6104` |
| `matching/currency.py` | `to_usd(amount, currency)` — FX conversion via configured spot rates; raises `ValueError` for unknown currencies | `1ca6104` |
| `matching/matcher.py` | `reconcile()` — EXACT match via doc-id in note field; FUZZY match by date ±3 days + amount ±10%; computes SAP vs receipt delta after alcohol exclusion | `1ca6104` |
| `main.py` | Phase 3 wired: `rule_engine.run(inputs)` then `reconcile(inputs, rule_results)` | `1ca6104` |

### Rule evaluation — all 6 submissions (43 transactions)

| Submission | Approved | Flagged | Rejected | Held→Approved |
|------------|----------|---------|----------|---------------|
| E-1041 (clean) | 5 | 2 | 0 | 0 |
| E-2210 (over-cap/alcohol) | 5 | 0 | 2 | 0 |
| E-3055 (hold/miscoded) | 3 | 2 | 1 | 1 |
| E-4501 (principal cap) | 3 | 1 | 0 | 1 |
| E-5102 (subcontractor) | 3 | 3 | 0 | 1 |
| E-7702 (currency/personal) | 3 | 2 | 2 | 0 |

### Rule precedence (implemented order, first match wins)

0. **PROJECT_MISMATCH** — `tx.project_id` ≠ `contract_sap_project` (hard reject; employee must recode in SAP)
1. **HOLD_ITEM** — SAP-flagged holds (stop immediately)
2. **POLICY_VIOLATION** — alcohol / lounge / personal (hard reject, no override)
3. **MISCODED_LABOUR** — non-billable time descriptions (hard reject)
4. **RATE_MISMATCH / TRAVEL_TIME** — contracted rate, 50% travel rate, 8-hr cap (labour)
5. **CURRENCY_MISMATCH** — non-USD receipt (flag for FX conversion)
6. **SUBCONTRACTOR_MARKUP** — vendor invoice with markup not applied
7. **OVER_CAP** — lodging / meal / per diem / mileage (runs before MISSING_BACKUP so cap violations surface even without a receipt in the store)
8. **COMPOSITE_DOCUMENT / UNREADABLE_DOC** — doc quality issues
9. **MISSING_BACKUP** — expense > $25 with no linked document (per diem and mileage exempt)
10. **AMOUNT_MISMATCH** — SAP amount overbills receipt billable amount (>$0.50)
11. **CLEAN** — no exception found

### Override resolution design

PL instructions are matched against each `RuleResult` using body-text regex (not just `instruction_type`) to handle mixed-intent emails. `_APPROVAL_RE`, `_REJECTION_RE`, `_HOLD_RELEASE_RE`, and `_MARKUP_CONFIRM_RE` patterns are checked independently. Key guards:

- `ALCOHOL`, `AIRPORT_LOUNGE`, `PERSONAL_ITEM` rule_ids are never overrideable
- Per diem items only rejected if body explicitly names "per diem"
- Clean items not rejected by mixed-intent emails (email primarily approves something else)
- `_amount_close()` within ±$1 prevents same-email approvals from blocking same-email rejections of different charges

---

## Phase 4 — Completed

### What was built

| Module | Description | Commit |
|--------|-------------|--------|
| `exceptions/models.py` | `ExceptionItem` dataclass (transaction, routing, blocking flag); `ExceptionReport` dataclass with computed properties (`blocking`, `unresolved_count`, `exception_count`) | HEAD |
| `exceptions/detector.py` | `run()` — classifies every non-APPROVE `RuleResult` into auto_resolved / hard_rejections / pl_rejections / escalate_analyst / escalate_employee / escalate_pl; logs triage summary | HEAD |
| `exceptions/__init__.py` | Package re-exports | HEAD |
| `main.py` | Phase 4 wired: `detect_exceptions(inputs, rule_results, match_results)` | HEAD |

### Routing table (rule_id → actor)

| Actor | Rule IDs | Action required |
|-------|----------|----------------|
| **ANALYST** | AMOUNT_MISMATCH, RATE_MISMATCH, TRAVEL_RATE, TRAVEL_HRS_CAP, MILEAGE_RATE, CURRENCY, COMPOSITE_DOC, MARKUP_MISSING | Review amounts / FX conversion / confirm figures |
| **PL** | LODGING_CAP, MEAL_CAP, PER_DIEM_CAP, HOLD_ITEM | Written approval or hold release |
| **EMPLOYEE** | NO_RECEIPT, UNREADABLE_DOC, MISCODED, ALCOHOL, AIRPORT_LOUNGE, PERSONAL_ITEM, **PROJECT_MISMATCH** | Submit receipt / correct SAP entry / recode to correct project |

### Blocking flag logic

An item is blocking (prevents appearance on draft invoice) when its `rule_id` is in:
`PROJECT_MISMATCH`, `LODGING_CAP`, `MEAL_CAP`, `PER_DIEM_CAP`, `HOLD_ITEM`, `NO_RECEIPT`, `UNREADABLE_DOC`, `CURRENCY`, `MARKUP_MISSING`

`PROJECT_MISMATCH` — hard reject, `approved_amount = $0`; transaction cannot appear on the invoice until the employee recodes it in SAP to `contract_sap_project`.

`AMOUNT_MISMATCH` is not blocking — analyst can approve the receipt amount while the SAP discrepancy is investigated.

### Exception triage — all 6 submissions

| Submission | Clean | Auto-resolved | Rejected | Analyst | Employee | PL | Blocking |
|------------|-------|--------------|----------|---------|----------|----|----------|
| E-1041 (clean) | 4 | 1 | 0 | 1 | 1 | 0 | 1 |
| E-2210 (over-cap/alcohol) | 3 | 2 | 2 | 0 | 0 | 0 | 0 |
| E-3055 (hold/miscoded) | 2 | 1 | 1 | 0 | 2 | 0 | 2 |
| E-4501 (principal cap) | 2 | 1 | 0 | 0 | 1 | 0 | 1 |
| E-5102 (subcontractor) | 2 | 1 | 0 | 0 | 3 | 0 | 3 |
| E-7702 (currency/personal) | 1 | 1 | 2 | 1 | 1 | 0 | 2 |

---

## Phase 5 — Completed (redesigned)

Phase 5 was redesigned to reflect how the real billing workflow operates: invoice generation is a SAP month-end activity, not a per-submission step. The pipeline now has two distinct triggers.

### Architecture — two triggers

| Trigger | When | Command | Output |
|---------|------|---------|--------|
| **Watcher** (per-submission) | Drop-folder CSV arrives | auto | Employee notices + analyst summary |
| **Invoice CLI** (month-end) | All submissions in for the month | `python3 -m billing_agent.invoice_run --project PRJ-NS-7421 --month 2026-04` | Draft invoice + exceptions report + audit trail |

### What was built

| Module | Description |
|--------|-------------|
| `data/contacts.json` | Contact directory — employee name/email/role, billing analyst, project lead per project |
| `ingestion/contacts_loader.py` | `load_contacts()` → `ContactDirectory`; `.employee(id)` and `.project_lead(project_id)` lookups |
| `output/notice_writer.py` | Per-submission: writes per-employee exception notices + analyst summary |
| `output/invoice_builder.py` | Project-level month-end: re-reads all completed CSVs for project+month, re-runs Phases 1–4, aggregates into one invoice |
| `output/__init__.py` | Package re-exports — `BuildResult`, `build`, `write_notices` |
| `invoice_run.py` | CLI entry point for month-end invoice trigger |
| `main.py` (updated) | Phase 5 now calls `write_notices()` instead of `build()`; `_contacts` loaded once at startup |

### Per-submission output files (watcher trigger)

| File | Format | Contents |
|------|--------|----------|
| `output/notices/exception-notice-{emp_id}-{stem}__{ts}.md` | Markdown | Addressed to employee by name; splits items into "Blocking" (must fix in SAP) and "Under review" tables; each item has plain-English corrective action |
| `output/analyst-summary-{stem}__{ts}.md` | Markdown | Addressed to billing analyst; headline counts table; blocking items, escalation sections, auto-resolved items |

### Project-level output files (invoice CLI — no timestamp, one canonical file per project+month)

| File | Format | Contents |
|------|--------|----------|
| `output/draft-invoice-{project}-{month}.md` | Markdown | Section A (labour with Employee column), Section B (expenses by category), Totals, Excluded/Blocked items |
| `output/audit-trail-{project}-{month}.csv` | CSV | 17 columns including `submission` and `employee_name`; one row per transaction across all submissions |
| `output/exceptions-report-{project}-{month}.md` | Markdown | Project-wide exception summary — auto-resolved, rejections, escalations with employee names |

### Key design decisions

- **Re-process from `completed/`:** Invoice build re-runs Phases 1–4 on each completed submission CSV rather than persisting intermediate results. Idempotent and catches any rule changes between submission and month-end.
- **One invoice per project per month:** `_find_submissions()` collects all CSVs in `completed/` whose name contains `billing_month`. A single canonical file is written — re-running overwrites the prior draft.
- **Contacts config:** All email addresses and role labels maintained in `data/contacts.json`. Employee and analyst contacts are resolved by `ContactDirectory` in both notice_writer and invoice_builder.
- **No hard-coding:** `_MARKUP_PCT` and `_MEAL_KWS` loaded from JSON at import (same source as rule engine).
- **Subcontractor split:** when `_has_markup()` is True (override_applied + approved > original), the invoice shows two rows — cost line + markup line.

### Project-level aggregation — all 6 submissions combined

| Metric | Value |
|--------|-------|
| Submissions processed | 6 |
| Labour total | $10,515.00 |
| Expense total | $4,136.54 |
| Grand total | $14,651.54 |
| Blocked items | 9 |
| Audit trail rows | 36 |

---

## Phase 7 — Testing (In Progress)

### Unit tests — 215 tests, all passing

Run with: `python3 run_tests.py`  
Results saved to: `output/Unit_test_runs/unit_test_run_<timestamp>.txt`  
Summary logged to: `output/billing_agent.log`

| Test file | Tests | Covers |
|-----------|-------|--------|
| `tests/test_sap_loader.py` | 22 | Transaction parsing (hold flags, currency, rate×qty arithmetic, labour/expense split); timecard employee filter |
| `tests/test_doc_parser.py` | 25 | Document ID filtering; composite/unreadable/alcohol/currency/type detection; line item extraction |
| `tests/test_loader.py` | 34 | `_referenced_doc_ids` helper; timecard scoping per submission; document scoping; static data always loaded |
| `tests/test_sync_rules.py` | 79 | JSON file existence and validity; all rule values from contract; builder functions with modified contract text; keyword list content; sync idempotency |
| `tests/test_invoice_builder.py` | 55 | `ContactsLoader` (6 tests) — load + lookup helpers; `NoticeWriter` (12 tests) — employee notices, analyst summary content; `InvoiceBuild` (19 tests) — project-level aggregation, audit trail, invoice/exceptions content; `_categorize`, `_has_markup`, `_infer_cycle` helpers (18 tests) |

### Remaining test work (Phase 7 completion)

| Test file | Status | Covers |
|-----------|--------|--------|
| `tests/test_rules.py` | ⏳ Pending | Each rule evaluation against known inputs |
| `tests/test_matching.py` | ⏳ Pending | Doc-to-transaction linkage for all 12 complex cases |
| `tests/test_currency.py` | ⏳ Pending | CAD→USD conversion for RC-015 |

---

## How to Use the Trigger

### Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Run the watcher (from repo root)
python -m billing_agent.main
```

You will see:
```
2026-06-17 21:00:00  INFO     ============================================================
2026-06-17 21:00:00  INFO     Billing Review Agent — drop folder watcher started
2026-06-17 21:00:00  INFO       Watching : /path/to/RDE-Bootcamp/submissions/incoming
2026-06-17 21:00:00  INFO       Poll     : every 5s
2026-06-17 21:00:00  INFO       Output   : /path/to/RDE-Bootcamp/output
2026-06-17 21:00:00  INFO     ============================================================
2026-06-17 21:00:00  INFO     Drop a submission CSV into 'incoming/' to trigger a run.
2026-06-17 21:00:00  INFO     Press Ctrl+C to stop.
```

### Trigger a run

Open a second terminal and drop the SAP transaction extract:

```bash
cp test-data/sample-inputs/transactions/unbilled-2026-04.csv submissions/incoming/
```

The watcher picks it up within 5 seconds:
```
2026-06-17 21:00:05  INFO     ── submission received: unbilled-2026-04.csv
2026-06-17 21:00:05  INFO         loading inputs ...
2026-06-17 21:00:05  INFO     Loaded 50 transactions from unbilled-2026-04.csv
2026-06-17 21:00:05  INFO     Loaded 23 timecard entries from timecards-2026-04.csv
2026-06-17 21:00:05  INFO     Loaded 6 rate entries and 18 contract clauses from contract-001.md
2026-06-17 21:00:05  INFO     Loaded 15 documents from documents
2026-06-17 21:00:05  INFO     Loaded 5 PL instructions from sample-emails.md
2026-06-17 21:00:05  INFO     Loaded 10 prior exception cases from resolutions.csv
2026-06-17 21:00:05  INFO     ── ingestion complete
2026-06-17 21:00:05  INFO     ── completed: unbilled-2026-04__20260617T210005Z.csv
```

### What happens to the file

```
submissions/
├── incoming/          ← you drop the CSV here
│                         (disappears within 5 seconds)
├── processing/        ← watcher moves it here mid-run
│                         (empty when run is done)
├── completed/         ← success: unbilled-2026-04__<timestamp>.csv
└── failed/            ← error:   unbilled-2026-04__<timestamp>.csv
```

### Crash recovery

If the process dies mid-run, any file left in `processing/` is automatically
moved back to `incoming/` on the next startup — no manual intervention needed.

### Re-triggering

Drop the same file again for a re-run. The timestamped filename in `completed/`
means each run is preserved and does not overwrite previous results.

### Stop the watcher

```bash
Ctrl+C
# or
kill -TERM <pid>
```

---

## Architecture Overview

Based on `design.svg`, the system uses **two LLM agents** and **three deterministic pipelines**:

```
SAP (TRX + receipts, contracts)     SharePoint / Teams / Email
              ↓                                  ↓
     ┌─────────────────────────────────────────────────────┐
     │      Billing Supervisor — LLM agent  (orchestrator) │
     │  Daily trigger · holds TRX state · sequences pipelines │
     └──┬──────────────┬─────────────────┬──────────────────┘
        ↓              ↓                 ↓                  ↓
  Document        Reconciliation    Exception           Package
  pipeline         pipeline         reasoning          pipeline
  (deterministic) (deterministic)  ← LLM agent →      (deterministic)
  Fetch receipts  Match doc→TRX    Pattern lookup
  Fetch SP docs   Check rules      PL guidance read
  Parse + split   Flag gaps             ↓
                               Pattern found?
                             yes ↓        ↓ no — novel
                          Auto-resolved  Flag to employee
                          (reinforced)   (corrects in SAP
                                          re-triggers ↺)
```

**Knowledge stores** (feed into Exception reasoning only):
- **Decision Memory store** — pattern library, grows with each resolution
- **Instruction store** — PL rules, standing decisions, per project

**Human approval chain** (below month-end divider — out of agent scope):  
SAP draft invoice → Billing Analyst review → PFA review → PL approval → Safe SAP write

---

## Project Structure

Legend: ✅ Built · ⏳ Planned

```
billing_agent/
├── main.py              ✅  Drop-folder watcher, crash recovery, retry logic
├── config.py            ✅  Paths, FX rates, contract constants, MAX_RETRIES
├── run_logger.py        ✅  Append-mode run log (output/billing_agent.log)
├── models/
│   ├── transaction.py   ✅  Transaction, TimecardEntry dataclasses
│   ├── document.py      ✅  ReceiptDocument (with alcohol_amount, currency)
│   ├── contract.py      ✅  ContractClause, RateEntry
│   └── instruction.py   ✅  ProjectInstruction, ExceptionCase
├── ingestion/
│   ├── loader.py        ✅  load_inputs() — scoped IngestionResult
│   ├── sap_loader.py    ✅  load_transactions(), load_timecards(employee_ids)
│   ├── contract_parser.py ✅ Parse contract-001.md → rates + clauses
│   ├── doc_parser.py    ✅  load_documents(doc_ids) — filtered by note refs
│   ├── email_parser.py  ✅  Parse sample-emails.md → ProjectInstruction list
│   └── exception_loader.py ✅ Parse resolutions.csv → ExceptionCase list
├── rules/               ✅  (Phases 2 & 3 — complete)
│   ├── data/
│   │   ├── expense_caps.json    ✅ Auto-generated from contract (caps, rates, markup)
│   │   ├── labour_rules.json    ✅ Auto-generated from contract (role rates, principal cap)
│   │   ├── policy_rules.json    ✅ Auto-generated from contract (hard policy flags)
│   │   └── keyword_lists.json   ✅ Manually maintained (detection word lists)
│   ├── sync_rules.py        ✅ Parses contract → regenerates 3 JSON files; idempotent
│   ├── models.py            ✅ RuleResult dataclass
│   ├── rule_engine.py       ✅ Orchestrator — evaluates all rules, returns List[RuleResult]
│   └── override_resolver.py ✅ PL instruction + prior-exception pattern application
├── matching/            ✅  (Phase 3 — complete)
│   ├── __init__.py           Re-exports MatchResult, reconcile
│   ├── matcher.py            ✅ EXACT/FUZZY document matching; FX delta computation
│   └── currency.py           ✅ FX conversion (spot rates from config)
├── exceptions/          ✅  (Phase 4 — complete)
│   ├── models.py             ✅ ExceptionItem, ExceptionReport dataclasses
│   └── detector.py           ✅ Classify and triage all non-APPROVE rule results
├── stores/              ✅  (Phase 6 — complete)
│   ├── decision_memory.py    ✅ load_memory() → List[ExceptionCase]; find_relevant(); format_for_prompt()
│   └── instruction_store.py  ✅ format_for_prompt(List[ProjectInstruction]) → prompt string
├── agents/              ✅  (Phase 6 — complete)
│   ├── supervisor.py         ✅ Claude tool-use loop → SupervisorResult; fallback to deterministic pipeline
│   └── exception_agent.py    ✅ Single-turn structured-output → List[ExceptionAnalysis]; fallback → []
├── data/                ✅  (Phase 5 — complete)
│   └── contacts.json         ✅ Contact directory — employees, billing analysts, project leads
├── invoice_run.py       ✅  (Phase 5) Month-end CLI: --project + --month args
└── output/              ✅  (Phase 5 — complete)
    ├── __init__.py           ✅ Re-exports BuildResult, build, write_notices
    ├── notice_writer.py      ✅ Per-submission: employee notices + analyst summary
    └── invoice_builder.py    ✅ Project-level month-end: draft invoice, audit trail, exceptions report

tests/                   🔄  (Phase 7 — in progress)
├── conftest.py          ✅  Shared paths and submission fixture constants
├── test_sap_loader.py   ✅  22 tests — transaction + timecard loading
├── test_doc_parser.py   ✅  25 tests — document parsing and filtering
├── test_loader.py       ✅  34 tests — scoped IngestionResult
├── test_sync_rules.py   ✅  79 tests — rule JSON values, sync, keyword lists, idempotency
├── test_invoice_builder.py ✅ 55 tests — contacts loader, notice writer, project-level invoice builder, helpers
├── test_rules.py        ⏳  Rule evaluation against known inputs (pending)
├── test_matching.py     ⏳  Doc-to-transaction linkage (pending)
└── test_currency.py     ⏳  CAD→USD conversion (pending)

run_tests.py             ✅  Pytest runner — saves timestamped result files +
                             appends pass/fail summary to billing_agent.log

test-data/sample-inputs/
├── transactions/
│   ├── unbilled-2026-04.csv          Full cycle extract (50 transactions)
│   └── test-exceptions-2026-05.csv  One of each exception type (15 rows)
├── submissions/                      Per-employee scenario files (6 CSVs) ✅
│   ├── submission-E1041-clean-2026-04.csv
│   ├── submission-E2210-over-cap-alcohol-2026-04.csv
│   ├── submission-E3055-hold-miscoded-2026-04.csv
│   ├── submission-E4501-principal-cap-2026-04.csv
│   ├── submission-E7702-currency-personal-2026-04.csv
│   └── submission-E5102-subcontractor-composite-2026-04.csv
├── documents/                        15 backup documents (RC-*, ML-*, VI-*)
├── sap-outputs/                      timecards-2026-04.csv
├── contracts/                        contract-001.md
├── pm-instructions/                  sample-emails.md
└── prior-exceptions/                 resolutions.csv

output/
├── billing_agent.log                 Append-mode run log (all submissions + test runs)
├── notices/                          Per-employee exception notices (one per affected employee per submission)
├── analyst-summary-{stem}__{ts}.md  Per-submission analyst summary
├── draft-invoice-{project}-{month}.md  Month-end project invoice (overwritten on re-run)
├── audit-trail-{project}-{month}.csv   36-column audit trail across all submissions
├── exceptions-report-{project}-{month}.md  Project-wide exception summary
└── Unit_test_runs/                   Timestamped test result files (one per run)
```

---

## Phase 1 — Project Scaffolding & Data Ingestion

**Goal:** Establish the project structure and load all input data into normalised Python objects.

### 1.1 Data Models

| Model | Fields |
|-------|--------|
| `Transaction` | tx_id, type (LABOUR/EXPENSE), amount, employee_id, task_code, category, status, narrative |
| `TimecardEntry` | timecard_id, tx_ref, employee_id, hours, approved_by, status |
| `ReceiptDocument` | doc_id, vendor, date, total_amount, currency, line_items[], doc_type |
| `ContractClause` | clause_id, category, rule_text, cap_amount, conditions |
| `ProjectInstruction` | instruction_id, date, scope, override_type, approved_by |
| `ExceptionCase` | pattern, resolution, precedent_date |

### 1.2 Loaders

| Loader | Input file | Output | Scoped? |
|--------|-----------|--------|---------|
| `sap_loader.load_transactions()` | Dropped submission CSV | `List[Transaction]` | ✅ Submission only |
| `sap_loader.load_timecards()` | `timecards-YYYY-MM.csv` (cycle-derived) | `List[TimecardEntry]` | ✅ Employees in submission |
| `contract_parser.py` | `contract-001.md` | `List[RateEntry]`, `List[ContractClause]`, `str` (SAP project code) | — (static ref) |
| `doc_parser.load_documents()` | `documents/` directory | `List[ReceiptDocument]` | ✅ Doc IDs in note fields |
| `email_parser.py` | `sample-emails.md` | `List[ProjectInstruction]` | — (static ref) |
| `exception_loader.py` | `resolutions.csv` | `List[ExceptionCase]` | — (static ref) |

**Typical scoped load:** 5–8 transactions · 3–8 timecards · 0–3 documents · 18 clauses · 6 rates · 5 instructions · 10 exceptions

---

## Phase 2 — Knowledge Base & Rule Engine

**Goal:** Codify all contract rules, project preferences, and historical precedents into a queryable rule engine.

### 2.1 Rule Precedence (strict order)

1. **Contract rules** — hard floors/ceilings (e.g. alcohol never reimbursable)
2. **Project preferences** — PL standing instructions (e.g. per diem on coastal sites)
3. **PL cycle overrides** — email-based approvals for this billing cycle
4. **Historical exception patterns** — prior resolutions for novel edge cases

### 2.2 Key Rules

| Rule | Value | Source |
|------|-------|--------|
| Lodging cap — major metro | $275/night | Contract §4.2 |
| Lodging cap — elsewhere | $195/night | Contract §4.2 |
| Meal cap — with receipts | $90/day | Contract §4.3 |
| Meal cap — per diem | $65/day (not stackable) | Contract §4.3 |
| Alcohol | Never reimbursable | Contract §4.3 |
| Air travel | Economy <6hr; premium economy ≥6hr | Contract §4.4 |
| Mileage rate | $0.67/mile | Contract §4.5 |
| Subcontractor markup | Cost + 8% | Contract §5.1 |
| Principal cap | Max 5% of monthly hours | Project preference |
| Receipt threshold | Required for expenses >$25 | Contract §4.1 |
| Per diem election | OK on coastal sites | PL email 2025-09 |

---

## Phase 3 — Matching & Reconciliation Engine

**Goal:** Link each SAP transaction to its supporting document and detect all mismatches.

### 3.1 Matching Logic

```
For each transaction:
  1. Normalise vendor name / employee ID across sources
  2. Find candidate receipts by: date window (±3 days), amount proximity (±10%), vendor similarity
  3. Score match confidence: exact / probable / no-match
  4. Handle special cases:
     - Composite scans (RC-016): split and match to multiple transactions
     - Foreign currency (RC-015): convert CAD at receipt-date FX rate
     - Unreadable docs (RC-018): flag as MISSING_BACKUP
```

### 3.2 Known Reconciliation Cases

| Transaction | Document | Issue | Expected resolution |
|-------------|----------|-------|-------------------|
| Team dinner $46.20 | RC-007 ($126.85 total) | Alcohol line $18.00 excluded; amount mismatch | Approve $46.20, reject alcohol |
| Hotel $310 | RC-012 | Over lodging cap $195; PL approved | Approve with override note |
| Client dinner $118 | RC-013 | Over meal cap $90; PL approved as working dinner | Approve with override |
| Lounge $55 | RC-014 | PL explicitly rejected | Exclude |
| FX receipt ~$40 USD | RC-015 (CAD $52) | Currency conversion needed | Convert at receipt-date rate |
| Composite $189.45 | RC-016 | Single scan = 3 receipts | Split and match individually |
| Laundry $22 | RC-017 | Personal item, non-reimbursable | Exclude |
| Principal 1.5hr hold | — | Principal cap hold | Release per PL email 2026-04-17 |
| Training 3hr | — | Miscoded to project | Remove per PL email 2026-04-22 |
| PMO admin 0.5hr | — | Non-billable | Remove per PL email 2026-04-22 |
| Workshop $1,147 | VI-001 | SAP shows room at $485 not $490; AV $45 missing | Flag $5 discrepancy; add AV line |
| Subcontractor $2,400 | VI-002 | Needs 8% markup | Bill $2,592 |

---

## Phase 4 — Exception Detection & Triage

**Goal:** Systematically detect all exception types and route each to the appropriate resolution path.

### 4.1 Exception Taxonomy

| Type | Description |
|------|-------------|
| `PROJECT_MISMATCH` | `tx.project_id` does not match the contract SAP project code — employee must recode |
| `AMOUNT_MISMATCH` | Transaction ≠ receipt amount |
| `MISSING_BACKUP` | No receipt found for expense >$25 |
| `POLICY_VIOLATION` | Hard rule breach (alcohol, personal items) |
| `OVER_CAP` | Amount exceeds contract limit |
| `MISCODED_LABOUR` | Time charged to wrong project/task |
| `UNREADABLE_DOCUMENT` | Receipt scan illegible |
| `COMPOSITE_DOCUMENT` | Multiple receipts in one scan |
| `CURRENCY_MISMATCH` | Foreign currency needs conversion |
| `HOLD_ITEM` | SAP-flagged hold transactions |
| `SUBCONTRACTOR_MARKUP` | Cost pass-through needs markup applied |

### 4.2 Resolution Routing

```
For each exception:
  1. Check if PL instruction in this cycle resolves it → apply override
  2. Check historical_exceptions for matching pattern → apply precedent
  3. If unresolved → escalate to analyst with full context
```

---

## Phase 5 — Invoice Builder & Output Generation

**Goal:** Assemble the validated invoice and produce all output artefacts.

### 5.1 Invoice Sections

| Section | Contents |
|---------|----------|
| A. Labour | Approved lines only; excludes miscoded training (3hr) and PMO admin (0.5hr); releases principal hold |
| B. Expenses — D. Okafor trip | Flights (with seat upgrade adjustment), hotel, meals (alcohol excluded), ground transport, mileage |
| C. Expenses — E. Rivers trip | Flights, hotel (over-cap approved by PL), meals; lounge excluded |
| D. Workshop (VI-001) | Room, catering, AV setup; $5 room discrepancy flagged |
| E. Subcontractor (VI-002) | $2,400 cost + $192 markup = **$2,592** |

**Expected invoice total:** ~$23,910–$23,980

### 5.2 Output Artefacts

| File | Description |
|------|-------------|
| `output/draft-invoice.md` | Final invoice matching SAP draft format |
| `output/audit-trail.csv` | Per-transaction decision log with rule citations |
| `output/exceptions-report.md` | All flagged items with resolution status |
| `output/kpi-summary.md` | 6 KPI metrics vs targets from pitch |
| `output/analyst-worksheet.md` | Open items requiring human judgment |

---

## Phase 6 — Agentic Orchestration (Claude API)

**Goal:** Wrap the deterministic rule engine with two LLM agents for orchestration and exception reasoning.

### 6.1 Billing Supervisor Agent

```python
client = anthropic.Anthropic()
response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=4096,
    tools=[
        load_inputs,
        run_rule_check,
        match_documents,
        flag_exception,
        apply_pl_override,
        build_invoice_line,
        generate_report,
    ],
    system=BILLING_SUPERVISOR_SYSTEM_PROMPT,
    messages=[{"role": "user", "content": trigger_message}]
)
```

### 6.2 Exception Reasoning Agent

Invoked only when the reconciliation pipeline returns flagged items.  
Reads **Decision Memory store** and **Instruction store**, returns:

```python
{
  "auto_resolved": [...],   # matched a historical pattern
  "escalate": [...]         # novel — needs analyst or employee correction
}
```

### 6.3 Re-trigger Loop

When a novel exception is flagged to the employee and they correct in SAP, the process re-enters from the top. Ingestion must be **idempotent** — already-resolved lines are not reprocessed.

---

## Phase 7 — Testing

**Goal:** Verify correctness against the ground-truth expected invoice.

### 7.1 Unit Tests

| Test file | Covers |
|-----------|--------|
| `test_ingestion.py` | All 52 transactions, 23 timecards, 19 documents load correctly |
| `test_rules.py` | Each rule against known inputs (alcohol, lodging cap, per diem, etc.) |
| `test_matching.py` | Correct doc-to-transaction linkage for all 12 complex cases |
| `test_currency.py` | CAD→USD conversion for RC-015 |

### 7.2 End-to-End Test

`test_invoice.py` runs the full pipeline and asserts all 22 checkpoints from `expected-invoice.md`:

- Labour subtotal = **$17,027.50**
- Alcohol charges = **$0** (fully excluded)
- Lounge charge = **$0** (PL rejected)
- Principal cap hold released
- Miscoded training removed (3 hrs)
- PMO admin removed (0.5 hrs)
- Subcontractor = **$2,592** ($2,400 + 8%)
- Hotel over-cap approved (E. Rivers trip)
- Foreign currency converted at receipt-date rate
- Workshop AV line included
- Composite receipt split correctly
- Invoice total within **$23,910–$23,980**

---

## Implementation Order

| Priority | Phase | Estimated effort |
|----------|-------|-----------------|
| 1 | Phase 1 — Scaffolding & ingestion | 4–6 hrs |
| 2 | Phase 2 — Rule engine | 3–4 hrs |
| 3 | Phase 3 — Matching engine | 4–5 hrs |
| 4 | Phase 4 — Exception detection | 2–3 hrs |
| 5 | Phase 5 — Invoice builder + outputs | 3–4 hrs |
| 6 | Phase 7 — Tests (parallel with above) | 2–3 hrs |
| 7 | Phase 6 — Agentic wrapper (Claude API) | 3–4 hrs |

**Recommended MVP path:** Complete Phases 1–5 first (deterministic pipeline passes all 22 checkpoints), then add the two-agent wrapper in Phase 6.

---

## Key Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Document parsing fragility — markdown receipts vary in format | Structured parser with regex fallback for key fields |
| Composite receipt splitting (RC-016) | Parse line items first; match totals to transactions individually |
| PL override vs contract conflict | Contract hard rules always beat PL overrides (alcohol is absolute) |
| Currency conversion (RC-015) | Hardcode 2026-04 CAD/USD rate; document the source |
| Workshop discrepancy (VI-001) $5 variance | Flag as AMOUNT_MISMATCH; analyst decides SAP vs vendor amount |
| Re-trigger idempotency | Track resolved tx_ids in a state file; skip on re-run |
