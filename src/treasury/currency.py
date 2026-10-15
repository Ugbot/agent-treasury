"""Settlement currency comes from the organisation policy (CURRENCY-1), not from code.

This is only the fallback for helper signatures; every decision is checked against
the effective policy's allowed_currency clause.
"""
from __future__ import annotations

from .policy import DEFAULT_POLICY, default_currency

DEFAULT_CURRENCY: str = default_currency(DEFAULT_POLICY) or "usd"
