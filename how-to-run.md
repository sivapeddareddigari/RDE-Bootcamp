# How to Run — Billing Agent

## Prerequisites

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and configure environment (once)
cp .env.example .env   # then edit .env with your API keys and SMTP credentials
```

---

## Configuring the `.env` File

The `.env` file lives at the project root and is **gitignored** — it is never committed. Create it once:

```bash
# Create your local .env (the file is already gitignored)
touch .env
```

Then populate it with the variables below. The system parses the file itself (no `python-dotenv` dependency required). Rules:
- Lines starting with `#` are comments and are ignored.
- Values may optionally be quoted with `"` or `'` — quotes are stripped automatically.
- Variables already set in the shell environment are **not** overridden by `.env`.

### Full variable reference

```ini
# ── Anthropic API (LLM agents — Phase 6) ──────────────────────────────────────
# Leave blank or omit to run in deterministic fallback mode (no API calls).
ANTHROPIC_API_KEY=sk-ant-api03-...

# ── Email ──────────────────────────────────────────────────────────────────────
# Set to true to send real emails. false (default) logs what would be sent
# without making any SMTP connection — safe for development and CI.
EMAIL_ENABLED=false

# SMTP server — defaults shown are for Gmail with STARTTLS.
# For Outlook / Office 365 use: smtp.office365.com  port 587
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587

# Gmail login and app password.
# For Gmail you MUST use an App Password, not your account password.
# Generate one at: Google Account → Security → 2-Step Verification → App passwords
SMTP_USER=you@gmail.com
SMTP_PASSWORD=xxxx-xxxx-xxxx-xxxx

# From header displayed in received emails.
# EMAIL_FROM_ADDR defaults to SMTP_USER when omitted.
EMAIL_FROM_NAME=Billing Agent
EMAIL_FROM_ADDR=you@gmail.com
```

### Variable-by-variable guide

| Variable | Required | Default | Effect when missing / blank |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | No | _(none)_ | System runs in deterministic fallback mode — no LLM calls, template notices only |
| `EMAIL_ENABLED` | No | `false` | Emails are logged but not sent — pipeline continues normally |
| `SMTP_HOST` | Only if `EMAIL_ENABLED=true` | `smtp.gmail.com` | — |
| `SMTP_PORT` | Only if `EMAIL_ENABLED=true` | `587` | — |
| `SMTP_USER` | Only if `EMAIL_ENABLED=true` | _(none)_ | Email skipped with a warning; pipeline unaffected |
| `SMTP_PASSWORD` | Only if `EMAIL_ENABLED=true` | _(none)_ | Email skipped with a warning; pipeline unaffected |
| `EMAIL_FROM_NAME` | No | `Billing Agent` | From header shows `Billing Agent <...>` |
| `EMAIL_FROM_ADDR` | No | same as `SMTP_USER` | From address defaults to the login address |

### Minimal `.env` for a local dev run (no emails, no LLM)

```ini
EMAIL_ENABLED=false
```

### `.env` for LLM mode, emails disabled

```ini
ANTHROPIC_API_KEY=sk-ant-api03-...
EMAIL_ENABLED=false
```

### `.env` for full production mode (LLM + email)

```ini
ANTHROPIC_API_KEY=sk-ant-api03-...

EMAIL_ENABLED=true
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASSWORD=xxxx-xxxx-xxxx-xxxx
EMAIL_FROM_NAME=Billing Agent
EMAIL_FROM_ADDR=you@gmail.com
```

