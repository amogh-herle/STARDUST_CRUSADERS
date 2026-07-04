"""
Wrapper to run optional AML model inference from the `aml_model/` folder.

If an IsolationForest model exists under `aml_model/outputs/models/` this helper
will load feature engineering and the trainer and produce per-transaction anomaly
scores, then aggregate to per-account scores (max / mean) for Phase 8 fusion.

It is optional: if the aml_model package isn't available or model files are missing
the functions return empty mappings and log a concise message.
"""
from __future__ import annotations
import os
import sys
from collections import defaultdict
import pandas as pd
import numpy as np


def get_account_isolation_scores(df: pd.DataFrame, model_name: str = "isolation_forest") -> dict:
    """Return a mapping account_id -> dict(mean_score, max_score).
    If AML model cannot be used, returns {}.
    """
    aml_root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aml_model")
    if not os.path.isdir(aml_root):
        print("[aml_inference] aml_model not found; skipping isolation scores")
        return {}

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, aml_root)
    try:
        from features.feature_engineering import FeatureEngineer  # type: ignore
        from models.isolation_forest_trainer import IsolationForestTrainer  # type: ignore
    except Exception as e:
        print(f"[aml_inference] Failed to import aml_model modules: {e}")
        return {}

    try:
        from schema_adapter import adapt_phase7_to_aml_schema, validate_aml_schema
    except Exception as e:
        print(f"[aml_inference] Failed to import phase8 schema adapter: {e}")
        return {}

    try:
        adapted_df = adapt_phase7_to_aml_schema(df.copy())
        missing_columns = validate_aml_schema(adapted_df)
        if missing_columns:
            print(f"✗ missing columns: {missing_columns}")
            return {}
        print("✓ schema compatible")

        fe = FeatureEngineer(verbose=False)
        df_feat = fe.fit_transform(adapted_df)
        feat_cols = [c for c in FeatureEngineer.feature_columns() if c in df_feat.columns]
    except Exception as e:
        print(f"[aml_inference] Feature engineering failed: {e}")
        return {}

    trainer = IsolationForestTrainer(model_dir=os.path.join(aml_root, "outputs", "models"), verbose=False)
    try:
        trainer.load(model_name)
    except Exception as e:
        print(f"[aml_inference] Could not load model '{model_name}': {e}")
        return {}

    try:
        scores = trainer.predict_score(df_feat)
    except Exception as e:
        print(f"[aml_inference] Prediction error: {e}")
        return {}

    df_feat = df_feat.copy()
    df_feat["_isolation_score"] = scores
    agg = df_feat.groupby("account_id")["_isolation_score"].agg(["mean", "max"]).reset_index()
    out = {row["account_id"]: {"mean_score": float(row["mean"]), "max_score": float(row["max"])} for _, row in agg.iterrows()}
    print(f"[aml_inference] Produced isolation scores for {len(out)} accounts")
    return out
