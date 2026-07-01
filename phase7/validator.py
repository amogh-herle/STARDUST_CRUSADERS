"""
Phase 7 — Validator  (v2)

Eight passes in sequence (up from six). Every pass appends to clean_flags
and appends a row to the ACTION LOG so there is a complete per-row audit
trail of exactly what was done and why.

  Pass 1 : Date validation          — multi-format parse + canonicalise
  Pass 2 : Amount cleaning          — brackets, CR/DR, lakh/crore, currency
  Pass 3 : Balance continuity       — tamper detection (forensic priority)
  Pass 4 : Counterparty validation  — self-transfers, malformed IFSC (new)
  Pass 5 : Statistical outliers     — per-account IQR, not global threshold
  Pass 6 : Velocity flagging        — N+ debits in a short window
  Pass 7 : Narration / channel      — OCR noise strip, canonical channel names
  Pass 8 : Narration integrity      — empty/near-empty narration flag (new)

Nothing is silently dropped. Every modification is logged.
The action log is returned to clean.py and written to all_actions.csv.
"""

import re
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

from cleaning_config import (
    DATE_FORMATS_ACCEPTED, DATE_FORMAT_CANONICAL,
    DATE_VALID_YEAR_MIN, DATE_VALID_YEAR_MAX,
    AMOUNT_BRACKET_NEGATIVE, AMOUNT_STRIP_CURRENCY, AMOUNT_HANDLE_CR_DR,
    AMOUNT_HANDLE_LAKH_WORDS, CURRENCY_SYMBOLS, LAKH_CRORE_MULTIPLIERS,
    BALANCE_TOLERANCE, BALANCE_BREACH_ACCOUNT_FLAG_THRESHOLD,
    OUTLIER_IQR_MULTIPLIER, OUTLIER_MIN_TXN_COUNT,
    VELOCITY_WINDOW_MINUTES, VELOCITY_MIN_TXNS, VELOCITY_MIN_AMOUNT,
    FLAG_SELF_TRANSFER_SAME_ACCOUNT, FLAG_MALFORMED_IFSC,
    NARRATION_STRIP_CHARS, NARRATION_MIN_LEN_FLAG, CHANNEL_NORMALISE,
)


# ─────────────────────────────────────────────────────────────────────────────
# Pass 1 — Date Validation  (multi-format, canonicalises to YYYY-MM-DD)
# ─────────────────────────────────────────────────────────────────────────────
def validate_dates(df: pd.DataFrame) -> tuple[pd.DataFrame, dict, list]:
    """
    Phase 6 v2's normalizer should already emit YYYY-MM-DD, but this pass
    is defensive: it accepts any format in DATE_FORMATS_ACCEPTED and
    rewrites the column to the canonical form, so a format drift upstream
    (e.g. a new bank parser that forgot to canonicalise) doesn't silently
    break every downstream date comparison in Phase 8/9/10.
    """
    report = {
        "null_dates": 0, "bad_format_dates": 0, "out_of_range_dates": 0,
        "reformatted_dates": 0,
    }
    flags   = [""] * len(df)
    actions = []
    df = df.copy()

    for i, val in enumerate(df["date"]):
        s = str(val).strip() if pd.notna(val) else ""
        if not s:
            flags[i] = _add(flags[i], "NULL_DATE")
            report["null_dates"] += 1
            actions.append(_action(df, i, "NULL_DATE",
                "Date column is empty — row kept but date-dependent checks skipped"))
            continue

        parsed, matched_fmt = _parse_any_date(s)

        if parsed is None:
            flags[i] = _add(flags[i], "BAD_DATE_FORMAT")
            report["bad_format_dates"] += 1
            actions.append(_action(df, i, "BAD_DATE_FORMAT",
                f"'{s}' could not be parsed with any known format"))
            continue

        if not (DATE_VALID_YEAR_MIN <= parsed.year <= DATE_VALID_YEAR_MAX):
            flags[i] = _add(flags[i], "DATE_OUT_OF_RANGE")
            report["out_of_range_dates"] += 1
            actions.append(_action(df, i, "DATE_OUT_OF_RANGE",
                f"Date {s} is outside valid range "
                f"{DATE_VALID_YEAR_MIN}–{DATE_VALID_YEAR_MAX}"))

        canonical = parsed.strftime(DATE_FORMAT_CANONICAL)
        if canonical != s:
            df.iat[i, df.columns.get_loc("date")] = canonical
            report["reformatted_dates"] += 1
            actions.append(_action(df, i, "DATE_REFORMATTED",
                f"'{s}' (matched {matched_fmt}) → '{canonical}'"))

    df["_date_flags"] = flags
    return df, report, actions


