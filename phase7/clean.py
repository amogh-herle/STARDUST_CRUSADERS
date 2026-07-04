"""
Phase 7 — Data Cleaning Engine  (v3)

Input : ingested_transactions.csv  (from Phase 6)
Output directory contains:

  cleaned_transactions.csv       ← ready for Phase 8 / 9 / 10
  removed_data.csv               ← ALL rows removed (exact duplicates)
                                    with reason column — court-ready audit
  flagged_data.csv               ← ALL rows kept but flagged, with every
                                    flag reason listed per row
  near_duplicates_flagged.csv    ← near-dupes, separated out for review
  utr_collisions_flagged.csv     ← UTR collisions, separated out for review
  all_actions.csv                ← row-level log of every single change
                                    made during cleaning (what, why, before/after)
  suspect_accounts.csv           ← accounts with balance integrity issues
  cleaning_report.json           ← machine-readable full audit trail
  quality_report.json            ← quality-assessment + duplicate-rate stats only
  cleaning_summary.txt           ← human-readable narrative summary

Design principle: NOTHING is silently dropped.
Every removal is in removed_data.csv with its reason.
Every flag is in flagged_data.csv with its reason.
Every value change is in all_actions.csv.
An investigator can reconstruct exactly what the system did.

Pipeline order (matches the Phase 7 architecture diagram exactly):

  1. Data Standardizer   — dates, amounts, narration/channel text, account IDs
  2. Duplicate Detector   — exact removal, near/UTR flagging
  3. Data Validator       — transaction-type, failed-transaction detection,
                             balance continuity (MINOR/MAJOR mismatch),
                             counterparty checks, outliers, velocity,
                             narration-integrity
  4. Missing Value Handler
  5. Quality Assessor     — final quality_score / quality_band per row

This ordering matters: standardizing BEFORE deduplication means dedup keys
are compared on canonical dates/amounts/narrations rather than raw,
inconsistently-formatted source values (v2 fix — dedup used to run first
and silently miss/mismatch duplicates that only look identical once
cleaned).

Usage:
    python clean.py --input ../phase6/ingested/ingested_transactions.csv
                    --out-dir cleaned/
"""

import os, json, argparse
import pandas as pd
from datetime import datetime

from cleaning_config import CLEANED_OUTPUT_COLS
from deduplicator     import run_deduplication
from validator        import (
    validate_dates,
    clean_amounts,
    normalise_text_fields,
    validate_transaction_types,
    flag_failed_transactions,
    validate_balance_continuity,
    validate_counterparties,
    flag_statistical_outliers,
    flag_velocity_bursts,
    flag_empty_narrations,
)
from missing_handler   import handle_missing_values
from quality_assessor  import assess_quality