> **Gmail app password**: go to [Google Account → Security → 2-Step Verification → App passwords](https://myaccount.google.com/apppasswords), create an app named "Billing Agent", and paste the 16-character password as `SMTP_PASSWORD`. Your main account password will not work.

---

## Feature Reference

| # | What it does | Command | Key output |
|---|---|---|---|
| 1 | **Watch folder for submissions** (primary mode) | `python3 billing_agent/main.py` | `output/notices/`, `output/analyst-summary-*.md`, `output/billing_agent.log` |
| 2 | **Drop a submission for processing** | `cp test-data/sample-inputs/submissions/<file>.csv submissions/incoming/` | Picked up by the watcher automatically |
| 3 | **Generate month-end invoice** from completed folder | `python3 -m billing_agent.invoice_run --project PRJ-NS-7421 --month 2026-04` | `output/draft-invoice-*.md`, `output/exceptions-report-*.csv`, `output/audit-trail-*.csv` |
| 4 | **Run full unit test suite** | `python3 run_tests.py` | Console + `output/Unit_test_runs/<timestamp>.txt` |
| 5 | **Run tests with pytest directly** | `pytest tests/ -v` | Console only |
| 6 | **Sync contract rules** (after editing `contract-001.md`) | `python3 billing_agent/rules/sync_rules.py` | `billing_agent/rules/data/*.json` |

---

## Step-by-step: Process a Submission (LLM mode)

1. **Start the watcher** in one terminal:
   ```bash
   python3 billing_agent/main.py
   ```

2. **Drop a submission CSV** into the incoming folder:
   ```bash
   cp test-data/sample-inputs/submissions/submission-E4501-principal-cap-2026-04.csv \
      submissions/incoming/
   ```

3. The watcher picks it up automatically (poll interval: 5 s). Watch the log:
   ```bash
   tail -f output/billing_agent.log
   ```

4. Once processed, the file moves to `submissions/completed/` and outputs appear in `output/`.

### Sample submissions and what they test

| File | Scenario | Best for showcasing |
|---|---|---|
| `submission-E1041-clean-2026-04.csv` | All transactions clean — no exceptions | Happy path |
| `submission-E2210-over-cap-alcohol-2026-04.csv` | Meal cap exceeded + alcohol charge | Hard rejection, cap enforcement |
| `submission-E3055-hold-miscoded-2026-04.csv` | SAP hold item + miscoded activity code | Hold routing, EMPLOYEE action required |
| `submission-E4501-principal-cap-2026-04.csv` | Principal hours at senior rate + PL email authorising | **LLM AUTO_RESOLVE** via PL instruction |
| `submission-E5102-subcontractor-composite-2026-04.csv` | Subcontractor missing markup + composite document | ANALYST routing, doc issues |
| `submission-E7702-currency-personal-2026-04.csv` | Foreign currency receipt + personal item | Currency flag, hard rejection |

---

## Step-by-step: Generate the Month-end Invoice

Run this **after** all employee submissions for the month have been processed (files are in `submissions/completed/`):

```bash
python3 -m billing_agent.invoice_run \
  --project PRJ-NS-7421 \
  --month   2026-04
```

Optional: point at a different completed folder:
```bash
python3 -m billing_agent.invoice_run \
  --project PRJ-NS-7421 \
  --month   2026-04 \
  --submissions-dir /path/to/completed
```

**Outputs written to `output/`:**

| File | Contents |
|---|---|
| `draft-invoice-PRJ-NS-7421-2026-04.md` | Line-item invoice ready for analyst review |
| `exceptions-report-PRJ-NS-7421-2026-04.csv` | All flagged items with routing and block status |
| `audit-trail-PRJ-NS-7421-2026-04.csv` | Transaction-level rule outcomes for every submission |

An email with all three attachments is sent to the billing analyst(s) listed in `contacts.yaml`.

**Exit codes:**

| Code | Meaning |
|---|---|
| `0` | Invoice generated, no blocking items |
| `1` | Argument error or no submissions found |
| `2` | Invoice generated but blocking items remain — review exceptions report |

---

## LLM Mode vs Deterministic Fallback — Full Comparison

| Dimension | LLM mode (Anthropic API key present) | Deterministic fallback (no API key) |
|---|---|---|
| **Trigger** | `ANTHROPIC_API_KEY` is set and non-blank in `.env` or environment | Key is absent, blank, or API call fails |
| **Orchestration** | Billing Supervisor Agent runs a Claude tool-use loop (`claude-haiku-4-5-20251001`) | Pipeline runs directly in Python — no API call |
| **Exception reasoning** | Exception Reasoning Agent (Claude) reads PL instructions + decision memory and reasons about each unresolved item | Not called — all unresolved items get generic template text |
| **Notice text** | Personalised, transaction-specific instructions written by Claude referencing the exact vendor, date, and SAP action | Generic template from `_ACTION` dict in `notice_writer.py` (e.g. "Upload the missing receipt to SAP and resubmit") |
| **AUTO_RESOLVE** | LLM can mark items `AUTO_RESOLVE` when a prior PL approval or recurring policy covers them — those items skip the employee action-required sections | Never — every unresolved item appears in an action-required section regardless of prior approvals |
| **"Handled automatically" section** | Appears in employee notice when LLM auto-resolves one or more items | Absent |
| **Decision Memory read** | Exception agent reads `billing_agent/stores/data/decision_memory.json` to find matching prior cases | Not read |
| **Instruction Store read** | Exception agent reads per-project PL instructions to find standing approvals | Not read |
| **API cost** | One call to Exception Reasoning Agent + one Supervisor tool-use loop (Haiku model) | Zero |
| **Speed** | Slower — depends on Anthropic API latency (~3–8 s per submission) | Fast — pure Python, sub-second |
| **Log signature** | `Supervisor agent — pipeline complete` | `Supervisor — falling back to deterministic pipeline` |
| **`result.analyses`** | List of `ExceptionAnalysis` objects (one per unresolved item) | Empty list `[]` |
| **`result.auto_resolved_by_llm`** | Count of AUTO_RESOLVE recommendations | Always `0` |
| **Best for** | Production / demo — shows contextual reasoning and reduces analyst workload | Offline dev, CI tests, environments without API access |

### Which sample submission best shows the difference

Use `submission-E4501-principal-cap-2026-04.csv`:

- The submission has a PRINCIPAL_HOURS exception where the employee billed at senior rate.
- A PL email in `pm-instructions/` explicitly authorises billing at that rate for this employee.
- **LLM mode**: Exception agent reads the PL email, recommends `AUTO_RESOLVE`, and the employee notice shows a "Handled automatically" section with no action required.
- **Fallback mode**: The same item appears under "Action required — please correct and resubmit" with the generic template text — the PL approval is ignored because the instruction store is never read.

---

## Simulating the Deterministic Fallback

### Option A — Blank the key in `.env` (recommended)

```ini
# .env
ANTHROPIC_API_KEY=          # leave blank → forces fallback
```

Restore LLM mode by putting your key back.

### Option B — Unset for a single run

```bash
ANTHROPIC_API_KEY= python3 billing_agent/main.py
```

### Option C — Force fallback in a pytest test

```python
from unittest.mock import patch
from billing_agent.agents.supervisor import run as supervisor_run

def test_fallback(contacts, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with patch("billing_agent.agents.supervisor._resolve_api_key", return_value=""), \
         patch("billing_agent.agents.exception_agent._resolve_api_key", return_value=""), \
         patch("anthropic.Anthropic", side_effect=Exception("no key")):
        result = supervisor_run(submission_path, contacts)
    assert result.analyses == []
    assert result.auto_resolved_by_llm == 0
```

### Verifying which mode ran

```bash
grep -E "fallback|LLM|auto-resolved|Supervisor" output/billing_agent.log | tail -20
```

LLM mode log lines look like:
```
✔  Supervisor agent — pipeline complete
✔  Exception agent — 2 item(s) analysed  (1 auto-resolved by LLM, 1 escalating)
```

Fallback mode log lines look like:
```
⚠  Supervisor — falling back to deterministic pipeline
```

---

## Output Folder Layout

```
output/
├── billing_agent.log                             # append-mode run log (all runs)
├── notices/
│   └── exception-notice-<EMP>-<stem>__<ts>.md   # one per employee with exceptions
├── analyst-summary-<stem>__<ts>.md               # per-submission analyst view
├── draft-invoice-<project>-<month>.md            # month-end invoice (invoice_run only)
├── exceptions-report-<project>-<month>.csv       # month-end exception roll-up
├── audit-trail-<project>-<month>.csv             # month-end transaction audit
└── Unit_test_runs/
    └── <timestamp>.txt                           # saved pytest output
```
