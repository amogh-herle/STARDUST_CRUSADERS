"""
Phase 7 — Shared Audit Utilities

Helper functions extracted from validator.py, missing_handler.py,
quality_assessor.py, deduplicator.py, and clean.py — previously copy-
pasted near-identically across all five modules. Centralized here to
eliminate duplication and ensure consistent audit-log behavior.

Functions:
  _add(existing, flag)       — merge a new flag token into clean_flags
  _merge_flag(existing, flag) — identical to _add (deduplicator's name)
  _action(df, idx, type, detail) — build one all_actions.csv row
  _is_blank(series)          — True where NaN/None/empty-string
"""

import pandas as pd


def _add(existing: str, flag: str) -> str:
    """
    Append a new flag token to an existing clean_flags string, delimited
    by " | ". If the token is already present, it is not duplicated.

    Args:
        existing: The current clean_flags value (may be empty, None, or
                  a " | "-delimited string).
        flag:     The new flag token to add.

    Returns:
        The merged clean_flags string.

    Examples:
        _add("", "FOO")                   → "FOO"
        _add("FOO", "BAR")                → "FOO | BAR"
        _add("FOO | BAR", "BAR")          → "FOO | BAR"  (no duplicate)
        _add("FOO | BAR", "BAZ")          → "FOO | BAR | BAZ"
    """
    existing = str(existing).strip() if existing else ""
    flag = str(flag).strip() if flag else ""
    if not flag:
        return existing
    if not existing:
        return flag
    # Check if the token is already present to avoid duplication
    existing_tokens = [t.strip() for t in existing.split("|")]
    if flag in existing_tokens:
        return existing
    return existing + " | " + flag


def _merge_flag(existing: str, new_flag: str) -> str:
    """
    Alias for _add() to match deduplicator.py's naming convention.
    Behavior is identical.
    """
    return _add(existing, new_flag)


def _action(df: pd.DataFrame, row_idx, action_type: str, detail: str) -> dict:
    """
    Build one row for the all_actions.csv audit log.

    Args:
        df:          The DataFrame (used to extract row context).
        row_idx:     The row's index (integer position or label).
        action_type: A short uppercase token describing the action
                     (e.g. "DATE_REFORMATTED", "BALANCE_MISMATCH_MAJOR").
        detail:      A human-readable explanation of what happened, why,
                     and what the before/after values were. Truncated to
                     80 chars for the narration field if necessary.

    Returns:
        A dict with keys: row_index, account_id, date, narration (max 80
        chars), debit, credit, balance, source_file, action_type, detail.
        If row extraction fails (e.g. index out of bounds), all row
        fields are left empty and only action_type/detail are populated.

    Design note:
        This function intentionally catches all exceptions and returns a
        valid (but sparse) dict rather than propagating the error — per
        the "never silently drop" design principle, an audit-log entry
        that says "something happened, but I couldn't capture the row
        context" is still better than no entry at all, and an unhandled
        exception here would abort the entire pipeline partway through
        with NO cleaned output written.
    """
    try:
        # Support both integer-position (iloc) and label-based (loc) indexing
        if isinstance(row_idx, int):
            row = df.iloc[row_idx]
        else:
            row = df.loc[row_idx]
        return {
            "row_index":   row_idx,
            "account_id":  row.get("account_id", ""),
            "date":        row.get("date", ""),
            "narration":   str(row.get("narration", ""))[:80],  # truncate to 80 chars
            "debit":       row.get("debit", ""),
            "credit":      row.get("credit", ""),
            "balance":     row.get("balance", ""),
            "source_file": row.get("source_file", ""),
            "action_type": action_type,
            "detail":      detail,
        }
    except Exception:
        # Fallback on exception: return a valid dict with empty row fields
        return {
            "row_index":   row_idx,
            "account_id":  "",
            "date":        "",
            "narration":   "",
            "debit":       "",
            "credit":      "",
            "balance":     "",
            "source_file": "",
            "action_type": action_type,
            "detail":      detail,
        }


def _is_blank(series: pd.Series) -> pd.Series:
    """
    Return a boolean Series indicating which values are blank (NaN, None,
    or an empty/whitespace-only string).

    Args:
        series: A pandas Series.

    Returns:
        A boolean Series of the same length, True where the value is
        missing or blank.

    Examples:
        _is_blank(pd.Series([None, "", "  ", "foo", np.nan]))
        → [True, True, True, False, True]
    """
    return series.isna() | (series.astype(str).str.strip() == "")
