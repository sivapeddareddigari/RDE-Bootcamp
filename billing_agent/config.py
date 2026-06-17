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

# ── Watcher settings ──────────────────────────────────────────────────────────

POLL_INTERVAL_SECONDS = 5
ACCEPTED_EXTENSIONS   = {".csv"}

# ── FX rates (receipt-date spot rates, April 2026) ────────────────────────────

FX_RATES = {
    "CAD": 0.74,
}

# ── Contract rule constants ───────────────────────────────────────────────────

LODGING_CAP_METRO      = 275.00
LODGING_CAP_OTHER      = 195.00
MEAL_CAP_RECEIPT       = 90.00
MEAL_CAP_PER_DIEM      = 65.00
MILEAGE_RATE           = 0.67
SUBCONTRACTOR_MARKUP   = 0.08
RECEIPT_THRESHOLD      = 25.00
PRINCIPAL_CAP_PCT      = 0.05
AIR_PREMIUM_ECONOMY_HR = 6