def run_cleaning_pipeline(input_path: str, out_dir: str) -> pd.DataFrame:
    os.makedirs(out_dir, exist_ok=True)

    print(f"\n{'='*62}")
    print("  Phase 7 — Data Cleaning Engine")
    print(f"{'='*62}")

    # ── Load ─────────────────────────────────────────────────────────────
    # debit/credit/balance are loaded and kept as RAW STRINGS here. They
    # are intentionally NOT coerced to numeric at load time (v2 bug fix):
    # doing so with pd.to_numeric(..., errors="coerce").fillna(0.0) forced
    # anything messy — "₹20,000", "(500)", "1 Cr", "1,234.56CR" — straight
    # to NaN -> 0.0 before clean_amounts() ever ran, which made the entire
    # bracket/CR-DR/lakh-crore parser in validator.py dead code on real
    # input. clean_amounts() (Module 1, below) is now the ONLY place these
    # columns get converted to numeric.
    df = pd.read_csv(input_path, dtype=str)

    print(f"  Loaded : {len(df):,} rows across {df['account_id'].nunique()} accounts")
    print(f"  Sources: {df['source_format'].value_counts().to_dict()}")

    report = {
        "run_timestamp": datetime.now().isoformat(),
        "input_file":    os.path.basename(input_path),
        "rows_input":    len(df),
    }

    # Audit log — every action taken on every row
    all_actions: list[dict] = []

    # Initialise audit flag columns
    df["clean_flags"]          = ""
    df["is_duplicate"]         = False
    df["is_utr_collision"]     = False
    df["is_multi_file_collision"] = False
    df["is_balance_breach"]    = False
    df["is_balance_mismatch_minor"] = False
    df["is_balance_mismatch_major"] = False
    df["is_high_value_flag"]   = False
    df["is_velocity_flag"]     = False
    df["is_self_transfer"]     = False
    df["is_malformed_ifsc"]    = False
    df["is_invalid_transaction"] = False
    df["is_failed_transaction"] = False
    df["is_ocr_row"]           = df["source_format"].isin(["image"])

    # ══════════════════════════════════════════════════════════════════════
    # MODULE 1 — DATA STANDARDIZER  (never removes rows)
    # ══════════════════════════════════════════════════════════════════════
    print("\n  [1/5] MODULE 1 — Data Standardizer ...")

    print("      1a. Date validation ...")
    df, date_report, date_actions = validate_dates(df)
    df["clean_flags"] = df.apply(
        lambda r: _merge(r["clean_flags"], r.get("_date_flags", "")), axis=1
    )
    df = df.drop(columns=["_date_flags"], errors="ignore")
    all_actions.extend(date_actions)
    report["date_validation"] = date_report
    print(f"          Null dates         : {date_report['null_dates']}")
    print(f"          Bad format dates   : {date_report['bad_format_dates']}")
    print(f"          Out-of-range dates : {date_report['out_of_range_dates']}")

    print("      1b. Amount cleaning ...")
    df, amount_report, amount_actions = clean_amounts(df)
    zero_mask = (df["debit"] == 0.0) & (df["credit"] == 0.0)
    df.loc[zero_mask, "clean_flags"] = df.loc[zero_mask, "clean_flags"].apply(
        lambda f: _merge(f, "ZERO_DEBIT_AND_CREDIT")
    )
    all_actions.extend(amount_actions)
    report["amount_cleaning"] = amount_report
    print(f"          Amount corrections : {amount_report['amount_corrections']}")
    print(f"          Zero debit+credit  : {amount_report['zero_debit_credit_rows']}")

    print("      1c. Narration / channel / account-id normalisation ...")
    df, text_report, text_actions = normalise_text_fields(df)
    all_actions.extend(text_actions)
    report["text_normalisation"] = text_report
    print(f"          Narrations cleaned : {text_report['narrations_cleaned']}")
    print(f"          Channels normalised: {text_report['channels_normalised']}")
    print(f"          Account IDs fixed  : {text_report['account_ids_stripped']}")

    # ══════════════════════════════════════════════════════════════════════
    # MODULE 2 — DUPLICATE DETECTOR
    # ══════════════════════════════════════════════════════════════════════
    # Runs AFTER standardization (v2 fix): dedup keys now compare canonical
    # dates/amounts/narrations, not raw source formatting, so two rows that
    # only *look* different because of formatting drift are correctly
    # recognised as the same transaction.
    print("\n  [2/5] MODULE 2 — Duplicate Detector ...")
    df, dedup_report, dedup_audit = run_deduplication(df)

    for _, row in dedup_audit["exact_duplicates_removed"].iterrows():
        all_actions.append({
            "row_index":   "REMOVED",
            "account_id":  row.get("account_id", ""),
            "date":        row.get("date", ""),
            "narration":   str(row.get("narration", ""))[:80],
            "debit":       row.get("debit", ""),
            "credit":      row.get("credit", ""),
            "balance":     row.get("balance", ""),
            "source_file": row.get("source_file", ""),
            "action_type": "EXACT_DUPLICATE_REMOVED",
            "detail":      "Row removed: identical account+date+narration+amounts already exists",
        })

    report["deduplication"] = dedup_report
    print(f"      Exact dupes removed : {dedup_report['exact_duplicates_found']} "
          f"({dedup_report.get('exact_duplicate_rate', 0):.1%} of input rows)")
    print(f"      Near dupes flagged  : {dedup_report['near_duplicates_flagged']}")
    print(f"      UTR collisions      : {dedup_report['utr_collisions_flagged']}")
    print(f"      Multi-file key collisions (flagged, NOT removed): "
          f"{dedup_report['multi_file_collisions_flagged']}")
    print(f"      Possible duplicates (near+UTR+multi-file, all flagged not removed): "
          f"{dedup_report.get('possible_duplicate_rate', 0):.1%} of input rows")
    if dedup_report.get("high_duplicate_rate_warning"):
        print(f"      ⚠️  {dedup_report.get('high_duplicate_rate_warning_message', 'HIGH_DUPLICATE_RATE_WARNING')}")

    # ══════════════════════════════════════════════════════════════════════
    # MODULE 3 — DATA VALIDATOR
    # ══════════════════════════════════════════════════════════════════════
    print("\n  [3/5] MODULE 3 — Data Validator ...")

    print("      3a. Transaction-type validation ...")
    df, txn_type_report, txn_type_actions = validate_transaction_types(df)
    all_actions.extend(txn_type_actions)
    report["transaction_type_validation"] = txn_type_report
    print(f"          Invalid (debit&credit both set): {txn_type_report['invalid_transaction_rows']}")
    print(f"          Both negative                  : {txn_type_report['both_negative_rows']}")

    print("      3b. Failed-transaction detection ...")
    df, failed_txn_report, failed_txn_actions = flag_failed_transactions(df)
    all_actions.extend(failed_txn_actions)
    report["failed_transaction_detection"] = failed_txn_report
    print(f"          Failed/declined/reversed/etc. rows (flagged, not removed): "
          f"{failed_txn_report['failed_transaction_rows']}")
    if failed_txn_report["failed_transaction_keyword_counts"]:
        for kw, cnt in failed_txn_report["failed_transaction_keyword_counts"].items():
            print(f"             {kw}: {cnt}")

    print("      3c. Balance continuity ...")
    df, balance_report, balance_actions = validate_balance_continuity(df)
    all_actions.extend(balance_actions)
    balance_review_accounts = balance_report.get(
        "suspect_accounts",
        balance_report.get("balance_review_accounts", []),
    )
    untracked_accounts = balance_report.get("balance_untracked_accounts", [])
    report["balance_validation"] = {
        k: v
        for k, v in balance_report.items()
        if k not in ("suspect_accounts", "balance_review_accounts", "balance_untracked_accounts")
    }
    report["balance_validation"]["n_suspect_accounts"] = len(balance_review_accounts)
    report["balance_validation"]["n_balance_untracked_accounts"] = len(untracked_accounts)
    print(f"          Accounts checked     : {balance_report['accounts_checked']}")
    print(f"          Accounts w/ mismatches : {balance_report['accounts_with_breaches']}")
    print(f"          Mismatch rows (total) : {balance_report['total_breach_rows']}")
    print(f"            MINOR (<=₹5, flagged): {balance_report['total_minor_mismatch_rows']}")
    print(f"            MAJOR (>₹5, flagged) : {balance_report['total_major_mismatch_rows']}")
    if untracked_accounts:
        print(f"          ⚙  BALANCE UNTRACKED : {len(untracked_accounts)} account(s) — "
              f"balance column doesn't move despite real transactions; "
              f"excluded from breach scoring")
        for ua in untracked_accounts:
            print(f"             {ua['account_id']} — balance unchanged on "
                  f"{ua['flatline_ratio']*100:.0f}% of {ua['txn_rows_checked']} txn rows")
    if balance_review_accounts:
        print(f"          ⚠  SUSPECT ACCOUNTS : {len(balance_review_accounts)}")
        for sa in balance_review_accounts:
            print(f"             {sa['account_id']} — {sa['breach_ratio']*100:.0f}% breach "
                  f"({sa['breach_rows']}/{sa['total_rows']}) [{sa['severity']}]")

    print("      3d. Counterparty validation ...")
    df, cp_report, cp_actions = validate_counterparties(df)
    all_actions.extend(cp_actions)
    report["counterparty_validation"] = cp_report
    print(f"          Self-transfers flagged : {cp_report['self_transfers_flagged']}")
    print(f"          Malformed IFSC flagged : {cp_report['malformed_ifsc_flagged']}")

    print("      3e. Statistical outliers + velocity check ...")
    df, outlier_report, outlier_actions = flag_statistical_outliers(df)
    df, velocity_report, velocity_actions = flag_velocity_bursts(df)
    all_actions.extend(outlier_actions)
    all_actions.extend(velocity_actions)
    report["outlier_flagging"]  = outlier_report
    report["velocity_flagging"] = velocity_report
    print(f"          Outlier rows flagged  : {outlier_report['outlier_rows_flagged']}")
    print(f"          Velocity rows flagged : {velocity_report['velocity_rows_flagged']}")
    if velocity_report["velocity_accounts_flagged"] > 0:
        print(f"          ⚠  Velocity accounts : {velocity_report['velocity_accounts_flagged']}")

    print("      3f. Narration integrity ...")
    df, narr_report, narr_actions = flag_empty_narrations(df)
    all_actions.extend(narr_actions)
    report["narration_integrity"] = narr_report
    print(f"          Empty/short narrations: {narr_report['empty_narration_rows']}")

    # ══════════════════════════════════════════════════════════════════════
    # MODULE 4 — MISSING VALUE HANDLER
    # ══════════════════════════════════════════════════════════════════════
    print("\n  [4/5] MODULE 4 — Missing Value Handler ...")
    df, missing_report, missing_actions = handle_missing_values(df)
    all_actions.extend(missing_actions)
    report["missing_values"] = missing_report
    print(f"      Missing account_id      : {missing_report['missing_account_id']}")
    print(f"      Missing date            : {missing_report['missing_date']}")
    print(f"      Narration filled        : {missing_report['missing_narration_filled']}")
    print(f"      Amount filled           : {missing_report['missing_amount_filled']}")
    print(f"      Missing balance         : {missing_report['missing_balance']}")
    print(f"      Missing UTR             : {missing_report['missing_utr']}")
    print(f"      Time defaulted          : {missing_report['missing_time_defaulted']}")

    # ══════════════════════════════════════════════════════════════════════
    # MODULE 5 — QUALITY ASSESSOR
    # ══════════════════════════════════════════════════════════════════════
    print("\n  [5/5] MODULE 5 — Quality Assessor ...")
    df, quality_report, quality_actions = assess_quality(df)
    all_actions.extend(quality_actions)
    report["quality_assessment"] = quality_report
    print(f"      Avg quality score : {quality_report['avg_quality_score']}")
    print(f"      Band counts       : {quality_report['band_counts']}")

    # ── Build flag columns ────────────────────────────────────────────────
    for col in CLEANED_OUTPUT_COLS:
        if col not in df.columns:
            df[col] = "" if col == "clean_flags" else False

    cleaned = df[CLEANED_OUTPUT_COLS].copy()
    report["rows_output"] = len(cleaned)

    any_flag = (
        (cleaned["clean_flags"].fillna("") != "") |
        cleaned["is_duplicate"].astype(bool) |
        cleaned["is_utr_collision"].astype(bool) |
        cleaned["is_multi_file_collision"].astype(bool) |
        cleaned["is_balance_breach"].astype(bool) |
        cleaned["is_high_value_flag"].astype(bool) |
        cleaned["is_velocity_flag"].astype(bool) |
        cleaned["is_self_transfer"].astype(bool) |
        cleaned["is_malformed_ifsc"].astype(bool) |
        cleaned["is_invalid_transaction"].astype(bool) |
        cleaned["is_failed_transaction"].astype(bool)
    )
    report["rows_with_any_flag"] = int(any_flag.sum())
    report["rows_fully_clean"]   = int((~any_flag).sum())

    # ── Write all output files ────────────────────────────────────────────
    _write_outputs(cleaned, dedup_audit, df, all_actions,
                   balance_report, report, out_dir)

    _print_summary(report, out_dir)
    return cleaned


