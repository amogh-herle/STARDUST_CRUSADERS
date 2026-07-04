"""
Feature Engineering Pipeline — LEAKAGE-FREE VERSION
Schema:
  account_id, account_holder, bank_name, date, time, narration, channel,
  debit, credit, balance, utr_ref, counterparty_name, counterparty_account,
  counterparty_ifsc, source_file, source_format, ingestion_warnings

CRITICAL FIX vs previous version:
  All per-account and global statistics (mean, std, median of amounts,
  balances, etc.) are computed ONCE during fit_transform() on the TRAINING
  set and FROZEN. transform() on new/unseen data reuses those frozen stats
  instead of recomputing them from the new file.

  This prevents a single chaotic test account from poisoning its own
  baseline — the #1 correctness bug in the previous version.

Produces ~50 features grouped into 8 families.
"""

import numpy as np
import pandas as pd
import warnings
warnings.filterwarnings("ignore")


class FeatureEngineer:

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.fitted_ = False

        # ── frozen statistics learned at fit time ───────────────────────────
        self.global_stats_ = {}        # dataset-wide stats (amount mean/std, etc.)
        self.account_stats_ = None     # per-account stats DataFrame (account_id indexed)
        self.hour_stats_ = None        # per-hour amount stats
        self.fallback_account_stats_ = None  # population-average row, used for UNSEEN accounts

    # ── public API ───────────────────────────────────────────────────────────

    def fit_transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Fit all statistics on this dataset (treated as the training set)
        and return the fully-featured DataFrame.
        """
        df = df.copy()
        df = self._f1_amount(df)
        df = self._f2_temporal(df)

        self._fit_global_stats(df)
        self._fit_account_stats(df)
        self._fit_hour_stats(df)

        df = self._apply_account_profile(df)
        df = self._f4_velocity(df)          # velocity is inherently per-transaction, no leakage risk
        df = self._f5_counterparty(df, fit=True)
        df = self._f6_channel_narration(df)
        df = self._apply_balance_features(df, fit=True)
        df = self._f8_structuring(df, fit=True)

        self.fitted_ = True
        if self.verbose:
            present = [c for c in self.feature_columns() if c in df.columns]
            print(f"[FeatureEngineer] FIT complete — {len(present)} features over {len(df)} rows, "
                  f"{df['account_id'].nunique()} accounts.")
        return df

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Transform NEW/unseen data using the FROZEN statistics from fit_transform().

        For accounts that WERE in the training set, frozen stats are reused (no leakage).
        For accounts that are NEW/UNSEEN (e.g. a fresh upload at inference time), their
        own stats are computed directly from the rows present in this dataframe — because
        using the population-average fallback for a single-account file would make every
        feature meaningless (all z-scores collapse to ~0, model goes blind).

        The fallback population-average is only used when an unseen account has too few
        rows (<5) to compute reliable per-account stats.
        """
        if not self.fitted_:
            raise RuntimeError("Call fit_transform() on training data before transform().")

        df = df.copy()
        df = self._f1_amount(df)
        df = self._f2_temporal(df)

        # ── Compute live stats for genuinely unseen accounts ─────────────────
        unseen = set(df["account_id"]) - set(self.account_stats_.index)
        if unseen:
            if self.verbose:
                print(f"[FeatureEngineer] {len(unseen)} unseen account(s) — "
                      f"computing stats from their own rows in this file.")
            self._inject_unseen_account_stats(df, unseen)

        df = self._apply_account_profile(df)
        df = self._f4_velocity(df)
        df = self._f5_counterparty(df, fit=False)
        df = self._f6_channel_narration(df)
        df = self._apply_balance_features(df, fit=False)
        df = self._f8_structuring(df, fit=False)

        return df

    def _inject_unseen_account_stats(self, df: pd.DataFrame, unseen_ids: set):
        """
        For each unseen account, compute per-account stats from the rows in df
        and add them into self.account_stats_ so _apply_account_profile() picks
        them up via the normal merge path.

        Accounts with fewer than MIN_ROWS rows fall back to the population average
        (too little data to compute meaningful own stats).
        """
        MIN_ROWS = 5
        sub = df[df["account_id"].isin(unseen_ids)].copy()

        for acc_id, grp in sub.groupby("account_id"):
            if len(grp) < MIN_ROWS:
                # Too few rows — use population-average fallback for this account
                row = self.fallback_account_stats_.copy()
            else:
                active_days = max(1, (grp["datetime"].max() - grp["datetime"].min()).days)
                amt = grp["abs_amount"]
                bal = grp["balance"]

                row = pd.Series({
                    "acc_avg_amount":         float(amt.mean()),
                    "acc_std_amount":         float(amt.std()) if len(grp) > 1 else float(amt.mean() * 0.1),
                    "acc_median_amount":      float(amt.median()),
                    "acc_max_amount":         float(amt.max()),
                    "acc_txn_count":          len(grp),
                    "acc_credit_ratio":       float(grp["is_credit"].mean()),
                    "acc_active_days":        float(active_days),
                    "acc_round_number_ratio": float((amt % 1000 == 0).mean()),
                    "acc_night_txn_ratio":    float(grp["is_night"].mean()),
                    "acc_rapid_transfer_ratio": float(grp["rapid_transfer_flag"].mean()),
                    "n_unique_counterparties": float(
                        grp["counterparty_account"].nunique()
                        if "counterparty_account" in grp.columns else 0
                    ),
                    "acc_debit_ratio":        float(1 - grp["is_credit"].mean()),
                    "acc_txns_per_day":       float(len(grp) / (active_days + 1)),
                    "_bal_mean":              float(bal.mean()),
                    "_bal_std":               float(bal.std()) if len(grp) > 1 else float(bal.mean() * 0.1 + 1),
                })

            # Append to the frozen account_stats_ so the merge finds it
            self.account_stats_.loc[acc_id] = row

    @staticmethod
    def feature_columns() -> list:
        return [
            "log_abs_amount", "is_credit", "is_debit", "amount_bucket",
            "hour", "day_of_week", "day_of_month", "month",
            "is_weekend", "is_night", "is_odd_hour",
            "hour_sin", "hour_cos", "dow_sin", "dow_cos",
            "log_mins_since_last_txn", "rapid_transfer_flag",
            "acc_avg_amount", "acc_std_amount", "acc_median_amount",
            "acc_max_amount", "acc_txn_count",
            "acc_credit_ratio", "acc_debit_ratio", "acc_txns_per_day",
            "amount_zscore_vs_account",
            "vel_sum_1d", "vel_count_1d", "vel_sum_7d", "vel_count_7d",
            "vel_sum_30d", "vel_count_30d",
            "n_unique_counterparties", "counterparty_txn_freq", "is_self_transfer",
            "channel_encoded", "narration_is_transfer", "narration_is_cash",
            "narration_is_salary", "narration_is_upi",
            "balance_after_zscore", "near_zero_balance", "balance_drop_pct", "balance_cv",
            "near_threshold", "is_round_1000", "is_round_5000", "is_round_10000",
            "acc_round_number_ratio", "acc_night_txn_ratio", "acc_rapid_transfer_ratio",
        ]

    # ── F1: amount ───────────────────────────────────────────────────────────

    def _f1_amount(self, df):
        df["debit"] = pd.to_numeric(df.get("debit", 0), errors="coerce").fillna(0)
        df["credit"] = pd.to_numeric(df.get("credit", 0), errors="coerce").fillna(0)
        df["balance"] = pd.to_numeric(df.get("balance", 0), errors="coerce").fillna(0)

        df["amount"] = df["credit"] - df["debit"]
        df["abs_amount"] = df["amount"].abs()
        df["txn_type"] = np.where(df["credit"] > 0, "credit",
                          np.where(df["debit"] > 0, "debit", "unknown"))

        df["log_abs_amount"] = np.log1p(df["abs_amount"])
        df["is_credit"] = (df["txn_type"] == "credit").astype(int)
        df["is_debit"]  = (df["txn_type"] == "debit").astype(int)
        df["amount_bucket"] = pd.cut(
            df["abs_amount"],
            bins=[0, 1_000, 5_000, 10_000, 50_000, 1_00_000, 5_00_000, np.inf],
            labels=[0, 1, 2, 3, 4, 5, 6], include_lowest=True
        ).astype(float)
        return df

    # ── F2: temporal ─────────────────────────────────────────────────────────

    def _f2_temporal(self, df):
        date_col = df.get("date", pd.Series(["2024-01-01"] * len(df)))
        time_col = df.get("time", pd.Series(["00:00:00"] * len(df)))

        dt = pd.to_datetime(
            date_col.astype(str) + " " + time_col.astype(str),
            errors="coerce"
        )
        dt = dt.fillna(pd.to_datetime(date_col, errors="coerce"))
        dt = dt.fillna(pd.Timestamp("2024-01-01"))
        df["datetime"] = dt

        df["hour"]         = df["datetime"].dt.hour.fillna(12).astype(int)
        df["day_of_week"]  = df["datetime"].dt.dayofweek.fillna(0).astype(int)
        df["day_of_month"] = df["datetime"].dt.day.fillna(1).astype(int)
        df["month"]        = df["datetime"].dt.month.fillna(1).astype(int)
        df["is_weekend"]   = (df["day_of_week"] >= 5).astype(int)
        df["is_night"]     = ((df["hour"] >= 22) | (df["hour"] <= 5)).astype(int)
        df["is_odd_hour"]  = ((df["hour"] >= 23) | (df["hour"] <= 4)).astype(int)

        df["hour_sin"] = np.sin(2 * np.pi * df["hour"] / 24)
        df["hour_cos"] = np.cos(2 * np.pi * df["hour"] / 24)
        df["dow_sin"]  = np.sin(2 * np.pi * df["day_of_week"] / 7)
        df["dow_cos"]  = np.cos(2 * np.pi * df["day_of_week"] / 7)

        df = df.sort_values(["account_id", "datetime"]).reset_index(drop=True)
        df["_prev_dt"] = df.groupby("account_id")["datetime"].shift(1)
        df["mins_since_last_txn"] = (
            (df["datetime"] - df["_prev_dt"]).dt.total_seconds() / 60
        ).fillna(99_999)
        df["log_mins_since_last_txn"] = np.log1p(df["mins_since_last_txn"])

        prev_type = df.groupby("account_id")["txn_type"].shift(1)
        df["rapid_transfer_flag"] = (
            (df["txn_type"] == "debit") &
            (prev_type == "credit") &
            (df["mins_since_last_txn"] < 120)
        ).astype(int)

        df.drop(columns=["_prev_dt"], inplace=True)
        return df

    # ── FIT: global stats ───────────────────────────────────────────────────

    def _fit_global_stats(self, df):
        self.global_stats_ = {
            "amount_mean": float(df["abs_amount"].mean()),
            "amount_std":  float(df["abs_amount"].std() + 1e-9),
            "amount_median": float(df["abs_amount"].median()),
        }

    # ── FIT: per-account profile stats ──────────────────────────────────────

    def _fit_account_stats(self, df):
        stats = df.groupby("account_id")["abs_amount"].agg(
            acc_avg_amount="mean", acc_std_amount="std",
            acc_median_amount="median", acc_max_amount="max",
            acc_txn_count="count",
        )
        stats["acc_std_amount"] = stats["acc_std_amount"].fillna(stats["acc_std_amount"].median())
        stats["acc_std_amount"] = stats["acc_std_amount"].fillna(1.0)

        credit_ratio = df.groupby("account_id")["is_credit"].mean().rename("acc_credit_ratio")
        date_span = (
            df.groupby("account_id")["datetime"]
              .apply(lambda x: max(1, (x.max() - x.min()).days))
              .rename("acc_active_days")
        )
        round_ratio = (
            df.assign(_r1000=(df["abs_amount"] % 1000 == 0).astype(int))
              .groupby("account_id")["_r1000"].mean().rename("acc_round_number_ratio")
        )
        night_ratio = df.groupby("account_id")["is_night"].mean().rename("acc_night_txn_ratio")
        rapid_ratio = df.groupby("account_id")["rapid_transfer_flag"].mean().rename("acc_rapid_transfer_ratio")
        cp_count = (
            df.groupby("account_id")["counterparty_account"].nunique()
              .rename("n_unique_counterparties") if "counterparty_account" in df.columns
              else pd.Series(0, index=stats.index, name="n_unique_counterparties")
        )

        combined = stats.join([credit_ratio, date_span, round_ratio, night_ratio, rapid_ratio, cp_count])
        combined["acc_debit_ratio"]  = 1 - combined["acc_credit_ratio"]
        combined["acc_txns_per_day"] = combined["acc_txn_count"] / (combined["acc_active_days"] + 1)

        bal_stats = df.groupby("account_id")["balance"].agg(
            _bal_mean="mean", _bal_std="std"
        )
        bal_stats["_bal_std"] = bal_stats["_bal_std"].fillna(bal_stats["_bal_std"].median()).fillna(1.0)
        combined = combined.join(bal_stats)

        self.account_stats_ = combined

        # Population-average fallback row for unseen accounts at inference time
        self.fallback_account_stats_ = combined.mean(numeric_only=True)

    def _fit_hour_stats(self, df):
        self.hour_stats_ = (
            df.groupby("hour")["abs_amount"].agg(
                hour_mean_amount="mean", hour_std_amount="std"
            )
        )
        self.hour_stats_["hour_std_amount"] = (
            self.hour_stats_["hour_std_amount"].fillna(self.hour_stats_["hour_std_amount"].median())
        )

    # ── APPLY: account profile (uses frozen stats, works for fit AND transform) ─

    def _apply_account_profile(self, df):
        stats_cols = [
            "acc_avg_amount", "acc_std_amount", "acc_median_amount", "acc_max_amount",
            "acc_txn_count", "acc_credit_ratio", "acc_active_days", "acc_round_number_ratio",
            "acc_night_txn_ratio", "acc_rapid_transfer_ratio", "n_unique_counterparties",
            "acc_debit_ratio", "acc_txns_per_day", "_bal_mean", "_bal_std",
        ]
        lookup = self.account_stats_[stats_cols]

        df = df.merge(lookup, on="account_id", how="left", suffixes=("", "_dup"))

        # Unseen accounts → fill with population-average fallback
        for col in stats_cols:
            if col in df.columns:
                df[col] = df[col].fillna(self.fallback_account_stats_.get(col, 0))

        df["amount_zscore_vs_account"] = (
            (df["abs_amount"] - df["acc_avg_amount"]) / (df["acc_std_amount"] + 1e-9)
        ).clip(-10, 10)

        return df

    # ── F4: velocity (computed live — no leakage, purely sequential per row) ──

    def _f4_velocity(self, df):
        df = df.sort_values(["account_id", "datetime"]).reset_index(drop=True)
        windows = {"1d": 24, "7d": 168, "30d": 720}

        for label, hours in windows.items():
            sum_col, count_col = f"vel_sum_{label}", f"vel_count_{label}"
            df[sum_col] = 0.0
            df[count_col] = 0
            window = np.timedelta64(hours, "h")

            for acc_id, grp in df.groupby("account_id"):
                idx = grp.index.tolist()
                dates = grp["datetime"].values
                amounts = grp["abs_amount"].values
                sums = np.zeros(len(grp))
                counts = np.zeros(len(grp), dtype=int)
                left = 0
                for right in range(len(grp)):
                    while dates[right] - dates[left] > window:
                        left += 1
                    sums[right] = amounts[left:right].sum()
                    counts[right] = right - left
                df.loc[idx, sum_col] = sums
                df.loc[idx, count_col] = counts

        return df

    # ── F5: counterparty ─────────────────────────────────────────────────────

    def _f5_counterparty(self, df, fit: bool):
        cp_col = "counterparty_account" if "counterparty_account" in df.columns else None
        if cp_col is None:
            df["counterparty_txn_freq"] = 0
            df["is_self_transfer"] = 0
            return df

        pair_freq = df.groupby(["account_id", cp_col]).size().rename("counterparty_txn_freq")
        df = df.merge(pair_freq, on=["account_id", cp_col], how="left")
        df["counterparty_txn_freq"] = df["counterparty_txn_freq"].fillna(0)

        holder = df.get("account_holder", pd.Series([""] * len(df))).astype(str).str.upper().str.strip()
        cp_name = df.get("counterparty_name", pd.Series([""] * len(df))).astype(str).str.upper().str.strip()
        df["is_self_transfer"] = (holder == cp_name).astype(int)
        return df

    # ── F6: channel / narration ──────────────────────────────────────────────

    def _f6_channel_narration(self, df):
        CHANNEL_MAP = {
            "NEFT": 1, "RTGS": 2, "IMPS": 3, "UPI": 4,
            "ATM": 5, "POS": 6, "ONLINE": 7, "CASH": 8,
            "CHARGES": 9, "CHEQUE": 10, "OTHER": 0, "NAN": 0, "": 0,
        }
        df["channel_encoded"] = (
            df.get("channel", pd.Series([""] * len(df)))
              .astype(str).str.upper().str.strip()
              .map(CHANNEL_MAP).fillna(0).astype(int)
        )

        narr = df.get("narration", pd.Series([""] * len(df))).astype(str).str.upper()
        df["narration_is_transfer"] = narr.str.contains(r"TRANSFER|NEFT|RTGS|IMPS|TRF", regex=True).astype(int)
        df["narration_is_cash"]     = narr.str.contains(r"CASH|ATM|WITHDRAW|WDL", regex=True).astype(int)
        df["narration_is_salary"]   = narr.str.contains(r"SALARY|SAL|PAYROLL", regex=True).astype(int)
        df["narration_is_upi"]      = narr.str.contains(r"UPI|PHONEPE|GPAY|PAYTM|BHIM", regex=True).astype(int)
        return df

    # ── F7: balance — uses frozen mean/std from fit ─────────────────────────

    def _apply_balance_features(self, df, fit: bool):
        df["balance_after_zscore"] = (
            (df["balance"] - df["_bal_mean"]) / (df["_bal_std"] + 1e-9)
        ).clip(-10, 10)

        df["near_zero_balance"] = (df["balance"] < 500).astype(int)

        df = df.sort_values(["account_id", "datetime"])
        prev_bal = df.groupby("account_id")["balance"].shift(1)
        df["balance_drop_pct"] = (
            (prev_bal - df["balance"]) / (prev_bal.abs() + 1e-9)
        ).fillna(0).clip(-5, 5)

        df["balance_cv"] = (df["_bal_std"] / (df["_bal_mean"].abs() + 1e-9)).clip(0, 10)

        df.drop(columns=["_bal_mean", "_bal_std"], inplace=True, errors="ignore")
        return df

    # ── F8: structuring ──────────────────────────────────────────────────────

    def _f8_structuring(self, df, fit: bool):
        amt = df["abs_amount"]
        bands = [(9_000, 10_000), (49_000, 50_000), (99_000, 100_000), (199_000, 200_000)]
        df["near_threshold"] = 0
        for lo, hi in bands:
            df["near_threshold"] |= ((amt >= lo) & (amt < hi)).astype(int)

        df["is_round_1000"]  = (amt % 1_000  == 0).astype(int)
        df["is_round_5000"]  = (amt % 5_000  == 0).astype(int)
        df["is_round_10000"] = (amt % 10_000 == 0).astype(int)
        return df


if __name__ == "__main__":
    sample = pd.DataFrame({
        "account_id": ["A1", "A1", "A2"],
        "account_holder": ["Alice", "Alice", "Bob"],
        "bank_name": ["SBI", "SBI", "HDFC"],
        "date": ["2024-03-01", "2024-03-01", "2024-03-02"],
        "time": ["10:00:00", "10:30:00", "02:00:00"],
        "narration": ["NEFT TRANSFER", "ATM WITHDRAWAL", "UPI PAYMENT"],
        "channel": ["NEFT", "ATM", "UPI"],
        "debit": [0, 5000, 0],
        "credit": [100000, 0, 8500],
        "balance": [100000, 95000, 8500],
        "counterparty_account": ["A3", None, "A4"],
        "counterparty_name": ["Charlie", None, "Dave"],
        "utr_ref": ["U1", "U2", "U3"],
    })
    fe = FeatureEngineer(verbose=True)
    out = fe.fit_transform(sample)
    cols = [c for c in FeatureEngineer.feature_columns() if c in out.columns]
    print(f"Features present: {len(cols)}/{len(FeatureEngineer.feature_columns())}")
    print(out[cols].head())