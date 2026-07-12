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
    except Exception as e:
        print(f"[aml_inference] Schema adaptation failed: {e}")
        return {}

    trainer = IsolationForestTrainer(model_dir=os.path.join(aml_root, "outputs", "models"), verbose=False)
    try:
        trainer.load(model_name)
    except Exception as e:
        print(f"[aml_inference] Could not load model '{model_name}': {e}")
        return {}

    if trainer.feature_engineer_ is None:
        print(f"[aml_inference] Model '{model_name}' was saved without a frozen "
              f"FeatureEngineer (old format); refusing to score to avoid leakage. Retrain.")
        return {}

    try:
        # predict_on_raw() runs adapted_df through the SAME FeatureEngineer that
        # was fitted at training time (frozen stats) — fitting a fresh one here
        # on this small uploaded batch would let it establish its own baseline
        # and silently reintroduce the leakage this model was designed to avoid.
        df_feat = trainer.predict_on_raw(adapted_df)
    except Exception as e:
        print(f"[aml_inference] Prediction error: {e}")
        return {}

    agg = df_feat.groupby("account_id")["anomaly_score"].agg(["mean", "max"]).reset_index()
    out = {row["account_id"]: {"mean_score": float(row["mean"]), "max_score": float(row["max"])} for _, row in agg.iterrows()}
    print(f"[aml_inference] Produced isolation scores for {len(out)} accounts")
    return out
