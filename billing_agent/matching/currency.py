"""FX conversion using the spot rates in config.FX_RATES."""
from billing_agent.config import FX_RATES


def to_usd(amount: float, currency: str) -> tuple[float, float]:
    """
    Convert amount to USD. Returns (usd_amount, rate_applied).
    Rate is 1.0 for USD. Raises ValueError for unknown currencies.
    """
    if currency.upper() == "USD":
        return round(amount, 2), 1.0
    rate = FX_RATES.get(currency.upper())
    if rate is None:
        raise ValueError(f"No FX rate configured for currency: {currency!r}")
    return round(amount * rate, 2), rate
