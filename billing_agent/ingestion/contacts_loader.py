"""Loads billing_agent/data/contacts.json into typed dataclasses."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

_CONTACTS_PATH = Path(__file__).parent.parent / "data" / "contacts.json"


@dataclass
class EmployeeContact:
    employee_id: str
    name: str
    email: str
    role_code: Optional[str]
    role_label: str


@dataclass
class AnalystContact:
    name: str
    email: str


@dataclass
class ProjectLeadContact:
    project_id: str
    name: str
    email: str


@dataclass
class ContactDirectory:
    employees: List[EmployeeContact]
    billing_analysts: List[AnalystContact]
    project_leads: List[ProjectLeadContact]

    def employee(self, employee_id: str) -> Optional[EmployeeContact]:
        for e in self.employees:
            if e.employee_id == employee_id:
                return e
        return None

    def project_lead(self, project_id: str) -> Optional[ProjectLeadContact]:
        for pl in self.project_leads:
            if pl.project_id == project_id:
                return pl
        return None


def load_contacts(path: Path = _CONTACTS_PATH) -> ContactDirectory:
    data = json.loads(path.read_text(encoding="utf-8"))

    employees = [
        EmployeeContact(
            employee_id = e["employee_id"],
            name        = e["name"],
            email       = e["email"],
            role_code   = e.get("role_code"),
            role_label  = e["role_label"],
        )
        for e in data.get("employees", [])
    ]

    analysts = [
        AnalystContact(name=a["name"], email=a["email"])
        for a in data.get("billing_analysts", [])
    ]

    leads = [
        ProjectLeadContact(
            project_id = pl["project_id"],
            name       = pl["name"],
            email      = pl["email"],
        )
        for pl in data.get("project_leads", [])
    ]

    return ContactDirectory(
        employees       = employees,
        billing_analysts = analysts,
        project_leads   = leads,
    )
