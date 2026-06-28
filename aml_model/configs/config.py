"""
Central config for the AML Isolation Forest pipeline.
Every tuneable value lives here — change once, applies everywhere.
"""

# ── Input schema ──────────────────────────────────────────────────────────────
# Exact column names from your CSV
SCHEMA = {
    "transaction_id":        "transaction_id",
    "account_id":            "account_id",
    "account_holder":        "account_holder",
    "bank_name":             "bank_name",
    "date":                  "date",
    "time":                  "time",
    "narration":             "narration",
    "channel":               "channel",
    "debit":                 "debit",
    "credit":                "credit",
    "balance":               "balance",
    "counterparty_account":  "counterparty_account_id",
    "counterparty_name":     "counterparty_name",
    "utr_ref":               "utr_ref",
}

# ── Date / time formats to try (tries each until one works) ──────────────────
DATE_FORMATS = [
    "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y",
    "%Y/%m/%d", "%d-%b-%Y", "%d %b %Y",
]
TIME_FORMATS = [
    "%H:%M:%S", "%H:%M", "%I:%M %p", "%I:%M:%S %p",
]

# ── Feature engineering ───────────────────────────────────────────────────────
# Fraud structuring thresholds (just-below reporting limits in India)
STRUCTURING_BANDS = [
    (9_000,  10_000),
    (49_000, 50_000),
    (99_000, 100_000),
    (199_000, 200_000),
]

# Rapid-transfer window: if gap between credit and next debit < N minutes → flag
RAPID_TRANSFER_MINUTES = 120

# Velocity windows (hours)
VELOCITY_WINDOWS = {"1d": 24, "7d": 168, "30d": 720}

# ── Model ─────────────────────────────────────────────────────────────────────
ISOLATION_FOREST = {
    "n_estimators":  300,
    "max_samples":   "auto",
    "contamination": 0.05,   # tune this to your actual fraud % if known
    "max_features":  1.0,
    "random_state":  42,
    "n_jobs":        -1,
}

# Hyperparameter search grid (used when tune=True)
SEARCH_GRID = {
    "n_estimators":  [100, 200, 300],
    "contamination": [0.03, 0.05, 0.08],
    "max_samples":   [256, 512, "auto"],
    "max_features":  [0.7, 0.85, 1.0],
}

# ── Risk tier thresholds (normalised 0-1 anomaly score) ──────────────────────
RISK_TIERS = {
    "Very Low": (0.00, 0.30),
    "Low":      (0.30, 0.50),
    "Medium":   (0.50, 0.65),
    "High":     (0.65, 0.80),
    "Critical": (0.80, 1.00),
}

# ── Paths ─────────────────────────────────────────────────────────────────────
import os
BASE_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.path.join(BASE_DIR, "outputs", "models")
REPORT_DIR = os.path.join(BASE_DIR, "outputs", "reports")