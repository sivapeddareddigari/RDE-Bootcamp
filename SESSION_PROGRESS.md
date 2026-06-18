# Session Progress — Agentic Billing Review System

**Project:** Meridian Atlas Partners — RDE Bootcamp  
**Repo:** https://github.com/sivapeddareddigari/RDE-Bootcamp  
**Active branch:** `develop`  
**Last updated:** 2026-06-18 (Phase 5 redesign complete)

---

## Quick Start on a New Machine

```bash
git clone https://github.com/sivapeddareddigari/RDE-Bootcamp.git
cd RDE-Bootcamp
git checkout develop
pip install -r requirements.txt

# Run the watcher
python3 -m billing_agent.main

# Drop a submission to trigger a run (second terminal)
cp test-data/sample-inputs/submissions/submission-E1041-clean-2026-04.csv submissions/incoming/

# Generate month-end project invoice
python3 -m billing_agent.invoice_run --project PRJ-NS-7421 --month 2026-04

# Run tests
python3 -m pytest tests/ -v
```

---

## Phase Status

| Phase | Description | Status | Latest commit |
|-------|-------------|--------|---------------|
| Trigger | Drop-folder watcher | ✅ Done | `e078864` |
| Phase 1 | Scaffolding & ingestion | ✅ Done | `624b1d5` |
| Phase 2 | Rule infrastructure & contract sync | ✅ Done | `6ea7ba5` |
| Phase 3 | Rule engine, document matching, override resolution | ✅ Done | `1ca6104` |
| Phase 4 | Exception detection & triage | ✅ Done | `65c3366` |
| Phase 5 | Employee notices + analyst summary (per-submission) + project invoice (month-end CLI) | ✅ Done | HEAD |
| Phase 6 | Agentic orchestration (Claude API) | ⏳ Next | — |
| Phase 7 | Testing | 🔄 In progress | HEAD |

**Test suite:** 215 tests, all passing. Run: `python3 -m pytest tests/ -v`

---

## Phase 5 Architecture — Two Triggers

Phase 5 was redesigned to reflect how the real billing workflow operates. Invoice generation is a SAP month-end activity, not a per-submission step.

### Trigger 1 — Watcher (per-submission, fires automatically)

Every CSV dropped into `submissions/incoming/` runs Phases 1–4 then:
- Writes a per-employee exception notice for each affected employee  
  (`output/notices/exception-notice-{emp_id}-{stem}__{ts}.md`)
- Writes an analyst summary for the billing analyst  
  (`output/analyst-summary-{stem}__{ts}.md`)
- Employee notices split items into "Blocking" (must fix in SAP) vs "Under review"
- Each item has plain-English corrective action instructions (`_ACTION` dict)

### Trigger 2 — Month-end invoice CLI

```bash
python3 -m billing_agent.invoice_run --project PRJ-NS-7421 --month 2026-04
```

Re-reads all completed submission CSVs for the project+month, re-runs Phases 1–4 for each, aggregates into:
- `output/draft-invoice-PRJ-NS-7421-2026-04.md` — one canonical invoice
- `output/audit-trail-PRJ-NS-7421-2026-04.csv` — 36 rows, 17 columns (incl. `employee_name`)
- `output/exceptions-report-PRJ-NS-7421-2026-04.md` — project-wide exception summary

**Key design:** re-process from `completed/` (idempotent, catches rule changes between submission and month-end).

### Project-level results — all 6 submissions combined

| Metric | Value |
|--------|-------|
| Labour total | $10,515.00 |
| Expense total | $4,136.54 |
| Grand total | $14,651.54 |
| Blocked items | 9 |

---

## Project File Tree (current)