def _parse_any_date(s: str):
    """Try every accepted format; return (datetime, format_string) or (None, None)."""
    for fmt in DATE_FORMATS_ACCEPTED:
        try:
            return datetime.strptime(s, fmt), fmt
        except ValueError:
            continue
    # Last resort: pandas' flexible parser (handles ISO timestamps etc.)
    try:
        ts = pd.to_datetime(s, errors="raise")
        return ts.to_pydatetime(), "pandas_flexible"
    except Exception:
        return None, None


# ─────────────────────────────────────────────────────────────────────────────
# Pass 2 — Amount Cleaning  (now handles lakh/crore word amounts too)
# ─────────────────────────────────────────────────────────────────────────────
def clean_amounts(df: pd.DataFrame) -> tuple[pd.DataFrame, dict, list]:
    report  = {
        "amount_corrections":     0,
        "zero_debit_credit_rows": 0,
        "lakh_crore_conversions": 0,
    }
    actions = []
    df      = df.copy()

    for col in ("debit", "credit", "balance"):
        for i, val in enumerate(df[col]):
            original = _safe_float(val)
            cleaned, was_lakh = _parse_amount_cell(val)
            df.iat[i, df.columns.get_loc(col)] = cleaned

            if was_lakh:
                report["lakh_crore_conversions"] += 1
                actions.append(_action(df, i, f"LAKH_CRORE_CONVERTED_{col.upper()}",
                    f"{col}: '{val}' → {cleaned} (word-multiplier expanded)"))
            elif abs(cleaned - original) > 1e-6 and not (
                original == 0.0 and str(val).strip().lower() in
                ("", "-", "--", "nil", "n/a", "nan", "none")
            ):
                report["amount_corrections"] += 1
                actions.append(_action(df, i, f"AMOUNT_CORRECTED_{col.upper()}",
                    f"{col}: '{val}' → {cleaned}"))

    zero_mask = (df["debit"] == 0.0) & (df["credit"] == 0.0)
    report["zero_debit_credit_rows"] = int(zero_mask.sum())
    for i in df.index[zero_mask]:
        actions.append(_action(df, i, "ZERO_DEBIT_AND_CREDIT",
            "Both debit and credit are zero — likely a summary/header row from PDF"))

    return df, report, actions


def _parse_amount_cell(val) -> tuple[float, bool]:
    """Returns (cleaned_amount, was_lakh_crore_word)."""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return 0.0, False
    s = str(val).strip()
    if s.lower() in ("", "-", "--", "nil", "n/a", "nan", "none"):
        return 0.0, False

    negative  = False
    was_lakh  = False

    if AMOUNT_BRACKET_NEGATIVE and s.startswith("(") and s.endswith(")"):
        s, negative = s[1:-1], True

    if AMOUNT_STRIP_CURRENCY:
        s = re.sub(CURRENCY_SYMBOLS, "", s, flags=re.IGNORECASE).strip()

    if re.match(r"^\s*-", s):
        negative = True

    # Lakh / Crore word multipliers — checked BEFORE CR/DR suffix stripping
    # since "2.5 Lakh" must not be confused with a trailing "...akh" eating
    # into a CR/DR match. Pattern requires the multiplier word to appear
    # as a whole word, not as a bare 2-letter "Cr" suffix (handled separately).
    if AMOUNT_HANDLE_LAKH_WORDS:
        m = re.search(
            r"([\d,]+\.?\d*)\s*(lakh|lac|crore)\b", s, flags=re.IGNORECASE
        )
        if m:
            base = float(m.group(1).replace(",", ""))
            mult = LAKH_CRORE_MULTIPLIERS[m.group(2).lower()]
            val_out = base * mult
            return (-abs(val_out) if negative else abs(val_out)), True

    if AMOUNT_HANDLE_CR_DR:
        su = s.upper()
        if re.search(r"(?i)(cr|c)\s*$", su):
            s = re.sub(r"(?i)(cr|c)\s*$", "", s).strip()
        elif re.search(r"(?i)(dr|d)\s*$", su):
            s = re.sub(r"(?i)(dr|d)\s*$", "", s).strip()
            negative = True
        if re.search(r"(?i)^\s*(cr|c)\b", s):
            s = re.sub(r"(?i)^\s*(cr|c)\b", "", s).strip()
        elif re.search(r"(?i)^\s*(dr|d)\b", s):
            s = re.sub(r"(?i)^\s*(dr|d)\b", "", s).strip()
            negative = True

    s = s.replace(",", "")
    s = re.sub(r"[^\d.\-]", "", s)
    if s.count(".") > 1:
        first, *rest = s.split(".")
        s = first + "." + "".join(rest)

    if not s:
        return 0.0, False
    try:
        return (-abs(float(s)) if negative else abs(float(s))), False
    except ValueError:
        return 0.0, False


