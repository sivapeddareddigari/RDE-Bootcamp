"""
Loads prior exception resolutions from resolutions.csv.

Columns:
  exception_id, project_id, cycle, exception_type, description,
  resolution, resolved_by, resolution_date, instruction_recurring
"""

import csv
import logging
from datetime import date
from pathlib import Path
from typing import List

from billing_agent.models.instruction import ExceptionCase

log = logging.getLogger(__name__)


def load_exceptions(csv_path: Path) -> List[ExceptionCase]:
    cases: List[ExceptionCase] = []
    with csv_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                cases.append(ExceptionCase(
                    exception_id     = row["exception_id"].strip(),
                    project_id       = row["project_id"].strip(),
                    cycle            = row["cycle"].strip(),
                    exception_type   = row["exception_type"].strip().upper(),
                    description      = row["description"].strip(),
                    resolution       = row["resolution"].strip(),
                    resolved_by      = row["resolved_by"].strip(),
                    resolution_date  = date.fromisoformat(row["resolution_date"].strip()),
                    recurring        = row.get("instruction_recurring", "N").strip().upper() == "Y",
                ))
            except (KeyError, ValueError) as exc:
                log.warning("Skipping malformed exception row %s: %s", row.get("exception_id"), exc)
    log.info("Loaded %d prior exception cases from %s", len(cases), csv_path.name)
    return cases