```
billing_agent/
├── __init__.py
├── config.py                   # paths, FX_RATES (only contract value here), watcher config
├── main.py                     # drop-folder watcher; Phases 1–5a wired; Phase 6 TODO stub
├── invoice_run.py              # month-end CLI — --project + --month args
├── run_logger.py               # append-mode output/billing_agent.log
├── models/
│   ├── transaction.py          # Transaction, TimecardEntry dataclasses
│   ├── document.py             # ReceiptDocument (alcohol_amount, is_composite, is_unreadable)
│   ├── contract.py             # ContractClause, RateEntry
│   └── instruction.py         # ProjectInstruction, ExceptionCase
├── ingestion/
│   ├── loader.py               # load_inputs() → IngestionResult
│   ├── sap_loader.py           # load_transactions(), load_timecards(employee_ids)
│   ├── contract_parser.py      # parses contract-001.md → rates + clauses
│   ├── doc_parser.py           # parses RC-*/ML-*/VI-* markdown docs (scoped by doc_id)
│   ├── email_parser.py         # parses sample-emails.md → List[ProjectInstruction]
│   ├── exception_loader.py    # parses resolutions.csv → List[ExceptionCase]
│   └── contacts_loader.py      # load_contacts() → ContactDirectory
├── data/
│   └── contacts.json           # employee/analyst/PL contact directory
├── rules/
│   ├── data/
│   │   ├── expense_caps.json   # auto-generated from contract — caps, rates, markup
│   │   ├── labour_rules.json   # auto-generated — role rates, principal cap, travel time
│   │   ├── policy_rules.json   # auto-generated — alcohol/personal/entertainment flags
│   │   └── keyword_lists.json  # manually maintained — alcohol, personal_items,
│   │                           #   miscoded_labour, airport_lounge, meals
│   ├── sync_rules.py           # parses contract → regenerates 3 JSON files; idempotent
│   ├── models.py               # RuleResult dataclass
│   ├── rule_engine.py          # run(inputs) → List[RuleResult]
│   └── override_resolver.py    # apply_overrides() — PL instructions + prior patterns
├── matching/
│   ├── __init__.py             # re-exports MatchResult, reconcile
│   ├── matcher.py              # EXACT (doc-id in note) + FUZZY (±3 days, ±10%) matching
│   └── currency.py             # to_usd(amount, currency) via FX_RATES
├── exceptions/
│   ├── __init__.py             # re-exports ExceptionItem, ExceptionReport, run
│   ├── models.py               # ExceptionItem, ExceptionReport dataclasses
│   └── detector.py             # run() → ExceptionReport
└── output/
    ├── __init__.py             # re-exports BuildResult, build, write_notices
    ├── notice_writer.py        # write_notices() → employee notices + analyst summary
    └── invoice_builder.py      # build() → project-level invoice + audit + exceptions

tests/
├── conftest.py                 # shared paths and submission fixture constants
├── test_sap_loader.py          # 22 tests
├── test_doc_parser.py          # 25 tests
├── test_loader.py              # 34 tests
├── test_sync_rules.py          # 79 tests
└── test_invoice_builder.py     # 55 tests (contacts loader, notice writer, invoice builder, helpers)

test-data/sample-inputs/
├── submissions/                # 6 per-employee scenario CSVs
│   ├── submission-E1041-clean-2026-04.csv
│   ├── submission-E2210-over-cap-alcohol-2026-04.csv
│   ├── submission-E3055-hold-miscoded-2026-04.csv
│   ├── submission-E4501-principal-cap-2026-04.csv
│   ├── submission-E5102-subcontractor-composite-2026-04.csv
│   └── submission-E7702-currency-personal-2026-04.csv
├── documents/                  # 15 backup docs (RC-*, ML-*, VI-*)
├── sap-outputs/timecards-2026-04.csv
├── contracts/contract-001.md
├── pm-instructions/sample-emails.md
└── prior-exceptions/resolutions.csv
```

---

## Key Dataclasses

### IngestionResult (Phase 1 output)
```python
@dataclass
class IngestionResult:
    submission_file: Path
    transactions:   List[Transaction]
    timecards:      List[TimecardEntry]
    documents:      List[ReceiptDocument]
    rate_table:     List[RateEntry]
    contract_clauses: List[ContractClause]
    instructions:   List[ProjectInstruction]
    exceptions:     List[ExceptionCase]
    loaded_at:      datetime
    # computed: .labour_transactions, .expense_transactions, .held_transactions
```

### RuleResult (Phase 3 output — one per transaction)
```python
@dataclass
class RuleResult:
    transaction_id:  str
    status:          str          # APPROVE | FLAG | REJECT | HOLD
    exception_type:  Optional[str]
    rule_id:         str          # CLEAN | ALCOHOL | LODGING_CAP | NO_RECEIPT | …
    override_applied: bool
    override_source: str          # e.g. "PL-EMAIL-03"
    original_amount: float
    approved_amount: float
    note:            str
```

