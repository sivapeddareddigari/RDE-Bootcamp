from dataclasses import dataclass
from typing import Optional


@dataclass
class RateEntry:
    role_code: str
    role_name: str
    rate_usd: float


@dataclass
class ContractClause:
    clause_id: str
    category: str       # LODGING | MEAL | TRAVEL | SUBCONTRACTOR | ALCOHOL | PERSONAL | MILEAGE | …
    rule_text: str
    cap_amount: Optional[float] = None
    condition: str = ""
