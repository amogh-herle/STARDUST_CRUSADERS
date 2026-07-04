"""
predict.py — Run inference on new transaction data (leakage-free)
───────────────────────────────────────────────────────────────
Run:
    python predict.py --data new_transactions.csv
    python predict.py --data new_transactions.csv --threshold 0.80
    python predict.py --data new_transactions.csv --no-postprocess

This uses predict_on_raw(), which routes the raw dataframe through the
SAME FeatureEngineer (with frozen training-time statistics) that was
saved inside the model. A single chaotic test account can no longer
poison its own baseline — stats come from the original training population.

After scoring, post-processing is applied automatically:
  1. Entity segmentation — classifies each account as "business" or "retail"
  2. Suppression rules — silences alerts matching known-safe patterns
     (frequent counterparty, salary narration, business normal-range amount)

The output CSV includes both is_flagged (raw ML) and final_flag (after
suppression). Investigators should work from final_flag.
"""

import argparse
import sys
import os
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.data_loader import DataLoader
from models.isolation_forest_trainer import IsolationForestTrainer
from models.post_processing import harden_predictions

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR  = os.path.join(BASE_DIR, "outputs", "models")
REPORT_DIR = os.path.join(BASE_DIR, "outputs", "reports")


def main():
    parser = argparse.ArgumentParser(description="AML Isolation Forest — Inference")
    parser.add_argument("--data",           required=True)
    parser.add_argument("--model",          default="isolation_forest")
    parser.add_argument("--threshold",      type=float, default=None,
                        help="Override anomaly score threshold (0-1, normalised)")
    parser.add_argument("--no-postprocess", action="store_true",
                        help="Skip entity segmentation and suppression rules "
                             "(outputs raw ML flags only)")
    args = parser.parse_args()

    print("=" * 60)
    print("  AML ISOLATION FOREST — INFERENCE (leakage-free)")
    print("=" * 60)

    # ── Load raw data ────────────────────────────────────────────────────────
    loader = DataLoader(verbose=True)
    df = loader.load(args.data)

    # ── Load model (includes the frozen FeatureEngineer) ─────────────────────
    trainer = IsolationForestTrainer(model_dir=MODEL_DIR, verbose=True)
    trainer.load(args.model)

    if trainer.feature_engineer_ is None:
        print("\nERROR: This model was saved without a FeatureEngineer (old format).")
        print("       Retrain using the updated train.py to fix this.")
        sys.exit(1)

    if args.threshold is not None:
        mn = trainer._score_min or 0
        mx = trainer._score_max or 1
        trainer.threshold_ = mn + args.threshold * (mx - mn)
        print(f"[Predict] Threshold overridden to normalised {args.threshold}")

    # ── Score using frozen training-time statistics ──────────────────────────
    print("\n[STEP 1] Scoring transactions …")
    df_scored = trainer.predict_on_raw(df)
    raw_flagged = int(df_scored["is_flagged"].sum())
    print(f"         Raw ML flags: {raw_flagged}/{len(df_scored)} "
          f"({raw_flagged/len(df_scored)*100:.2f}%)")

    # ── Post-processing: entity segmentation + suppression rules ─────────────
    if not args.no_postprocess:
        print("\n[STEP 2] Applying post-processing …")
        df_scored = harden_predictions(df_scored)
    else:
        print("\n[STEP 2] Post-processing skipped (--no-postprocess)")
        df_scored["final_flag"]          = df_scored["is_flagged"]
        df_scored["suppressed"]          = False
        df_scored["suppression_reason"]  = ""
        df_scored["entity_segment"]      = "unknown"

    # ── Save output ───────────────────────────────────────────────────────────
    os.makedirs(REPORT_DIR, exist_ok=True)
    out_path = os.path.join(REPORT_DIR, "prediction_output.csv")
    keep_cols = [c for c in [
        "transaction_id", "account_id", "account_holder", "bank_name",
        "datetime", "narration", "channel", "debit", "credit", "balance",
        "counterparty_account", "counterparty_name", "utr_ref",
        "anomaly_score", "is_flagged", "risk_tier",
        # post-processing columns
        "entity_segment", "volume_per_30d", "txns_per_30d",
        "suppressed", "suppression_reason", "final_flag",
    ] if c in df_scored.columns]
    df_scored[keep_cols].to_csv(out_path, index=False)

    # ── Summary ───────────────────────────────────────────────────────────────
    final_flagged = int(df_scored["final_flag"].sum())
    suppressed    = int(df_scored["suppressed"].sum())

    print("\n" + "=" * 60)
    print("  INFERENCE COMPLETE")
    print("=" * 60)
    print(f"  Total transactions :  {len(df_scored)}")
    if "entity_segment" in df_scored.columns and df_scored["entity_segment"].ne("unknown").any():
        n_biz    = (df_scored["entity_segment"] == "business").sum()
        n_retail = (df_scored["entity_segment"] == "retail").sum()
        print(f"  Business rows      :  {n_biz}  ({n_biz/len(df_scored)*100:.1f}%)")
        print(f"  Retail rows        :  {n_retail}  ({n_retail/len(df_scored)*100:.1f}%)")
    print(f"  Raw ML flags       :  {raw_flagged}  ({raw_flagged/len(df_scored)*100:.2f}%)")
    print(f"  Suppressed by rules:  {suppressed}")
    print(f"  Final alerts       :  {final_flagged}  ({final_flagged/len(df_scored)*100:.2f}%)")
    if suppressed > 0 and "suppression_reason" in df_scored.columns:
        reasons = (
            df_scored[df_scored["suppressed"]]
            ["suppression_reason"]
            .str.rstrip(";").str.split(";")
            .explode()
            .value_counts()
        )
        print(f"  Suppression breakdown:")
        for reason, count in reasons.items():
            print(f"    {reason:30s}: {count}")
    print(f"\n  Output -> {out_path}")
    print("=" * 60)

    print("\n[Risk Tier Summary — Final Alerts Only]")
    if "risk_tier" in df_scored.columns:
        final_alerts = df_scored[df_scored["final_flag"] == 1]
        print(final_alerts["risk_tier"].value_counts().to_string())

    if "entity_segment" in df_scored.columns:
        print("\n[Final Alerts by Segment]")
        print(
            df_scored[df_scored["final_flag"] == 1]
            .groupby("entity_segment")
            .size()
            .rename("alert_count")
            .to_string()
        )


if __name__ == "__main__":
    main()