### ExceptionReport (Phase 4 output)
```python
@dataclass
class ExceptionReport:
    submission_file:    str
    generated_at:       str
    total_transactions: int
    clean_count:        int
    auto_resolved:      List[ExceptionItem]
    pl_rejections:      List[ExceptionItem]
    hard_rejections:    List[ExceptionItem]
    escalate_analyst:   List[ExceptionItem]
    escalate_employee:  List[ExceptionItem]
    escalate_pl:        List[ExceptionItem]
    # computed: .blocking, .unresolved_count, .exception_count
```

### BuildResult (Phase 5b output — project invoice)
```python
@dataclass
class BuildResult:
    project_id:      str
    billing_month:   str
    invoice_path:    Path    # output/draft-invoice-{project}-{month}.md
    audit_path:      Path    # output/audit-trail-{project}-{month}.csv
    exceptions_path: Path    # output/exceptions-report-{project}-{month}.md
    labour_total:    float
    expense_total:   float
    grand_total:     float
    blocked_count:   int
    submission_count: int
```

### ContactDirectory (Phase 5 — contacts)
```python
@dataclass
class ContactDirectory:
    employees:       List[EmployeeContact]      # from data/contacts.json
    billing_analysts: List[AnalystContact]
    project_leads:   List[ProjectLeadContact]

    def employee(self, employee_id) -> Optional[EmployeeContact]: ...
    def project_lead(self, project_id) -> Optional[ProjectLeadContact]: ...
```

---

## Rule Engine — Key Design Points

### Rule order in `_eval_expense()` (first match wins)
1. `HOLD_ITEM` — SAP hold flag → stop immediately
2. `POLICY_VIOLATION` — ALCOHOL / AIRPORT_LOUNGE / PERSONAL_ITEM → hard REJECT, approved=0
3. `MISCODED_LABOUR` — non-billable descriptions → hard REJECT
4. `RATE_MISMATCH / TRAVEL_TIME` — labour only
5. `CURRENCY_MISMATCH` — non-USD receipt → FLAG
6. `SUBCONTRACTOR_MARKUP` — vendor invoice with markup not applied → FLAG, approved=cost×1.08
7. **`OVER_CAP`** — lodging/meal/per diem/mileage caps → FLAG *(runs BEFORE MISSING_BACKUP)*
8. `COMPOSITE_DOCUMENT / UNREADABLE_DOC`
9. `MISSING_BACKUP` — >$25 with no linked doc (per diem and mileage exempt)
10. `AMOUNT_MISMATCH` — SAP overbills receipt by >$0.50
11. `CLEAN`

### Override resolver design
- Body-text regex (`_HOLD_RELEASE_RE`, `_MARKUP_CONFIRM_RE`, `_APPROVAL_RE`, `_REJECTION_RE`)
- ALCOHOL / AIRPORT_LOUNGE / PERSONAL_ITEM are **never** overrideable
- Guard: don't reject a CLEAN item from a mixed-intent email

---

## Exception Routing

| Actor | Rule IDs |
|-------|----------|
| ANALYST | RATE_MISMATCH, TRAVEL_RATE, TRAVEL_HRS_CAP, MILEAGE_RATE, CURRENCY, COMPOSITE_DOC, MARKUP_MISSING, AMOUNT_MISMATCH |
| PL | LODGING_CAP, MEAL_CAP, PER_DIEM_CAP, HOLD_ITEM |
| EMPLOYEE | NO_RECEIPT, UNREADABLE_DOC, MISCODED, ALCOHOL, AIRPORT_LOUNGE, PERSONAL_ITEM |

**Blocking rules:** `LODGING_CAP`, `MEAL_CAP`, `PER_DIEM_CAP`, `HOLD_ITEM`, `NO_RECEIPT`, `UNREADABLE_DOC`, `CURRENCY`, `MARKUP_MISSING`

`AMOUNT_MISMATCH` is **not** blocking — analyst approves receipt amount while SAP discrepancy is investigated.

---

## Test Suite

