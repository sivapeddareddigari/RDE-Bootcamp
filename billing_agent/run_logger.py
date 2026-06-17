"""
RunLogger — writes a live markdown run document to output/runs/ as the
agent executes. Each step is one line: timestamp, status icon, description.

Usage from any module:
    from billing_agent import run_logger
    run_logger.step("Loaded 50 transactions (23 labour, 27 expense)")
    run_logger.step("OVER_CAP: Hotel $310 exceeds $275 metro cap — TX-2026-05-0005", "warn")
    run_logger.step("ALCOHOL: Drinks $38 rejected — non-reimbursable per §4", "error")

Status values:
    ok    ✓  — step completed successfully
    warn  ⚠  — flagged / exception detected
    error ✗  — hard policy violation / rejection
    info  →  — informational / phase transition
    skip  ○  — item skipped / not applicable
"""

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from billing_agent.config import OUTPUT_DIR

log = logging.getLogger(__name__)

_ICONS = {
    "ok":    "✓",
    "warn":  "⚠",
    "error": "✗",
    "info":  "→",
    "skip":  "○",
}

_current: Optional["RunLogger"] = None


# ── Public API ────────────────────────────────────────────────────────────────

def init_run(submission_name: str) -> "RunLogger":
    """Create a new RunLogger for a submission. Call once at the start of each run."""
    global _current
    _current = RunLogger(submission_name)
    return _current


def step(description: str, status: str = "ok") -> None:
    """Log one step to the current run document. No-op if no run is active."""
    if _current:
        _current.step(description, status)
    else:
        log.info(description)


def close_run(success: bool = True) -> Optional[Path]:
    """Finalise the current run document and return its path."""
    global _current
    if _current:
        path = _current.close(success)
        _current = None
        return path
    return None


def current_log_path() -> Optional[Path]:
    return _current.log_path if _current else None


# ── RunLogger class ───────────────────────────────────────────────────────────

class RunLogger:

    def __init__(self, submission_name: str):
        self._start_time = time.monotonic()
        self._start_dt   = datetime.now(timezone.utc)
        ts               = self._start_dt.strftime("%Y%m%dT%H%M%SZ")
        stem             = Path(submission_name).stem

        runs_dir         = OUTPUT_DIR / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)

        self.log_path    = runs_dir / f"{stem}__{ts}.md"
        self._fh         = self.log_path.open("w", encoding="utf-8", buffering=1)

        self._write_header(submission_name)
        log.info("Run log → %s", self.log_path)

    # ── Public ────────────────────────────────────────────────────────────────

    def step(self, description: str, status: str = "ok") -> None:
        now  = datetime.now(timezone.utc).strftime("%H:%M:%S")
        icon = _ICONS.get(status, "→")
        self._fh.write(f"| `{now}` | {icon} | {description} |\n")
        self._fh.flush()
        log.info("%s %s", icon, description)

    def close(self, success: bool = True) -> Path:
        elapsed = time.monotonic() - self._start_time
        end_dt  = datetime.now(timezone.utc)
        result  = "SUCCESS" if success else "FAILED"
        icon    = "✓" if success else "✗"

        self._fh.write("\n")
        self._fh.write(f"---\n\n")
        self._fh.write(f"**Completed:** {end_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}  \n")
        self._fh.write(f"**Duration:** {elapsed:.1f}s  \n")
        self._fh.write(f"**Result:** {icon} {result}  \n")
        self._fh.flush()
        self._fh.close()

        log.info("Run log saved → %s  (%s  %.1fs)", self.log_path.name, result, elapsed)
        return self.log_path

    # ── Private ───────────────────────────────────────────────────────────────

    def _write_header(self, submission_name: str) -> None:
        started = self._start_dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        self._fh.write(f"# Billing Review Run\n\n")
        self._fh.write(f"**Submission:** `{submission_name}`  \n")
        self._fh.write(f"**Started:** {started}  \n")
        self._fh.write(f"**Log:** `{self.log_path}`  \n\n")
        self._fh.write(f"---\n\n")
        self._fh.write(f"| Time | Status | Step |\n")
        self._fh.write(f"|------|--------|------|\n")
        self._fh.flush()
