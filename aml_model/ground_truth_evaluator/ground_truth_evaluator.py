"""
ground_truth_evaluator.py
─────────────────────────
Evaluates the Isolation Forest output against the ground truth labels
that came with the synthetic dataset generator.

Usage:
    python ground_truth_evaluator.py

Expects (all in the same folder):
    master_transactions_clean.csv
    accounts_master.csv
    outputs/reports/isolation_forest_scored_transactions.csv

Produces:
    outputs/reports/gt_evaluation_report.json
    outputs/reports/gt_fraud_ring_breakdown.csv
    outputs/reports/gt_confusion_by_persona.csv
    outputs/reports/gt_score_distribution.png
    outputs/reports/gt_missed_fraud.csv
    outputs/reports/gt_caught_fraud.csv
"""

import os
import sys
import json
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.metrics import (
    roc_auc_score, average_precision_score,
    precision_score, recall_score, f1_score,
    confusion_matrix,
)

# ── paths ─────────────────────────────────────────────────────────────────────
BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
SCORED_CSV       = os.path.join(BASE_DIR, "outputs", "reports", "isolation_forest_scored_transactions.csv")
ACCOUNTS_CSV     = os.path.join(BASE_DIR, "..", "accounts_master.csv")
TRANSACTIONS_CSV = os.path.join(BASE_DIR, "..", "master_transactions_clean.csv")
GT_DIR           = os.path.join(BASE_DIR, "..", "ground_truth")
REPORT_DIR       = os.path.join(BASE_DIR, "outputs", "reports")
os.makedirs(REPORT_DIR, exist_ok=True)

# ── also check ground_truth/ folder if it exists ──────────────────────────────
GT_DIR = os.path.join(BASE_DIR, "ground_truth")


def load_data():
    print("[1] Loading scored transactions …")
    if not os.path.exists(SCORED_CSV):
        print(f"ERROR: Scored CSV not found at {SCORED_CSV}")
        print("       Run train.py first, then come back here.")
        sys.exit(1)

    scored = pd.read_csv(SCORED_CSV, dtype=str, low_memory=False)
    scored["anomaly_score"] = scored["anomaly_score"].astype(float)
    scored["is_flagged"]    = scored["is_flagged"].astype(int)
    print(f"   Scored transactions : {len(scored)}")

    print("[2] Loading accounts master …")
    accounts = pd.read_csv(ACCOUNTS_CSV, dtype=str, low_memory=False)
    accounts["is_fraud"] = accounts["is_fraud"].astype(str).str.strip().str.lower()
    accounts["is_fraud_bool"] = accounts["is_fraud"].isin(["true", "1", "yes"])
    print(f"   Accounts            : {len(accounts)}")
    print(f"   Fraud accounts      : {accounts['is_fraud_bool'].sum()}")

    # Load ground_truth files if they exist
    gt_txn_labels = None
    gt_ring_labels = None
    if os.path.exists(GT_DIR):
        for fname in os.listdir(GT_DIR):
            fpath = os.path.join(GT_DIR, fname)
            if "transaction" in fname.lower():
                gt_txn_labels = pd.read_csv(fpath, dtype=str)
                print(f"   GT transaction labels: {len(gt_txn_labels)} rows  ({fname})")
            elif "ring" in fname.lower() or "account" in fname.lower():
                gt_ring_labels = pd.read_csv(fpath, dtype=str)
                print(f"   GT account/ring labels: {len(gt_ring_labels)} rows  ({fname})")

    return scored, accounts, gt_txn_labels, gt_ring_labels


def build_transaction_labels(scored, accounts, gt_txn_labels):
    """
    Creates a single DataFrame with:
      - Every scored transaction
      - A ground-truth label (is_fraud_gt) derived from:
          1. gt_txn_labels file (most precise) if available
          2. accounts_master.is_fraud joined on account_id (account-level label)
    """
    # Join account-level fraud flag onto transactions
    acc_fraud = accounts[["account_id", "is_fraud_bool", "fraud_role", "fraud_ring_id", "persona"]].copy()
    acc_fraud.columns = ["account_id", "acc_is_fraud", "fraud_role", "fraud_ring_id", "persona"]

    merged = scored.merge(acc_fraud, on="account_id", how="left")
    merged["acc_is_fraud"] = merged["acc_is_fraud"].fillna(False)

    # Use transaction-level GT labels if available (more precise)
    if gt_txn_labels is not None:
        # Try to find the fraud label column
        label_col = None
        for c in gt_txn_labels.columns:
            if "fraud" in c.lower() or "label" in c.lower() or "anomaly" in c.lower():
                label_col = c
                break

        if label_col and "transaction_id" in gt_txn_labels.columns:
            gt_txn_labels["is_fraud_gt"] = (
                gt_txn_labels[label_col].astype(str).str.lower().isin(["true", "1", "yes"])
            )
            merged = merged.merge(
                gt_txn_labels[["transaction_id", "is_fraud_gt"]],
                on="transaction_id", how="left"
            )
            merged["is_fraud_gt"] = merged["is_fraud_gt"].fillna(False)
            print(f"   Using transaction-level GT labels: {merged['is_fraud_gt'].sum()} fraud txns")
        else:
            merged["is_fraud_gt"] = merged["acc_is_fraud"]
            print("   Using account-level labels for transactions")
    else:
        merged["is_fraud_gt"] = merged["acc_is_fraud"]
        print("   Using account-level labels for transactions")

    return merged


