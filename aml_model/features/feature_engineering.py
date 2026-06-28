"""
Feature Engineering Pipeline
Built specifically for the schema:
  transaction_id, account_id, account_holder, bank_name, date, time,
  narration, channel, debit, credit, balance, counterparty_account_id,
  counterparty_name, utr_ref

Produces 55 features grouped into 8 families:
  1. Amount features
  2. Temporal features
  3. Account behavioural profile
  4. Velocity (rolling windows)
  5. Counterparty features
  6. Channel / narration features
  7. Balance features
  8. Structuring & round-number features
"""

import numpy as np
import pandas as pd
import warnings
warnings.filterwarnings("ignore")

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from configs.config import (
    SCHEMA, STRUCTURING_BANDS, RAPID_TRANSFER_MINUTES, VELOCITY_WINDOWS
)


class FeatureEngineer:

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self._fitted_stats = {}   # stores per-account stats for inference

    # ── public ───────────────────────────────────────────────────────────────

    def fit_transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """Train on + transform the full dataset."""
        df = df.copy()
        df = self._f1_amount(df)
        df = self._f2_temporal(df)
        df = self._f3_account_profile(df, fit=True)
        df = self._f4_velocity(df)
        df = self._f5_counterparty(df)
        df = self._f6_channel_narration(df)
        df = self._f7_balance(df)
        df = self._f8_structuring(df)
        if self.verbose:
            print(f"[FeatureEngineer] Generated {len(self.feature_columns())} features "
                  f"over {len(df)} transactions.")
        return df

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """Transform new data using stats fitted during fit_transform."""
        df = df.copy()
        df = self._f1_amount(df)
        df = self._f2_temporal(df)
        df = self._f3_account_profile(df, fit=False)
        df = self._f4_velocity(df)
        df = self._f5_counterparty(df)
        df = self._f6_channel_narration(df)
        df = self._f7_balance(df)
        df = self._f8_structuring(df)
        return df

    @staticmethod
    def feature_columns() -> list:
        return [
            # ── F1 amount ────────────────────────────────────────────
            "log_abs_amount",
            "is_credit", "is_debit",
            "amount_bucket",
            # ── F2 temporal ──────────────────────────────────────────
            "hour", "day_of_week", "day_of_month", "month",
            "is_weekend", "is_night", "is_odd_hour",
            "hour_sin", "hour_cos", "dow_sin", "dow_cos",
            "log_mins_since_last_txn",
            "rapid_transfer_flag",
            # ── F3 account profile ───────────────────────────────────
            "acc_avg_amount", "acc_std_amount", "acc_median_amount",
            "acc_max_amount", "acc_txn_count",
            "acc_credit_ratio", "acc_debit_ratio",
            "acc_txns_per_day",
            "amount_zscore_vs_account",
            # ── F4 velocity ──────────────────────────────────────────
            "vel_sum_1d", "vel_count_1d",
            "vel_sum_7d", "vel_count_7d",
            "vel_sum_30d", "vel_count_30d",
            # ── F5 counterparty ──────────────────────────────────────
            "n_unique_counterparties",
            "counterparty_txn_freq",
            "is_self_transfer",
            # ── F6 channel / narration ───────────────────────────────
            "channel_encoded",
            "narration_is_transfer",
            "narration_is_cash",
            "narration_is_salary",
            "narration_is_upi",
            # ── F7 balance ───────────────────────────────────────────
            "balance_after_zscore",
            "near_zero_balance",
            "balance_drop_pct",
            "balance_cv",
            # ── F8 structuring ───────────────────────────────────────
            "near_threshold",
            "is_round_1000", "is_round_5000", "is_round_10000",
            "acc_round_number_ratio",
            "acc_night_txn_ratio",
            "acc_rapid_transfer_ratio",
        ]

    # ── F1: Amount features ───────────────────────────────────────────────────

    def _f1_amount(self, df):
        df["log_abs_amount"] = np.log1p(df["abs_amount"])
        df["is_credit"] = (df["txn_type"] == "credit").astype(int)
        df["is_debit"]  = (df["txn_type"] == "debit").astype(int)
        df["amount_bucket"] = pd.cut(
            df["abs_amount"],
            bins=[0, 1_000, 5_000, 10_000, 50_000, 1_00_000, 5_00_000, np.inf],
            labels=[0, 1, 2, 3, 4, 5, 6],
            include_lowest=True
        ).astype(float)
        return df

    # ── F2: Temporal features ─────────────────────────────────────────────────

    def _f2_temporal(self, df):
        dt = df["datetime"]
        df["hour"]         = dt.dt.hour.fillna(12).astype(int)
        df["day_of_week"]  = dt.dt.dayofweek.fillna(0).astype(int)
        df["day_of_month"] = dt.dt.day.fillna(1).astype(int)
        df["month"]        = dt.dt.month.fillna(1).astype(int)
        df["is_weekend"]   = (df["day_of_week"] >= 5).astype(int)
        df["is_night"]     = ((df["hour"] >= 22) | (df["hour"] <= 5)).astype(int)
        df["is_odd_hour"]  = ((df["hour"] >= 23) | (df["hour"] <= 4)).astype(int)

        # Cyclical encoding
        df["hour_sin"] = np.sin(2 * np.pi * df["hour"] / 24)
        df["hour_cos"] = np.cos(2 * np.pi * df["hour"] / 24)
        df["dow_sin"]  = np.sin(2 * np.pi * df["day_of_week"] / 7)
        df["dow_cos"]  = np.cos(2 * np.pi * df["day_of_week"] / 7)

        # Minutes since previous transaction for the SAME account
        df = df.sort_values([SCHEMA["account_id"], "datetime"])
        df["_prev_dt"] = df.groupby(SCHEMA["account_id"])["datetime"].shift(1)
        df["mins_since_last_txn"] = (
            (df["datetime"] - df["_prev_dt"]).dt.total_seconds() / 60
        ).fillna(99_999)
        df["log_mins_since_last_txn"] = np.log1p(df["mins_since_last_txn"])

        # Rapid transfer: < RAPID_TRANSFER_MINUTES minutes after a credit → debit
        prev_type = df.groupby(SCHEMA["account_id"])["txn_type"].shift(1)
        df["rapid_transfer_flag"] = (
            (df["txn_type"] == "debit") &
            (prev_type == "credit") &
            (df["mins_since_last_txn"] < RAPID_TRANSFER_MINUTES)
        ).astype(int)

        df.drop(columns=["_prev_dt"], inplace=True)
        return df

    # ── F3: Per-account behavioural profile ───────────────────────────────────

    def _f3_account_profile(self, df, fit: bool = True):
        acct = SCHEMA["account_id"]

        if fit:
            stats = df.groupby(acct)["abs_amount"].agg(
                acc_avg_amount="mean",
                acc_std_amount="std",
                acc_median_amount="median",
                acc_max_amount="max",
                acc_txn_count="count",
            ).reset_index()
            stats["acc_std_amount"] = stats["acc_std_amount"].fillna(0)

            credit_ratio = (
                df.groupby(acct)["is_credit"].mean()
                  .reset_index(name="acc_credit_ratio")
            )
            date_span = (
                df.groupby(acct)["datetime"]
                  .apply(lambda x: max(1, (x.max() - x.min()).days))
                  .reset_index(name="acc_active_days")
            )
            self._fitted_stats["profile"] = stats
            self._fitted_stats["credit_ratio"] = credit_ratio
            self._fitted_stats["date_span"] = date_span
        else:
            stats        = self._fitted_stats["profile"]
            credit_ratio = self._fitted_stats["credit_ratio"]
            date_span    = self._fitted_stats["date_span"]

        df = df.merge(stats, on=acct, how="left")
        df = df.merge(credit_ratio, on=acct, how="left")
        df = df.merge(date_span, on=acct, how="left")

        df["acc_credit_ratio"] = df["acc_credit_ratio"].fillna(0.5)
        df["acc_debit_ratio"]  = 1 - df["acc_credit_ratio"]
        df["acc_active_days"]  = df["acc_active_days"].fillna(1)
        df["acc_txns_per_day"] = df["acc_txn_count"] / (df["acc_active_days"] + 1)

        df["amount_zscore_vs_account"] = (
            (df["abs_amount"] - df["acc_avg_amount"]) /
            (df["acc_std_amount"] + 1e-9)
        ).clip(-10, 10)

        return df

    # ── F4: Velocity (rolling windows) ───────────────────────────────────────

    def _f4_velocity(self, df):
        """
        For each transaction, count / sum of amounts from the same account
        within 1-day, 7-day, 30-day look-back windows.
        Uses a vectorised approach for speed.
        """
        df = df.sort_values([SCHEMA["account_id"], "datetime"]).reset_index(drop=True)
        acct_col = SCHEMA["account_id"]

        for label, hours in VELOCITY_WINDOWS.items():
            sum_col   = f"vel_sum_{label}"
            count_col = f"vel_count_{label}"
            df[sum_col]   = 0.0
            df[count_col] = 0

            for acc_id, grp in df.groupby(acct_col):
                idx = grp.index.tolist()
                dates   = grp["datetime"].values
                amounts = grp["abs_amount"].values
                window  = np.timedelta64(hours, "h")

                sums   = np.zeros(len(grp))
                counts = np.zeros(len(grp), dtype=int)

                left = 0
                for right in range(len(grp)):
                    while dates[right] - dates[left] > window:
                        left += 1
                    sums[right]   = amounts[left:right].sum()
                    counts[right] = right - left

                df.loc[idx, sum_col]   = sums
                df.loc[idx, count_col] = counts

        return df

    # ── F5: Counterparty features ─────────────────────────────────────────────

    def _f5_counterparty(self, df):
        cp_col = SCHEMA["counterparty_account"]
        acct   = SCHEMA["account_id"]

        # Unique counterparties per account
        n_cp = (
            df.groupby(acct)[cp_col]
              .nunique()
              .reset_index(name="n_unique_counterparties")
        )
        df = df.merge(n_cp, on=acct, how="left")
        df["n_unique_counterparties"] = df["n_unique_counterparties"].fillna(0)

        # How often does this specific (account, counterparty) pair transact?
        pair_freq = (
            df.groupby([acct, cp_col])
              .size()
              .reset_index(name="counterparty_txn_freq")
        )
        df = df.merge(pair_freq, on=[acct, cp_col], how="left")
        df["counterparty_txn_freq"] = df["counterparty_txn_freq"].fillna(0)

        # Self-transfer detection: same holder name on both sides
        holder = SCHEMA["account_holder"]
        cp_name = SCHEMA["counterparty_name"]
        df["is_self_transfer"] = (
            df[holder].str.upper().str.strip() ==
            df[cp_name].str.upper().str.strip()
        ).astype(int)

        return df

    # ── F6: Channel / narration features ─────────────────────────────────────

    def _f6_channel_narration(self, df):
        CHANNEL_MAP = {
            "NEFT": 1, "RTGS": 2, "IMPS": 3, "UPI": 4,
            "ATM": 5, "POS": 6, "ONLINE": 7, "CASH": 8,
            "NAN": 0, "": 0,
        }
        df["channel_encoded"] = (
            df[SCHEMA["channel"]]
              .astype(str).str.upper().str.strip()
              .map(CHANNEL_MAP)
              .fillna(0)
              .astype(int)
        )

        narr = df[SCHEMA["narration"]].astype(str).str.upper()
        df["narration_is_transfer"] = narr.str.contains(
            r"TRANSFER|NEFT|RTGS|IMPS|TRF", regex=True).astype(int)
        df["narration_is_cash"]     = narr.str.contains(
            r"CASH|ATM|WITHDRAW|WDL", regex=True).astype(int)
        df["narration_is_salary"]   = narr.str.contains(
            r"SALARY|SAL|PAYROLL", regex=True).astype(int)
        df["narration_is_upi"]      = narr.str.contains(
            r"UPI|PHONEPE|GPAY|PAYTM|BHIM", regex=True).astype(int)

        return df

    # ── F7: Balance features ──────────────────────────────────────────────────

    def _f7_balance(self, df):
        acct = SCHEMA["account_id"]

        bal_stats = df.groupby(acct)["balance_clean"].agg(
            _bal_mean="mean", _bal_std="std"
        ).reset_index()
        bal_stats["_bal_std"] = bal_stats["_bal_std"].fillna(1)
        df = df.merge(bal_stats, on=acct, how="left")

        df["balance_after_zscore"] = (
            (df["balance_clean"] - df["_bal_mean"]) /
            (df["_bal_std"] + 1e-9)
        ).clip(-10, 10)

        df["near_zero_balance"] = (df["balance_clean"] < 500).astype(int)

        prev_bal = df.groupby(acct)["balance_clean"].shift(1)
        df["balance_drop_pct"] = (
            (prev_bal - df["balance_clean"]) / (prev_bal.abs() + 1e-9)
        ).fillna(0).clip(-5, 5)

        df["balance_cv"] = (df["_bal_std"] / (df["_bal_mean"].abs() + 1e-9)).clip(0, 10)

        df.drop(columns=["_bal_mean", "_bal_std"], inplace=True)
        return df

    # ── F8: Structuring & round-number features ───────────────────────────────

    def _f8_structuring(self, df):
        amt = df["abs_amount"]

        # Just-below threshold bands
        df["near_threshold"] = 0
        for lo, hi in STRUCTURING_BANDS:
            df["near_threshold"] |= ((amt >= lo) & (amt < hi)).astype(int)

        df["is_round_1000"]  = (amt % 1_000  == 0).astype(int)
        df["is_round_5000"]  = (amt % 5_000  == 0).astype(int)
        df["is_round_10000"] = (amt % 10_000 == 0).astype(int)

        # Per-account aggregates
        acct = SCHEMA["account_id"]
        rn_ratio = (
            df.groupby(acct)["is_round_1000"].mean()
              .reset_index(name="acc_round_number_ratio")
        )
        night_ratio = (
            df.groupby(acct)["is_night"].mean()
              .reset_index(name="acc_night_txn_ratio")
        )
        rapid_ratio = (
            df.groupby(acct)["rapid_transfer_flag"].mean()
              .reset_index(name="acc_rapid_transfer_ratio")
        )
        df = df.merge(rn_ratio,    on=acct, how="left")
        df = df.merge(night_ratio, on=acct, how="left")
        df = df.merge(rapid_ratio, on=acct, how="left")

        return df


