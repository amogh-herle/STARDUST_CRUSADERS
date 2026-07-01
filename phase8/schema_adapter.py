"""
Phase 8 schema adapter for the AML feature pipeline.

Converts Phase 7 cleaned transactions into the exact enriched schema
expected by aml_model.FeatureEngineer, without modifying Phase 7
output or the AML model code.
"""
from __future__ import annotations
import re
from typing import Iterable

import numpy as np
import pandas as pd

# Required columns for FeatureEngineer input and validation.
_FEATURE_ENGINEER_COLUMNS = [
    "transaction_id",
    "account_id",
    "account_holder",
    "bank_name",
    "date",
    "time",
    "narration",
    "channel",
    "debit",
    "credit",
    "balance",
    "balance_clean",
    "datetime",
    "amount",
    "abs_amount",
    "txn_type",
    "counterparty_account_id",
    "counterparty_name",
    "utr_ref",
]

# Phase 7 field names that need to be renamed or mapped.
_PHASE7_TO_AML_FIELD_MAP = {
    "counterparty_account": "counterparty_account_id",
}


def _clean_money(series: pd.Series) -> pd.Series:
    """Normalize a money column to float values."""
    cleaned = series.astype(str).str.replace(r"[₹,\s]", "", regex=True)
    cleaned = cleaned.str.replace(r"[^0-9.\-]", "", regex=True)
    cleaned = cleaned.replace("", "0")
    return pd.to_numeric(cleaned, errors="coerce").fillna(0.0)


def _normalize_text_columns(df: pd.DataFrame, cols: Iterable[str]) -> pd.DataFrame:
    for col in cols:
        if col in df.columns:
            df[col] = df[col].astype(str).replace({"nan": "", "None": "", "none": ""})
        else:
            df[col] = ""
    return df


def _build_datetime(df: pd.DataFrame) -> pd.DataFrame:
    df["date"] = df["date"].astype(str)
    df["time"] = df["time"].fillna("00:00:00").astype(str).replace("nan", "00:00:00")
    df["time"] = df["time"].where(df["time"].str.strip() != "", "00:00:00")

    dt_strings = df["date"].str.strip() + " " + df["time"].str.strip()
    df["datetime"] = pd.to_datetime(dt_strings, errors="coerce")
    invalid = df["datetime"].isna() & df["date"].notna()
    if invalid.any():
        df.loc[invalid, "datetime"] = pd.to_datetime(df.loc[invalid, "date"], errors="coerce")
    return df


def _ensure_transaction_id(df: pd.DataFrame) -> pd.DataFrame:
    if "transaction_id" not in df.columns:
        df["transaction_id"] = (
            df["account_id"].astype(str).fillna("UNKNOWN") + "_" +
            df["date"].astype(str).fillna("1970-01-01") + "_" +
            df["time"].astype(str).fillna("00:00:00") + "_" +
            df.index.astype(str)
        )
    return df


def _ensure_counterparty_account_id(df: pd.DataFrame) -> pd.DataFrame:
    if "counterparty_account_id" not in df.columns:
        if "counterparty_account" in df.columns:
            df["counterparty_account_id"] = df["counterparty_account"].astype(str)
            df.loc[df["counterparty_account_id"].str.lower().isin({"nan", "none", "null", ""}),
                   "counterparty_account_id"] = ""
        else:
            df["counterparty_account_id"] = ""
    return df


def validate_aml_schema(df: pd.DataFrame) -> list[str]:
    missing = [col for col in _FEATURE_ENGINEER_COLUMNS if col not in df.columns]
    return missing


def adapt_phase7_to_aml_schema(df: pd.DataFrame) -> pd.DataFrame:
    """Convert Phase 7 output into the enriched schema required by FeatureEngineer."""
    df = df.copy()

    # Preserve raw Phase 7 fields while adding any renamed AML-specific fields.
    for src, dst in _PHASE7_TO_AML_FIELD_MAP.items():
        if dst not in df.columns and src in df.columns:
            df[dst] = df[src]

    # Ensure all base string columns exist.
    df = _normalize_text_columns(df, [
        "transaction_id", "account_id", "account_holder", "bank_name",
        "date", "time", "narration", "channel",
        "counterparty_name", "counterparty_account_id", "utr_ref",
    ])

    df = _ensure_transaction_id(df)
    df = _ensure_counterparty_account_id(df)

    # Clean numeric amount and balance fields.
    for col in ["debit", "credit", "balance"]:
        if col not in df.columns:
            df[col] = 0.0
        df[col] = _clean_money(df[col])

    df["balance_clean"] = df["balance"]
    df["amount"] = df["credit"] - df["debit"]
    df["abs_amount"] = df["amount"].abs()
    df["txn_type"] = np.where(
        df["credit"] > 0,
        "credit",
        np.where(df["debit"] > 0, "debit", "unknown")
    )

    df = _build_datetime(df)

    # Ensure any computed fields exist even if the source schema is incomplete.
    for col in ["balance_clean", "amount", "abs_amount", "txn_type", "datetime"]:
        if col not in df.columns:
            df[col] = 0.0 if col != "txn_type" else "unknown"

    return df
