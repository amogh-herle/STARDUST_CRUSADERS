"""
Isolation Forest Trainer — saves the fitted FeatureEngineer alongside the
model so inference always reuses frozen training-time statistics.
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import RobustScaler
from sklearn.model_selection import ParameterGrid
from sklearn.metrics import (
    roc_auc_score, average_precision_score,
    precision_recall_curve, f1_score,
    precision_score, recall_score, confusion_matrix,
)
import joblib, json, os, sys, warnings
warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ISOLATION_FOREST_DEFAULTS = {
    "n_estimators":  300,
    "max_samples":   "auto",
    "contamination": 0.05,
    "max_features":  1.0,
    "random_state":  42,
    "n_jobs":        -1,
}

SEARCH_GRID = {
    "n_estimators":  [100, 200, 300],
    "contamination": [0.02, 0.03, 0.05, 0.08],
    "max_samples":   [256, 512, "auto"],
    "max_features":  [0.7, 0.85, 1.0],
}


class IsolationForestTrainer:

    def __init__(self, model_dir: str, verbose: bool = True):
        self.model_dir = model_dir
        self.verbose = verbose
        os.makedirs(model_dir, exist_ok=True)

        self.scaler_       = RobustScaler()
        self.model_        = None
        self.threshold_    = None
        self.feature_cols_ = None
        self.best_params_  = None
        self.feature_importances_ = {}
        self.feature_engineer_ = None   # <-- the fitted FeatureEngineer, saved with the model
        self._score_min = None
        self._score_max = None

    # ─────────────────────────────────────────
    # FIT
    # ─────────────────────────────────────────

    def fit(
        self,
        df: pd.DataFrame,
        feature_cols: list,
        feature_engineer,                  # the FeatureEngineer instance used to build df
        labels: pd.Series = None,
        tune: bool = True,
        contamination: float = None,
    ):
        self.feature_cols_ = feature_cols
        self.feature_engineer_ = feature_engineer
        X = self._prepare(df)

        params = dict(ISOLATION_FOREST_DEFAULTS)
        if contamination is not None:
            params["contamination"] = contamination

        if tune and labels is not None:
            self._log("[Trainer] Running hyperparameter search …")
            params = self._grid_search(X, labels)
            self._log(f"[Trainer] Best params: {params}")
        self.best_params_ = params

        self._log(f"[Trainer] Fitting on {X.shape[0]} rows x {X.shape[1]} features …")
        X_scaled = self.scaler_.fit_transform(X)
        self.model_ = IsolationForest(**params)
        self.model_.fit(X_scaled)

        raw_scores = self._raw_scores(X_scaled)
        self._score_min = float(raw_scores.min())
        self._score_max = float(raw_scores.max())

        if labels is not None:
            self.threshold_ = self._best_threshold(raw_scores, labels)
            self._log(f"[Trainer] Calibrated threshold: {self.threshold_:.5f}")
        else:
            pct = params["contamination"] * 100
            self.threshold_ = float(np.percentile(raw_scores, 100 - pct))
            self._log(f"[Trainer] Unsupervised threshold (top {pct:.1f}%): {self.threshold_:.5f}")

        self._log("[Trainer] Computing feature importance …")
        self._permutation_importance(X_scaled)

        self._log("[Trainer] Training complete \u2713")
        return self

    # ─────────────────────────────────────────
    # PREDICT (always uses the SAME feature_engineer that was fitted at train time)
    # ─────────────────────────────────────────

    def predict_on_raw(self, raw_df: pd.DataFrame) -> pd.DataFrame:
        """
        Takes a RAW transaction dataframe (not yet feature-engineered),
        runs it through the frozen FeatureEngineer.transform(), then scores it.
        This is the safe entry point for inference — guarantees no leakage.
        """
        df_feat = self.feature_engineer_.transform(raw_df)
        scores = self.predict_score(df_feat)
        preds  = self.predict(df_feat)
        tiers  = self.predict_risk_tier(df_feat)
        df_feat["anomaly_score"] = np.round(scores, 4)
        df_feat["is_flagged"]    = preds
        df_feat["risk_tier"]     = tiers.astype(str)
        return df_feat

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        scores = self._raw_scores(self.scaler_.transform(self._prepare(df)))
        return (scores >= self.threshold_).astype(int)

    def predict_score(self, df: pd.DataFrame) -> np.ndarray:
        raw = self._raw_scores(self.scaler_.transform(self._prepare(df)))
        mn  = self._score_min if self._score_min is not None else raw.min()
        mx  = self._score_max if self._score_max is not None else raw.max()
        return (raw - mn) / (mx - mn + 1e-9)

    def predict_risk_tier(self, df: pd.DataFrame) -> pd.Series:
        scores = self.predict_score(df)
        return pd.cut(
            scores, bins=[0, 0.30, 0.50, 0.65, 0.80, 1.01],
            labels=["Very Low", "Low", "Medium", "High", "Critical"],
            include_lowest=True,
        )

    # ─────────────────────────────────────────
    # EVALUATE
    # ─────────────────────────────────────────

    def evaluate(self, df: pd.DataFrame, labels: pd.Series) -> dict:
        X_scaled = self.scaler_.transform(self._prepare(df))
        raw      = self._raw_scores(X_scaled)
        preds    = (raw >= self.threshold_).astype(int)

        cm = confusion_matrix(labels, preds)
        tn, fp, fn, tp = cm.ravel() if cm.shape == (2,2) else (0,0,0,0)

        return {
            "roc_auc":            round(roc_auc_score(labels, raw), 4),
            "average_precision":  round(average_precision_score(labels, raw), 4),
            "f1_score":           round(f1_score(labels, preds, zero_division=0), 4),
            "precision":          round(precision_score(labels, preds, zero_division=0), 4),
            "recall":             round(recall_score(labels, preds, zero_division=0), 4),
            "true_positives":     int(tp), "false_positives": int(fp),
            "true_negatives":     int(tn), "false_negatives": int(fn),
            "fraud_rate_actual":  round(float(labels.mean()), 4),
            "fraud_rate_predicted": round(float(preds.mean()), 4),
            "threshold":          round(float(self.threshold_), 5),
            "n_samples":          len(labels),
            "best_params":        self.best_params_,
        }

    # ─────────────────────────────────────────
    # SAVE / LOAD (now includes the FeatureEngineer)
    # ─────────────────────────────────────────

    def save(self, name: str = "isolation_forest"):
        model_path = os.path.join(self.model_dir, f"{name}.pkl")
        meta_path  = os.path.join(self.model_dir, f"{name}_meta.json")

        joblib.dump({
            "scaler":           self.scaler_,
            "model":            self.model_,
            "threshold":        self.threshold_,
            "feature_cols":     self.feature_cols_,
            "best_params":      self.best_params_,
            "score_min":        self._score_min,
            "score_max":        self._score_max,
            "feature_engineer": self.feature_engineer_,   # <-- frozen stats travel with the model
        }, model_path)

        meta = {
            "feature_importances": dict(list(self.feature_importances_.items())[:20]),
            "best_params":         self.best_params_,
            "threshold":           round(float(self.threshold_), 5),
            "n_features":          len(self.feature_cols_),
            "feature_columns":     self.feature_cols_,
        }
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)

        self._log(f"[Trainer] Model -> {model_path}")
        self._log(f"[Trainer] Meta  -> {meta_path}")

    def load(self, name: str = "isolation_forest"):
        path = os.path.join(self.model_dir, f"{name}.pkl")
        obj  = joblib.load(path)
        self.scaler_           = obj["scaler"]
        self.model_            = obj["model"]
        self.threshold_        = obj["threshold"]
        self.feature_cols_     = obj["feature_cols"]
        self.best_params_      = obj["best_params"]
        self._score_min        = obj.get("score_min")
        self._score_max        = obj.get("score_max")
        self.feature_engineer_ = obj.get("feature_engineer")
        self._log(f"[Trainer] Loaded from {path}")
        if self.feature_engineer_ is None:
            self._log("[Trainer] WARNING: this model was saved without a FeatureEngineer "
                      "(old format) — predict_on_raw() will not work. Retrain to fix.")
        return self

    # ─────────────────────────────────────────
    # PRIVATE
    # ─────────────────────────────────────────

    def _prepare(self, df: pd.DataFrame) -> np.ndarray:
        X = df[self.feature_cols_].copy()
        X = X.replace([np.inf, -np.inf], np.nan)
        X = X.fillna(X.median())
        return X.values.astype(float)

    def _raw_scores(self, X_scaled: np.ndarray) -> np.ndarray:
        return -self.model_.score_samples(X_scaled)

    def _grid_search(self, X: np.ndarray, labels: pd.Series) -> dict:
        best_ap, best_params = -1, None
        for params in ParameterGrid(SEARCH_GRID):
            scaler = RobustScaler()
            Xs = scaler.fit_transform(X)
            clf = IsolationForest(**params, random_state=42, n_jobs=-1)
            clf.fit(Xs)
            scores = -clf.score_samples(Xs)
            ap = average_precision_score(labels, scores)
            if ap > best_ap:
                best_ap, best_params = ap, params
        self._log(f"[Trainer] Grid search best AP: {best_ap:.4f}")
        return best_params

    def _best_threshold(self, raw_scores: np.ndarray, labels: pd.Series) -> float:
        prec, rec, thresholds = precision_recall_curve(labels, raw_scores)
        f1 = 2 * prec * rec / (prec + rec + 1e-9)
        best_idx = int(np.argmax(f1))
        if best_idx < len(thresholds):
            return float(thresholds[best_idx])
        return float(np.percentile(raw_scores, 95))

    def _permutation_importance(self, X_scaled: np.ndarray, n_repeats: int = 5):
        base = self._raw_scores(X_scaled)
        imp = {}
        for i, col in enumerate(self.feature_cols_):
            deltas = []
            for _ in range(n_repeats):
                Xp = X_scaled.copy()
                np.random.shuffle(Xp[:, i])
                deltas.append(np.abs(self._raw_scores(Xp) - base).mean())
            imp[col] = float(np.mean(deltas))
        total = sum(imp.values()) + 1e-9
        self.feature_importances_ = dict(
            sorted({k: v/total for k, v in imp.items()}.items(), key=lambda x: -x[1])
        )

    def _log(self, msg):
        if self.verbose:
            print(msg)