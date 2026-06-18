# Execution Flow: Agentic Billing Review System

**Project:** Meridian Atlas Partners — Coastal Greenway (PRJ-NS-7421)  
**Last updated:** 2026-06-18 (Phase 5 redesign — two triggers)

This document describes what happens step-by-step when a submission file enters the system, how each phase transforms the data, and what flows into the next phase.

---

## System Overview

### Trigger 1 — Drop-folder watcher (per submission)

```
Employee / SAP
     │
     ▼
submissions/incoming/          ← CSV dropped here
     │  (watcher picks up within 5 s)
     ▼
[TRIGGER] DropFolderWatcher
     │
     ▼
[PHASE 1] Ingestion ─────────────────────────────────────────────────► IngestionResult
     │
     ▼
[PHASE 2] Rule Engine ◄──── rules/data/*.json ◄──── contract-001.md
     │
     ▼
[PHASE 3] Matching & Reconciliation
     │
     ▼
[PHASE 4] Exception Detection & Triage ◄──── PL emails, prior exceptions
     │
     ▼
[PHASE 5a] Notice Writer (per-submission)
     │
     ▼
[PHASE 6] Agentic Orchestration (Claude API)  ← stub today
     │
     ▼
submissions/completed/         ← timestamped archive
output/billing_agent.log       ← full run trace
output/notices/exception-notice-{emp}-{stem}__{ts}.md    ← per-employee notice
output/analyst-summary-{stem}__{ts}.md                   ← analyst summary
```

### Trigger 2 — Month-end invoice CLI

```
All submissions for project+month are in completed/
     │
     ▼
python3 -m billing_agent.invoice_run --project PRJ-NS-7421 --month 2026-04
     │
     ▼
[PHASE 5b] Invoice Builder (project-level)
     │  re-reads all completed CSVs for project+month
     │  re-runs Phases 1–4 for each submission
     │  aggregates all results
     │
     ▼
output/draft-invoice-{project}-{month}.md          ← one canonical invoice
output/audit-trail-{project}-{month}.csv           ← all 36 transactions
output/exceptions-report-{project}-{month}.md      ← project-wide exceptions
```

---

## Trigger — Drop-Folder Watcher

**Entry point:** `python3 -m billing_agent.main`  
**Module:** `billing_agent/main.py`

### Startup sequence

```
main()
  └─► DropFolderWatcher.start()
        ├─► _ensure_folders()          create incoming/processing/completed/failed/ if missing
        ├─► _recover_in_flight()       move any stale processing/ files back to incoming/
        │     ├─► _retry_count(stem)   reads __rN suffix from filename
        │     ├─► if retries >= 3  →  quarantine to failed/
        │     └─► else             →  bump to __r(N+1), move to incoming/
        └─► poll loop every 5 s
              └─► _poll()
                    └─► for each .csv in incoming/ (sorted):
                          _handle(submission_path)
```

### Per-file handling

```
_handle(submission)
  ├─► shutil.move(incoming/ → processing/)       atomic hand-off
  ├─► process_submission(processing_path)
  │     ├─► run_logger.init_run(filename)        open log section in billing_agent.log
  │     ├─► [Phase 1]  load_inputs()
  │     ├─► [Phase 2/3]  rule_engine.run()         ← wired
  │     ├─► [Phase 3]  matcher.reconcile()        ← wired
  │     ├─► [Phase 4]  detector.run()             ← wired
  │     │     └─► [Phase 6]  supervisor_run(submission_path, contacts)  ← wired
  ├─► shutil.move(processing/ → completed/__timestamp__.csv)
  └─► run_logger.close_run(success=True)          seal log after file is safely archived

  on exception →
  ├─► run_logger.close_run(success=False)
  └─► shutil.move(processing/ → failed/__timestamp__.csv)
```

**Crash recovery:** If the process dies mid-run the file stays in `processing/`. On next startup `_recover_in_flight()` bumps the `__rN` retry counter and returns it to `incoming/`. After `MAX_RETRIES=3` it is quarantined in `failed/`.

---

## Phase 1 — Ingestion

