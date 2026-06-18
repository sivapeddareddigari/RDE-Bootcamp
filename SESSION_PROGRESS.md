# Session Progress — Agentic Billing Review System

**Project:** Meridian Atlas Partners — RDE Bootcamp  
**Repo:** https://github.com/sivapeddareddigari/RDE-Bootcamp  
**Active branch:** `develop`  
**Last session date:** 2026-06-17  

---

## Quick Start on a New Machine

```bash
# 1. Clone the repo
git clone https://github.com/sivapeddareddigari/RDE-Bootcamp.git
cd RDE-Bootcamp
git checkout develop

# 2. Install dependencies (Python 3.10+)
pip install -r requirements.txt

# 3. Start the watcher
python -m billing_agent.main

# 4. In a second terminal, drop a test file to trigger a run
cp test-data/sample-inputs/transactions/unbilled-2026-04.csv submissions/incoming/
# or for exception testing:
cp test-data/sample-inputs/transactions/test-exceptions-2026-05.csv submissions/incoming/

# 5. View the run log (updates live as the agent runs)
cat output/runs/<latest>.md
```

---

## What Was Decided in This Session

### Architecture decisions

| Decision | Outcome |
|----------|---------|
| Multi-agent vs single agent | Two LLM agents only — Billing Supervisor (orchestrator) + Exception Reasoning agent. Three pipelines stay deterministic Python. |
| Design.svg confirmed this | Supervisor labelled "LLM agent" (purple). Exception Reasoning labelled "LLM agent" (orange). Document, Reconciliation, Package pipelines shown in green (deterministic). |
| Trigger mechanism | Drop folder watch — agent polls `submissions/incoming/` every 5s. File moves through `processing/` → `completed/` or `failed/`. |
| Output visibility | Live markdown run log written to `output/runs/<stem>__<timestamp>.md`, flushed after every step. |

### Key design document findings

- **`design/agentic_billing_review_solution_design.md`** — full technical spec, 8-step workflow
- **`design/existing_process_and_pain_points.md`** — 7 pain points driving the solution
- **`design.svg`** — authoritative architecture diagram; two LLM agents, three deterministic pipelines, two knowledge stores (Decision Memory + Instruction Store), re-trigger loop on novel exceptions
- **`appendix-sample-data/restricted/expected-invoice.md`** — ground truth answer for cycle 2026-04 (~$23,910–$23,980, 22 verification checkpoints)

---

## What Has Been Built

### Commit history

| Commit | What it did |
|--------|-------------|
| `172d2d2` | Initial commit — all design docs, diagrams, test data |
| `a2b9542` | Added `IMPLEMENTATION_PLAN.md` with full phased plan |
| `10eea00` | **Drop folder watcher** — `billing_agent/main.py`, `config.py`, `submissions/` structure |
| `e078864` | **Phase 1 ingestion** — all data models + 5 loaders, verified against test data |
| `24a34d6` | Fix `.gitignore` for `submissions/.gitkeep` files |
| `bb24d29` | **Test exceptions file** — `test-exceptions-2026-05.csv` with one of each exception type |
| `02efcce` | **RunLogger** — live markdown run document per submission |

---

### File tree — what exists now

