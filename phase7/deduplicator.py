"""
Phase 7 — Deduplicator  (v2)

Three duplicate classes now (up from two):

  EXACT  — same account_id + date + narration + debit + credit.
           Caused by re-uploaded statements or overlapping export windows.
           Safe to drop; removed rows are saved to the audit file.

  NEAR   — same account + date + time + amounts + resulting balance, narration differs slightly.
           Caused by OCR variation or bank narration truncation across
           channels. FLAGGED but kept — a human must confirm before removal.

  UTR COLLISION (new) — same utr_ref appears on two different rows that
           are NOT exact duplicates (different account, different amount,
           or both). A genuine UTR is supposed to be globally unique;
           a collision usually means either (a) two halves of the same
           transfer correctly appearing on both legs' statements — expected
           and fine — or (b) a forged/garbled reference number worth a
           second look. FLAGGED, never dropped.

Returns a 3-tuple: (cleaned_df, report_dict, audit_dict)
  audit_dict has three DataFrames: exact_removed, near_flagged,
  utr_collisions — all written to separate CSV files by clean.py.
"""

import pandas as pd
from cleaning_config import EXACT_DEDUP_KEYS, NEAR_DEDUP_KEYS, UTR_DEDUP_ENABLED


def run_deduplication(df: pd.DataFrame) -> tuple[pd.DataFrame, dict, dict]:
    report = {
        "exact_duplicates_found":   0,
        "near_duplicates_flagged":  0,
        "utr_collisions_flagged":   0,
        "rows_before":              len(df),
        "rows_after":               0,
    }

    df = df.copy()
    df["is_duplicate"]      = False
    df["is_utr_collision"]  = False

    # ── 1. Exact deduplication ────────────────────────────────────────────
    exact_mask    = df.duplicated(subset=EXACT_DEDUP_KEYS, keep="first")
    exact_removed = df.loc[exact_mask].copy().reset_index(drop=True)
    exact_removed["removal_reason"] = "EXACT_DUPLICATE"
    exact_removed["duplicate_of_row"] = (
        df[~exact_mask]
        .reset_index()
        .set_index(EXACT_DEDUP_KEYS)
        .reindex(exact_removed.set_index(EXACT_DEDUP_KEYS).index)["index"]
        .values
        if not exact_removed.empty else []
    )

    df.loc[exact_mask, "is_duplicate"] = True
    df = df[~exact_mask].reset_index(drop=True)
    report["exact_duplicates_found"] = int(exact_mask.sum())

    # ── 2. Near-duplicate flagging ────────────────────────────────────────
    near_flag_idx = []
    for _, group in df.groupby(NEAR_DEDUP_KEYS, sort=False):
        if len(group) >= 2:
            near_flag_idx.extend(group.index[1:].tolist())

    near_flagged = pd.DataFrame()
    if near_flag_idx:
        df.loc[near_flag_idx, "is_duplicate"] = True
        near_flagged = df.loc[near_flag_idx].copy().reset_index(drop=True)
        near_flagged["flag_reason"] = "NEAR_DUPLICATE_SAME_AMOUNT_DIFFERENT_NARRATION"
        report["near_duplicates_flagged"] = len(near_flag_idx)

    # ── 3. UTR / reference-number collision detection (new) ────────────────
    # A UTR appearing on rows that are NOT already exact/near duplicates and
    # NOT a legitimate two-legged transfer pair (same amount, different
    # account, opposite debit/credit) is worth flagging for review.
    utr_collisions = pd.DataFrame()
    if UTR_DEDUP_ENABLED and "utr_ref" in df.columns:
        utr_idx = []
        electronic_channels = {"UPI", "IMPS", "NEFT", "RTGS", "WIRE"}
        non_empty_utr = df[df["utr_ref"].fillna("").astype(str).str.strip() != ""].copy()
        if "channel" in non_empty_utr.columns:
            channel = non_empty_utr["channel"].fillna("").astype(str).str.upper().str.strip()
            non_empty_utr = non_empty_utr[channel.isin(electronic_channels)]

        for utr, group in non_empty_utr.groupby("utr_ref", sort=False):
            if len(group) < 2:
                continue

            # Legitimate case: exactly 2 rows, different accounts, one
            # debit-leg + one credit-leg of matching amount — that's just
            # both sides of the same transfer appearing in two statements.
            if len(group) == 2:
                rows = group.to_dict("records")
                a, b = rows[0], rows[1]
                same_amount = (
                    abs(a.get("debit", 0) - b.get("credit", 0)) < 0.01
                    or abs(a.get("credit", 0) - b.get("debit", 0)) < 0.01
                )
                diff_account = a.get("account_id") != b.get("account_id")
                if same_amount and diff_account:
                    continue   # expected — both legs of one transfer

            utr_idx.extend(group.index.tolist())

        if utr_idx:
            df.loc[utr_idx, "is_utr_collision"] = True
            utr_collisions = df.loc[utr_idx].copy().reset_index(drop=True)
            utr_collisions["flag_reason"] = "UTR_COLLISION_NOT_MATCHING_TRANSFER_PAIR"
            report["utr_collisions_flagged"] = len(utr_idx)

    report["rows_after"] = len(df)

    audit = {
        "exact_duplicates_removed": exact_removed,
        "near_duplicates_flagged":  near_flagged if not near_flagged.empty else pd.DataFrame(),
        "utr_collisions":           utr_collisions if not utr_collisions.empty else pd.DataFrame(),
    }
    return df, report, audit
