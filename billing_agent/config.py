from pathlib import Path

# ── Directory layout ──────────────────────────────────────────────────────────

BASE_DIR        = Path(__file__).parent.parent
DATA_DIR        = BASE_DIR / "test-data" / "sample-inputs"
SUBMISSIONS_DIR = BASE_DIR / "submissions"

INCOMING_DIR   = SUBMISSIONS_DIR / "incoming"
PROCESSING_DIR = SUBMISSIONS_DIR / "processing"
COMPLETED_DIR  = SUBMISSIONS_DIR / "completed"
FAILED_DIR     = SUBMISSIONS_DIR / "failed"

OUTPUT_DIR     = BASE_DIR / "output"
LOG_FILE       = OUTPUT_DIR / "billing_agent.log"

# ── Watcher settings ──────────────────────────────────────────────────────────

POLL_INTERVAL_SECONDS = 5
ACCEPTED_EXTENSIONS   = {".csv"}
MAX_RETRIES           = 3          # crash-recovery: quarantine after this many restarts

# ── FX rates (receipt-date spot rates, April 2026) ────────────────────────────

FX_RATES = {
    "CAD": 0.74,
}