**Entry point:** `load_inputs(submission_path)` in `billing_agent/ingestion/loader.py`  
**Output:** `IngestionResult` dataclass

### Execution steps

```
load_inputs(submission_path)
  │
  ├─1─ load_transactions(submission_path)
  │      └─► sap_loader.py reads the dropped CSV row by row
  │          → List[Transaction]  (LABOR and EXPENSE rows, with hold flags)
  │
  ├─2─ derive scope from transactions
  │      employee_ids = {tx.employee_id for tx in transactions}
  │      doc_ids      = _referenced_doc_ids(transactions)
  │                      └─► regex (RC|ML|VI)-\d{3} over every tx.note field
  │
  ├─3─ load_timecards(timecard_path, employee_ids=employee_ids)
  │      └─► sap_loader.py reads timecards-YYYY-MM.csv
  │          skips rows whose employee_id is not in employee_ids
  │          → List[TimecardEntry]  (only employees in this submission)
  │
  ├─4─ load_contract(contract_path)
  │      └─► contract_parser.py reads contract-001.md
  │          _parse_rates()         → List[RateEntry]      (§3 role rates)
  │          _parse_expense_rules() → List[ContractClause] (§4 caps)
  │          _parse_sap_notes()     → List[ContractClause] (project preferences)
  │
  ├─5─ load_documents(docs_dir, doc_ids=doc_ids)
  │      └─► doc_parser.py iterates documents/ directory
  │          skips files whose RC/ML/VI prefix is not in doc_ids
  │          for each matching file:
  │            _parse_document(md_file)
  │              ├─ _extract_code_block()    receipt text between ``` fences
  │              ├─ _extract_vendor()        first non-date line in code block
  │              ├─ _extract_date()          first YYYY-MM-DD in code block
  │              ├─ _extract_total()         last TOTAL ... NNN.NN line
  │              ├─ _extract_currency()      CAD|EUR|GBP|... regex
  │              ├─ _extract_line_items()    amount-bearing lines
  │              ├─ alcohol flag             _ALCOHOL_WORDS regex over code+notes
  │              ├─ alcohol_amount           sum of alcohol line item amounts
  │              ├─ composite flag           "composite" in preamble or RC-016
  │              └─ unreadable flag          "unreadable|illegible" in preamble
  │          → List[ReceiptDocument]  (only documents referenced in submission)
  │
  ├─6─ load_instructions(emails_path)
  │      └─► email_parser.py reads sample-emails.md
  │          splits on "## Email N" headings
  │          classifies each as OVERRIDE_APPROVE | OVERRIDE_REJECT |
  │                             RELEASE_HOLD | CONFIRM_MARKUP | STANDING
  │          → List[ProjectInstruction]
  │
  └─7─ load_exceptions(exceptions_path)
         └─► exception_loader.py reads resolutions.csv
             → List[ExceptionCase]  (recurring=True rows are auto-apply patterns)

  returns IngestionResult{
    submission_file, transactions, timecards, documents,
    rate_table, contract_clauses, instructions, exceptions
  }