```
RDE-Bootcamp/
├── billing_agent/
│   ├── __init__.py
│   ├── config.py                   # paths, constants, FX rates, rule caps
│   ├── main.py                     # drop folder watcher + pipeline orchestrator
│   ├── run_logger.py               # live markdown run document logger
│   ├── models/
│   │   ├── transaction.py          # Transaction, TimecardEntry dataclasses
│   │   ├── document.py             # ReceiptDocument, LineItem dataclasses
│   │   ├── contract.py             # ContractClause, RateEntry dataclasses
│   │   └── instruction.py          # ProjectInstruction, ExceptionCase dataclasses
│   └── ingestion/
│       ├── loader.py               # load_inputs() — single entry point, returns IngestionResult
│       ├── sap_loader.py           # parses unbilled CSV + timecard CSV
│       ├── contract_parser.py      # parses contract-001.md → rate table + clauses
│       ├── doc_parser.py           # parses RC-*/ML-*/VI-* markdown receipts
│       ├── email_parser.py         # parses PL emails → typed override instructions
│       └── exception_loader.py     # parses resolutions.csv → prior exception patterns
│
├── submissions/
│   ├── incoming/       ← drop CSV here to trigger a run
│   ├── processing/     ← in-flight (auto-cleared on completion)
│   ├── completed/      ← timestamped copy of processed files
│   └── failed/         ← timestamped copy of failed files
│
├── output/
│   └── runs/           ← live markdown run logs (one per submission)
│
├── test-data/sample-inputs/
│   ├── transactions/
│   │   ├── unbilled-2026-04.csv         # real SAP extract — 50 transactions
│   │   └── test-exceptions-2026-05.csv  # test file — one of each exception type
│   ├── sap-outputs/timecards-2026-04.csv
│   ├── contracts/contract-001.md
│   ├── documents/                       # 15 backup docs (RC-*, ML-*, VI-*)
│   ├── pm-instructions/sample-emails.md
│   └── prior-exceptions/resolutions.csv
│
├── design/
│   ├── agentic_billing_review_solution_design.md
│   ├── existing_process_and_pain_points.md
│   ├── proposed_solution.md
│   └── diagrams/  (agent_architecture.png, review_workflow.png, policy_kb.png)
│
├── design.svg                      # authoritative architecture diagram
├── IMPLEMENTATION_PLAN.md          # full phased plan with progress tracker
├── SESSION_PROGRESS.md             # this file
└── requirements.txt                # anthropic>=0.40.0
```

### Ingestion — verified numbers (Phase 1)

Running `load_inputs()` against the real test data produces:

| Source | Count |
|--------|-------|
| Transactions | 50 (23 labour, 27 expense, 3 held) |
| Timecard entries | 23 |
| Backup documents | 15 (1 composite, 1 unreadable, 2 with alcohol flagged) |
| Role rates | 6 (ENG1–ADMIN, $95–$320/hr) |
| Contract clauses | 18 |
| PL instructions | 5 (STANDING, OVERRIDE_APPROVE, OVERRIDE_REJECT, RELEASE_HOLD, CONFIRM_MARKUP) |
| Prior exceptions | 10 |

### Run logger — output format

Each run writes `output/runs/<submission>__<timestamp>.md`:

```markdown
# Billing Review Run

**Submission:** `unbilled-2026-04.csv`
**Started:** 2026-06-17 21:31:21 UTC

| Time | Status | Step |
|------|--------|------|
| `21:31:21` | → | Submission received — unbilled-2026-04.csv |
| `21:31:21` | → | Phase 1 — loading all inputs |
| `21:31:21` | ✓ | Loaded 50 transactions (23 labour, 27 expense, 3 held) |
| `21:31:21` | ✓ | Loaded 23 timecard entries |
| `21:31:21` | ✓ | Loaded contract — 6 role rates, 18 expense clauses |
| `21:31:21` | ✓ | Loaded 15 backup documents |
| `21:31:21` | ✓ | Loaded 5 PL instructions |
| `21:31:21` | ✓ | Loaded 10 prior exception patterns |
| `21:31:21` | ✓ | Ingestion complete — all inputs loaded and normalised |

**Result:** ✓ SUCCESS
```

Status icons: `✓` ok · `⚠` flagged/exception · `✗` hard rejection · `→` phase transition · `○` skipped

---

## What Still Needs to Be Built

### Phase 2 — Rule Engine

**File to create:** `billing_agent/rules/`

The rule engine evaluates each transaction against contract rules in strict priority order:
1. Contract rules (hard caps — alcohol always rejected)
2. Project preferences (per-diem on coastal sites, hotel grouping)
3. PL cycle overrides (email-based approvals for this cycle)
4. Historical exception patterns

