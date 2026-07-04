"""
Phase 7 — Deduplicator  (v3)

Four duplicate/collision classes:

    EXACT  — same account_id + date + narration + debit + credit + balance,
                     AND (a) the row's key-group spans EXACTLY 2 distinct
                     source_files, i.e. a genuinely bilateral re-uploaded/
                     overlapping statement, OR (b) same source_file and
                     immediately adjacent in original row order, i.e. a
                     parser/OCR artifact literally repeating a line.
                     `time` is deliberately NOT part of this key: it's often
                     missing/defaulted to 00:00:00, and templated bank
                     narrations (bulk payment batches, standing instructions)
                     repeat verbatim across genuinely different transactions
                     — so amount+balance+narration alone (with or without
                     time) is not a safe duplicate key. Row position + source
                     file is what actually distinguishes "the same line
                     extracted twice" from "two different transactions that
                     happen to look alike". Removed rows are saved to the
                     audit file.

  NEAR   — same account + date + amounts, narration differs.
           Caused by OCR variation or bank narration truncation across
           channels. FLAGGED but kept — a human must confirm before removal.

  UTR COLLISION — same utr_ref appears on two different rows that are NOT
           exact duplicates (different account, different amount, or both).
           A genuine UTR is supposed to be globally unique; a collision
           usually means either (a) two halves of the same transfer
           correctly appearing on both legs' statements — expected and fine
           — or (b) a forged/garbled reference number worth a second look.
           FLAGGED, never dropped.

  MULTI-FILE KEY COLLISION (new — bug fix) — 3 or more distinct
           source_files all match the same exact-dedup key. A genuine
           re-upload is inherently a two-party event (one statement,
           uploaded twice); once 3+ files agree on the exact same key, the
           "genuine re-upload" explanation is materially weaker than the
           "coincidental collision on a templated/bulk narration" one — the
           same risk the EXACT rule already guards against for same-file
           matches, just previously left unguarded across files. Rows past
           the first in these groups are FLAGGED (is_multi_file_collision),
           never auto-removed.

Returns a 4-tuple: (cleaned_df, report_dict, audit_dict)
  audit_dict has four DataFrames: exact_removed, near_flagged,
  utr_collisions, multi_file_collisions — all written to separate CSV
  files by clean.py.
"""

import pandas as pd
from cleaning_config import (
    EXACT_DEDUP_KEYS, NEAR_DEDUP_KEYS, UTR_DEDUP_ENABLED,
    EXACT_DEDUP_SAME_FILE_MAX_GAP,
)

def _find_exact_duplicate_index(df: pd.DataFrame) -> tuple[list, list]:
    has_source_file = "source_file" in df.columns
    dup_positions = []
    collision_positions = []

    for _, group in df.groupby(EXACT_DEDUP_KEYS, sort=False, dropna=False):
        if len(group) < 2:
            continue
        group_sorted = group.sort_index()

        n_distinct_files = (
            group_sorted["source_file"].nunique() if has_source_file else 1
        )

        prev_idx = None
        prev_file = None
        for idx, row in group_sorted.iterrows():
            cur_file = row["source_file"] if has_source_file else None

            if prev_idx is None:
                prev_idx, prev_file = idx, cur_file
                continue

            same_file = (cur_file == prev_file) if has_source_file else True
            gap = idx - prev_idx

            has_valid_balance = pd.notna(row.get("balance")) and str(row.get("balance")).strip() != ""
            has_valid_utr = pd.notna(row.get("utr_ref")) and str(row.get("utr_ref")).strip() != ""

            if same_file:
                if gap <= EXACT_DEDUP_SAME_FILE_MAX_GAP:
                    if has_valid_balance or has_valid_utr:
                        dup_positions.append(idx)
                    else:
                        collision_positions.append(idx)
            else:
                # Auto-drop cross-file duplicates ONLY if it spans exactly 2 files 
                # AND we have a verified balance or UTR match.
                if n_distinct_files == 2 and (has_valid_balance or has_valid_utr):
                    dup_positions.append(idx)
                else:
                    collision_positions.append(idx)

            prev_idx, prev_file = idx, cur_file

    return dup_positions, collision_positions


def run_deduplication(df: pd.DataFrame) -> tuple[pd.DataFrame, dict, dict]:
    report = {
        "exact_duplicates_found":      0,
        "near_duplicates_flagged":     0,
        "utr_collisions_flagged":      0,
        "multi_file_collisions_flagged": 0,
        "rows_before":                 len(df),
        "rows_after":                  0,
    }

    df = df.copy()
    df["is_duplicate"]           = False
    df["is_utr_collision"]       = False
    df["is_multi_file_collision"] = False

    # ── 1. Exact deduplication (position/source_file-aware) ────────────────
    dup_idx, collision_idx = _find_exact_duplicate_index(df)
    exact_mask = pd.Series(False, index=df.index)
    exact_mask.loc[dup_idx] = True

    # Multi-file key collisions (3+ distinct files matching one exact key)
    # are kept, not removed — flagged for human review since they're a
    # weaker re-upload signal and a stronger coincidence-collision signal.
    collision_mask = pd.Series(False, index=df.index)
    collision_mask.loc[collision_idx] = True
    df.loc[collision_mask, "is_multi_file_collision"] = True
    df.loc[collision_mask, "clean_flags"] = df.loc[collision_mask, "clean_flags"].apply(
        lambda f: (str(f).strip() + " | " if str(f).strip() else "") + "EXACT_KEY_COLLISION_REVIEW_REQUIRED"
    )
    
    multi_file_collisions = df.loc[collision_mask].copy().reset_index(drop=True)
    if not multi_file_collisions.empty:
        multi_file_collisions["flag_reason"] = "COLLISION_CROSS_FILE_OR_MISSING_BALANCE"

    exact_removed = df.loc[exact_mask].copy().reset_index(drop=True)
    exact_removed["removal_reason"] = "EXACT_DUPLICATE"

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
        "multi_file_collisions":    multi_file_collisions if not multi_file_collisions.empty else pd.DataFrame(),
    }
    return df, report, audit