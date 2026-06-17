# Implementation Plan: Agentic Billing Review System

**Project:** Meridian Atlas Partners — RDE Bootcamp  
**Client:** Northstar Civic Group  
**Engagement:** Coastal Greenway Feasibility Study (PRJ-NS-7421)  
**Target invoice cycle:** 2026-04  

---

## Progress Tracker

| Phase | Description | Status | Commit |
|-------|-------------|--------|--------|
| Trigger | Drop folder watcher | **Done** | `10eea00` |
| Phase 1 | Scaffolding & ingestion | **Done** | `(current)` |
| Phase 2 | Rule engine | Pending | — |
| Phase 3 | Matching & reconciliation | Pending | — |
| Phase 4 | Exception detection & triage | Pending | — |
| Phase 5 | Invoice builder & outputs | Pending | — |
| Phase 6 | Agentic orchestration (Claude API) | Pending | — |
| Phase 7 | Testing | Pending | — |

**Phase 1 ingestion results (verified against test data):**
- 50 transactions loaded (23 labour, 27 expense, 3 held)
- 23 timecard entries
- 15 backup documents (receipts, mileage logs, vendor invoices)
- 6 rate table entries
- 18 contract clauses
- 5 PL email instructions
- 10 prior exception cases

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

```
billing_agent/
├── main.py                        # Entry point / orchestrator trigger
├── config.py                      # Paths, FX rates, constants
├── models/
│   ├── transaction.py             # Transaction, Expense, LabourEntry dataclasses
│   ├── document.py                # ReceiptDocument, VendorInvoice, MileageLog
│   ├── contract.py                # ContractClause, RateTable
│   └── instruction.py            # ProjectInstruction, ExceptionCase
├── ingestion/
│   ├── sap_loader.py              # Parse unbilled-2026-04.csv + timecards CSV
│   ├── contract_parser.py         # Parse contract-001.md → ContractClause objects
│   ├── doc_parser.py              # Parse RC-*/ML-*/VI-* markdown receipts
│   ├── email_parser.py            # Parse sample-emails.md → ProjectInstruction list
│   └── exception_loader.py       # Parse resolutions.csv → ExceptionCase list
├── rules/
│   ├── rule_engine.py             # Central rule dispatcher (priority-ordered)
│   ├── lodging_rules.py           # $275 metro / $195 elsewhere caps
│   ├── meal_rules.py              # $90/day receipt vs $65 per diem
│   ├── travel_rules.py            # Economy/premium economy, mileage rate
│   ├── labour_rules.py            # Principal cap, miscoded time, overtime
│   └── subcontractor_rules.py    # 8% markup
├── matching/
│   ├── matcher.py                 # Link transactions → documents
│   ├── id_normalizer.py           # Resolve employee/vendor IDs across sources
│   └── currency.py                # FX conversion (CAD → USD at receipt date)
├── exceptions/
│   ├── detector.py                # Identify all exception types
│   └── resolver.py                # Apply PL overrides & historical patterns
├── stores/
│   ├── decision_memory.py         # R/W pattern library (grows each cycle)
│   └── instruction_store.py      # R/W PL rules per project
├── agents/
│   ├── supervisor.py              # LLM agent — orchestrates pipeline sequence
│   └── exception_agent.py        # LLM agent — pattern lookup + novel case routing
├── output/
│   ├── invoice_builder.py         # Assemble final invoice lines
│   ├── audit_trail.py             # Record per-line decision rationale
│   └── report_generator.py       # Summary report, KPI dashboard
└── tests/
    ├── test_ingestion.py
    ├── test_rules.py
    ├── test_matching.py
    ├── test_currency.py
    └── test_invoice.py            # End-to-end against expected-invoice.md
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

| Loader | Input file | Output |
|--------|-----------|--------|
| `sap_loader.py` | `unbilled-2026-04.csv` + `timecards-2026-04.csv` | `List[Transaction]`, `List[TimecardEntry]` |
| `contract_parser.py` | `contract-001.md` | `List[ContractClause]` |
| `doc_parser.py` | All `RC-*`, `ML-*`, `VI-*` files | `List[ReceiptDocument]` |
| `email_parser.py` | `sample-emails.md` | `List[ProjectInstruction]` |
| `exception_loader.py` | `resolutions.csv` | `List[ExceptionCase]` |

**Input counts:** 52 transactions, 23 timecards, 19 backup documents, 5 PL emails, 11 historical patterns.

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