**Key rules to implement:**

| Rule | Value | Source |
|------|-------|--------|
| Lodging cap — metro | $275/night | Contract §4.2 |
| Lodging cap — elsewhere | $195/night | Contract §4.2 |
| Meal cap — receipt | $90/day | Contract §4.3 |
| Meal cap — per diem | $65/day (not stackable) | Contract §4.3 |
| Alcohol | Always rejected — no exceptions | Contract §4.3 |
| Air — economy | Flights < 6 hours | Contract §4.4 |
| Mileage rate | $0.67/mile | Contract §4.5 |
| Subcontractor markup | Cost + 8% | Contract §5.1 |
| Principal cap | Max 5% monthly hours | Project preference |
| Receipt threshold | Required for expenses > $25 | Contract §4.1 |

**Expected output per transaction:** `RuleVerdict(tx_id, status, rule_cited, adjusted_amount, note)`

**Run logger lines to add:**
```
✓ | TX-2026-04-0001  LABOR   $700.00 — APPROVED  (ENG2 standard rate)
⚠ | TX-2026-04-0038  EXPENSE $310.00 — OVER_CAP  (lodging cap $195 elsewhere)
✗ | TX-2026-04-0008  EXPENSE  $38.00 — REJECTED   (alcohol — non-reimbursable §4)
```

---

### Phase 3 — Document Matching & Reconciliation

**File to create:** `billing_agent/matching/`

Match each expense transaction to its backup document:

1. Normalise vendor names and employee IDs across sources
2. Score candidates by: date proximity (±3 days), amount proximity (±10%), vendor similarity
3. Handle special cases:
   - **RC-016 composite** — split into 3 sub-receipts, match individually
   - **RC-015 foreign currency** — convert CAD → USD at receipt-date FX rate (CAD 0.74)
   - **RC-018 unreadable** — flag as MISSING_BACKUP

**Known complex matches from test data:**

| Transaction | Document | Issue |
|-------------|----------|-------|
| Team dinner $46.20 | RC-007 ($126.85 total) | Alcohol $18 excluded; amount mismatch |
| Hotel $310 | RC-012 | Over cap; PL approved |
| Client dinner $118 | RC-013 | Over cap; PL approved as working dinner |
| Lounge $55 | RC-014 | PL rejected |
| FX ~$40 USD | RC-015 (CAD $52) | Convert at receipt date |
| $189.45 composite | RC-016 | Split 3 receipts: $33 + $102.50 + $53.95 |
| Laundry $22 | RC-017 | Personal item |
| Workshop $1,147 | VI-001 | SAP room $485 vs vendor $490; AV $45 missing |
| Subcontractor $2,400 | VI-002 | Needs 8% markup → $2,592 |

---

### Phase 4 — Exception Detection & Triage

**File to create:** `billing_agent/exceptions/`

Detect all exception types:

| Type | Detection logic |
|------|----------------|
| `AMOUNT_MISMATCH` | `abs(tx.amount - doc.total_amount) > 0.01` |
| `MISSING_BACKUP` | No matched document AND `tx.amount > 25` |
| `POLICY_VIOLATION` | `doc.has_alcohol` OR personal item keyword |
| `OVER_CAP` | `tx.amount > rule_cap` AND no PL override |
| `MISCODED_LABOUR` | Training/admin keywords in description |
| `COMPOSITE_DOCUMENT` | `doc.is_composite` |
| `CURRENCY_MISMATCH` | `doc.currency != "USD"` |
| `HOLD_ITEM` | `tx.hold_flag == True` |
| `SUBCONTRACTOR_MARKUP` | VI doc with no markup applied |

Resolution routing:
1. PL cycle instruction resolves it → apply override
2. Historical pattern matches → apply precedent
3. Neither → escalate to analyst

---

### Phase 5 — Invoice Builder & Outputs

