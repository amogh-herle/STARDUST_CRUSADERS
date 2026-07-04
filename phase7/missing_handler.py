"""
Phase 7 — Module 4: Missing Value Handler

This module did not exist prior to this fix (bug: architecture names it as
a required pipeline stage but no code implemented it). It runs AFTER
standardisation, deduplication, and validation, and BEFORE quality scoring
— exactly where the architecture diagram places it.

Design principle (same as everywhere else in Phase 7): nothing is silently
dropped or silently guessed. Two different strategies are used depending on
whether a field can be safely imputed:

  NO SAFE IMPUTATION — flag only, value left as-is:
    account_id   — guessing an account number would corrupt every grouping
                    operation downstream (dedup, balance continuity,
                    velocity, outliers). Flagged MISSING_ACCOUNT_ID.
    date         — already flagged NULL_DATE by validate_dates (Pass 1);
                    this module does not double-flag it, it only rolls the
                    count into the missing-value report for visibility.
    balance      — a missing balance can't be reconstructed without
                    replaying the account's full transaction history, and
                    guessing it would poison the balance-continuity check
                    for the row after it too. Flagged MISSING_BALANCE.

  SAFE IMPUTATION — value filled, original preserved in the action log:
    narration    — filled with MISSING_NARRATION_FILL (an actual NULL/empty
                    value per the revised spec, not a text placeholder),
                    flagged MISSING_NARRATION_FILLED.
    debit/credit — filled with MISSING_AMOUNT_FILL (0.0), flagged
                    MISSING_AMOUNT_FILLED — but ONLY when the OPPOSITE side
                    of the same row has a real (non-missing) value. A
                    genuinely missing amount cell where the opposite side
                    is populated (as opposed to one that parsed to 0 from
                    "-" or "Nil") is functionally the same as "no movement
                    recorded on this side" for continuity purposes. If BOTH
                    debit and credit are missing on the same row, neither
                    is auto-filled — that's a worse problem (e.g. a
                    mis-mapped amount column), flagged BOTH_AMOUNTS_MISSING
                    instead, per the same "don't guess" principle as
                    account_id/date/balance.
    time         — filled with MISSING_TIME_DEFAULT ("00:00:00"), flagged
                    MISSING_TIME_DEFAULTED. This matches the existing
                    assumption elsewhere in the codebase that a missing
                    time defaults to midnight.
    utr_ref      — left blank (not filled with a placeholder value, since
                    a fabricated reference would be actively misleading),
                    but flagged MISSING_UTR for visibility. Perfectly
                    normal for cash/ATM/cheque rows that have no reference
                    number by nature — informational, not a data quality
                    problem — so this flag is intentionally cheap in the
                    Module 5 quality-score penalty table.

Returns (df, report, actions) — same contract as every other pass in
validator.py, so clean.py can log it into all_actions.csv identically.
"""

import pandas as pd

from cleaning_config import (
    MISSING_TIME_DEFAULT, MISSING_NARRATION_FILL, MISSING_AMOUNT_FILL,
)


def _add(existing: str, flag: str) -> str:
    existing = str(existing).strip() if existing else ""
    return flag if not existing else existing + " | " + flag


def _action(df: pd.DataFrame, row_idx, action_type: str, detail: str) -> dict:
    try:
        row = df.loc[row_idx]
        return {
            "row_index":   row_idx,
            "account_id":  row.get("account_id", ""),
            "date":        row.get("date", ""),
            "narration":   str(row.get("narration", ""))[:80],
            "debit":       row.get("debit", ""),
            "credit":      row.get("credit", ""),
            "balance":     row.get("balance", ""),
            "source_file": row.get("source_file", ""),
            "action_type": action_type,
            "detail":      detail,
        }
    except Exception:
        return {
            "row_index": row_idx, "account_id": "", "date": "",
            "narration": "", "debit": "", "credit": "", "balance": "",
            "source_file": "", "action_type": action_type, "detail": detail,
        }


def _is_blank(series: pd.Series) -> pd.Series:
    """True where a value is NaN, None, or an empty/whitespace-only string."""
    return series.isna() | (series.astype(str).str.strip() == "")


