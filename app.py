"""
Masterfile loader for the NaroIX Sanctions Screener.

Expects a sheet named 'Instruments' with columns:
    ISIN, Company Name, FactSet ID, Exchange Country,
    Country of Inc., Country of Risk,
    Alternative 1..N
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd


REQUIRED_COLUMNS = ["ISIN", "Company Name"]
COUNTRY_COLUMNS = ["Exchange Country", "Country of Inc.", "Country of Risk"]
INSTRUMENTS_SHEET = "Instruments"


def load_masterfile(source: Path | BytesIO | bytes | str) -> pd.DataFrame:
    """
    Load and validate the masterfile.

    Accepts a filesystem path, raw bytes, or a BytesIO (for Streamlit upload).
    Returns a cleaned DataFrame.
    """
    if isinstance(source, bytes):
        source = BytesIO(source)

    df = pd.read_excel(source, sheet_name=INSTRUMENTS_SHEET, dtype=str)
    df.columns = [c.strip() for c in df.columns]

    # Validate required columns
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"Masterfile missing required columns: {missing}. "
            f"Found columns: {list(df.columns)}"
        )

    # Drop entirely empty rows
    df = df.dropna(subset=["ISIN", "Company Name"], how="all").reset_index(drop=True)

    # Strip whitespace from all string columns
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].astype(str).str.strip().replace({"nan": "", "None": ""})

    return df


def get_alternative_columns(df: pd.DataFrame) -> list[str]:
    """Return all 'Alternative N' columns present in the masterfile."""
    return [c for c in df.columns if c.startswith("Alternative")]


def get_row_by_isin(df: pd.DataFrame, isin: str) -> pd.Series | None:
    """Look up a single row by ISIN. Returns None if not found."""
    match = df[df["ISIN"] == isin]
    if match.empty:
        return None
    return match.iloc[0]


def extract_alternatives(row: pd.Series) -> list[str]:
    """Extract non-empty alternative names from a row."""
    alts: list[str] = []
    for col in row.index:
        if col.startswith("Alternative"):
            val = row.get(col)
            if val and str(val).strip() and str(val).strip().lower() not in {"nan", "none"}:
                alts.append(str(val).strip())
    return alts


def get_country_of_risk(row: pd.Series) -> str:
    """Return the Country of Risk from a row, or empty string."""
    val = row.get("Country of Risk", "")
    if not val or str(val).strip().lower() in {"nan", "none"}:
        return ""
    return str(val).strip()
