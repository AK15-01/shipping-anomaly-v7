"""Safe in-memory exports for user-provided tabular data."""

from __future__ import annotations

import pandas as pd


FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _escape_formula(value):
    if isinstance(value, str) and value.lstrip().startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def dataframe_to_csv_bytes(frame: pd.DataFrame) -> bytes:
    """Export UTF-8-SIG CSV while neutralising spreadsheet formula injection."""

    safe = frame.copy()
    for column in safe.select_dtypes(include=["object", "string"]).columns:
        safe[column] = safe[column].map(_escape_formula)
    return safe.to_csv(index=False).encode("utf-8-sig")