# ─────────────────────────────────────────────────────────────────────────────
def _write_outputs(cleaned, dedup_audit, df_full, all_actions,
                   balance_report, report, out_dir):

    # 1. Main cleaned output
    cleaned.to_csv(os.path.join(out_dir, "cleaned_transactions.csv"), index=False)

    # 2. removed_data.csv — every row physically removed, with reason
    removed_parts = []

    exact_removed = dedup_audit.get("exact_duplicates_removed", pd.DataFrame())
    if not exact_removed.empty:
        exact_removed = exact_removed.copy()
        exact_removed["removal_reason"] = "EXACT_DUPLICATE"
        exact_removed["removal_detail"] = (
            "Identical account_id + date + narration + debit + credit already exists "
            "(same source_file adjacent rows, or a different source_file re-upload). "
            "Second occurrence removed."
        )
        removed_parts.append(exact_removed)

    if removed_parts:
        removed_df = pd.concat(removed_parts, ignore_index=True)
    else:
        removed_df = pd.DataFrame(columns=["account_id","date","narration",
                                            "debit","credit","removal_reason","removal_detail"])
    removed_df.to_csv(os.path.join(out_dir, "removed_data.csv"), index=False)

    # 3. flagged_data.csv — every row kept but flagged, with all reasons
    any_flag_mask = (
        (cleaned["clean_flags"].fillna("") != "") |
        cleaned["is_duplicate"].astype(bool) |
        cleaned["is_utr_collision"].astype(bool) |
        cleaned["is_multi_file_collision"].astype(bool) |
        cleaned["is_balance_breach"].astype(bool) |
        cleaned["is_high_value_flag"].astype(bool) |
        cleaned["is_velocity_flag"].astype(bool) |
        cleaned["is_self_transfer"].astype(bool) |
        cleaned["is_malformed_ifsc"].astype(bool) |
        cleaned["is_invalid_transaction"].astype(bool) |
        cleaned["is_failed_transaction"].astype(bool)
    )
    flagged_df = cleaned[any_flag_mask].copy()

    def _describe_flags(row):
        reasons = []
        flags = str(row.get("clean_flags", "")).strip()
        if flags:
            reasons.append(flags)
        if row.get("is_duplicate"):
            reasons.append("POSSIBLE_DUPLICATE: same account/date/amounts + narration similarity >=95%")
        if row.get("is_utr_collision"):
            reasons.append("POSSIBLE_DUPLICATE (UTR_COLLISION): same reference number on a non-matching-pair row")
        if row.get("is_multi_file_collision"):
            reasons.append("POSSIBLE_DUPLICATE (MULTI_FILE_KEY_COLLISION): 3+ different source files match the exact same key — reviewed, not auto-removed")
        if row.get("is_balance_mismatch_minor"):
            reasons.append("BALANCE_MISMATCH_MINOR: balance gap <= ₹5 vs. prior row")
        if row.get("is_balance_mismatch_major"):
            reasons.append("BALANCE_MISMATCH_MAJOR: balance gap > ₹5 vs. prior row — possible tamper or OCR error")
        if row.get("is_high_value_flag"):
            reasons.append("HIGH_VALUE_OUTLIER: amount exceeds 3×IQR for this account")
        if row.get("is_velocity_flag"):
            reasons.append("VELOCITY_BURST: rapid succession of debits in short window")
        if row.get("is_self_transfer"):
            reasons.append("SELF_TRANSFER: counterparty_account equals account_id")
        if row.get("is_malformed_ifsc"):
            reasons.append("MALFORMED_IFSC: counterparty IFSC fails structural check")
        if row.get("is_invalid_transaction"):
            reasons.append("INVALID_TRANSACTION: debit and credit both set on one row")
        if row.get("is_failed_transaction"):
            reasons.append("FAILED_TRANSACTION: narration/channel/status indicates the transaction did not settle (failed/declined/timeout/reversed/cancelled/rollback)")
        if row.get("is_ocr_row"):
            reasons.append("OCR_SOURCE: lower data confidence (scanned image)")
        return " | ".join(reasons)

    flagged_df["all_flag_reasons"] = flagged_df.apply(_describe_flags, axis=1)
    flagged_df.to_csv(os.path.join(out_dir, "flagged_data.csv"), index=False)

    # 4. Near duplicates / UTR collisions — separate files for easy review
    near_flagged = dedup_audit.get("near_duplicates_flagged", pd.DataFrame())
    if not near_flagged.empty:
        near_flagged.to_csv(os.path.join(out_dir, "near_duplicates_flagged.csv"), index=False)

    utr_collisions = dedup_audit.get("utr_collisions", pd.DataFrame())
    if not utr_collisions.empty:
        utr_collisions.to_csv(os.path.join(out_dir, "utr_collisions_flagged.csv"), index=False)

    multi_file_collisions = dedup_audit.get("multi_file_collisions", pd.DataFrame())
    if not multi_file_collisions.empty:
        multi_file_collisions.to_csv(
            os.path.join(out_dir, "multi_file_key_collisions.csv"), index=False)

    # 5. all_actions.csv — every single change made during cleaning
    if all_actions:
        actions_df = pd.DataFrame(all_actions)
        actions_df.to_csv(os.path.join(out_dir, "all_actions.csv"), index=False)
    else:
        pd.DataFrame(columns=["row_index","account_id","date","narration",
                               "debit","credit","balance","source_file",
                               "action_type","detail"]
                     ).to_csv(os.path.join(out_dir, "all_actions.csv"), index=False)

    # 6. suspect_accounts.csv
    suspects = balance_report.get(
        "suspect_accounts",
        balance_report.get("balance_review_accounts", []),
    )
    pd.DataFrame(suspects if suspects else [],
                 columns=["account_id","breach_ratio","breach_rows","total_rows","severity"]
                 ).to_csv(os.path.join(out_dir, "suspect_accounts.csv"), index=False)

    # 6b. balance_untracked_accounts.csv — accounts excluded from per-row
    # BALANCE_BREACH scoring because their balance column never actually
    # moves despite real transactions (source never populated a real
    # running balance for that account).
    untracked = balance_report.get("balance_untracked_accounts", [])
    pd.DataFrame(untracked if untracked else [],
                 columns=["account_id","flatline_ratio","stuck_rows","txn_rows_checked","total_rows"]
                 ).to_csv(os.path.join(out_dir, "balance_untracked_accounts.csv"), index=False)

    # 7. cleaning_report.json — full machine-readable audit trail
    with open(os.path.join(out_dir, "cleaning_report.json"), "w") as f:
        json.dump(report, f, indent=2, default=str)

    # 7b. quality_report.json — dedicated quality-assessment report (spec-named
    # output file). cleaning_report.json above is the full superset; this is
    # just the quality_assessment section plus the duplicate-rate guardrail
    # stats, for anyone consuming ONLY the quality signal.
    quality_report = {
        "run_timestamp":    report.get("run_timestamp"),
        "rows_scored":      report.get("quality_assessment", {}).get("rows_scored"),
        "avg_quality_score": report.get("quality_assessment", {}).get("avg_quality_score"),
        "band_counts":      report.get("quality_assessment", {}).get("band_counts"),
        "exact_duplicate_rate":     report.get("deduplication", {}).get("exact_duplicate_rate"),
        "possible_duplicate_rate":  report.get("deduplication", {}).get("possible_duplicate_rate"),
        "high_duplicate_rate_warning": report.get("deduplication", {}).get("high_duplicate_rate_warning"),
        "high_duplicate_rate_warning_message": report.get("deduplication", {}).get("high_duplicate_rate_warning_message"),
    }
    with open(os.path.join(out_dir, "quality_report.json"), "w") as f:
        json.dump(quality_report, f, indent=2, default=str)

    # 8. cleaning_summary.txt — human-readable narrative
    _write_summary_txt(report, balance_report, len(removed_df),
                       len(flagged_df), all_actions, out_dir)


