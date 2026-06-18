"""
Loads SAP transaction and timecard CSV extracts.

Columns (unbilled CSV):
  transaction_id, project_id, task_code, transaction_date, type,
  employee_id, role_code, description, quantity, unit, rate, amount,
  currency, classification, hold_flag, hold_reason, note

Columns (timecard CSV):
  timecard_id, employee_id, employee_name, project_id, task_code,
  entry_date, week_ending, hours, activity_description, submitted_date,
  approver_id, approver_name, approval_date, approval_status,
  linked_tx_id, note
"""

import csv
import logging
from datetime import date
from pathlib import Path
from typing import List, Tuple

from billing_agent.models.transaction import TimecardEntry, Transaction

log = logging.getLogger(__name__)


def load_transactions(csv_path: Path) -> List[Transaction]:
    transactions: List[Transaction] = []
    with csv_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                transactions.append(Transaction(
                    transaction_id   = row["transaction_id"].strip(),
                    project_id       = row["project_id"].strip(),
                    task_code        = row["task_code"].strip(),
                    transaction_date = _date(row["transaction_date"]),
                    type             = row["type"].strip().upper(),
                    employee_id      = row["employee_id"].strip(),
                    role_code        = row["role_code"].strip(),
                    description      = row["description"].strip(),
                    quantity         = float(row["quantity"].strip() or 0),
                    unit             = row["unit"].strip(),
                    rate             = float(row["rate"].strip() or 0),
                    amount           = float(row["amount"].strip() or 0),
                    currency         = row["currency"].strip(),
                    classification   = row["classification"].strip(),
                    hold_flag        = row["hold_flag"].strip().upper() == "Y",
                    hold_reason      = row.get("hold_reason", "").strip(),
                    note             = row.get("note", "").strip(),
                ))
            except (KeyError, ValueError) as exc:
                log.warning("Skipping malformed transaction row %s: %s", row.get("transaction_id"), exc)
    log.info("Loaded %d transactions from %s", len(transactions), csv_path.name)
    return transactions


def load_timecards(csv_path: Path) -> List[TimecardEntry]:
    timecards: List[TimecardEntry] = []
    with csv_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                raw_approval = row.get("approval_date", "").strip()
                timecards.append(TimecardEntry(
                    timecard_id          = row["timecard_id"].strip(),
                    employee_id          = row["employee_id"].strip(),
                    employee_name        = row["employee_name"].strip(),
                    project_id           = row["project_id"].strip(),
                    task_code            = row["task_code"].strip(),
                    entry_date           = _date(row["entry_date"]),
                    week_ending          = _date(row["week_ending"]),
                    hours                = float(row["hours"] or 0),
                    activity_description = row["activity_description"].strip(),
                    submitted_date       = _date(row["submitted_date"]),
                    approver_id          = row["approver_id"].strip(),
                    approver_name        = row["approver_name"].strip(),
                    approval_date        = _date(raw_approval) if raw_approval else None,
                    approval_status      = row["approval_status"].strip().upper(),
                    linked_tx_id         = row.get("linked_tx_id", "").strip(),
                    note                 = row.get("note", "").strip(),
                ))
            except (KeyError, ValueError) as exc:
                log.warning("Skipping malformed timecard row %s: %s", row.get("timecard_id"), exc)
    log.info("Loaded %d timecard entries from %s", len(timecards), csv_path.name)
    return timecards


def _date(value: str) -> date:
    return date.fromisoformat(value.strip())
