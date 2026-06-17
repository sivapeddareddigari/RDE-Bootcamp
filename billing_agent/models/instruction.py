from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class ProjectInstruction:
    instruction_id: str
    instruction_date: date
    from_name: str
    subject: str
    instruction_type: str   # STANDING | OVERRIDE_APPROVE | OVERRIDE_REJECT | RELEASE_HOLD | CONFIRM_MARKUP
    scope: str              # plain-text description of what this applies to
    amount: Optional[float]
    body: str
    recurring: bool = False


@dataclass
class ExceptionCase:
    exception_id: str
    project_id: str
    cycle: str
    exception_type: str
    description: str
    resolution: str
    resolved_by: str
    resolution_date: date
    recurring: bool