def _write_summary_txt(report, balance_report, n_removed, n_flagged,
                        all_actions, out_dir):
    lines = [
        "=" * 70,
        "PHASE 7 — DATA CLEANING SUMMARY",
        f"Run at: {report['run_timestamp']}",
        f"Input : {report['input_file']}",
        "=" * 70,
        "",
        "OVERVIEW",
        "-" * 40,
        f"  Rows loaded              : {report['rows_input']:,}",
        f"  Rows in cleaned output   : {report['rows_output']:,}",
        f"  Rows removed             : {report['rows_input'] - report['rows_output']:,}",
        f"  Rows flagged (kept)      : {n_flagged:,}",
        f"  Rows fully clean         : {report['rows_fully_clean']:,}",
        "",
        "MODULE 1 — DATA STANDARDIZER",
        "-" * 40,
        f"  Null dates      : {report['date_validation']['null_dates']}",
        f"  Bad format dates: {report['date_validation']['bad_format_dates']}",
        f"  Out of range    : {report['date_validation']['out_of_range_dates']}",
        f"  Amount corrections       : {report['amount_cleaning']['amount_corrections']}",
        f"  Zero debit+credit rows   : {report['amount_cleaning']['zero_debit_credit_rows']}",
        f"  Narrations cleaned       : {report['text_normalisation']['narrations_cleaned']}",
        f"  Channels normalised      : {report['text_normalisation']['channels_normalised']}",
        f"  Account IDs space-fixed  : {report['text_normalisation']['account_ids_stripped']}",
        "",
        "MODULE 2 — DUPLICATE DETECTOR",
        "-" * 40,
        f"  Exact duplicates removed : {report['deduplication']['exact_duplicates_found']} "
        f"({report['deduplication'].get('exact_duplicate_rate', 0):.1%} of input rows; target <3%)",
        f"  → Requires: account+date+narration+debit+credit+balance+UTR (if present) all match",
        f"  → Cause: same statement uploaded twice, or a parser/OCR artifact",
        f"    re-emitting the same line back-to-back in one file",
        f"  → Action: duplicate removed; first occurrence kept",
        f"  Near duplicates flagged  : {report['deduplication']['near_duplicates_flagged']}",
        f"  → Cause: same account/date/amounts + narration similarity >=95%",
        f"  → Action: flagged is_duplicate=True + POSSIBLE_DUPLICATE; human review required, never removed",
        f"  UTR collisions flagged   : {report['deduplication']['utr_collisions_flagged']}",
        f"  → Cause: same reference number on rows that aren't a matching transfer pair",
        f"  Possible duplicates (near + UTR + multi-file), combined: "
        f"{report['deduplication'].get('possible_duplicate_rate', 0):.1%} of input rows (target 5-15%)",
    ]
    if report["deduplication"].get("high_duplicate_rate_warning"):
        lines += [
            "",
            f"  ⚠️  HIGH_DUPLICATE_RATE_WARNING",
            f"     {report['deduplication'].get('high_duplicate_rate_warning_message', '')}",
        ]
    lines += [
        "",
        "MODULE 3 — DATA VALIDATOR",
        "-" * 40,
        f"  Invalid transactions (debit & credit both set): "
        f"{report['transaction_type_validation']['invalid_transaction_rows']}",
        f"  Both-negative amount rows                      : "
        f"{report['transaction_type_validation']['both_negative_rows']}",
        f"  Failed/declined/reversed/etc. transactions (flagged, never removed): "
        f"{report['failed_transaction_detection']['failed_transaction_rows']}",
    ]
    kw_counts = report["failed_transaction_detection"].get("failed_transaction_keyword_counts", {})
    for kw, cnt in kw_counts.items():
        lines.append(f"     • {kw}: {cnt}")
    lines += [
        f"  Accounts checked (balance)      : {report['balance_validation']['accounts_checked']}",
        f"  Accounts with mismatches        : {report['balance_validation']['accounts_with_breaches']}",
        f"  Mismatch rows total             : {report['balance_validation']['total_breach_rows']}",
        f"    BALANCE_MISMATCH_MINOR (<=₹5) : {report['balance_validation']['total_minor_mismatch_rows']}",
        f"    BALANCE_MISMATCH_MAJOR (>₹5)  : {report['balance_validation']['total_major_mismatch_rows']}",
        f"  → Formula: prev_balance + credit - debit ≈ current_balance",
        f"  → Rows are never removed for balance mismatches, only flagged",
        f"  Accounts w/ untracked balance    : {report['balance_validation']['n_balance_untracked_accounts']}",
        f"  → Balance column never moves despite real transactions on these accounts;",
        f"    excluded from per-row scoring, see balance_untracked_accounts.csv",
        f"  Self-transfers flagged          : {report['counterparty_validation']['self_transfers_flagged']}",
        f"  Malformed IFSC flagged          : {report['counterparty_validation']['malformed_ifsc_flagged']}",
        f"  Outlier rows flagged            : {report['outlier_flagging']['outlier_rows_flagged']}",
        f"  Velocity rows flagged           : {report['velocity_flagging']['velocity_rows_flagged']}",
        f"  Empty/short narrations flagged  : {report['narration_integrity']['empty_narration_rows']}",
    ]

    suspects = balance_report.get(
        "suspect_accounts",
        balance_report.get("balance_review_accounts", []),
    )
    if suspects:
        lines += ["", f"  ⚠  SUSPECT ACCOUNTS ({len(suspects)}):"]
        for sa in suspects:
            lines.append(
                f"     • {sa['account_id']} — {sa['breach_ratio']*100:.0f}% of rows breach "
                f"({sa['breach_rows']}/{sa['total_rows']}) [{sa['severity']}]"
            )
        lines.append(
            "     These accounts need investigator review — balances do not reconcile."
        )

    mv = report["missing_values"]
    lines += [
        "",
        "MODULE 4 — MISSING VALUE HANDLER",
        "-" * 40,
        f"  Missing account_id (flagged, not imputed) : {mv['missing_account_id']}",
        f"  Missing date (flagged, not imputed)       : {mv['missing_date']}",
        f"  Narration filled with NULL (flagged)       : {mv['missing_narration_filled']}",
        f"  Debit/credit filled with 0.0 (opposite side present): {mv['missing_amount_filled']}",
        f"  Both debit AND credit missing (flagged, NOT filled) : {mv['both_amounts_missing']}",
        f"  Missing balance (flagged, not imputed)    : {mv['missing_balance']}",
        f"  Missing UTR (informational)               : {mv['missing_utr']}",
        f"  Time defaulted to 00:00:00                : {mv['missing_time_defaulted']}",
    ]

    qa = report["quality_assessment"]
    lines += [
        "",
        "MODULE 5 — QUALITY ASSESSOR",
        "-" * 40,
        f"  Average quality score : {qa['avg_quality_score']}",
        f"  HIGH band rows        : {qa['band_counts']['HIGH']}",
        f"  MEDIUM band rows      : {qa['band_counts']['MEDIUM']}",
        f"  LOW band rows         : {qa['band_counts']['LOW']}",
        "",
        "ACTION LOG SUMMARY",
        "-" * 40,
    ]

    from collections import Counter
    action_counts = Counter(a["action_type"] for a in all_actions)
    for atype, count in sorted(action_counts.items(), key=lambda x: -x[1]):
        lines.append(f"  {atype:<40}: {count:,}")

    lines += [
        "",
        "OUTPUT FILES",
        "-" * 40,
        "  cleaned_transactions.csv      ← Feed to Phase 8/9/10",
        "  removed_data.csv              ← Every removed row with reason",
        "  flagged_data.csv              ← Every flagged row with reasons",
        "  near_duplicates_flagged.csv   ← Near-dupes for human review",
        "  utr_collisions_flagged.csv    ← UTR collisions for human review",
        "  all_actions.csv               ← Row-level audit log of every change",
        "  suspect_accounts.csv          ← Balance integrity failures",
        "  balance_untracked_accounts.csv ← Accounts whose balance column never moves",
        "  cleaning_report.json          ← Machine-readable full report",
        "  quality_report.json           ← Quality-assessment + duplicate-rate stats only",
        "  cleaning_summary.txt          ← This file",
        "=" * 70,
    ]

    with open(os.path.join(out_dir, "cleaning_summary.txt"), "w") as f:
        f.write("\n".join(lines))


