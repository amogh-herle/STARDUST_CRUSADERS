"""
predict.py — Run inference on new transaction data (leakage-free)
───────────────────────────────────────────────────────────────
Run:
    python predict.py --data new_transactions.csv
    python predict.py --data new_transactions.csv --threshold 0.80

This uses predict_on_raw(), which routes the raw dataframe through the
SAME FeatureEngineer (with frozen training-time statistics) that was
saved inside the model. A single chaotic test account can no longer
poison its own baseline — stats come from the original training population.
"""

import argparse
import sys
import os
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.data_loader import DataLoader
from models.isolation_forest_trainer import IsolationForestTrainer

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR  = os.path.join(BASE_DIR, "outputs", "models")
REPORT_DIR = os.path.join(BASE_DIR, "outputs", "reports")


def main():
    parser = argparse.ArgumentParser(description="AML Isolation Forest — Inference")
    parser.add_argument("--data",      required=True)
    parser.add_argument("--model",     default="isolation_forest")
    parser.add_argument("--threshold", type=float, default=None,
                        help="Override anomaly score threshold (0-1, normalised)")
    args = parser.parse_args()

    print("=" * 60)
    print("  AML ISOLATION FOREST — INFERENCE (leakage-free)")
    print("=" * 60)

    # Load raw data
    loader = DataLoader(verbose=True)
    df = loader.load(args.data)

    # Load model (includes the frozen FeatureEngineer)
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

    # Score using frozen training-time statistics — no recomputation from this file
    df_scored = trainer.predict_on_raw(df)

    os.makedirs(REPORT_DIR, exist_ok=True)
    out_path = os.path.join(REPORT_DIR, "prediction_output.csv")
    keep_cols = [c for c in [
        "transaction_id", "account_id", "account_holder", "bank_name",
        "datetime", "narration", "channel", "debit", "credit", "balance",
        "counterparty_account", "counterparty_name", "utr_ref",
        "anomaly_score", "is_flagged", "risk_tier",
    ] if c in df_scored.columns]
    df_scored[keep_cols].to_csv(out_path, index=False)

    flagged = int(df_scored["is_flagged"].sum())
    print(f"\n[Done] {flagged}/{len(df)} transactions flagged ({flagged/len(df)*100:.2f}%)")
    print(f"[Done] Output -> {out_path}")

    print("\n[Risk Tier Summary]")
    print(df_scored["risk_tier"].value_counts().to_string())


if __name__ == "__main__":
    main()