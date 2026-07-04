"""
post_processing.py
────────────────────
Two hardening layers applied AFTER the Isolation Forest scores transactions:

1. Entity Segmentation (Business vs Retail)
   Computes 30-day rolling volume per account and splits accounts into
   "retail" vs "business" BEFORE scoring. A business doing Rs.50,000/day in
   recurring vendor payments looks anomalous to a model trained mostly on
   retail behaviour — segmenting prevents that false positive class entirely.

2. Deterministic Suppression Rules (the "human logic" safety net)
   Wraps the raw ML flags: if a transaction is flagged BUT matches a known
   safe pattern (frequent counterparty, salary narration, business segment
   with usual volume), forcefully suppress the alert. This is what stops
   investigators from drowning in false positives ("alert fatigue").

Usage:
    from models.post_processing import segment_entities, apply_suppression_rules

    df = segment_entities(df)
    df = apply_suppression_rules(df)
"""

import pandas as pd
import numpy as np


# ── 1. Entity segmentation: Retail vs Business ────────────────────────────────

def segment_entities(df: pd.DataFrame,
                     business_volume_threshold: float = 500_000,
                     business_txn_threshold: int = 60,
                     business_avg_txn_threshold: float = 5_000) -> pd.DataFrame:
    """
    Classifies each account as "business" or "retail" using THREE signals,
    all normalised to a 30-day window to avoid penalising accounts just
    because they have a long history in the file.

    An account is "business" only if ALL of the following are true:
      1. volume_per_30d  >= business_volume_threshold  (default Rs.5,00,000)
      2. txns_per_30d    >= business_txn_threshold      (default 60 txns/30d)
      3. avg_txn_amount  >= business_avg_txn_threshold  (default Rs.5,000)

    WHY three conditions instead of one:
      - A student making 4 UPI payments/day (Rs.200 each) over 6 months would
        hit ~120 txns/30d but only Rs.24,000 volume/30d and Rs.200 avg —
        not a business, retail.
      - A shopkeeper with Rs.10L/30d turnover in 30 transactions of Rs.30,000
        each is clearly a business.
      - Using OR logic (any one condition triggers "business") was the bug —
        it caught high-frequency low-value retail accounts as businesses and
        suppressed all their alerts.
    """
    df = df.copy()
    if "datetime" not in df.columns:
        df["datetime"] = pd.to_datetime(df.get("date"), errors="coerce")
    else:
        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")

    debit_col  = "debit_clean" if "debit_clean" in df.columns else "debit"
    credit_col = "credit_clean" if "credit_clean" in df.columns else "credit"
    df["_abs_amount"] = (
        pd.to_numeric(df.get(debit_col, 0), errors="coerce").fillna(0) +
        pd.to_numeric(df.get(credit_col, 0), errors="coerce").fillna(0)
    )

    account_stats = df.groupby("account_id").agg(
        total_volume=("_abs_amount", "sum"),
        n_txns=("_abs_amount", "count"),
        avg_txn_amount=("_abs_amount", "mean"),
        active_days=("datetime", lambda x: max(1, (x.max() - x.min()).days)),
    )
    account_stats["volume_per_30d"] = (
        account_stats["total_volume"] / (account_stats["active_days"] / 30)
    )
    account_stats["txns_per_30d"] = (
        account_stats["n_txns"] / (account_stats["active_days"] / 30)
    )

    # ALL THREE conditions must be true — AND logic, not OR
    account_stats["entity_segment"] = np.where(
        (account_stats["volume_per_30d"]  >= business_volume_threshold) &
        (account_stats["txns_per_30d"]    >= business_txn_threshold)    &
        (account_stats["avg_txn_amount"]  >= business_avg_txn_threshold),
        "business", "retail"
    )

    df = df.merge(
        account_stats[["entity_segment", "volume_per_30d", "txns_per_30d", "avg_txn_amount"]],
        on="account_id", how="left"
    )
    df.drop(columns=["_abs_amount"], inplace=True, errors="ignore")
    return df


