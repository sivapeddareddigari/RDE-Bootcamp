"""Shared fixtures and paths for all billing agent test modules."""

from pathlib import Path
import pytest

DATA_DIR        = Path("test-data/sample-inputs")
SUBMISSIONS_DIR = DATA_DIR / "submissions"
DOCS_DIR        = DATA_DIR / "documents"
SAP_DIR         = DATA_DIR / "sap-outputs"
TIMECARD_PATH   = SAP_DIR  / "timecards-2026-04.csv"

# One fixture per submission scenario
SUBMISSION_CLEAN         = SUBMISSIONS_DIR / "submission-E1041-clean-2026-04.csv"
SUBMISSION_OVER_CAP      = SUBMISSIONS_DIR / "submission-E2210-over-cap-alcohol-2026-04.csv"
SUBMISSION_HOLD_MISCODED = SUBMISSIONS_DIR / "submission-E3055-hold-miscoded-2026-04.csv"
SUBMISSION_PRINCIPAL_CAP = SUBMISSIONS_DIR / "submission-E4501-principal-cap-2026-04.csv"
SUBMISSION_CURRENCY      = SUBMISSIONS_DIR / "submission-E7702-currency-personal-2026-04.csv"
SUBMISSION_SUBCON        = SUBMISSIONS_DIR / "submission-E5102-subcontractor-composite-2026-04.csv"
