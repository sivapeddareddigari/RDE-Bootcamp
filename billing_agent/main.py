"""
Drop-folder watcher — entry point for the agentic billing review system.

Usage:
    python billing_agent/main.py

Drop a submission CSV into  submissions/incoming/  and the agent fires.
Completed runs land in       submissions/completed/
Failed runs land in          submissions/failed/

Files in submissions/processing/ at startup indicate a prior crash;
they are automatically recovered to submissions/incoming/ for re-run.
"""

import logging
import re
import shutil
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from billing_agent.ingestion import load_inputs
from billing_agent import run_logger
from billing_agent.config import (
    ACCEPTED_EXTENSIONS,
    COMPLETED_DIR,
    FAILED_DIR,
    INCOMING_DIR,
    MAX_RETRIES,
    OUTPUT_DIR,
    POLL_INTERVAL_SECONDS,
    PROCESSING_DIR,
)

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Pipeline stub (filled in by later phases) ─────────────────────────────────

def process_submission(submission_path: Path) -> None:
    """
    Orchestrates the full billing review pipeline for one submission file.
    Each phase below will replace its TODO stub with a real call.
    """
    run_logger.init_run(submission_path.name)
    run_logger.step(f"Submission received — {submission_path.name}", "info")

    run_logger.step("Phase 1 — loading all inputs", "info")
    inputs = load_inputs(submission_path)

    run_logger.step("Phase 2 — rule engine", "info")
    # TODO Phase 2 — rules.rule_engine.run(inputs)

    run_logger.step("Phase 3 — document matching & reconciliation", "info")
    # TODO Phase 3 — matching.matcher.reconcile(inputs)

    run_logger.step("Phase 4 — exception detection & triage", "info")
    # TODO Phase 4 — exceptions.detector.run(inputs)

    run_logger.step("Phase 5 — invoice builder & outputs", "info")
    # TODO Phase 5 — output.invoice_builder.build(inputs)

    run_logger.step("Phase 6 — supervisor agent reasoning", "info")
    # TODO Phase 6 — agents.supervisor.run(inputs)

    run_logger.step("Pipeline complete", "ok")
    # run_logger.close_run() is called by _handle after the file is safely archived


# ── Folder watcher ────────────────────────────────────────────────────────────

class DropFolderWatcher:

    def __init__(self):
        self._running = False

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        _ensure_folders()
        _recover_in_flight()
        self._running = True

        log.info("=" * 60)
        log.info("Billing Review Agent — drop folder watcher started")
        log.info("  Watching : %s", INCOMING_DIR)
        log.info("  Poll     : every %ss", POLL_INTERVAL_SECONDS)
        log.info("  Output   : %s", OUTPUT_DIR)
        log.info("=" * 60)
        log.info("Drop a submission CSV into 'incoming/' to trigger a run.")
        log.info("Press Ctrl+C to stop.")

        while self._running:
            self._poll()
            time.sleep(POLL_INTERVAL_SECONDS)

    def stop(self) -> None:
        self._running = False
        log.info("Watcher stopped.")

    # ── poll ──────────────────────────────────────────────────────────────────

    def _poll(self) -> None:
        candidates = sorted(
            f for f in INCOMING_DIR.iterdir()
            if f.is_file() and f.suffix.lower() in ACCEPTED_EXTENSIONS
        )
        for submission in candidates:
            self._handle(submission)

    def _handle(self, submission: Path) -> None:
        processing_path = PROCESSING_DIR / submission.name
        try:
            shutil.move(str(submission), processing_path)
            log.info("── submission received: %s", submission.name)
            process_submission(processing_path)
            completed_path = COMPLETED_DIR / _stamp(submission.name)
            shutil.move(str(processing_path), completed_path)
            run_logger.close_run(success=True)   # seal log only after file is archived
            log.info("── completed: %s", completed_path.name)
        except Exception:
            log.exception("── failed: %s", submission.name)
            run_logger.step("Pipeline failed — see console for traceback", "error")
            run_logger.close_run(success=False)
            failed_path = FAILED_DIR / _stamp(submission.name)
            if processing_path.exists():
                shutil.move(str(processing_path), failed_path)
                log.info("   moved to failed/: %s", failed_path.name)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ensure_folders() -> None:
    for folder in (INCOMING_DIR, PROCESSING_DIR, COMPLETED_DIR, FAILED_DIR, OUTPUT_DIR):
        folder.mkdir(parents=True, exist_ok=True)


_RETRY_RE = re.compile(r"__r(\d+)$")


def _retry_count(stem: str) -> int:
    m = _RETRY_RE.search(stem)
    return int(m.group(1)) if m else 0


def _bump_retry(name: str) -> str:
    p = Path(name)
    n = _retry_count(p.stem)
    clean = _RETRY_RE.sub("", p.stem)
    return f"{clean}__r{n + 1}{p.suffix}"


def _recover_in_flight() -> None:
    """
    Move files left in processing/ back to incoming/ for a retry.
    After MAX_RETRIES crashes the file is quarantined in failed/ instead.
    """
    stale = list(PROCESSING_DIR.iterdir())
    if not stale:
        return
    log.warning(
        "Found %d in-flight file(s) from a previous run — recovering",
        len(stale),
    )
    for f in stale:
        n = _retry_count(Path(f.name).stem)
        if n >= MAX_RETRIES:
            dest = FAILED_DIR / _stamp(f.name)
            shutil.move(str(f), dest)
            log.error(
                "  quarantined after %d retries: %s → failed/", n, f.name
            )
        else:
            new_name = _bump_retry(f.name)
            dest = INCOMING_DIR / new_name
            shutil.move(str(f), dest)
            log.warning(
                "  recovered (attempt %d/%d): %s", n + 1, MAX_RETRIES, new_name
            )


def _stamp(name: str) -> str:
    """Append a UTC timestamp to a filename to avoid collisions in completed/failed."""
    ts   = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = Path(name).stem
    sfx  = Path(name).suffix
    return f"{stem}__{ts}{sfx}"


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    watcher = DropFolderWatcher()

    def _shutdown(sig, frame):
        log.info("Shutdown signal received (signal %s).", sig)
        watcher.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    watcher.start()


if __name__ == "__main__":
    main()