if __name__ == "__main__":
    # Quick smoke-test with a tiny synthetic frame
    sample = pd.DataFrame({
        "transaction_id":       ["T1", "T2", "T3"],
        "account_id":           ["A1", "A1", "A2"],
        "account_holder":       ["Alice", "Alice", "Bob"],
        "bank_name":            ["SBI", "SBI", "HDFC"],
        "datetime":             pd.to_datetime(["2024-03-01 10:00", "2024-03-01 10:30", "2024-03-02 02:00"]),
        "narration":            ["NEFT TRANSFER", "ATM WITHDRAWAL", "UPI PAYMENT"],
        "channel":              ["NEFT", "ATM", "UPI"],
        "debit_clean":          [0, 5000, 0],
        "credit_clean":         [100000, 0, 8500],
        "balance_clean":        [100000, 95000, 8500],
        "abs_amount":           [100000, 5000, 8500],
        "amount":               [100000, -5000, 8500],
        "txn_type":             ["credit", "debit", "credit"],
        "counterparty_account_id": ["A3", None, "A4"],
        "counterparty_name":    ["Charlie", None, "Dave"],
        "utr_ref":              ["U1", "U2", "U3"],
    })
    fe = FeatureEngineer(verbose=True)
    out = fe.fit_transform(sample)
    cols = FeatureEngineer.feature_columns()
    present = [c for c in cols if c in out.columns]
    print(f"Feature columns present: {len(present)}/{len(cols)}")
    print(out[present].head())