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

from audit_utils import _action
from cleaning_config import (
    QUALITY_SCORE_START, QUALITY_PENALTIES_BY_FLAG_TOKEN,
    QUALITY_PENALTIES_BY_BOOL_COLUMN, QUALITY_BAND_THRESHOLDS,
)


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

    # Vectorized (Task 1): calculate scores using vectorized operations
    # Start all rows at 100
    scores = pd.Series([QUALITY_SCORE_START] * len(df), index=df.index)
    reasons_list = [[] for _ in range(len(df))]

    # Apply flag token penalties
    for idx in df.index:
        tokens = [t.strip() for t in str(flags_col.loc[idx]).split("|") if t.strip()]
        for tok in tokens:
            penalty = QUALITY_PENALTIES_BY_FLAG_TOKEN.get(tok)
            if penalty:
                scores.loc[idx] -= penalty
                reasons_list[idx].append(f"{tok}(-{penalty})")

    # Apply boolean column penalties (vectorized where possible)
    for col, penalty in QUALITY_PENALTIES_BY_BOOL_COLUMN.items():
        if col in df.columns:
            mask = df[col].astype(bool)
            scores.loc[mask] -= penalty
            for idx in df.index[mask]:
                reasons_list[idx].append(f"{col}(-{penalty})")

    # Floor scores at 0
    scores = scores.clip(lower=0)

    # Calculate bands
    bands = scores.apply(_band_for)

    # Count bands
    report["band_counts"] = bands.value_counts().to_dict()
    for band in ["HIGH", "MEDIUM", "LOW"]:
        if band not in report["band_counts"]:
            report["band_counts"][band] = 0

    # Log actions only for LOW band rows
    low_mask = bands == "LOW"
    for idx in df.index[low_mask]:
        score = scores.loc[idx]
        reasons = reasons_list[idx]
        actions.append(_action(df, idx, "QUALITY_SCORE_LOW",
            f"score={score} band=LOW — penalties: {', '.join(reasons) if reasons else 'none'}"))

    df["quality_score"] = scores
    df["quality_band"]  = bands

    report["avg_quality_score"] = round(scores.mean(), 2) if len(scores) > 0 else 0.0

    return df, report, actions