def evaluate_transaction_level(merged):
    """Core metrics: how well did IF catch the fraud transactions?"""
    y_true  = merged["is_fraud_gt"].astype(int)
    y_score = merged["anomaly_score"]
    y_pred  = merged["is_flagged"]

    # Handle edge case where no positives exist in ground truth
    if y_true.sum() == 0:
        print("WARNING: No fraud transactions found in ground truth. Check your label columns.")
        return {}

    cm = confusion_matrix(y_true, y_pred)
    tn, fp, fn, tp = cm.ravel() if cm.shape == (2, 2) else (0, 0, 0, 0)

    metrics = {
        "total_transactions":        int(len(merged)),
        "fraud_transactions_actual": int(y_true.sum()),
        "flagged_by_model":          int(y_pred.sum()),
        "true_positives":            int(tp),
        "false_positives":           int(fp),
        "true_negatives":            int(tn),
        "false_negatives":           int(fn),
        "precision":    round(precision_score(y_true, y_pred, zero_division=0), 4),
        "recall":       round(recall_score(y_true, y_pred, zero_division=0), 4),
        "f1_score":     round(f1_score(y_true, y_pred, zero_division=0), 4),
        "roc_auc":      round(roc_auc_score(y_true, y_score), 4),
        "average_precision": round(average_precision_score(y_true, y_score), 4),
        "fraud_caught_pct": round(tp / max(1, y_true.sum()) * 100, 2),
        "false_alarm_rate": round(fp / max(1, (tn + fp)) * 100, 2),
    }
    return metrics


def evaluate_account_level(merged, accounts):
    """
    Account-level evaluation:
    An account is 'detected' if ANY of its transactions were flagged.
    """
    acc_summary = merged.groupby("account_id").agg(
        any_flagged    = ("is_flagged", "max"),
        max_score      = ("anomaly_score", "max"),
        avg_score      = ("anomaly_score", "mean"),
        n_flagged      = ("is_flagged", "sum"),
        n_transactions = ("transaction_id", "count"),
    ).reset_index()

    acc_gt = accounts[["account_id", "is_fraud_bool", "fraud_role", "fraud_ring_id", "persona"]].copy()
    acc_summary = acc_summary.merge(acc_gt, on="account_id", how="left")
    acc_summary["is_fraud_bool"] = acc_summary["is_fraud_bool"].fillna(False)

    y_true = acc_summary["is_fraud_bool"].astype(int)
    y_pred = acc_summary["any_flagged"].astype(int)

    if y_true.sum() == 0:
        return {}, acc_summary

    cm = confusion_matrix(y_true, y_pred)
    tn, fp, fn, tp = cm.ravel() if cm.shape == (2, 2) else (0, 0, 0, 0)

    metrics = {
        "total_accounts":        int(len(acc_summary)),
        "fraud_accounts_actual": int(y_true.sum()),
        "fraud_accounts_detected": int(tp),
        "fraud_accounts_missed":   int(fn),
        "legit_accounts_falsely_flagged": int(fp),
        "account_precision": round(precision_score(y_true, y_pred, zero_division=0), 4),
        "account_recall":    round(recall_score(y_true, y_pred, zero_division=0), 4),
        "account_f1":        round(f1_score(y_true, y_pred, zero_division=0), 4),
        "accounts_caught_pct": round(tp / max(1, y_true.sum()) * 100, 2),
    }
    return metrics, acc_summary


def fraud_ring_breakdown(acc_summary):
    """Per-ring detection rate"""
    fraud_accs = acc_summary[acc_summary["is_fraud_bool"] == True].copy()
    if "fraud_ring_id" not in fraud_accs.columns or fraud_accs.empty:
        return pd.DataFrame()

    ring_stats = fraud_accs.groupby("fraud_ring_id").agg(
        total_accounts   = ("account_id", "count"),
        detected         = ("any_flagged", "sum"),
        avg_max_score    = ("max_score", "mean"),
        roles            = ("fraud_role", lambda x: ", ".join(x.dropna().unique())),
    ).reset_index()
    ring_stats["detection_rate_pct"] = (
        ring_stats["detected"] / ring_stats["total_accounts"] * 100
    ).round(1)
    return ring_stats


def persona_breakdown(acc_summary):
    """FP rate by persona — shows which legitimate personas are being over-flagged"""
    legit = acc_summary[acc_summary["is_fraud_bool"] == False].copy()
    if "persona" not in legit.columns:
        return pd.DataFrame()

    stats = legit.groupby("persona").agg(
        total   = ("account_id", "count"),
        flagged = ("any_flagged", "sum"),
    ).reset_index()
    stats["false_positive_rate_pct"] = (stats["flagged"] / stats["total"] * 100).round(1)
    return stats


