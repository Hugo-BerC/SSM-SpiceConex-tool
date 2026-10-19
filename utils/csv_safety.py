"""Safe handling for spreadsheet-oriented exports."""
from __future__ import annotations


def csv_safe_value(value: object) -> object:
    """Prevent spreadsheet formula evaluation while retaining ordinary values.

    CSV is commonly opened in Excel or LibreOffice.  A value beginning with a
    formula sigil may otherwise execute as a formula even when it originated
    from a tag, DNS name or AWS resource metadata.
    """
    if not isinstance(value, str):
        return value
    if value.lstrip(" \t\r\n")[:1] in {"=", "+", "-", "@"}:
        return "'" + value
    return value