def handle_missing_values(df: pd.DataFrame) -> tuple[pd.DataFrame, dict, list]:
    report = {
        "missing_account_id":        0,
        "missing_date":              0,
        "missing_narration_filled":  0,
        "missing_amount_filled":     0,
        "both_amounts_missing":      0,
        "missing_balance":           0,
        "missing_utr":               0,
        "missing_time_defaulted":    0,
    }
    actions = []
    df = df.copy()
    df["clean_flags"] = df.get("clean_flags", "")

    # ── account_id — flag only, never imputed ───────────────────────────
    if "account_id" in df.columns:
        mask = _is_blank(df["account_id"])
        report["missing_account_id"] = int(mask.sum())
        for idx in df.index[mask]:
            df.at[idx, "clean_flags"] = _add(df.at[idx, "clean_flags"], "MISSING_ACCOUNT_ID")
            actions.append(_action(df, idx, "MISSING_ACCOUNT_ID",
                "account_id is empty — row kept but cannot be grouped with "
                "any account for dedup/continuity/outlier/velocity checks"))

    # ── date — already flagged NULL_DATE upstream; just count it here ──
    if "date" in df.columns:
        report["missing_date"] = int(_is_blank(df["date"]).sum())

    # ── narration — safe to fill with NULL (spec: not a text placeholder) ──
    if "narration" in df.columns:
        mask = _is_blank(df["narration"])
        for idx in df.index[mask]:
            original = df.at[idx, "narration"]
            df.at[idx, "narration"] = MISSING_NARRATION_FILL
            df.at[idx, "clean_flags"] = _add(df.at[idx, "clean_flags"], "MISSING_NARRATION_FILLED")
            actions.append(_action(df, idx, "MISSING_NARRATION_FILLED",
                f"narration was empty ('{original}') — left NULL "
                f"(flagged, not fabricated with a placeholder string)"))
        report["missing_narration_filled"] = int(mask.sum())

    # ── debit / credit — safe to fill with 0.0 ──────────────────────────
    # Bug fix: clean_amounts() (Module 1b, runs before this module) already
    # coerces every blank/"-"/"Nil" cell to 0.0, so `.isna()` here always
    # comes back empty — this used to make missing-amount detection dead
    # code (confirmed by "Amount filled: 0" on every real run regardless
    # of actual data). Use the `_missing_<col>` mask clean_amounts leaves
    # behind (captured before it overwrote the value) instead, falling
    # back to `.isna()` only if that mask isn't present.
    # Bug fix (revised-spec compliance): only auto-fill a missing debit/
    # credit with 0 when the OPPOSITE side of the same row has a real,
    # non-missing value — i.e. the row is clearly a one-sided movement.
    # If BOTH sides are missing on the same row, filling both with 0 would
    # silently manufacture a fake "zero-value transaction" out of a row
    # that may simply have its amount column mis-mapped — that's flagged
    # instead, following the same "never guess" principle as account_id/
    # date/balance.
    debit_missing_mask  = (df.get("_missing_debit")  if "_missing_debit"  in df.columns
                            else pd.Series(False, index=df.index)).fillna(False).astype(bool)
    credit_missing_mask = (df.get("_missing_credit") if "_missing_credit" in df.columns
                            else pd.Series(False, index=df.index)).fillna(False).astype(bool)

    both_missing_mask = debit_missing_mask & credit_missing_mask
    report["both_amounts_missing"] = int(both_missing_mask.sum())
    for idx in df.index[both_missing_mask]:
        df.at[idx, "clean_flags"] = _add(df.at[idx, "clean_flags"], "BOTH_AMOUNTS_MISSING")
        actions.append(_action(df, idx, "BOTH_AMOUNTS_MISSING",
            "Both debit AND credit are missing on this row — not auto-filled "
            "(likely a mis-mapped amount column); flagged for investigator review"))

    for col, missing_mask, opposite_missing_mask in (
        ("debit",  debit_missing_mask,  credit_missing_mask),
        ("credit", credit_missing_mask, debit_missing_mask),
    ):
        if col not in df.columns:
            continue
        # Safe to fill only when this side is missing AND the opposite
        # side is NOT missing (i.e. genuinely a one-sided row).
        fill_mask = missing_mask & ~opposite_missing_mask
        for idx in df.index[fill_mask]:
            df.at[idx, col] = MISSING_AMOUNT_FILL
            df.at[idx, "clean_flags"] = _add(df.at[idx, "clean_flags"], "MISSING_AMOUNT_FILLED")
            actions.append(_action(df, idx, "MISSING_AMOUNT_FILLED",
                f"{col} was missing but the opposite side has a value — "
                f"filled {col} with {MISSING_AMOUNT_FILL}"))
        report["missing_amount_filled"] += int(fill_mask.sum())

    # ── balance — flag only, never imputed ───────────────────────────────
    # Same bug fix as debit/credit above: balance is also coerced to 0.0
    # by clean_amounts, so `.isna()` here was always empty. Use the
    # preserved `_missing_balance` mask instead.
    if "balance" in df.columns:
        if "_missing_balance" in df.columns:
            mask = df["_missing_balance"].fillna(False).astype(bool)
        else:
            mask = df["balance"].isna()
        report["missing_balance"] = int(mask.sum())
        for idx in df.index[mask]:
            df.at[idx, "clean_flags"] = _add(df.at[idx, "clean_flags"], "MISSING_BALANCE")
            actions.append(_action(df, idx, "MISSING_BALANCE",
                "balance is missing — cannot verify continuity for this row "
                "or safely use it as the previous-balance anchor for the next row"))

    # ── utr_ref — flag only, informational (normal for cash/ATM/cheque) ─
    if "utr_ref" in df.columns:
        mask = _is_blank(df["utr_ref"])
        report["missing_utr"] = int(mask.sum())
        for idx in df.index[mask]:
            df.at[idx, "clean_flags"] = _add(df.at[idx, "clean_flags"], "MISSING_UTR")
            actions.append(_action(df, idx, "MISSING_UTR",
                "utr_ref is empty — expected for cash/ATM/cheque rows, "
                "informational only"))

    # ── time — safe to default to midnight ───────────────────────────────
    if "time" in df.columns:
        mask = _is_blank(df["time"])
        for idx in df.index[mask]:
            df.at[idx, "time"] = MISSING_TIME_DEFAULT
            df.at[idx, "clean_flags"] = _add(df.at[idx, "clean_flags"], "MISSING_TIME_DEFAULTED")
            actions.append(_action(df, idx, "MISSING_TIME_DEFAULTED",
                f"time was empty — defaulted to {MISSING_TIME_DEFAULT}"))
        report["missing_time_defaulted"] = int(mask.sum())

    # Drop the temp masks clean_amounts left behind now that this module
    # (their sole intended consumer) has used them — mirrors the
    # `_date_flags` cleanup pattern in clean.py for validate_dates().
    df = df.drop(
        columns=[c for c in ("_missing_debit", "_missing_credit", "_missing_balance")
                 if c in df.columns],
        errors="ignore",
    )

    return df, report, actions