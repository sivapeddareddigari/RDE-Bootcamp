from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class Transaction:
    transaction_id: str
    project_id: str
    task_code: str
    transaction_date: date
    type: str                   # LABOR | EXPENSE
    employee_id: str
    role_code: str
    description: str
    quantity: float
    unit: str
    rate: float
    amount: float
    currency: str
    classification: str
    hold_flag: bool
    hold_reason: str
    note: str

    @property
    def is_labor(self) -> bool:
        return self.type.upper() == "LABOR"

    @property
    def is_expense(self) -> bool:
        return self.type.upper() == "EXPENSE"

    @property
    def is_on_hold(self) -> bool:
        return self.hold_flag


@dataclass
class TimecardEntry:
    timecard_id: str
    employee_id: str
    employee_name: str
    project_id: str
    task_code: str
    entry_date: date
    week_ending: date
    hours: float
    activity_description: str
    submitted_date: date
    approver_id: str
    approver_name: str
    approval_date: Optional[date]
    approval_status: str        # APPROVED | APPROVED_HOLD | PENDING
    linked_tx_id: str
    note: str