def _merge(existing: str, new_flag: str) -> str:
    e = str(existing).strip() if existing else ""
    n = str(new_flag).strip()  if new_flag  else ""
    if not n: return e
    if not e: return n
    return e + " | " + n


def _print_summary(report, out_dir):
    print(f"\n{'='*62}")
    print("  CLEANING SUMMARY")
    print(f"{'='*62}")
    print(f"  Rows input              : {report['rows_input']:,}")
    print(f"  Rows output             : {report['rows_output']:,}")
    dedup = report['deduplication']
    print(f"  Exact dupes removed     : {dedup['exact_duplicates_found']} "
          f"({dedup.get('exact_duplicate_rate', 0):.1%}, target <3%)")
    print(f"  Near dupes flagged      : {dedup['near_duplicates_flagged']}")
    print(f"  UTR collisions flagged  : {dedup['utr_collisions_flagged']}")
    print(f"  Possible dupes (total)  : {dedup.get('possible_duplicate_rate', 0):.1%} (target 5-15%)")
    if dedup.get("high_duplicate_rate_warning"):
        print(f"  ⚠️  HIGH_DUPLICATE_RATE_WARNING — see cleaning_summary.txt")
    print(f"  Invalid transactions    : {report['transaction_type_validation']['invalid_transaction_rows']}")
    print(f"  Failed transactions     : {report['failed_transaction_detection']['failed_transaction_rows']} (flagged, never removed)")
    print(f"  Balance mismatch rows   : {report['balance_validation']['total_breach_rows']} "
          f"(minor: {report['balance_validation']['total_minor_mismatch_rows']}, "
          f"major: {report['balance_validation']['total_major_mismatch_rows']})")
    print(f"  Suspect accounts        : {report['balance_validation']['n_suspect_accounts']}")
    print(f"  High-value outlier rows : {report['outlier_flagging']['outlier_rows_flagged']}")
    print(f"  Velocity burst rows     : {report['velocity_flagging']['velocity_rows_flagged']}")
    print(f"  Avg quality score       : {report['quality_assessment']['avg_quality_score']}")
    print(f"  Rows with any flag      : {report['rows_with_any_flag']:,}")
    print(f"  Rows fully clean        : {report['rows_fully_clean']:,}")
    print(f"\n  Outputs → {os.path.abspath(out_dir)}/")
    print(f"    • cleaned_transactions.csv     ← feed to Phase 8/9/10")
    print(f"    • removed_data.csv             ← all removed rows with reasons")
    print(f"    • flagged_data.csv             ← all flagged rows with reasons")
    print(f"    • all_actions.csv              ← every change made, row by row")
    print(f"    • suspect_accounts.csv         ← balance integrity failures")
    print(f"    • cleaning_report.json         ← machine-readable audit trail")
    print(f"    • quality_report.json          ← quality-assessment + duplicate-rate stats")
    print(f"    • cleaning_summary.txt         ← human-readable narrative")
    print(f"{'='*62}\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Phase 7 — Data Cleaning Engine")
    ap.add_argument("--input",   required=True)
    ap.add_argument("--out-dir", default="cleaned")
    args = ap.parse_args()
    run_cleaning_pipeline(args.input, args.out_dir)