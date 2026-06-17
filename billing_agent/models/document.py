from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional


@dataclass
class LineItem:
    description: str
    amount: float
    currency: str = "USD"


@dataclass
class ReceiptDocument:
    doc_id: str                     # RC-001, ML-001, VI-001, …
    doc_type: str                   # RECEIPT | MILEAGE_LOG | VENDOR_INVOICE | COMPOSITE | UNREADABLE
    filename: str
    vendor: str
    doc_date: Optional[date]
    total_amount: float
    currency: str
    line_items: List[LineItem] = field(default_factory=list)
    linked_tx_ids: List[str] = field(default_factory=list)
    notes: str = ""
    raw_text: str = ""
    is_composite: bool = False
    has_alcohol: bool = False
    is_unreadable: bool = False
