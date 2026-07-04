"""
train.py — Main entry point (leakage-free version)
─────────────────────────────────────────────────
Run:
    python train.py --data transactions.csv
    python train.py --data transactions.csv --contamination 0.03 --tune

The fitted FeatureEngineer (with frozen account/global statistics) is saved
INSIDE the model file. predict.py reuses those exact same frozen stats —
no more leakage when scoring new single-account files.

Post-processing (entity segmentation + suppression rules) is applied
automatically after scoring. The saved CSV includes entity_segment,
suppressed, suppression_reason, and final_flag columns alongside the
raw ML is_flagged. Investigators should act on final_flag, not is_flagged.
"""

import argparse
import sys
import os
import json
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.data_loader import DataLoader
from features.feature_engineering import FeatureEngineer
from models.isolation_forest_trainer import IsolationForestTrainer
from models.post_processing import harden_predictions
from evaluation.evaluator import Evaluator

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR  = os.path.join(BASE_DIR, "outputs", "models")
REPORT_DIR = os.path.join(BASE_DIR, "outputs", "reports")


def main():
    parser = argparse.ArgumentParser(description="AML Isolation Forest Trainer")
    parser.add_argument("--data",          required=True)
    parser.add_argument("--labels",        default=None, help="Column name for fraud labels (0/1), optional")
    parser.add_argument("--tune",          action="store_true")
    parser.add_argument("--contamination", type=float, default=0.03)
    parser.add_argument("--name",          default="isolation_forest")
    parser.add_argument("--feature-list",  default=None,
                        help="Path to a JSON file with a pruned list of feature names to use "
                             "(output of prune_features.py). If omitted, uses all features.")
    args = parser.parse_args()

    print("=" * 60)
    print("  AML ISOLATION FOREST — TRAINING PIPELINE (leakage-free)")
    print("=" * 60)

    # ── STEP 1: Load ────────────────────────────────────────────────────────
    print("\n[STEP 1] Loading data …")
    loader = DataLoader(verbose=True)
    df = loader.load(args.data)
    print(f"         Shape: {df.shape}  |  Unique accounts: {df['account_id'].nunique()}")

    # ── STEP 2: Feature engineering (FIT — freezes all statistics) ──────────
    print("\n[STEP 2] Engineering features (fit_transform — freezes stats) …")
    fe = FeatureEngineer(verbose=True)
    df_feat = fe.fit_transform(df)

    feature_cols = [c for c in FeatureEngineer.feature_columns() if c in df_feat.columns]

    if args.feature_list:
        with open(args.feature_list) as f:
            pruned = json.load(f)
        feature_cols = [c for c in pruned if c in df_feat.columns]
        print(f"         Loaded pruned feature list -> {len(feature_cols)} features "
              f"(from {args.feature_list})")
    else:
        print(f"         Using {len(feature_cols)} features (full set)")

    # ── STEP 3: Labels ───────────────────────────────────────────────────────
    labels = None
    if args.labels and args.labels in df_feat.columns:
        labels = df_feat[args.labels].astype(int)
        print(f"\n[STEP 3] Labels loaded — fraud: {labels.sum()}/{len(labels)} "
              f"({labels.mean()*100:.2f}%)")
    else:
        print("\n[STEP 3] No labels — unsupervised mode "
              f"(contamination={args.contamination})")

    # ── STEP 4: Train ────────────────────────────────────────────────────────
    print("\n[STEP 4] Training Isolation Forest …")
    trainer = IsolationForestTrainer(model_dir=MODEL_DIR, verbose=True)
    trainer.fit(
        df_feat, feature_cols, feature_engineer=fe,
        labels=labels, tune=args.tune and labels is not None,
        contamination=args.contamination,
    )

    # ── STEP 5: Evaluate ─────────────────────────────────────────────────────
    print("\n[STEP 5] Evaluating on training data …")
    scores = trainer.predict_score(df_feat)
    preds  = trainer.predict(df_feat)
    tiers  = trainer.predict_risk_tier(df_feat)

    df_feat["anomaly_score"] = np.round(scores, 4)
    df_feat["is_flagged"]    = preds
    df_feat["risk_tier"]     = tiers.astype(str)

    evaluator = Evaluator(report_dir=REPORT_DIR)
    metrics = evaluator.full_report(
        df=df_feat, scores=scores, preds=preds, labels=labels,
        feature_importances=trainer.feature_importances_, run_name=args.name,
    )
    if metrics:
        print("\n[RESULTS]")
        for k, v in metrics.items():
            print(f"   {k:25s}: {v}")

    # ── STEP 6: Save ─────────────────────────────────────────────────────────
    print("\n[STEP 6] Saving model (includes frozen FeatureEngineer) …")
    trainer.save(args.name)

    # ── STEP 7: Post-processing — entity segmentation + suppression rules ────
    print("\n[STEP 7] Applying post-processing (entity segmentation + suppression) …")
    df_feat = harden_predictions(df_feat)

    # ── STEP 8: Save scored + hardened dataset ───────────────────────────────
    out_path = os.path.join(REPORT_DIR, f"{args.name}_scored_transactions.csv")
    keep_cols = [c for c in [
        "transaction_id", "account_id", "account_holder", "bank_name",
        "datetime", "narration", "channel", "debit", "credit", "balance",
        "counterparty_account", "counterparty_name", "utr_ref",
        "anomaly_score", "is_flagged", "risk_tier",
        # post-processing output columns
        "entity_segment", "volume_per_30d", "txns_per_30d",
        "suppressed", "suppression_reason", "final_flag",
        # suppression rule inputs (kept for audit / ground truth evaluator)
        "counterparty_txn_freq", "narration_is_salary", "abs_amount",
        "acc_median_amount",
    ] if c in df_feat.columns]
    df_feat[keep_cols].to_csv(out_path, index=False)
    print(f"[STEP 8] Scored + hardened transactions -> {out_path}")

    raw_flagged   = int(preds.sum())
    final_flagged = int(df_feat["final_flag"].sum()) if "final_flag" in df_feat.columns else raw_flagged
    suppressed    = int(df_feat["suppressed"].sum()) if "suppressed" in df_feat.columns else 0

    print("\n" + "=" * 60)
    print("  TRAINING COMPLETE")
    print("=" * 60)
    print(f"  Total transactions :  {len(df_feat)}")
    print(f"  Accounts           :  {df_feat['account_id'].nunique()}")
    if "entity_segment" in df_feat.columns:
        n_biz    = (df_feat["entity_segment"] == "business").sum()
        n_retail = (df_feat["entity_segment"] == "retail").sum()
        print(f"  Business rows      :  {n_biz}  ({n_biz/len(df_feat)*100:.1f}%)  [vol/30d>=5L & txns/30d>=60 & avg>=5K]")
        print(f"  Retail rows        :  {n_retail}  ({n_retail/len(df_feat)*100:.1f}%)")
    print(f"  Raw ML flags       :  {raw_flagged}  ({raw_flagged/len(df_feat)*100:.2f}%)")
    print(f"  Suppressed by rules:  {suppressed}")
    print(f"  Final alerts       :  {final_flagged}  ({final_flagged/len(df_feat)*100:.2f}%)")
    if suppressed > 0 and "suppression_reason" in df_feat.columns:
        reasons = (
            df_feat[df_feat["suppressed"]]
            ["suppression_reason"]
            .str.rstrip(";").str.split(";")
            .explode()
            .value_counts()
        )
        print(f"  Suppression breakdown:")
        for reason, count in reasons.items():
            print(f"    {reason:30s}: {count}")
    print(f"  Model saved to     :  {MODEL_DIR}/{args.name}.pkl")
    print(f"  Reports saved to   :  {REPORT_DIR}/")
    print("=" * 60)


if __name__ == "__main__":
    main()