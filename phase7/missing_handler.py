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

from audit_utils import _add, _action, _is_blank
from cleaning_config import (
    MISSING_TIME_DEFAULT, MISSING_NARRATION_FILL, MISSING_AMOUNT_FILL,
)


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
    # Vectorized (Task 1): use .loc instead of row-by-row .at assignment
    if "account_id" in df.columns:
        mask = _is_blank(df["account_id"])
        report["missing_account_id"] = int(mask.sum())
        if mask.any():
            df.loc[mask, "clean_flags"] = df.loc[mask, "clean_flags"].apply(
                lambda f: _add(f, "MISSING_ACCOUNT_ID")
            )
            for idx in df.index[mask]:
                actions.append(_action(df, idx, "MISSING_ACCOUNT_ID",
                    "account_id is empty — row kept but cannot be grouped with "
                    "any account for dedup/continuity/outlier/velocity checks"))

    # ── date — already flagged NULL_DATE upstream; just count it here ──
    if "date" in df.columns:
        report["missing_date"] = int(_is_blank(df["date"]).sum())

    # ── narration — safe to fill with NULL (spec: not a text placeholder) ──
    # Vectorized (Task 1): bulk assignment with .loc
    if "narration" in df.columns:
        mask = _is_blank(df["narration"])
        if mask.any():
            # Store original values for action log
            original_narrations = df.loc[mask, "narration"].copy()
            # Bulk assignment
            df.loc[mask, "narration"] = MISSING_NARRATION_FILL
            df.loc[mask, "clean_flags"] = df.loc[mask, "clean_flags"].apply(
                lambda f: _add(f, "MISSING_NARRATION_FILLED")
            )
            # Log actions (still need individual rows for audit trail)
            for idx in df.index[mask]:
                original = original_narrations.loc[idx]
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
    # Task 6: Tighten coupling with clean_amounts — if _missing_<col>
    # columns aren't present, this is a loud assertion failure rather than
    # a silent fallback to `.isna()` (which would quietly turn off missing-
    # amount detection if clean_amounts() is ever refactored and forgets to
    # populate these masks).
    if "_missing_debit" not in df.columns or "_missing_credit" not in df.columns:
        raise AssertionError(
            "handle_missing_values() requires _missing_debit and _missing_credit "
            "columns to be present (these should be created by clean_amounts() in "
            "Module 1b, validator.py). If you see this error, clean_amounts() was "
            "either not run, or was refactored and no longer populates these masks. "
            "Missing-amount detection cannot proceed without them."
        )

    debit_missing_mask  = df["_missing_debit"].fillna(False).astype(bool)
    credit_missing_mask = df["_missing_credit"].fillna(False).astype(bool)

    both_missing_mask = debit_missing_mask & credit_missing_mask
    report["both_amounts_missing"] = int(both_missing_mask.sum())
    if both_missing_mask.any():
        # Vectorized (Task 1): bulk flag assignment
        df.loc[both_missing_mask, "clean_flags"] = df.loc[both_missing_mask, "clean_flags"].apply(
            lambda f: _add(f, "BOTH_AMOUNTS_MISSING")
        )
        for idx in df.index[both_missing_mask]:
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
        if fill_mask.any():
            # Vectorized (Task 1): bulk assignment
            df.loc[fill_mask, col] = MISSING_AMOUNT_FILL
            df.loc[fill_mask, "clean_flags"] = df.loc[fill_mask, "clean_flags"].apply(
                lambda f: _add(f, "MISSING_AMOUNT_FILLED")
            )
            for idx in df.index[fill_mask]:
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
        if mask.any():
            # Vectorized (Task 1): bulk flag assignment
            df.loc[mask, "clean_flags"] = df.loc[mask, "clean_flags"].apply(
                lambda f: _add(f, "MISSING_BALANCE")
            )
            for idx in df.index[mask]:
                actions.append(_action(df, idx, "MISSING_BALANCE",
                    "balance is missing — cannot verify continuity for this row "
                    "or safely use it as the previous-balance anchor for the next row"))

    # ── utr_ref — flag only, informational (normal for cash/ATM/cheque) ─
    if "utr_ref" in df.columns:
        mask = _is_blank(df["utr_ref"])
        report["missing_utr"] = int(mask.sum())
        if mask.any():
            # Vectorized (Task 1): bulk flag assignment
            df.loc[mask, "clean_flags"] = df.loc[mask, "clean_flags"].apply(
                lambda f: _add(f, "MISSING_UTR")
            )
            for idx in df.index[mask]:
                actions.append(_action(df, idx, "MISSING_UTR",
                    "utr_ref is empty — expected for cash/ATM/cheque rows, "
                    "informational only"))

    # ── time — safe to default to midnight ───────────────────────────────
    if "time" in df.columns:
        mask = _is_blank(df["time"])
        if mask.any():
            # Vectorized (Task 1): bulk assignment
            df.loc[mask, "time"] = MISSING_TIME_DEFAULT
            df.loc[mask, "clean_flags"] = df.loc[mask, "clean_flags"].apply(
                lambda f: _add(f, "MISSING_TIME_DEFAULTED")
            )
            for idx in df.index[mask]:
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