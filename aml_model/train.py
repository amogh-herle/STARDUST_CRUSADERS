"""
train.py  —  Main entry point
─────────────────────────────
Run:
    python train.py --data transactions.csv
    python train.py --data transactions.csv --labels is_fraud --tune
    python train.py --data transactions.csv --contamination 0.04

Flags:
    --data            Path to your CSV file (required)
    --labels          Column name in CSV that contains 0/1 fraud labels (optional)
    --tune            Enable hyperparameter grid search (requires --labels)
    --contamination   Override default contamination (default: 0.05)
    --no-tune         Disable tuning (faster, uses config defaults)
    --name            Model save name (default: isolation_forest)
"""

import argparse
import sys
import os
import json
import pandas as pd
import numpy as np

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.data_loader import DataLoader
from features.feature_engineering import FeatureEngineer
from models.isolation_forest_trainer import IsolationForestTrainer
from evaluation.evaluator import Evaluator
from configs.config import MODEL_DIR, REPORT_DIR


def main():
    # ── CLI args ──────────────────────────────────────────────────────────────
    parser = argparse.ArgumentParser(description="AML Isolation Forest Trainer")
    parser.add_argument("--data",          required=True,  help="Path to transaction CSV/Excel")
    parser.add_argument("--labels",        default=None,   help="Column name for fraud labels (0/1)")
    parser.add_argument("--tune",          action="store_true", help="Run hyperparameter search")
    parser.add_argument("--contamination", type=float, default=None)
    parser.add_argument("--name",          default="isolation_forest")
    args = parser.parse_args()

    print("=" * 60)
    print("  AML ISOLATION FOREST — TRAINING PIPELINE")
    print("=" * 60)

    # ── STEP 1: Load data ─────────────────────────────────────────────────────
    print("\n[STEP 1] Loading data …")
    loader = DataLoader(verbose=True)
    df = loader.load(args.data)
    print(f"         Shape: {df.shape}")

    # ── STEP 2: Feature engineering ───────────────────────────────────────────
    print("\n[STEP 2] Engineering features …")
    fe = FeatureEngineer(verbose=True)
    df_feat = fe.fit_transform(df)

    feature_cols = FeatureEngineer.feature_columns()
    feature_cols = [c for c in feature_cols if c in df_feat.columns]
    print(f"         Using {len(feature_cols)} features")

    # ── STEP 3: Extract labels if provided ────────────────────────────────────
    labels = None
    if args.labels and args.labels in df_feat.columns:
        labels = df_feat[args.labels].astype(int)
        fraud_count = labels.sum()
        total       = len(labels)
        print(f"\n[STEP 3] Labels loaded — fraud: {fraud_count}/{total} "
              f"({fraud_count/total*100:.2f}%)")
    else:
        print("\n[STEP 3] No labels provided — running unsupervised mode")

    # ── STEP 4: Train ─────────────────────────────────────────────────────────
    print("\n[STEP 4] Training Isolation Forest …")
    trainer = IsolationForestTrainer(model_dir=MODEL_DIR, verbose=True)
    trainer.fit(
        df_feat,
        feature_cols,
        labels=labels,
        tune=args.tune and labels is not None,
        contamination=args.contamination,
    )

    # ── STEP 5: Evaluate ──────────────────────────────────────────────────────
    print("\n[STEP 5] Evaluating …")
    scores = trainer.predict_score(df_feat)
    preds  = trainer.predict(df_feat)
    tiers  = trainer.predict_risk_tier(df_feat)

    df_feat["anomaly_score"] = np.round(scores, 4)
    df_feat["is_flagged"]    = preds
    df_feat["risk_tier"]     = tiers.astype(str)

    evaluator = Evaluator(report_dir=REPORT_DIR)
    metrics = evaluator.full_report(
        df       = df_feat,
        scores   = scores,
        preds    = preds,
        labels   = labels,
        feature_importances = trainer.feature_importances_,
        run_name = args.name,
    )

    if metrics:
        print("\n[RESULTS]")
        for k, v in metrics.items():
            print(f"   {k:25s}: {v}")

    # ── STEP 6: Save model ────────────────────────────────────────────────────
    print("\n[STEP 6] Saving model …")
    trainer.save(args.name)

    # ── STEP 7: Save full scored dataset ──────────────────────────────────────
    out_path = os.path.join(REPORT_DIR, f"{args.name}_scored_transactions.csv")
    keep_cols = [
        c for c in [
            "transaction_id", "account_id", "account_holder",
            "bank_name", "datetime", "narration", "channel",
            "debit_clean", "credit_clean", "balance_clean",
            "counterparty_account_id", "counterparty_name", "utr_ref",
            "anomaly_score", "is_flagged", "risk_tier",
        ] if c in df_feat.columns
    ]
    df_feat[keep_cols].to_csv(out_path, index=False)
    print(f"[STEP 7] Scored transactions → {out_path}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  TRAINING COMPLETE")
    print("=" * 60)
    flagged = int(preds.sum())
    print(f"  Total transactions :  {len(df_feat)}")
    print(f"  Flagged anomalies  :  {flagged}  ({flagged/len(df_feat)*100:.2f}%)")
    print(f"  Model saved to     :  {MODEL_DIR}/{args.name}.pkl")
    print(f"  Reports saved to   :  {REPORT_DIR}/")
    print("=" * 60)


if __name__ == "__main__":
    main()