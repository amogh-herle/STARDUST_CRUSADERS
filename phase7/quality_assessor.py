"""
Phase 7 — Module 5: Quality Assessor

This module did not exist prior to this fix (bug: architecture names it as
the final pipeline stage but no code implemented it — no quality_score
column, no penalty table). It runs LAST, after every other pass has had a
chance to write to clean_flags or set a boolean flag column, so the score
reflects the row's complete final state.

Scoring model
-------------
Every row starts at QUALITY_SCORE_START (100) and loses points for each
distinct issue attached to it, from two sources:

  1. Tokens present in the `clean_flags` string (delimited by " | "),
     scored against QUALITY_PENALTIES_BY_FLAG_TOKEN.
  2. Boolean flag columns already on the frame (is_duplicate,
     is_balance_breach, etc.), scored against
     QUALITY_PENALTIES_BY_BOOL_COLUMN.

Both tables live in cleaning_config.py so the weights can be tuned without
touching code. Score is floored at 0. A row with no issues at all scores
100 (quality_band "HIGH").

quality_band is a coarse bucket over quality_score using
QUALITY_BAND_THRESHOLDS: >= HIGH threshold → "HIGH", >= MEDIUM threshold →
"MEDIUM", otherwise "LOW". This gives Phase 8/9/10 (and a human reviewer)
a fast way to triage without parsing the flag text themselves.

Returns (df, report, actions) — actions are only emitted for rows landing
in the LOW band, since a full per-row score explanation for every clean
row would just be noise in all_actions.csv.
"""

import pandas as pd

from cleaning_config import (
    QUALITY_SCORE_START, QUALITY_PENALTIES_BY_FLAG_TOKEN,
    QUALITY_PENALTIES_BY_BOOL_COLUMN, QUALITY_BAND_THRESHOLDS,
)


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


def _band_for(score: int) -> str:
    if score >= QUALITY_BAND_THRESHOLDS["HIGH"]:
        return "HIGH"
    if score >= QUALITY_BAND_THRESHOLDS["MEDIUM"]:
        return "MEDIUM"
    return "LOW"


def assess_quality(df: pd.DataFrame) -> tuple[pd.DataFrame, dict, list]:
    report = {
        "rows_scored":        len(df),
        "band_counts":        {"HIGH": 0, "MEDIUM": 0, "LOW": 0},
        "avg_quality_score":  0.0,
    }
    actions = []
    df = df.copy()

    flags_col = df.get("clean_flags", pd.Series([""] * len(df), index=df.index)).fillna("")

    scores = []
    for idx in df.index:
        score = QUALITY_SCORE_START
        reasons = []

        tokens = [t.strip() for t in str(flags_col.loc[idx]).split("|") if t.strip()]
        for tok in tokens:
            penalty = QUALITY_PENALTIES_BY_FLAG_TOKEN.get(tok)
            if penalty:
                score -= penalty
                reasons.append(f"{tok}(-{penalty})")

        for col, penalty in QUALITY_PENALTIES_BY_BOOL_COLUMN.items():
            if col in df.columns and bool(df.at[idx, col]):
                score -= penalty
                reasons.append(f"{col}(-{penalty})")

        score = max(0, score)
        scores.append(score)

        band = _band_for(score)
        report["band_counts"][band] += 1

        if band == "LOW":
            actions.append(_action(df, idx, "QUALITY_SCORE_LOW",
                f"score={score} band=LOW — penalties: {', '.join(reasons) if reasons else 'none'}"))

    df["quality_score"] = scores
    df["quality_band"]  = [_band_for(s) for s in scores]

    report["avg_quality_score"] = round(sum(scores) / len(scores), 2) if scores else 0.0

    return df, report, actions