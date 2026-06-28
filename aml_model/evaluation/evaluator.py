"""
Evaluation Suite
Generates:
  - Full metrics report (JSON)
  - Precision-Recall curve
  - ROC curve
  - Anomaly score distribution
  - Feature importance bar chart
  - Top-N flagged transactions table (CSV)
"""

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from sklearn.metrics import (
    roc_curve, auc,
    precision_recall_curve,
    average_precision_score,
    roc_auc_score,
)
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from configs.config import REPORT_DIR


class Evaluator:

    def __init__(self, report_dir: str = REPORT_DIR):
        self.report_dir = report_dir
        os.makedirs(report_dir, exist_ok=True)

    # ─────────────────────────────────────────
    # FULL EVALUATION REPORT
    # ─────────────────────────────────────────

    def full_report(
        self,
        df: pd.DataFrame,
        scores: np.ndarray,
        preds: np.ndarray,
        labels: pd.Series = None,
        feature_importances: dict = None,
        run_name: str = "eval",
    ):
        """
        Generates all plots + metrics JSON.
        labels is optional; skip curve plots if absent.
        """
        print(f"[Evaluator] Generating report: {run_name}")

        if labels is not None:
            metrics = self._compute_metrics(labels, scores, preds)
            self._save_json(metrics, f"{run_name}_metrics.json")
            self._plot_pr_curve(labels, scores, run_name)
            self._plot_roc_curve(labels, scores, run_name)
        else:
            metrics = {"note": "No labels provided — unsupervised run"}

        self._plot_score_distribution(scores, labels, run_name)

        if feature_importances:
            self._plot_feature_importance(feature_importances, run_name)

        self._save_flagged_transactions(df, scores, preds, run_name)

        print(f"[Evaluator] Done — outputs in {self.report_dir}")
        return metrics

    # ─────────────────────────────────────────
    # METRICS
    # ─────────────────────────────────────────

    def _compute_metrics(self, labels, scores, preds) -> dict:
        from sklearn.metrics import (
            confusion_matrix, f1_score, precision_score, recall_score
        )
        cm = confusion_matrix(labels, preds)
        tn, fp, fn, tp = cm.ravel() if cm.shape == (2,2) else (0,0,0,0)

        return {
            "roc_auc":           round(roc_auc_score(labels, scores), 4),
            "average_precision": round(average_precision_score(labels, scores), 4),
            "f1":                round(f1_score(labels, preds, zero_division=0), 4),
            "precision":         round(precision_score(labels, preds, zero_division=0), 4),
            "recall":            round(recall_score(labels, preds, zero_division=0), 4),
            "TP": int(tp), "FP": int(fp), "TN": int(tn), "FN": int(fn),
            "fraud_actual_pct":    round(float(labels.mean()) * 100, 2),
            "fraud_predicted_pct": round(float(preds.mean()) * 100, 2),
            "n_samples": len(labels),
        }

    # ─────────────────────────────────────────
    # PLOTS
    # ─────────────────────────────────────────

    def _plot_pr_curve(self, labels, scores, name):
        prec, rec, _ = precision_recall_curve(labels, scores)
        ap = average_precision_score(labels, scores)

        fig, ax = plt.subplots(figsize=(6, 5))
        ax.plot(rec, prec, color="#E63946", lw=2, label=f"AP = {ap:.3f}")
        ax.fill_between(rec, prec, alpha=0.15, color="#E63946")
        ax.set_xlabel("Recall")
        ax.set_ylabel("Precision")
        ax.set_title("Precision-Recall Curve")
        ax.legend()
        ax.grid(alpha=0.3)
        self._save_fig(fig, f"{name}_pr_curve.png")

    def _plot_roc_curve(self, labels, scores, name):
        fpr, tpr, _ = roc_curve(labels, scores)
        roc_auc = auc(fpr, tpr)

        fig, ax = plt.subplots(figsize=(6, 5))
        ax.plot(fpr, tpr, color="#457B9D", lw=2, label=f"AUC = {roc_auc:.3f}")
        ax.plot([0,1],[0,1], "--", color="grey", lw=1)
        ax.fill_between(fpr, tpr, alpha=0.12, color="#457B9D")
        ax.set_xlabel("False Positive Rate")
        ax.set_ylabel("True Positive Rate")
        ax.set_title("ROC Curve")
        ax.legend()
        ax.grid(alpha=0.3)
        self._save_fig(fig, f"{name}_roc_curve.png")

    def _plot_score_distribution(self, scores, labels, name):
        fig, ax = plt.subplots(figsize=(8, 4))

        if labels is not None:
            normal_scores = scores[labels == 0]
            fraud_scores  = scores[labels == 1]
            ax.hist(normal_scores, bins=60, alpha=0.65, color="#2A9D8F",
                    label=f"Normal (n={len(normal_scores)})", density=True)
            ax.hist(fraud_scores,  bins=40, alpha=0.75, color="#E63946",
                    label=f"Fraud (n={len(fraud_scores)})", density=True)
        else:
            ax.hist(scores, bins=60, color="#457B9D", alpha=0.8, density=True)

        ax.set_xlabel("Anomaly Score (normalised 0-1)")
        ax.set_ylabel("Density")
        ax.set_title("Anomaly Score Distribution")
        ax.legend()
        ax.grid(alpha=0.3)
        self._save_fig(fig, f"{name}_score_dist.png")

    def _plot_feature_importance(self, importances: dict, name: str, top_n: int = 25):
        top = dict(list(importances.items())[:top_n])
        features = list(top.keys())[::-1]
        values   = list(top.values())[::-1]

        fig, ax = plt.subplots(figsize=(9, max(5, top_n * 0.35)))
        bars = ax.barh(features, values, color="#457B9D", edgecolor="white")
        ax.set_xlabel("Relative Importance")
        ax.set_title(f"Top-{top_n} Feature Importances (Permutation)")
        ax.grid(axis="x", alpha=0.3)
        for bar, val in zip(bars, values):
            ax.text(bar.get_width() + 0.001, bar.get_y() + bar.get_height()/2,
                    f"{val:.3f}", va="center", fontsize=8)
        plt.tight_layout()
        self._save_fig(fig, f"{name}_feature_importance.png")

    # ─────────────────────────────────────────
    # FLAGGED TRANSACTIONS TABLE
    # ─────────────────────────────────────────

    def _save_flagged_transactions(
        self, df: pd.DataFrame, scores: np.ndarray, preds: np.ndarray, name: str, top_n: int = 500
    ):
        out = df.copy()
        out["anomaly_score"] = np.round(scores, 4)
        out["is_flagged"]    = preds
        flagged = out[out["is_flagged"] == 1].sort_values("anomaly_score", ascending=False)
        path = os.path.join(self.report_dir, f"{name}_flagged_transactions.csv")
        flagged.head(top_n).to_csv(path, index=False)
        print(f"[Evaluator] Flagged transactions → {path}  ({len(flagged)} rows)")

    # ─────────────────────────────────────────
    # UTILS
    # ─────────────────────────────────────────

    def _save_json(self, data: dict, filename: str):
        path = os.path.join(self.report_dir, filename)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"[Evaluator] Metrics  → {path}")

    def _save_fig(self, fig, filename: str):
        path = os.path.join(self.report_dir, filename)
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"[Evaluator] Plot     → {path}")