def _safe_float(val) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Pass 3 — Balance Continuity  (unchanged logic, richer per-account stats)
# ─────────────────────────────────────────────────────────────────────────────
def validate_balance_continuity(df: pd.DataFrame) -> tuple[pd.DataFrame, dict, list]:
    """
    Forensic tamper-detection: prior_balance + credit - debit ≈ current_balance.
    A statement that doesn't reconcile is either OCR-corrupted or tampered.
    Both need investigator attention.
    """
    report = {
        "accounts_checked":       0,
        "accounts_with_breaches": 0,
        "total_breach_rows":      0,
        "max_single_breach_amt":  0.0,
        "balance_review_accounts":       [],
    }
    actions = []
    df = df.copy()
    df["is_balance_breach"] = False

    for account_id, group in df.groupby("account_id"):
        valid = group[group["date"].notna() & (group["date"] != "")].copy()
        valid = valid.sort_values(["date", "time"])
        if len(valid) < 2:
            continue

        report["accounts_checked"] += 1
        breach_indices = []
        max_diff_this_account = 0.0

        for i in range(1, len(valid)):
            prev_row = valid.iloc[i - 1]
            curr_row = valid.iloc[i]
            prev_bal = prev_row["balance"]
            curr_bal = curr_row["balance"]
            debit    = curr_row["debit"]
            credit   = curr_row["credit"]

            if prev_bal == 0 and curr_bal == 0:
                continue

            expected = round(prev_bal + credit - debit, 2)
            actual   = round(curr_bal, 2)
            diff     = abs(expected - actual)

            if diff > BALANCE_TOLERANCE:
                idx = curr_row.name
                breach_indices.append(idx)
                max_diff_this_account = max(max_diff_this_account, diff)
                actions.append(_action(df, idx, "BALANCE_BREACH",
                    f"Expected balance {expected} but got {actual} "
                    f"(diff ₹{diff:.2f}) — possible tamper or OCR error"))

        if breach_indices:
            breach_ratio = len(breach_indices) / len(valid)
            df.loc[breach_indices, "is_balance_breach"] = True
            report["total_breach_rows"]      += len(breach_indices)
            report["accounts_with_breaches"] += 1
            report["max_single_breach_amt"]   = max(
                report["max_single_breach_amt"], max_diff_this_account
            )

            if breach_ratio > BALANCE_BREACH_ACCOUNT_FLAG_THRESHOLD:
                report["balance_review_accounts"].append({
                    "account_id":   account_id,
                    "breach_ratio": round(breach_ratio, 3),
                    "breach_rows":  len(breach_indices),
                    "total_rows":   len(valid),
                    "max_breach_amount": round(max_diff_this_account, 2),
                    "severity":     "HIGH" if breach_ratio > 0.5 else "MEDIUM",
                })

    return df, report, actions


# ─────────────────────────────────────────────────────────────────────────────
# Pass 4 — Counterparty Validation  (new — uses Phase 6 v2 counterparty cols)
# ─────────────────────────────────────────────────────────────────────────────
def validate_counterparties(df: pd.DataFrame) -> tuple[pd.DataFrame, dict, list]:
    """
    Phase 6 v2 surfaces counterparty_account and counterparty_ifsc wherever
    the source statement provided them (Paytm XLSX, BOB/Federal XLSX, etc).
    Two checks here:

      SELF_TRANSFER  — counterparty_account == account_id. Either a benign
                        own-account sweep, or (more interesting for an
                        investigator) a layering hop disguised as a
                        same-name internal transfer. Flagged, not dropped.

      MALFORMED_IFSC — counterparty_ifsc present but doesn't match the
                        structural IFSC rule (11 chars, first 4 alpha,
                        5th char literal '0', last 6 alphanumeric).
                        Signals OCR corruption or a fabricated reference.
    """
    report  = {"self_transfers_flagged": 0, "malformed_ifsc_flagged": 0}
    actions = []
    df = df.copy()
    df["is_self_transfer"]   = False
    df["is_malformed_ifsc"]  = False

    has_cp_account = "counterparty_account" in df.columns
    has_cp_ifsc    = "counterparty_ifsc" in df.columns

    ifsc_pattern = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")

    if FLAG_SELF_TRANSFER_SAME_ACCOUNT and has_cp_account:
        cp = df["counterparty_account"].fillna("").astype(str).str.strip()
        acc = df["account_id"].fillna("").astype(str).str.strip()
        self_mask = (cp != "") & (cp == acc)
        df.loc[self_mask, "is_self_transfer"] = True
        report["self_transfers_flagged"] = int(self_mask.sum())
        for idx in df.index[self_mask]:
            actions.append(_action(df, idx, "SELF_TRANSFER",
                f"counterparty_account equals account_id ({df.loc[idx, 'account_id']}) "
                f"— same-account transfer, review for disguised layering hop"))

    if FLAG_MALFORMED_IFSC and has_cp_ifsc:
        ifsc_vals = df["counterparty_ifsc"].fillna("").astype(str).str.strip().str.upper()
        present_mask = ifsc_vals != ""
        malformed_mask = present_mask & ~ifsc_vals.apply(lambda v: bool(ifsc_pattern.match(v)))
        df.loc[malformed_mask, "is_malformed_ifsc"] = True
        report["malformed_ifsc_flagged"] = int(malformed_mask.sum())
        for idx in df.index[malformed_mask]:
            actions.append(_action(df, idx, "MALFORMED_IFSC",
                f"counterparty_ifsc '{df.loc[idx, 'counterparty_ifsc']}' does not match "
                f"the standard IFSC pattern (4 letters + 0 + 6 alphanumeric)"))

    return df, report, actions


