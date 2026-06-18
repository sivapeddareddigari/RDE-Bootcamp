"""
Test runner for the billing agent unit test suite.

Usage:
    python3 run_tests.py

Actions:
  1. Runs pytest on the tests/ directory with verbose output.
  2. Saves the full test output to output/Unit_test_runs/<timestamp>.txt.
  3. Appends a summary line to output/billing_agent.log in the standard run format.
"""

import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

OUTPUT_DIR    = Path("output")
UNIT_TEST_DIR = OUTPUT_DIR / "Unit_test_runs"
LOG_FILE      = OUTPUT_DIR / "billing_agent.log"

_ICONS = {"ok": "✓", "warn": "⚠", "error": "✗", "info": "→"}


def _log(fh, status: str, description: str) -> None:
    now  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    icon = _ICONS.get(status, "→")
    fh.write(f"{now}  [unit-tests]  {icon}  {description}\n")
    fh.flush()


def _parse_summary(output: str) -> dict:
    """Extract passed/failed/error counts from pytest's last summary line."""
    # e.g. "42 passed, 3 failed, 1 error in 2.34s"
    pattern = re.compile(
        r"(?:(\d+) passed)?[,\s]*"
        r"(?:(\d+) failed)?[,\s]*"
        r"(?:(\d+) error(?:s)?)?"
        r"\s+in\s+([\d.]+)s",
        re.IGNORECASE,
    )
    m = pattern.search(output)
    if m:
        return {
            "passed":  int(m.group(1) or 0),
            "failed":  int(m.group(2) or 0),
            "errors":  int(m.group(3) or 0),
            "elapsed": float(m.group(4)),
        }
    # Fallback: no tests collected or syntax error before any ran
    return {"passed": 0, "failed": 0, "errors": 0, "elapsed": 0.0}


def main() -> int:
    UNIT_TEST_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    run_ts  = datetime.now(timezone.utc)
    ts_str  = run_ts.strftime("%Y%m%d_%H%M%S")
    started = run_ts.strftime("%Y-%m-%d %H:%M:%S UTC")
    result_file = UNIT_TEST_DIR / f"unit_test_run_{ts_str}.txt"

    print(f"Running tests — output → {result_file}")

    t0 = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short", "--no-header"],
        capture_output=True,
        text=True,
    )
    elapsed = time.monotonic() - t0

    full_output = proc.stdout + proc.stderr
    counts = _parse_summary(full_output)
    passed  = counts["passed"]
    failed  = counts["failed"]
    errors  = counts["errors"]
    total   = passed + failed + errors
    success = (failed == 0 and errors == 0 and total > 0)

    # ── Write result file ──────────────────────────────────────────────────────
    with result_file.open("w", encoding="utf-8") as rf:
        rf.write(f"{'=' * 72}\n")
        rf.write(f"BILLING AGENT — UNIT TEST RUN\n")
        rf.write(f"Started : {started}\n")
        rf.write(f"Result  : {'PASS' if success else 'FAIL'}\n")
        rf.write(f"Summary : {passed} passed  {failed} failed  {errors} errors  "
                 f"({total} total)  {elapsed:.1f}s\n")
        rf.write(f"{'=' * 72}\n\n")
        rf.write(full_output)

    # ── Append to billing_agent.log ────────────────────────────────────────────
    with LOG_FILE.open("a", encoding="utf-8") as lf:
        lf.write(f"{'=' * 72}\n")
        lf.write(f"RUN  unit-tests  started {started}\n")
        lf.write(f"{'=' * 72}\n")

        _log(lf, "info",  f"Unit test suite started — {total} test(s) discovered")
        _log(lf, "ok",    f"Passed  : {passed}")

        if failed:
            _log(lf, "error", f"Failed  : {failed}")
        if errors:
            _log(lf, "error", f"Errors  : {errors}")

        result_icon = "✓" if success else "✗"
        result_word = "SUCCESS" if success else "FAILED"
        end_dt = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        lf.write(f"{'-' * 72}\n")
        lf.write(
            f"RESULT  [unit-tests]  {result_icon} {result_word}"
            f"  completed {end_dt}  ({elapsed:.1f}s)\n\n"
        )

    # ── Console summary ────────────────────────────────────────────────────────
    print(full_output)
    print(f"\n{'=' * 60}")
    print(f"  {'PASS' if success else 'FAIL'}  —  "
          f"{passed} passed  {failed} failed  {errors} errors  ({elapsed:.1f}s)")
    print(f"  Result file : {result_file}")
    print(f"  Log updated : {LOG_FILE}")
    print(f"{'=' * 60}")

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