```

**Key design principle:** Only data relevant to the submission is loaded. Timecards and documents are filtered by employee ID and document ID respectively before any processing begins.

---

## Phase 2 — Rule Infrastructure  ✅ Complete

**Built:** `billing_agent/rules/sync_rules.py` + `billing_agent/rules/data/*.json`  
**Runtime entry point (Phase 3):** `rule_engine.run(inputs)` in `billing_agent/rules/rule_engine.py`  
**Input:** `IngestionResult`  
**Output:** `List[RuleResult]` — one per transaction

### Rule data sources

```
contract-001.md
  └─► sync_rules.py  ✅ built — auto-triggered on git commit or in-session edit
        ├─► rules/data/expense_caps.json   ✅  lodging, meal, air, mileage, markup, receipt threshold
        ├─► rules/data/labour_rules.json   ✅  role rates, principal cap, travel time
        └─► rules/data/policy_rules.json   ✅  alcohol/personal/entertainment flags + override policy

rules/data/keyword_lists.json  ✅  manually maintained — alcohol, personal items,
                                    miscoded labour, airport lounge, entertainment

IngestionResult
  ├─► instructions   PL email overrides for this cycle
  └─► exceptions     recurring prior-cycle resolution patterns
```

### Auto-sync triggers

| Trigger | When | How |
|---------|------|-----|
| `.githooks/pre-commit` | On `git commit` when `contract-001.md` is staged | Runs `sync_rules.py`, stages updated JSON files |
| `.claude/settings.json` PostToolUse | When `contract-001.md` is edited in a Claude Code session | Runs `sync_rules.py` immediately after save |

### Rule evaluation execution

```
rule_engine.run(inputs)                       ← reads built JSON files at import
  │
  ├─► load JSON rule files once at import time
  │
  └─► for each tx in inputs.transactions:
        │
        ├─1─ if tx.hold_flag
        │      → RuleResult(HOLD_ITEM)  ── stop, no further rules
        │
        ├─2─ find matched_doc
        │      tx.note references a doc_id → direct lookup in inputs.documents
        │
        ├─3─ if tx.is_labor:
        │      ├─ rate_validation()        tx.rate vs labour_rules.json[tx.role_code]
        │      ├─ miscoded_check()         keyword_lists.json[miscoded_labour] vs tx.description
        │      ├─ principal_cap_check()    cumulative PRIN hours in batch vs 5% monthly cap
        │      └─ travel_time_check()      50% rate, 8hr/direction cap
        │
        ├─4─ if tx.is_expense:
        │      ├─ alcohol_check()          keyword_lists.json[alcohol] vs doc + description
        │      │    → REJECT, approved_amount = total − alcohol_amount (split, not full reject)
        │      ├─ personal_item_check()    keyword_lists.json[personal_items]
        │      │    → REJECT
        │      ├─ currency_check()         tx.currency != "USD"
        │      │    → FLAG CURRENCY_MISMATCH (feeds Phase 3 FX conversion)
        │      ├─ receipt_check()          tx.amount > 25 and no matched_doc
        │      │    → FLAG MISSING_BACKUP
        │      ├─ document_quality_check()
        │      │    matched_doc.is_composite  → FLAG COMPOSITE_DOCUMENT
        │      │    matched_doc.is_unreadable → FLAG MISSING_BACKUP
        │      ├─ amount_mismatch_check()  abs(tx.amount − doc.total_amount) > 0.01
        │      │    → FLAG AMOUNT_MISMATCH
        │      ├─ cap_check()
        │      │    LODGING   → compare tx.amount vs metro/other cap in expense_caps.json
        │      │    MEAL      → compare vs 90/day or 65 per diem
        │      │    AIR       → check economy vs premium based on flight hours
        │      │    MILEAGE   → check rate matches 0.67/mile
        │      │    → FLAG OVER_CAP if exceeded
        │      └─ subcontractor_check()
        │           tx doc_type == VENDOR_INVOICE and markup not applied
        │           → FLAG SUBCONTRACTOR_MARKUP, approved_amount = cost × 1.08
        │
        └─5─ override_resolver(result, inputs.instructions, inputs.exceptions)
               ├─ scan inputs.instructions for matching scope + type
               │    OVERRIDE_APPROVE → change status to APPROVE
               │    OVERRIDE_REJECT  → change status to REJECT
               │    RELEASE_HOLD     → clear HOLD_ITEM
               │    CONFIRM_MARKUP   → confirm SUBCONTRACTOR_MARKUP approved_amount
               └─ scan inputs.exceptions where recurring=True and same exception_type
                    → apply historical resolution automatically

  returns List[RuleResult]{
    transaction_id, status, exception_type, rule_id,
    override_applied, override_source,
    original_amount, approved_amount, note
  }
```

**Priority rule:** Contract hard rules always beat PL overrides. Alcohol is rejected even if an email tries to approve it (policy_rules.json `override_allowed: false`).

---

## Phase 3 — Matching & Reconciliation  ✅ Complete

**Entry point:** `reconcile(inputs, rule_results)` in `billing_agent/matching/matcher.py`  
**Input:** `IngestionResult` + `List[RuleResult]` from Phase 2  
**Output:** `List[MatchResult]` — each expense transaction paired with its document and reconciliation delta

### Execution steps

```
reconcile(inputs, rule_results)
  │
  └─► for each expense tx in inputs.transactions:
        │
        ├─1─ direct match
        │      _DOC_ID_RE regex over tx.note → e.g. "RC-003" → lookup in docs_by_id
        │      → MatchResult(confidence=EXACT)
        │
        ├─2─ fuzzy match (if no direct match)
        │      candidate docs = documents within ±3-day date window
        │      score by amount proximity: |doc_usd − tx.amount| / max < 10%
        │      return single candidate only (ambiguous → NO_MATCH)
        │      → MatchResult(confidence=FUZZY)
        │
        ├─3─ no match
        │      → MatchResult(confidence=NO_MATCH, usd_amount=tx.amount, delta=0.0)
        │
        └─4─ build result (EXACT or FUZZY)
               to_usd(doc.total_amount, doc.currency) → applies FX_RATES if non-USD
               doc_billable = usd_amount − doc.alcohol_amount
               amount_delta = tx.amount − doc_billable
               (positive = SAP higher than receipt; negative = line-item of larger folio)

  returns List[MatchResult]{
    transaction_id, matched_doc_id, confidence,
    usd_amount, fx_rate_applied, amount_delta, note
  }
```

**FX conversion** (`billing_agent/matching/currency.py`): `to_usd(amount, currency)` applies `FX_RATES` from `config.py`. Raises `ValueError` for unknown currencies.

**Alcohol exclusion:** `doc_billable = doc.total_amount − doc.alcohol_amount` is used in the delta computation. The amount mismatch rule in Phase 2 also uses this to only flag overbilling direction (tx.amount > doc_billable + $0.50).

---

## Phase 4 — Exception Detection & Triage  ✅ Complete

**Entry point:** `run(inputs, rule_results, match_results)` in `billing_agent/exceptions/detector.py`  
**Input:** `IngestionResult` + `List[RuleResult]` from Phase 2/3  
**Output:** `ExceptionReport` — all exceptions classified by routing and blocking status

### Execution steps

```
detector.run(inputs, rule_results, match_results)
  │
  └─► for each RuleResult:
        │
        ├─ status=APPROVE, override_applied=False
        │    → clean_count += 1  (no exception, no action)
        │
        ├─ status=APPROVE, override_applied=True
        │    → auto_resolved[]  (was FLAG/HOLD; PL or prior-exception resolved it)
        │      routing = AUTO_RESOLVED
        │
        ├─ status=REJECT, override_applied=True
        │    → pl_rejections[]  (PL explicitly rejected this line)
        │      routing = AUTO_RESOLVED
        │
        ├─ status=REJECT, override_applied=False
        │    → hard_rejections[]  (contract violation: alcohol, lounge, personal, miscoded)
        │      routing = _route(rule_id)  → EMPLOYEE
        │
        └─ status=FLAG or HOLD (unresolved)
             routing = _route(rule_id):
               ANALYST  ← AMOUNT_MISMATCH, RATE_MISMATCH, CURRENCY, COMPOSITE_DOC, MARKUP_MISSING, ...
               PL       ← LODGING_CAP, MEAL_CAP, PER_DIEM_CAP, HOLD_ITEM
               EMPLOYEE ← NO_RECEIPT, UNREADABLE_DOC, MISCODED, ALCOHOL, ...
             blocks_invoice = rule_id in _BLOCKING_RULES
             → escalate_analyst[] / escalate_pl[] / escalate_employee[]

  returns ExceptionReport{
    clean_count, auto_resolved[], pl_rejections[], hard_rejections[],
    escalate_analyst[], escalate_employee[], escalate_pl[],
    .blocking   → computed: unresolved items where blocks_invoice=True
    .unresolved_count → len(analyst) + len(employee) + len(pl)
  }
```

**Blocking items** (cannot appear on draft invoice without resolution):
`LODGING_CAP`, `MEAL_CAP`, `PER_DIEM_CAP`, `HOLD_ITEM`, `NO_RECEIPT`, `UNREADABLE_DOC`, `CURRENCY`, `MARKUP_MISSING`

`AMOUNT_MISMATCH` is not blocking — analyst can approve the receipt amount while SAP discrepancy is investigated.

---

## Phase 5a — Notice Writer (per-submission)  ✅ Complete

**Entry point:** `write_notices(inputs, rule_results, exception_report, contacts)` in `billing_agent/output/notice_writer.py`  
**Input:** `IngestionResult` + `List[RuleResult]` + `ExceptionReport` + `ContactDirectory`  
**Output:** `List[Path]` — employee notice files + analyst summary file

### Execution steps

```
notice_writer.write_notices(inputs, rule_results, exception_report, contacts)
  │
  ├─1─ for each employee_id in submission:
  │      items = all exception items for this employee
  │        (escalate_employee + escalate_analyst + escalate_pl + hard_rejections)
  │      if no items → skip (clean employee, no notice)
  │      else:
  │        emp = contacts.employee(emp_id)
  │        _write_employee_notice(notices_dir / exception-notice-{id}-{stem}__{ts}.md)
  │          addressed to emp.name (emp.email)
  │          split items into "Blocking" and "Under review" tables
  │          each row includes _ACTION[rule_id] — plain-English corrective action
  │
  └─2─ _write_analyst_summary(output/analyst-summary-{stem}__{ts}.md)
         addressed to contacts.billing_analysts[0]
         headline counts table
         ⚠ Blocking items section (if any)
         Requires analyst review section
         Requires PL approval section
         Auto-resolved section
         Rejected items section
         footer: "Run billing_agent.invoice_run at month-end…"

  returns [notice_path_1, ..., analyst_summary_path]
```

### `_ACTION` corrective instruction map (rule_id → plain English)

| Rule | Employee instruction |
|------|---------------------|
| NO_RECEIPT | Upload missing receipt to SAP and resubmit |
| ALCOHOL | Remove charge — not reimbursable (§4) |
| PERSONAL_ITEM | Remove charge — personal items not reimbursable (§4) |
| MISCODED | Correct task code in SAP |
| CURRENCY | Resubmit with USD amount + exchange rate note |
| … | (full map in `notice_writer._ACTION`) |

---

## Phase 5b — Invoice Builder (project-level month-end)  ✅ Complete

**Entry point:** `build(project_id, billing_month, contacts, submissions_dir)` in `billing_agent/output/invoice_builder.py`  
**CLI:** `python3 -m billing_agent.invoice_run --project PRJ-NS-7421 --month 2026-04`  
**Output:** `BuildResult` + three canonical files in `output/`

### Execution steps

```
invoice_builder.build(project_id, billing_month, contacts, submissions_dir)
  │
  ├─1─ _find_submissions(submissions_dir, billing_month)
  │      → all CSVs in submissions_dir whose name contains billing_month
  │      → raises FileNotFoundError if none found
  │
  ├─2─ for each csv_path in csv_files:
  │      re-run full Phases 1–4 (idempotent):
  │        inputs = load_inputs(csv_path)
  │        rr     = rule_engine.run(inputs)
  │        mr     = reconcile(inputs, rr)
  │        er     = detect_exceptions(inputs, rr, mr)
  │      aggregate into all_inputs[], all_rr[], all_mr[], exception lists
  │
  ├─3─ build combined ExceptionReport (project-wide across all submissions)
  │
  ├─4─ partition rule results
  │      billable  = [r for r in all_rr if r.status == "APPROVE"]
  │      labour / expenses / non_bill split
  │
  ├─5─ compute totals
  │      labour_total  = sum(r.approved_amount for r in labour)
  │      expense_total = sum(r.approved_amount for r in expenses)
  │      grand_total   = labour_total + expense_total
  │
  ├─6─ write draft invoice (Markdown)
  │      output/draft-invoice-{project_id}-{billing_month}.md
  │      Section A — Labour (with Employee column, names from contacts)
  │      Section B — Reimbursable Expenses (by category)
  │        SUBCONTRACTOR with _has_markup() → cost + markup split rows
  │      Totals + Excluded/Blocked items
  │
  ├─7─ write audit trail (CSV)
  │      output/audit-trail-{project_id}-{billing_month}.csv
  │      17 columns including submission (filename stem) and employee_name
  │      one row per transaction across all submissions
  │
  ├─8─ write exceptions report (Markdown)
  │      output/exceptions-report-{project_id}-{billing_month}.md
  │      project-wide view: all 6 submissions combined
  │      employee names resolved from contacts
  │
  └─9─ validate
         assert: ALCOHOL / AIRPORT_LOUNGE / PERSONAL_ITEM approved_amount == 0.0
         assert: abs(grand_total − labour − expense) <= 0.01
         log result to billing_agent.log

  returns BuildResult{
    project_id, billing_month,
    invoice_path, audit_path, exceptions_path,
    labour_total, expense_total, grand_total,
    blocked_count, submission_count
  }
```

### Helper functions

| Helper | Location | Purpose |
|--------|----------|---------|
| `_categorize(tx)` | invoice_builder | Maps tx to `AIR/LODGING/MEALS/GROUND/MILEAGE/SUBCONTRACTOR/OTHER` |
| `_has_markup(r)` | invoice_builder | True when SUBCONTRACTOR_MARKUP override_applied + approved > original |
| `_expense_note(r, mr)` | invoice_builder | Note column: override citation + FX delta direction |
| `_find_submissions(dir, month)` | invoice_builder | Collects all CSVs in dir whose name contains billing_month |
| `_validate(result, rr, tx_by_id)` | invoice_builder | Asserts hard-reject items billed $0 and totals balance |
| `_infer_cycle(inputs)` | notice_writer | Extracts `YYYY-MM` from submission filename for notice header |

---

## Phase 6 — Agentic Orchestration  *(complete)*

**Entry point:** `supervisor.run(submission_path, contacts)` in `billing_agent/agents/supervisor.py`  
**Model:** `claude-haiku-4-5-20251001` for both agents (fast, cost-efficient)  
**Fallback:** If the Anthropic API is unavailable both agents fall back gracefully (no crash)

### Execution steps

```
supervisor.run(submission_path, contacts)
  │
  ├─ Claude tool-use loop (max 10 iterations)
  │    system prompt: always run pipeline first; only call exception agent if has_unresolved;
  │                   always write notices last; don't call any tool more than once
  │
  │    Turn 1 → tool: run_pipeline_phases_1_to_4(submission_path)
  │              → load_inputs() + rule_engine.run() + reconcile() + detect_exceptions()
  │              → state[run_id] stored in memory for subsequent turns
  │              → returns JSON: {run_id, total_transactions, unresolved_count, has_unresolved}
  │
  │    Turn 2 (if has_unresolved) → tool: analyse_unresolved_exceptions(run_id)
  │              → Exception Reasoning Agent (single-turn, structured output)
  │                    context: PL instructions + relevant prior cases + contract clauses (12)
  │                    → List[ExceptionAnalysis] (one per unresolved item)
  │                    → each: recommendation, routing, reasoning,
  │                             employee_notice_text, analyst_note
  │                    fallback → [] (template notices used instead)
  │
  │    Turn 3 → tool: write_notices_and_summary(run_id, use_llm_text=true)
  │              → notice_writer.write_notices(inputs, rr, er, contacts, llm_texts={…})
  │              → llm_texts dict: tx_id → employee_notice_text (overrides _ACTION template)
  │              → writes exception-notice-*.md (per employee) + analyst-summary-*.md
  │
  │    Turn 4 → end_turn (supervisor writes one-sentence summary)
  │
  └─ fallback path (API unavailable):
       _direct_fallback() → deterministic pipeline → template notices
       result.analyses = [], result.auto_resolved_by_llm = 0
```

### Return type: `SupervisorResult`
| Field | Type | Description |
|-------|------|-------------|
| `inputs` | `IngestionResult` | All ingested data for this submission |
| `rule_results` | `List[RuleResult]` | Rule engine output |
| `exception_report` | `ExceptionReport` | All exception items |
| `analyses` | `List[ExceptionAnalysis]` | LLM analysis (empty if API unavailable) |
| `notices_written` | `List[Path]` | Paths to all output files written |
| `auto_resolved_by_llm` | `int` | Count of items the LLM marked AUTO_RESOLVE |

---

## Data Flow Summary

```
Submission CSV
     │
     ▼ Phase 1
IngestionResult {
  transactions[]       SAP rows (scoped to this submission)
  timecards[]          SAP timecards (scoped to employees in submission)
  documents[]          Receipt/mileage/invoice docs (scoped to note references)
  rate_table[]         Role rates from contract
  contract_clauses[]   Expense rules from contract
  instructions[]       PL email overrides for this cycle
  exceptions[]         Prior resolutions (recurring patterns)
}
     │
     ▼ Phase 2
List[RuleResult] {
  per transaction: status, exception_type, rule_id,
                   override_applied, approved_amount, note
}
     │
     ▼ Phase 3
List[MatchResult] {
  per transaction: matched_doc_id, confidence,
                   reconciled_amount, fx_rate_applied
}
     │
     ▼ Phase 4
ExceptionReport {
  auto_resolved[], escalate_analyst[], escalate_employee[],
  escalate_pl[], blocking[]
}
     │
     ▼ Phase 5a (per-submission watcher trigger)
List[Path] — notices written {
  output/notices/exception-notice-{emp_id}-{stem}__{ts}.md   one per affected employee
  output/analyst-summary-{stem}__{ts}.md                     aggregate analyst view
}

     ▼ Phase 5b (month-end CLI trigger — re-reads completed/)
BuildResult + three canonical project files {
  output/draft-invoice-{project}-{month}.md      Section A (labour+Employee) + Section B (by category)
  output/audit-trail-{project}-{month}.csv       17 cols including submission + employee_name
  output/exceptions-report-{project}-{month}.md  project-wide: all submissions combined
}
```

---

## Log Output Format

Every run appends to `output/billing_agent.log`:

```
========================================================================
RUN  submission-E2210-over-cap-alcohol-2026-04.csv  started 2026-06-18 13:00:00 UTC
========================================================================
2026-06-18 13:00:00  [submission-E2210...]  →  Submission received — Phase 1 starting
2026-06-18 13:00:00  [submission-E2210...]  ✓  Loaded 7 transactions (2 labour, 5 expense, 0 held)
2026-06-18 13:00:00  [submission-E2210...]  →  Scope: 1 employee(s) ['E-2210'], 2 doc(s) ['RC-012','RC-013']
2026-06-18 13:00:00  [submission-E2210...]  ✓  Loaded 6 timecard entries for submission employees
2026-06-18 13:00:00  [submission-E2210...]  ✓  Loaded contract — 6 role rates, 18 expense clauses
2026-06-18 13:00:00  [submission-E2210...]  ✓  Loaded 2 backup documents (0 composite, 0 unreadable, 0 alcohol)
2026-06-18 13:00:00  [submission-E2210...]  ⚠  OVER_CAP: Hotel $310.00 exceeds $275.00 metro cap
2026-06-18 13:00:00  [submission-E2210...]  ⚠  OVER_CAP: Meal $118.00 exceeds $90.00 receipt cap
2026-06-18 13:00:00  [submission-E2210...]  ✗  POLICY_VIOLATION: Alcohol charge $38.00 — rejected §4.3
2026-06-18 13:00:00  [submission-E2210...]  →  OVERRIDE_APPROVE: Meal $118 approved — PL email 2026-04-12
2026-06-18 13:00:00  [submission-E2210...]  →  OVERRIDE_APPROVE: Hotel $310 approved — PL email 2026-04-15
------------------------------------------------------------------------
RESULT  [submission-E2210...]  ✓ SUCCESS  completed 2026-06-18 13:00:01 UTC  (0.8s)
```

Icon reference: `✓` ok · `⚠` warning/exception · `✗` error/rejection · `→` info/transition
