"""
Phase 8 — Investigation Analytics Engine Configuration

All amount gates are dataset-relative or account-relative. Counts and time
windows are behavioural parameters, not rupee thresholds, so the analytics
can generalise across unknown judge datasets.
"""

# Money trail / fund tracing
TRAIL_MAX_HOPS = 8
TRAIL_MIN_MATCH_RATIO = 0.65
TRAIL_MAX_HOP_HOURS = 72

# Round-trip detection
ROUND_TRIP_MAX_DAYS = 30
ROUND_TRIP_MIN_RETURN_RATIO = 0.65
ROUND_TRIP_MAX_RETURN_RATIO = 1.35
ROUND_TRIP_TOP_QUANTILE = 0.60
ROUND_TRIP_MAX_FINDINGS_PER_PAIR = 3

# Layering detection
LAYERING_MIN_CHAIN = 3
LAYERING_MAX_CHAIN = 6
LAYERING_MAX_HOP_HOURS = 72
LAYERING_MIN_KEEP_RATIO = 0.50
LAYERING_TOP_QUANTILE = 0.60

# Fan-in / fan-out detection
FAN_IN_MIN_SENDERS = 4
FAN_IN_WINDOW_HOURS = 72
FAN_IN_TOP_QUANTILE = 0.60
FAN_OUT_MIN_RECEIVERS = 4
FAN_OUT_WINDOW_HOURS = 48
FAN_OUT_TOP_QUANTILE = 0.60

# Smurfing / structuring detection
SMURF_MIN_TXNS = 3
SMURF_WINDOW_DAYS = 14
SMURF_MIN_UNIQUE_DEST = 2
SMURF_UPPER_QUANTILE = 0.75
SMURF_LOWER_RATIO = 0.80
SMURF_MIN_ACCOUNT_TXNS = 8

# Odd-hour detection. Ignore default midnight rows because many PDF parsers
# emit 00:00:00 when the statement has no time column.
ODD_HOUR_START = 0
ODD_HOUR_END = 5
ODD_HOUR_MIN_TXNS = 3
ODD_HOUR_MIN_TIMED_TXN_RATIO = 0.20
ODD_HOUR_MIN_ODD_RATIO = 0.25

# Beneficiary analysis
BENE_HIGH_VALUE_ZSCORE = 2.5
BENE_NEW_HIGH_VALUE_RATIO = 0.5

# Risk scoring weights. Graph weight is included because central mule accounts
# are often more important than isolated high-value rows.
RISK_WEIGHTS = {
    "round_trip": 0.14,
    "layering": 0.16,
    "fan_in": 0.11,
    "fan_out": 0.11,
    "smurfing": 0.12,
    "odd_hour": 0.06,
    "velocity": 0.08,
    "high_value": 0.07,
    "balance_breach": 0.05,
    "new_hv_bene": 0.04,
    "graph": 0.06,
}

RISK_TIERS = {
    "CRITICAL": 75,
    "HIGH": 50,
    "MEDIUM": 25,
    "LOW": 0,
}

# When a dataset produces no accounts reaching the absolute HIGH or CRITICAL
# thresholds, fall back to lower thresholds to preserve tier differentiation
# and maintain investigative visibility on low-volume datasets.
RISK_TIER_FALLBACK_ENABLED = True
RISK_TIER_FALLBACK_HIGH = 30
RISK_TIER_FALLBACK_CRITICAL = 45

ANALYTICS_FLAG_COLS = [
    "account_id", "account_holder", "bank_name",
    "date", "time", "narration", "channel",
    "debit", "credit", "balance",
    "utr_ref", "counterparty_name", "counterparty_account", "counterparty_ifsc",
    "clean_flags", "is_duplicate", "is_balance_breach",
    "is_high_value_flag", "is_ocr_row", "is_velocity_flag",
    "is_utr_collision", "is_self_transfer", "is_malformed_ifsc",
    "is_round_trip", "is_layering", "is_fan_in", "is_fan_out",
    "is_smurfing", "is_odd_hour", "analytics_flags",
]
