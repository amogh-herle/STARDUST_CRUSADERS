"""
DataLoader — reads your exact CSV schema and normalises it into a
clean DataFrame ready for feature engineering.

Handles:
  - date/time in any common format
  - debit/credit as strings with commas or currency symbols
  - missing balance / UTR
  - duplicate transaction_id detection
  - amount column unification (debit → negative, credit → positive)
"""

import pandas as pd
import numpy as np
import re
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from configs.config import SCHEMA, DATE_FORMATS, TIME_FORMATS


# ── helpers ──────────────────────────────────────────────────────────────────

def _clean_money(series: pd.Series) -> pd.Series:
    """Remove ₹, commas, spaces; coerce to float; fill NaN → 0."""
    return (
        series.astype(str)
              .str.replace(r"[₹,\s]", "", regex=True)
              .str.replace(r"[^0-9.\-]", "", regex=True)
              .replace("", "0")
              .astype(float)
              .fillna(0.0)
    )

def _parse_date(series: pd.Series) -> pd.Series:
    for fmt in DATE_FORMATS:
        try:
            parsed = pd.to_datetime(series, format=fmt, errors="raise")
            return parsed
        except Exception:
            continue
    # fallback: infer
    return pd.to_datetime(series, infer_datetime_format=True, errors="coerce")

def _parse_time(series: pd.Series) -> pd.Series:
    for fmt in TIME_FORMATS:
        try:
            parsed = pd.to_datetime("2000-01-01 " + series.astype(str), format=f"%Y-%m-%d {fmt}", errors="raise")
            return parsed.dt.time
        except Exception:
            continue
    return pd.to_datetime("2000-01-01 " + series.astype(str),
                          infer_datetime_format=True, errors="coerce").dt.time


# ── main loader ───────────────────────────────────────────────────────────────

class DataLoader:
    """
    Usage:
        loader = DataLoader()
        df = loader.load("transactions.csv")
        # df has a unified 'datetime' column, 'amount' (signed), and all originals
    """

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.load_report_ = {}

    def load(self, filepath: str) -> pd.DataFrame:
        df = self._read_file(filepath)
        df = self._validate_schema(df)
        df = self._parse_datetime(df)
        df = self._parse_amounts(df)
        df = self._clean_text(df)
        df = self._detect_duplicates(df)
        df = self._sort(df)

        self.load_report_ = {
            "total_rows":        len(df),
            "duplicate_rows":    int(df["is_duplicate"].sum()),
            "missing_balance":   int(df["balance"].isna().sum()),
            "date_parse_errors": int(df["datetime"].isna().sum()),
            "accounts":          df[SCHEMA["account_id"]].nunique(),
            "counterparties":    df[SCHEMA["counterparty_account"]].nunique(),
        }

        if self.verbose:
            print("[DataLoader] Load report:")
            for k, v in self.load_report_.items():
                print(f"   {k:25s}: {v}")

        return df

    # ── private ──────────────────────────────────────────────────────────────

    def _read_file(self, filepath: str) -> pd.DataFrame:
        ext = os.path.splitext(filepath)[-1].lower()
        if ext == ".csv":
            df = pd.read_csv(filepath, dtype=str, low_memory=False)
        elif ext in (".xlsx", ".xls"):
            df = pd.read_excel(filepath, dtype=str)
        else:
            raise ValueError(f"Unsupported file type: {ext}")
        df.columns = df.columns.str.strip().str.lower().str.replace(" ", "_")
        if self.verbose:
            print(f"[DataLoader] Read {len(df)} rows × {len(df.columns)} cols from {filepath}")
        return df

    def _validate_schema(self, df: pd.DataFrame) -> pd.DataFrame:
        required = [
            SCHEMA["transaction_id"], SCHEMA["account_id"],
            SCHEMA["date"], SCHEMA["debit"], SCHEMA["credit"], SCHEMA["balance"],
        ]
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(f"Missing required columns: {missing}\nFound: {list(df.columns)}")

        # Add optional columns if absent
        for col in [SCHEMA["time"], SCHEMA["narration"], SCHEMA["channel"],
                    SCHEMA["counterparty_account"], SCHEMA["counterparty_name"],
                    SCHEMA["utr_ref"], SCHEMA["bank_name"], SCHEMA["account_holder"]]:
            if col not in df.columns:
                df[col] = np.nan
        return df

    def _parse_datetime(self, df: pd.DataFrame) -> pd.DataFrame:
        dates = _parse_date(df[SCHEMA["date"]])
        times = df[SCHEMA["time"]]

        # Combine date + time into a single datetime
        if times.notna().any():
            try:
                df["datetime"] = pd.to_datetime(
                    dates.astype(str) + " " + times.astype(str),
                    errors="coerce"
                )
            except Exception:
                df["datetime"] = dates
        else:
            df["datetime"] = dates

        df["date_parsed"] = df["datetime"].dt.date
        return df

    def _parse_amounts(self, df: pd.DataFrame) -> pd.DataFrame:
        df["debit_clean"]  = _clean_money(df[SCHEMA["debit"]])
        df["credit_clean"] = _clean_money(df[SCHEMA["credit"]])
        df["balance_clean"] = _clean_money(df[SCHEMA["balance"]])

        # Unified signed amount: credit = +, debit = -
        df["amount"] = df["credit_clean"] - df["debit_clean"]

        # Absolute amount (used in most features)
        df["abs_amount"] = df["amount"].abs()

        # Transaction type
        df["txn_type"] = np.where(
            df["credit_clean"] > 0, "credit",
            np.where(df["debit_clean"] > 0, "debit", "unknown")
        )
        return df

    def _clean_text(self, df: pd.DataFrame) -> pd.DataFrame:
        for col in [SCHEMA["narration"], SCHEMA["channel"],
                    SCHEMA["counterparty_name"], SCHEMA["bank_name"]]:
            df[col] = df[col].astype(str).str.strip().str.upper().replace("NAN", np.nan)
        return df

    def _detect_duplicates(self, df: pd.DataFrame) -> pd.DataFrame:
        dup_keys = [SCHEMA["transaction_id"], SCHEMA["account_id"],
                    SCHEMA["date"], "debit_clean", "credit_clean"]
        df["is_duplicate"] = df.duplicated(subset=dup_keys, keep="first")
        return df

    def _sort(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.sort_values(["account_id", "datetime"]).reset_index(drop=True)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        loader = DataLoader()
        df = loader.load(sys.argv[1])
        print(df.dtypes)
        print(df.head(3))
    else:
        print("Usage: python data_loader.py <path_to_csv>")