"""
RunLogger — appends every run to a single output/billing_agent.log file.

Each run is separated by a header banner. Every step line carries the
submission filename so the log stays grep-friendly across multiple runs.

Usage from any module:
    from billing_agent import run_logger
    run_logger.step("Loaded 50 transactions (23 labour, 27 expense)")
    run_logger.step("OVER_CAP: Hotel $310 exceeds $275 metro cap", "warn")
    run_logger.step("ALCOHOL: Drinks $38 rejected — non-reimbursable §4", "error")

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
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")

from billing_agent.config import LOG_FILE, OUTPUT_DIR

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
    """Open a new run section in the shared log. Call once per submission."""
    global _current
    _current = RunLogger(submission_name)
    return _current


def step(description: str, status: str = "ok") -> None:
    """Append one step to the current run. No-op if no run is active."""
    if _current:
        _current.step(description, status)
    else:
        log.info(description)


def close_run(success: bool = True) -> Optional[Path]:
    """Write the run footer and return the log path."""
    global _current
    if _current:
        path = _current.close(success)
        _current = None
        return path
    return None


def current_log_path() -> Optional[Path]:
    return LOG_FILE if _current else None


# ── RunLogger class ───────────────────────────────────────────────────────────

class RunLogger:

    def __init__(self, submission_name: str):
        self._submission = submission_name
        self._start_time = time.monotonic()
        self._start_dt   = datetime.now(_ET)

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self._fh = LOG_FILE.open("a", encoding="utf-8", buffering=1)

        self._write_header()
        log.info("Run log → %s", LOG_FILE)

    # ── Public ────────────────────────────────────────────────────────────────

    def step(self, description: str, status: str = "ok") -> None:
        now  = datetime.now(_ET).strftime("%Y-%m-%d %H:%M:%S")
        icon = _ICONS.get(status, "→")
        self._fh.write(f"{now}  [{self._submission}]  {icon}  {description}\n")
        log.info("%s %s", icon, description)

    def close(self, success: bool = True) -> Path:
        elapsed = time.monotonic() - self._start_time
        result  = "SUCCESS" if success else "FAILED"
        icon    = "✓" if success else "✗"
        end_dt  = datetime.now(_ET).strftime("%Y-%m-%d %H:%M:%S %Z")

        self._fh.write(
            f"{'-' * 72}\n"
            f"RESULT  [{self._submission}]  {icon} {result}"
            f"  completed {end_dt}  ({elapsed:.1f}s)\n\n"
        )
        self._fh.close()

        log.info("Run log saved → %s  (%s  %.1fs)", LOG_FILE.name, result, elapsed)
        return LOG_FILE

    # ── Private ───────────────────────────────────────────────────────────────

    def _write_header(self) -> None:
        started = self._start_dt.strftime("%Y-%m-%d %H:%M:%S %Z")
        self._fh.write(
            f"{'=' * 72}\n"
            f"RUN  {self._submission}  started {started}\n"
            f"{'=' * 72}\n"
        )