# ─────────────────────────────────────────────────────────────────────────────
# Pass 5 — Statistical Outlier Flagging  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────
def flag_statistical_outliers(df: pd.DataFrame) -> tuple[pd.DataFrame, dict, list]:
    """
    Per-account IQR outlier detection — not global rupee thresholds.
    A ₹10L transaction is normal for a business account but an outlier
    for a dormant student account. The ML layer uses this as a feature.
    """
    report  = {"accounts_analysed": 0, "outlier_rows_flagged": 0}
    actions = []
    df      = df.copy()
    df["is_high_value_flag"] = False

    for account_id, group in df.groupby("account_id"):
        if len(group) < OUTLIER_MIN_TXN_COUNT:
            continue
        report["accounts_analysed"] += 1

        for col in ("debit", "credit"):
            nonzero = group[group[col] > 0][col]
            if len(nonzero) < 4:
                continue
            q1, q3 = nonzero.quantile(0.25), nonzero.quantile(0.75)
            iqr = q3 - q1
            if iqr == 0:
                continue
            fence = q3 + OUTLIER_IQR_MULTIPLIER * iqr
            mask  = group[col] > fence

            for idx in group[mask].index:
                amount = group.loc[idx, col]
                df.loc[idx, "is_high_value_flag"] = True
                actions.append(_action(df, idx, f"HIGH_VALUE_{col.upper()}",
                    f"{col} ₹{amount:,.2f} exceeds IQR fence ₹{fence:,.2f} "
                    f"(Q3={q3:,.2f}, IQR={iqr:,.2f}) for account {account_id}"))
                report["outlier_rows_flagged"] += 1

    return df, report, actions


# ─────────────────────────────────────────────────────────────────────────────
# Pass 6 — Velocity Flagging  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────
def flag_velocity_bursts(df: pd.DataFrame) -> tuple[pd.DataFrame, dict, list]:
    """
    Flag accounts where N+ debit transactions occur within a short window.
    Classic mule account signal — money arrives, then multiple rapid
    outbound transfers follow within minutes.
    """
    report  = {"velocity_accounts_flagged": 0, "velocity_rows_flagged": 0}
    actions = []
    df      = df.copy()
    df["is_velocity_flag"] = False

    for account_id, group in df.groupby("account_id"):
        timed = group[
            group["date"].notna() & (group["date"] != "") &
            group["time"].notna() & (group["time"] != "00:00:00")
        ].copy()

        if len(timed) < VELOCITY_MIN_TXNS:
            continue

        try:
            timed["_dt"] = pd.to_datetime(
                timed["date"] + " " + timed["time"], errors="coerce"
            )
            timed = timed.dropna(subset=["_dt"]).sort_values("_dt")
        except Exception:
            continue

        debits = timed[timed["debit"] >= VELOCITY_MIN_AMOUNT].copy()
        if len(debits) < VELOCITY_MIN_TXNS:
            continue

        window = timedelta(minutes=VELOCITY_WINDOW_MINUTES)
        flagged_set = set()

        for i in range(len(debits)):
            t0    = debits.iloc[i]["_dt"]
            burst = debits[(debits["_dt"] >= t0) & (debits["_dt"] <= t0 + window)]
            if len(burst) >= VELOCITY_MIN_TXNS:
                for idx in burst.index:
                    if idx not in flagged_set:
                        flagged_set.add(idx)
                        total = burst["debit"].sum()
                        actions.append(_action(df, idx, "VELOCITY_BURST",
                            f"{len(burst)} debits totalling ₹{total:,.2f} "
                            f"within {VELOCITY_WINDOW_MINUTES} min for account {account_id}"))

        if flagged_set:
            df.loc[list(flagged_set), "is_velocity_flag"] = True
            report["velocity_accounts_flagged"] += 1
            report["velocity_rows_flagged"] += len(flagged_set)

    return df, report, actions


