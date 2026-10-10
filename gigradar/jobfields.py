"""Shared reading of Job fields, so alerts and scoring agree on what "missing" means."""

from __future__ import annotations


def parse_amount(value: str | None) -> float | None:
    """A money amount from the API ("50.0"), or None when missing: None, "", not a number,
    or <= 0 (Upwork sends 0 for "no budget given")."""
    if value in (None, ""):
        return None
    try:
        amount = float(value)
    except ValueError:
        return None
    return amount if amount > 0 else None


def format_money(amount: float) -> str:
    """$1,500 / $42.50"""
    return f"${amount:,.0f}" if amount == int(amount) else f"${amount:,.2f}"
