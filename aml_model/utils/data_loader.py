"""
DataLoader — reads transaction CSVs and normalises them.
Handles missing transaction_id, extra columns, alternate column names,
and all common date/amount formats.
"""

import pandas as pd
import numpy as np
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATE_FORMATS = [
    "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y",
    "%Y/%m/%d", "%d-%b-%Y", "%d %b %Y",
    "%m/%d/%Y", "%m-%d-%Y",
]
TIME_FORMATS = [
    "%H:%M:%S", "%H:%M", "%I:%M %p", "%I:%M:%S %p",
]


# ── helpers ───────────────────────────────────────────────────────────────────

def _clean_money(series: pd.Series) -> pd.Series:
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
            return pd.to_datetime(series, format=fmt, errors="raise")
        except Exception:
            continue
    return pd.to_datetime(series, infer_datetime_format=True, errors="coerce")


def _parse_time(series: pd.Series) -> pd.Series:
    for fmt in TIME_FORMATS:
        try:
            parsed = pd.to_datetime(
                "2000-01-01 " + series.astype(str),
                format=f"%Y-%m-%d {fmt}",
                errors="raise"
            )
            return parsed.dt.time
        except Exception:
            continue
    return pd.to_datetime(
        "2000-01-01 " + series.astype(str),
        infer_datetime_format=True,
        errors="coerce"
    ).dt.time


# ── main loader ───────────────────────────────────────────────────────────────

class DataLoader:

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.load_report_ = {}

    def load(self, filepath: str) -> pd.DataFrame:
        df = self._read_file(filepath)
        df = self._normalise_columns(df)
        df = self._map_alternate_names(df)
        df = self._add_missing_columns(df)
        df = self._parse_datetime(df)
        df = self._parse_amounts(df)
        df = self._clean_text(df)
        df = self._detect_duplicates(df)
        df = df.sort_values(["account_id", "datetime"]).reset_index(drop=True)

        self.load_report_ = {
            "total_rows":     len(df),
            "accounts":       df["account_id"].nunique(),
            "duplicates":     int(df["is_duplicate"].sum()),
            "date_errors":    int(df["datetime"].isna().sum()),
        }

        if self.verbose:
            print("[DataLoader] Load report:")
            for k, v in self.load_report_.items():
                print(f"   {k:25s}: {v}")

        return df

    # ── private ───────────────────────────────────────────────────────────────

    def _read_file(self, filepath: str) -> pd.DataFrame:
        ext = os.path.splitext(filepath)[-1].lower()
        if ext == ".csv":
            df = pd.read_csv(filepath, dtype=str, low_memory=False)
        elif ext in (".xlsx", ".xls"):
            df = pd.read_excel(filepath, dtype=str)
        else:
            raise ValueError(f"Unsupported file type: {ext}")
        if self.verbose:
            print(f"[DataLoader] Read {len(df)} rows x {len(df.columns)} cols from {filepath}")
        return df

    def _normalise_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        df.columns = (
            df.columns.str.strip()
                      .str.lower()
                      .str.replace(" ", "_")
                      .str.replace(r"[^a-z0-9_]", "", regex=True)
        )
        return df

    def _map_alternate_names(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Different exports use slightly different column names for the
        same concept. Map them onto the internal canonical name here.
        Add new entries to this dict whenever a new file format shows up.
        """
        rename_map = {
            "counterparty_account":     "counterparty_account_id",
            "counterparty_acc_id":      "counterparty_account_id",
            "counterparty_acc":         "counterparty_account_id",
            "cp_account_id":            "counterparty_account_id",
            "txn_id":                   "transaction_id",
            "txnid":                    "transaction_id",
            "acc_id":                   "account_id",
            "acc_holder":               "account_holder",
            "holder_name":              "account_holder",
            "bank":                     "bank_name",
            "txn_date":                 "date",
            "txn_time":                 "time",
            "description":              "narration",
            "remarks":                  "narration",
            "mode":                     "channel",
            "debit_amount":             "debit",
            "credit_amount":            "credit",
            "closing_balance":          "balance",
            "utr":                      "utr_ref",
            "utr_number":               "utr_ref",
            "reference_number":         "utr_ref",
        }
        for src, dst in rename_map.items():
            if src in df.columns and dst not in df.columns:
                df[dst] = df[src]
        return df

    def _add_missing_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        defaults = {
            "transaction_id":          lambda: pd.Series(["TXN" + uuid.uuid4().hex[:10].upper() for _ in range(len(df))]),
            "account_holder":          lambda: pd.Series(["UNKNOWN"] * len(df)),
            "bank_name":               lambda: pd.Series(["UNKNOWN"] * len(df)),
            "time":                    lambda: pd.Series(["00:00:00"] * len(df)),
            "narration":               lambda: pd.Series([""] * len(df)),
            "channel":                 lambda: pd.Series([""] * len(df)),
            "counterparty_account_id": lambda: pd.Series([np.nan] * len(df)),
            "counterparty_name":       lambda: pd.Series([np.nan] * len(df)),
            "utr_ref":                 lambda: pd.Series([np.nan] * len(df)),
        }
        for col, default_fn in defaults.items():
            if col not in df.columns:
                df[col] = default_fn()
                if self.verbose:
                    print(f"[DataLoader] Added missing column: {col}")

        # Required columns that MUST exist — fail loudly if not
        required = ["account_id", "date", "debit", "credit", "balance"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(
                f"Missing required columns: {missing}\n"
                f"Found columns: {list(df.columns)}\n"
                f"Add a mapping for these in _map_alternate_names() if they exist "
                f"under a different name in your file."
            )
        return df

    def _parse_datetime(self, df: pd.DataFrame) -> pd.DataFrame:
        dates = _parse_date(df["date"])
        times = df["time"]

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

        mask = df["datetime"].isna()
        if mask.any():
            df.loc[mask, "datetime"] = dates[mask]

        df["date_parsed"] = df["datetime"].dt.date
        return df

    def _parse_amounts(self, df: pd.DataFrame) -> pd.DataFrame:
        df["debit_clean"]   = _clean_money(df["debit"])
        df["credit_clean"]  = _clean_money(df["credit"])
        df["balance_clean"] = _clean_money(df["balance"])

        df["amount"]     = df["credit_clean"] - df["debit_clean"]
        df["abs_amount"] = df["amount"].abs()

        df["txn_type"] = np.where(
            df["credit_clean"] > 0, "credit",
            np.where(df["debit_clean"] > 0, "debit", "unknown")
        )
        return df

    def _clean_text(self, df: pd.DataFrame) -> pd.DataFrame:
        for col in ["narration", "channel", "counterparty_name", "bank_name"]:
            if col in df.columns:
                df[col] = (
                    df[col].astype(str)
                           .str.strip()
                           .str.upper()
                           .replace("NAN", np.nan)
                           .replace("", np.nan)
                )
        return df

    def _detect_duplicates(self, df: pd.DataFrame) -> pd.DataFrame:
        dup_keys = [c for c in ["transaction_id", "account_id", "date", "debit_clean", "credit_clean"]
                    if c in df.columns]
        df["is_duplicate"] = df.duplicated(subset=dup_keys, keep="first")
        return df


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        loader = DataLoader()
        df = loader.load(sys.argv[1])
        print(df.dtypes)
        print(df.head(3))
    else:
        print("Usage: python data_loader.py <path_to_csv>")