# ─────────────────────────────────────────────────────────────────────────────
# Pass 7 — Narration & Channel Normalisation  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────
def normalise_text_fields(df: pd.DataFrame) -> tuple[pd.DataFrame, dict, list]:
    report  = {"narrations_cleaned": 0, "channels_normalised": 0}
    actions = []
    df      = df.copy()

    original_narrations = df["narration"].copy()
    df["narration"] = (
        df["narration"]
        .fillna("")
        .astype(str)
        .str.replace(NARRATION_STRIP_CHARS, " ", regex=True)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
        .str.upper()
    )
    changed_narr = df["narration"] != original_narrations.fillna("").str.upper()
    report["narrations_cleaned"] = int(changed_narr.sum())
    for i in df.index[changed_narr]:
        actions.append(_action(df, i, "NARRATION_CLEANED",
            f"OCR noise stripped: '{original_narrations.iloc[i]}' → '{df.loc[i,'narration']}'"))

    def _norm_ch(ch):
        ch = str(ch).strip().upper()
        return CHANNEL_NORMALISE.get(ch, ch)

    original_channels = df["channel"].copy()
    df["channel"]     = df["channel"].apply(_norm_ch)
    changed_ch        = df["channel"] != original_channels
    report["channels_normalised"] = int(changed_ch.sum())
    for i in df.index[changed_ch]:
        actions.append(_action(df, i, "CHANNEL_NORMALISED",
            f"'{original_channels.iloc[i]}' → '{df.loc[i,'channel']}'"))

    return df, report, actions


# ─────────────────────────────────────────────────────────────────────────────
# Pass 8 — Narration Integrity  (new)
# ─────────────────────────────────────────────────────────────────────────────
def flag_empty_narrations(df: pd.DataFrame) -> tuple[pd.DataFrame, dict, list]:
    """
    After Pass 7 normalisation, flag rows whose narration is empty or
    near-empty (below NARRATION_MIN_LEN_FLAG characters). This usually
    means the source column mapping picked the wrong field or OCR failed
    to extract anything meaningful — the row is still financially valid
    (debit/credit/balance present) but lacks the context an investigator
    needs to understand WHY the money moved.
    """
    report  = {"empty_narration_rows": 0}
    actions = []
    df = df.copy()

    narr = df["narration"].fillna("").astype(str).str.strip()
    short_mask = narr.str.len() < NARRATION_MIN_LEN_FLAG

    df["clean_flags"] = df.get("clean_flags", "")
    for idx in df.index[short_mask]:
        df.at[idx, "clean_flags"] = _add(df.at[idx, "clean_flags"], "EMPTY_OR_SHORT_NARRATION")
        actions.append(_action(df, idx, "EMPTY_OR_SHORT_NARRATION",
            f"Narration is empty or under {NARRATION_MIN_LEN_FLAG} chars after cleaning "
            f"— context may be missing for this transaction"))
    report["empty_narration_rows"] = int(short_mask.sum())

    return df, report, actions


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _add(existing: str, flag: str) -> str:
    existing = str(existing).strip() if existing else ""
    return flag if not existing else existing + " | " + flag


def _action(df: pd.DataFrame, row_idx, action_type: str, detail: str) -> dict:
    """Build one row for the all_actions.csv audit log."""
    try:
        row = df.iloc[row_idx] if isinstance(row_idx, int) else df.loc[row_idx]
        return {
            "row_index":    row_idx,
            "account_id":   row.get("account_id", ""),
            "date":         row.get("date", ""),
            "narration":    str(row.get("narration", ""))[:80],
            "debit":        row.get("debit", ""),
            "credit":       row.get("credit", ""),
            "balance":      row.get("balance", ""),
            "source_file":  row.get("source_file", ""),
            "action_type":  action_type,
            "detail":       detail,
        }
    except Exception:
        return {
            "row_index": row_idx, "account_id": "", "date": "",
            "narration": "", "debit": "", "credit": "", "balance": "",
            "source_file": "", "action_type": action_type, "detail": detail,
        }
