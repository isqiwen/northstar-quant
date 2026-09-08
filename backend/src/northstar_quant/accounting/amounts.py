"""Exact canonical decimal amounts used in persisted financial evidence."""

from decimal import Decimal


def decimal_text(value: Decimal) -> str:
    """Canonical financial text for the application's persisted run evidence."""

    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text
