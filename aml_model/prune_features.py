"""
prune_features.py
────────────────────
Reads the feature importances from an already-trained model's meta.json
and produces a pruned feature list — dropping the bottom 15-20 weakest,
most collinear features (e.g. redundant time encodings like hour vs
is_night vs hour_sin/cos, which all measure the same "time of day" signal).

Sharper feature set -> more decisive anomaly scores, faster training.

Usage:
    python prune_features.py
    python prune_features.py --drop-n 20
    python prune_features.py --model-name isolation_forest
"""

import json
import argparse
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(BASE_DIR, "outputs", "models")

# Known redundant/collinear feature groups — if multiple from the same group
# survive pruning, keep only the strongest one (avoids the "flatness trap")
COLLINEAR_GROUPS = [
    ["hour", "is_night", "is_odd_hour", "hour_sin", "hour_cos"],
    ["day_of_week", "dow_sin", "dow_cos", "is_weekend"],
    ["acc_avg_amount", "acc_median_amount"],
    ["is_round_1000", "is_round_5000", "is_round_10000", "acc_round_number_ratio"],
    ["vel_count_1d", "vel_count_7d", "vel_count_30d"],
]


def prune(meta_path: str, drop_n: int = 18) -> list:
    with open(meta_path) as f:
        meta = json.load(f)

    all_features = meta["feature_columns"]
    importances = meta.get("feature_importances", {})

    # Rank all features by importance (features missing from the dict = least important)
    ranked = sorted(all_features, key=lambda f: importances.get(f, 0), reverse=True)

    # Drop the bottom N by raw importance
    keep = ranked[:-drop_n] if drop_n < len(ranked) else ranked

    # Collinearity cleanup: within each known redundant group, keep only the
    # single strongest-ranked survivor
    keep_set = set(keep)
    for group in COLLINEAR_GROUPS:
        present = [f for f in group if f in keep_set]
        if len(present) > 1:
            # keep the highest-ranked one, drop the rest
            best = max(present, key=lambda f: importances.get(f, 0))
            for f in present:
                if f != best:
                    keep_set.discard(f)

    pruned_list = [f for f in all_features if f in keep_set]

    print(f"Original feature count: {len(all_features)}")
    print(f"Pruned feature count:   {len(pruned_list)}")
    print(f"\nDropped features:")
    for f in all_features:
        if f not in keep_set:
            print(f"  - {f}  (importance: {importances.get(f, 0):.4f})")

    return pruned_list


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-name", default="isolation_forest")
    parser.add_argument("--drop-n", type=int, default=18)
    args = parser.parse_args()

    meta_path = os.path.join(MODEL_DIR, f"{args.model_name}_meta.json")
    if not os.path.exists(meta_path):
        print(f"ERROR: {meta_path} not found. Train a model first with train.py")
        exit(1)

    pruned = prune(meta_path, drop_n=args.drop_n)

    out_path = os.path.join(BASE_DIR, "pruned_feature_list.json")
    with open(out_path, "w") as f:
        json.dump(pruned, f, indent=2)
    print(f"\nPruned feature list saved -> {out_path}")
    print("\nTo retrain with only these features, pass them via a custom")
    print("feature_cols argument in train.py (see PRUNING_GUIDE.md)")