**File to create:** `billing_agent/output/`

**Files to produce in `output/`:**

| File | Description |
|------|-------------|
| `draft-invoice.md` | Final validated invoice in SAP format |
| `audit-trail.csv` | Every transaction with rule citation and decision |
| `exceptions-report.md` | All flagged items with resolution status |
| `kpi-summary.md` | 6 KPI metrics vs pitch targets |
| `analyst-worksheet.md` | Open items requiring human judgment |

**Expected totals for 2026-04 cycle (ground truth):**
- Labour subtotal: **$17,027.50**
- Subcontractor (with markup): **$2,592.00**
- Invoice total: **~$23,910–$23,980**
- Alcohol: **$0.00** (100% excluded)
- Lounge: **$0.00** (PL rejected)

---

### Phase 6 — Agentic Orchestration (Claude API)

**Files to create:** `billing_agent/agents/`

Two LLM agents:

**Billing Supervisor** (`agents/supervisor.py`):
```python
client = anthropic.Anthropic()
response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=4096,
    tools=[load_inputs, run_rule_check, match_documents,
           flag_exception, apply_pl_override, build_invoice_line, generate_report],
    system=BILLING_SUPERVISOR_SYSTEM_PROMPT,
    messages=[{"role": "user", "content": trigger_message}]
)
```

**Exception Reasoning Agent** (`agents/exception_agent.py`):
- Invoked only when reconciliation returns flagged items
- Reads Decision Memory store + Instruction store
- Returns `{"auto_resolved": [...], "escalate": [...]}`
- Novel exceptions → flag to employee → re-trigger loop (idempotent)

**Knowledge stores** (`billing_agent/stores/`):
- `decision_memory.py` — pattern library, grows each cycle
- `instruction_store.py` — PL rules per project

---

### Phase 7 — Tests

**File to create:** `tests/`

| Test file | Covers |
|-----------|--------|
| `test_ingestion.py` | All 50 transactions, 23 timecards, 15 docs load correctly |
| `test_rules.py` | Each rule vs known inputs (alcohol, lodging cap, per diem) |
| `test_matching.py` | All 9 complex doc↔transaction matches |
| `test_currency.py` | CAD→USD conversion for RC-015 |
| `test_invoice.py` | End-to-end: all 22 checkpoints from `expected-invoice.md` |

---

## Key Files to Read First on a New Machine

1. **[design.svg](design.svg)** — Architecture diagram (open in browser or VS Code SVG viewer)
2. **[design/agentic_billing_review_solution_design.md](design/agentic_billing_review_solution_design.md)** — Full technical spec
3. **[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)** — Phased plan with progress tracker
4. **[billing_agent/main.py](billing_agent/main.py)** — Entry point, pipeline stub with TODOs
5. **[billing_agent/ingestion/loader.py](billing_agent/ingestion/loader.py)** — `load_inputs()` and `IngestionResult`
6. **[appendix-sample-data/restricted/expected-invoice.md](appendix-sample-data/restricted/expected-invoice.md)** — Ground truth answer with 22 checkpoints

---

## Environment Notes

- **Python version:** 3.10+
- **Only dependency:** `anthropic>=0.40.0` (not needed until Phase 6)
- **No database** — all state is files (CSVs, markdown, JSON stores)
- **No Docker/cloud** — runs fully local
- **GitHub credentials:** Set `GITHUB_USER` and `GITHUB_TOKEN` as env vars (see Claude Code settings or `.env`)

---

## Next Step

**Build Phase 2 — Rule Engine.**

Start with `billing_agent/rules/rule_engine.py`. The entry point should be:

```python
def run(inputs: IngestionResult) -> List[RuleVerdict]:
    ...
```

Where `RuleVerdict` is a dataclass with `tx_id`, `status` (APPROVE/HOLD/REJECT/OVER_CAP),  
`rule_cited`, `adjusted_amount`, and `note`. Each verdict also calls `run_logger.step()`.
