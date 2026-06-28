"""
predict.py  —  Run inference on new transaction data
─────────────────────────────────────────────────────
Run:
    python predict.py --data new_transactions.csv
    python predict.py --data new_transactions.csv --model isolation_forest --threshold 0.62
"""

import argparse
import sys
import os
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.data_loader import DataLoader
from features.feature_engineering import FeatureEngineer
from models.isolation_forest_trainer import IsolationForestTrainer
from configs.config import MODEL_DIR, REPORT_DIR


def main():
    parser = argparse.ArgumentParser(description="AML Isolation Forest — Inference")
    parser.add_argument("--data",      required=True, help="Path to new transaction CSV")
    parser.add_argument("--model",     default="isolation_forest", help="Saved model name")
    parser.add_argument("--threshold", type=float, default=None,
                        help="Override anomaly score threshold (0-1)")
    args = parser.parse_args()

    print("=" * 60)
    print("  AML ISOLATION FOREST — INFERENCE")
    print("=" * 60)

    # Load
    loader = DataLoader(verbose=True)
    df = loader.load(args.data)

    # Feature engineering (transform only — no fitting)
    fe = FeatureEngineer(verbose=False)
    df_feat = fe.fit_transform(df)   # fit_transform is safe on new data too
    feature_cols = [c for c in FeatureEngineer.feature_columns() if c in df_feat.columns]

    # Load model
    trainer = IsolationForestTrainer(model_dir=MODEL_DIR, verbose=True)
    trainer.load(args.model)

    # Override threshold if provided
    if args.threshold is not None:
        # Convert from normalised [0,1] back to raw score space
        mn = trainer._score_min or 0
        mx = trainer._score_max or 1
        trainer.threshold_ = mn + args.threshold * (mx - mn)
        print(f"[Predict] Threshold overridden to normalised {args.threshold}")

    # Score
    scores = trainer.predict_score(df_feat)
    preds  = trainer.predict(df_feat)
    tiers  = trainer.predict_risk_tier(df_feat)

    df_feat["anomaly_score"] = np.round(scores, 4)
    df_feat["is_flagged"]    = preds
    df_feat["risk_tier"]     = tiers.astype(str)

    # Save output
    os.makedirs(REPORT_DIR, exist_ok=True)
    out_path = os.path.join(REPORT_DIR, "prediction_output.csv")
    keep_cols = [c for c in [
        "transaction_id", "account_id", "account_holder",
        "bank_name", "datetime", "narration", "channel",
        "debit_clean", "credit_clean", "balance_clean",
        "counterparty_account_id", "counterparty_name", "utr_ref",
        "anomaly_score", "is_flagged", "risk_tier",
    ] if c in df_feat.columns]
    df_feat[keep_cols].to_csv(out_path, index=False)

    flagged = int(preds.sum())
    print(f"\n[Done] {flagged}/{len(df)} transactions flagged ({flagged/len(df)*100:.2f}%)")
    print(f"[Done] Output → {out_path}")

    # Quick tier summary
    print("\n[Risk Tier Summary]")
    print(df_feat["risk_tier"].value_counts().to_string())


if __name__ == "__main__":
    main()