| Test file | Tests | Scope |
|-----------|-------|-------|
| `test_sap_loader.py` | 22 | Transaction parsing, timecard employee filter |
| `test_doc_parser.py` | 25 | Document ID filtering, composite/unreadable/alcohol detection |
| `test_loader.py` | 34 | IngestionResult scoping — timecards, documents, static data |
| `test_sync_rules.py` | 79 | JSON rule values, sync idempotency, keyword lists |
| `test_invoice_builder.py` | 55 | Contacts loader (6), notice writer (12), project invoice (19), helpers (18) |
| **Total** | **215** | **All passing** |

### Pending tests (Phase 7 completion)
- `tests/test_rules.py` — each rule evaluation against known inputs
- `tests/test_matching.py` — doc-to-transaction linkage
- `tests/test_currency.py` — CAD→USD conversion

---

## Contract Rule Values

| Rule | Value |
|------|-------|
| Lodging — metro | $275/night |
| Lodging — elsewhere | $195/night |
| Meals — with receipt | $90/day |
| Per diem | $65/day (not stackable) |
| Mileage | $0.67/mile |
| Subcontractor markup | 8% |
| Receipt threshold | >$25 requires backup |
| Travel time billing | 50% of role rate, max 8 hr/direction |
| Role rates | ENG1 $145 · ENG2 $175 · ENG3 $230 · PM1 $215 · PRIN $320 · ADMIN $95 |

---

## PL Email Instructions (5 emails in sample-emails.md)

| ID | Type | What it does |
|----|------|-------------|
| PL-EMAIL-01 | OVERRIDE_REJECT | Rejects airport lounge |
| PL-EMAIL-02 | OVERRIDE_REJECT (mixed) | Rejects "not appropriate" charges; also approves working dinner $118 |
| PL-EMAIL-03 | OVERRIDE_APPROVE | Approves over-cap hotel $310 |
| PL-EMAIL-04 | RELEASE_HOLD (mixed) | Releases principal cap hold; also confirms drone subcontractor markup |
| PL-EMAIL-05 | OVERRIDE_REJECT | Rejects miscoded training and PMO admin labour |

---

## Security Notes

- GitHub PAT stored in `~/.git-credentials` and `.claude/settings.local.json` (gitignored)
- **Never commit the PAT** — it must not appear in any tracked file

---

## What's Next — Phase 6

**Goal:** Wrap the deterministic pipeline with two LLM agents.

### Files to create
- `billing_agent/agents/supervisor.py` — Billing Supervisor Agent (Claude API)
- `billing_agent/agents/exception_agent.py` — Exception Reasoning Agent
- `billing_agent/stores/decision_memory.py` — pattern library (R/W resolutions.csv)
- `billing_agent/stores/instruction_store.py` — PL rules per project

### Billing Supervisor skeleton
```python
import anthropic

client = anthropic.Anthropic()

def run(submission_path: Path) -> None:
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        tools=[
            load_inputs_tool,
            run_rule_check_tool,
            match_documents_tool,
            detect_exceptions_tool,
            write_notices_tool,
        ],
        system=BILLING_SUPERVISOR_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Process submission: {submission_path}"}],
    )
```

### Exception Reasoning Agent
- Invoked only when `exception_report.unresolved_count > 0`
- Reads Decision Memory + Instruction store
- Returns `{"auto_resolved": [...], "escalate": [...]}`
- Novel exceptions → flag to employee via notice → re-trigger loop (idempotent by tx_id)

---

## Key Reference Files

| File | Purpose |
|------|---------|
| `IMPLEMENTATION_PLAN.md` | Full phased plan with progress tracker and rule values |
| `EXECUTION_FLOW.md` | Step-by-step data flow — both triggers explained |
| `billing_agent/main.py` | Per-submission pipeline orchestrator |
| `billing_agent/invoice_run.py` | Month-end invoice CLI |
| `billing_agent/rules/rule_engine.py` | Rule evaluation logic |
| `billing_agent/output/notice_writer.py` | Employee notices + analyst summary |
| `billing_agent/output/invoice_builder.py` | Project-level invoice generation |
| `billing_agent/data/contacts.json` | Employee/analyst/PL contact directory |
| `output/billing_agent.log` | Append-mode run log — all historical runs |