def plot_score_distribution(merged):
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))

    # ── Left: score distribution ──────────────────────────────────────────────
    ax = axes[0]
    legit_scores  = merged.loc[~merged["is_fraud_gt"], "anomaly_score"]
    fraud_scores  = merged.loc[ merged["is_fraud_gt"], "anomaly_score"]

    ax.hist(legit_scores, bins=80, alpha=0.6, color="#2A9D8F",
            label=f"Legitimate ({len(legit_scores)})", density=True)
    ax.hist(fraud_scores, bins=30, alpha=0.8, color="#E63946",
            label=f"Fraud ({len(fraud_scores)})", density=True)
    ax.set_xlabel("Anomaly Score (0–1)")
    ax.set_ylabel("Density")
    ax.set_title("Score Distribution: Fraud vs Legitimate")
    ax.legend()
    ax.grid(alpha=0.3)

    # ── Right: score by risk tier ─────────────────────────────────────────────
    ax2 = axes[1]
    if "risk_tier" in merged.columns:
        tier_order  = ["Very Low", "Low", "Medium", "High", "Critical"]
        tier_counts = merged["risk_tier"].value_counts().reindex(tier_order, fill_value=0)
        colors = ["#2A9D8F", "#57CC99", "#F4A261", "#E76F51", "#E63946"]
        bars = ax2.bar(tier_order, tier_counts.values, color=colors, edgecolor="white")
        for bar, val in zip(bars, tier_counts.values):
            ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 50,
                     str(val), ha="center", fontsize=9)
        ax2.set_title("Transactions by Risk Tier")
        ax2.set_ylabel("Count")
        ax2.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    out = os.path.join(REPORT_DIR, "gt_score_distribution.png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"   Plot → {out}")


def main():
    print("=" * 60)
    print("  GROUND TRUTH EVALUATION")
    print("=" * 60)

    scored, accounts, gt_txn_labels, gt_ring_labels = load_data()
    merged = build_transaction_labels(scored, accounts, gt_txn_labels)

    # ── Transaction-level ─────────────────────────────────────────────────────
    print("\n[3] Transaction-level evaluation …")
    txn_metrics = evaluate_transaction_level(merged)
    if txn_metrics:
        for k, v in txn_metrics.items():
            print(f"   {k:35s}: {v}")

    # ── Account-level ─────────────────────────────────────────────────────────
    print("\n[4] Account-level evaluation …")
    acc_metrics, acc_summary = evaluate_account_level(merged, accounts)
    for k, v in acc_metrics.items():
        print(f"   {k:35s}: {v}")

    # ── Fraud ring breakdown ───────────────────────────────────────────────────
    print("\n[5] Fraud ring breakdown …")
    ring_df = fraud_ring_breakdown(acc_summary)
    if not ring_df.empty:
        print(ring_df.to_string(index=False))

    # ── Persona FP breakdown ──────────────────────────────────────────────────
    print("\n[6] False positive rate by persona …")
    persona_df = persona_breakdown(acc_summary)
    if not persona_df.empty:
        print(persona_df.to_string(index=False))

    # ── Plots ─────────────────────────────────────────────────────────────────
    print("\n[7] Generating plots …")
    plot_score_distribution(merged)

    # ── Save CSVs ─────────────────────────────────────────────────────────────
    print("\n[8] Saving detailed CSVs …")

    caught = merged[merged["is_fraud_gt"] & (merged["is_flagged"] == 1)]
    missed = merged[merged["is_fraud_gt"] & (merged["is_flagged"] == 0)]

    caught.to_csv(os.path.join(REPORT_DIR, "gt_caught_fraud.csv"), index=False)
    missed.to_csv(os.path.join(REPORT_DIR, "gt_missed_fraud.csv"), index=False)
    if not ring_df.empty:
        ring_df.to_csv(os.path.join(REPORT_DIR, "gt_fraud_ring_breakdown.csv"), index=False)
    if not persona_df.empty:
        persona_df.to_csv(os.path.join(REPORT_DIR, "gt_confusion_by_persona.csv"), index=False)

    print(f"   gt_caught_fraud.csv        ({len(caught)} rows)")
    print(f"   gt_missed_fraud.csv        ({len(missed)} rows)")

    # ── Save JSON report ──────────────────────────────────────────────────────
    full_report = {
        "transaction_level": txn_metrics,
        "account_level":     acc_metrics,
        "fraud_rings":       ring_df.to_dict(orient="records") if not ring_df.empty else [],
        "persona_fp_rates":  persona_df.to_dict(orient="records") if not persona_df.empty else [],
    }
    out_json = os.path.join(REPORT_DIR, "gt_evaluation_report.json")
    with open(out_json, "w") as f:
        json.dump(full_report, f, indent=2)

    print(f"\n[Done] Full report → {out_json}")
    print("=" * 60)


if __name__ == "__main__":
    main()