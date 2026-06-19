"""
Email configuration — loaded from environment variables or a .env file.

Required env vars when EMAIL_ENABLED=true:
    SMTP_HOST       SMTP server hostname  (default: smtp.office365.com)
    SMTP_PORT       SMTP port             (default: 587)
    SMTP_USER       Sender login / From address
    SMTP_PASSWORD   Sender password / app password

Optional:
    EMAIL_FROM_NAME Display name in From header (default: Billing Agent)
    EMAIL_FROM_ADDR From address if different from SMTP_USER
"""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class EmailConfig:
    enabled:   bool
    host:      str
    port:      int
    user:      str
    password:  str
    from_addr: str
    from_name: str


def load_config() -> EmailConfig:
    _load_dotenv()
    user = os.environ.get("SMTP_USER", "")
    return EmailConfig(
        enabled   = os.environ.get("EMAIL_ENABLED", "false").strip().lower() in ("true", "1", "yes"),
        host      = os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        port      = int(os.environ.get("SMTP_PORT", "587")),
        user      = user,
        password  = os.environ.get("SMTP_PASSWORD", ""),
        from_addr = os.environ.get("EMAIL_FROM_ADDR", user),
        from_name = os.environ.get("EMAIL_FROM_NAME", "Billing Agent"),
    )


def _load_dotenv() -> None:
    """Parse a .env file at the project root without requiring python-dotenv."""
    env_path = Path(__file__).parent.parent.parent / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val