# ── 2. Deterministic suppression rules (human-logic safety net) ─────────────

def apply_suppression_rules(df: pd.DataFrame,
                              counterparty_freq_threshold: int = 5) -> pd.DataFrame:
    """
    Takes the ML-flagged output and suppresses alerts that match known-safe
    deterministic patterns. Adds two columns:
      - suppressed: True if the alert was overridden
      - suppression_reason: why (for audit trail — investigators can see
        exactly why an alert was silenced, nothing is a silent black box)

    Rules (any ONE match suppresses the alert):
      R1. counterparty_txn_freq > threshold
          -> this is a habitual, recurring payment relationship (e.g. rent,
             subscription, regular vendor) — not a one-off suspicious transfer
      R2. narration_is_salary == 1
          -> salary credit/debit narrations are near-never laundering
      R3. entity_segment == "business" AND amount is within its own normal range
          -> legitimate high-volume recurring business payment
    """
    df = df.copy()
    if "is_flagged" not in df.columns:
        raise ValueError("df must already have 'is_flagged' column from the model")

    df["suppressed"] = False
    df["suppression_reason"] = ""

    flagged_mask = df["is_flagged"] == 1

    # R1: frequent counterparty relationship
    if "counterparty_txn_freq" in df.columns:
        r1 = flagged_mask & (df["counterparty_txn_freq"] > counterparty_freq_threshold)
        df.loc[r1, "suppressed"] = True
        df.loc[r1, "suppression_reason"] += "frequent_counterparty;"

    # R2: salary narration
    if "narration_is_salary" in df.columns:
        r2 = flagged_mask & (df["narration_is_salary"] == 1)
        df.loc[r2, "suppressed"] = True
        df.loc[r2, "suppression_reason"] += "salary_pattern;"

    # R3: business segment with in-range amount (within 2x its own median)
    if "entity_segment" in df.columns and "abs_amount" in df.columns:
        business_mask = flagged_mask & (df["entity_segment"] == "business")
        if business_mask.any() and "acc_median_amount" in df.columns:
            in_range = business_mask & (df["abs_amount"] <= df["acc_median_amount"] * 2.5)
            df.loc[in_range, "suppressed"] = True
            df.loc[in_range, "suppression_reason"] += "business_normal_range;"

    # Final decision: an alert only stands if flagged AND not suppressed
    df["final_flag"] = (df["is_flagged"] == 1) & (~df["suppressed"])

    return df


# ── convenience: run both steps + print a before/after summary ──────────────

def harden_predictions(df: pd.DataFrame) -> pd.DataFrame:
    before = int(df["is_flagged"].sum()) if "is_flagged" in df.columns else 0

    df = segment_entities(df)
    df = apply_suppression_rules(df)

    after = int(df["final_flag"].sum())
    suppressed = int(df["suppressed"].sum())

    print(f"[PostProcessing] Raw ML flags:        {before}")
    print(f"[PostProcessing] Suppressed by rules:  {suppressed}")
    print(f"[PostProcessing] Final flags:          {after}")
    if "entity_segment" in df.columns:
        biz_rows    = (df['entity_segment'] == 'business').sum()
        retail_rows = (df['entity_segment'] == 'retail').sum()
        print(f"[PostProcessing] Business rows:        {biz_rows}  "
              f"(vol/30d >= 5L AND txns/30d >= 60 AND avg_txn >= 5K)")
        print(f"[PostProcessing] Retail rows:          {retail_rows}")

    return df


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "outputs/reports/isolation_forest_scored_transactions.csv"
    df = pd.read_csv(path, low_memory=False)
    df = harden_predictions(df)
    out_path = path.replace(".csv", "_hardened.csv")
    df.to_csv(out_path, index=False)
    print(f"\nSaved -> {out_path}")