# Execution Flow: Agentic Billing Review System

**Project:** Meridian Atlas Partners — Coastal Greenway (PRJ-NS-7421)  
**Last updated:** 2026-06-18 (Phase 4 complete)

This document describes what happens step-by-step when a submission file enters the system, how each phase transforms the data, and what flows into the next phase.

---

## System Overview

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
     │                                                                        │
     ▼                                                                        │
[PHASE 2] Rule Engine ◄──── rules/data/*.json ◄──── contract-001.md          │
     │                                                                        │
     ▼                                                                        │
[PHASE 3] Matching & Reconciliation                                           │
     │                                                                        │
     ▼                                                                        │
[PHASE 4] Exception Detection & Triage ◄──── PL emails, prior exceptions     │
     │                                                                        │
     ▼                                                                        │
[PHASE 5] Invoice Builder & Output Generation                                 │
     │                                                                        │
     ▼                                                                        │
[PHASE 6] Agentic Orchestration (Claude API)  ◄───────────────────────────── ┘
     │
     ▼
submissions/completed/         ← timestamped archive
output/billing_agent.log       ← full run trace
output/draft-invoice.md        ← billable invoice
output/exceptions-report.md    ← flagged items + resolutions
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
  │     ├─► [Phase 5]  invoice_builder.build()    ← stub today
  │     └─► [Phase 6]  supervisor.run()           ← stub today
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

## Phase 5 — Invoice Builder & Output Generation  *(not yet built)*

**Entry point:** `invoice_builder.build(inputs, rule_results, match_results, exception_report)` in `billing_agent/output/invoice_builder.py`  
**Input:** All prior phase outputs  
**Output:** Draft invoice + audit trail + exception report + analyst worksheet

### Execution steps (planned)

```
invoice_builder.build(...)
  │
  ├─1─ filter to billable transactions
  │      include: status == APPROVE or AUTO_RESOLVED
  │      exclude: REJECT, unresolved MISSING_BACKUP, ALCOHOL, MISCODED_LABOUR
  │
  ├─2─ build invoice sections
  │      Section A — Labour
  │        group by role_code, sum hours × approved rate
  │        exclude: miscoded lines, unreleased holds
  │        include: released holds (RELEASE_HOLD override applied)
  │        travel time: flag at 50% rate, cap 8hrs/direction
  │      │
  │      Section B — Expenses by category
  │        LODGING       group by trip, one line per trip (PL standing instruction)
  │        MEALS         receipt-based or per diem, not both same day
  │        AIR TRAVEL    economy/premium economy as reconciled
  │        GROUND        rideshare, transit, mileage at $0.67/mile
  │        SUBCONTRACTOR cost line + separate 8% markup line (contract §5)
  │
  ├─3─ apply adjustments
  │      alcohol exclusion   → split amount out of approved receipt total
  │      FX conversion       → CAD/EUR amounts at receipt-date spot rate
  │      subcontractor       → add markup line separately on invoice
  │      over-cap approved   → bill actual with SAP note citing PL override
  │
  ├─4─ write output files
  │      output/draft-invoice.md        final invoice in SAP format
  │      output/audit-trail.csv         per-transaction: rule fired, decision, amount
  │      output/exceptions-report.md   all exceptions with resolution or escalation
  │      output/kpi-summary.md          6 KPIs (cycle time, exception rate, etc.)
  │      output/analyst-worksheet.md    open items requiring human judgment
  │
  └─5─ validate totals
         labour subtotal, expense subtotal, grand total
         assert: alcohol = $0, lounge = $0, subcontractor includes markup
         log to billing_agent.log
```

---

## Phase 6 — Agentic Orchestration  *(not yet built)*

**Entry point:** `supervisor.run(submission_path)` in `billing_agent/agents/supervisor.py`  
**Replaces:** The linear stub calls in `process_submission()` with an LLM-driven loop

### Execution steps (planned)

```
supervisor.run(submission_path)
  │
  ├─1─ Billing Supervisor Agent (Claude API)
  │      model: claude-sonnet-4-6
  │      tools: [load_inputs, run_rules, match_docs, detect_exceptions,
  │              build_invoice, get_pl_instructions, write_audit_trail]
  │      system_prompt: BILLING_SUPERVISOR_SYSTEM_PROMPT
  │      │
  │      └─► agent decides tool call order based on what it sees
  │            → calls load_inputs()     → IngestionResult
  │            → calls run_rules()       → List[RuleResult]
  │            → calls match_docs()      → List[MatchResult]
  │            → calls detect_exceptions()
  │                  │
  │                  └─► if unresolved exceptions present:
  │                        Exception Reasoning Agent (nested call)
  │                          tools: [query_decision_memory, query_instruction_store,
  │                                  search_prior_exceptions]
  │                          → returns { auto_resolved, escalate }
  │            → calls build_invoice()   → output files
  │
  ├─2─ Re-trigger loop
  │      if escalate_employee is non-empty:
  │        → notify employee via Teams/email (out of scope for MVP)
  │        → employee corrects in SAP
  │        → drops corrected CSV into incoming/
  │        → full pipeline re-runs from Phase 1
  │        → already-resolved tx_ids skipped (idempotency by tx_id tracking)
  │
  └─3─ Knowledge store updates
         Decision Memory: new resolution → append to resolutions.csv (recurring=Y if PL confirms)
         Instruction Store: new PL preference → append to sample-emails.md equivalent
```

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
     ▼ Phase 5
Output files {
  draft-invoice.md      billable lines, approved amounts
  audit-trail.csv       per-line rule citation
  exceptions-report.md  flagged items + resolution
  analyst-worksheet.md  open items for human review
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
