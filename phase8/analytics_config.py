"""
Phase 8 — Investigation Analytics Engine Configuration

All amount gates are dataset-relative or account-relative. Counts and time
windows are behavioural parameters, not rupee thresholds, so the analytics
can generalise across unknown judge datasets.
"""

# Money trail / fund tracing
TRAIL_MAX_HOPS = 8
TRAIL_MIN_MATCH_RATIO = 0.10
TRAIL_MAX_HOP_HOURS = 72

# Round-trip detection (direct A->B->A pair, 2-hop only)
ROUND_TRIP_MAX_DAYS = 30
ROUND_TRIP_MIN_RETURN_RATIO = 0.65
ROUND_TRIP_MAX_RETURN_RATIO = 1.35
ROUND_TRIP_TOP_QUANTILE = 0.60
ROUND_TRIP_MAX_FINDINGS_PER_PAIR = 3

# Multi-hop round-trip / cycle detection (A->B->C->...->A, 3+ hops).
# Complements detect_round_trips(), which by construction can only ever
# find the direct 2-hop case because it requires the return credit's
# counterparty to equal the original debit's counterparty. Genuine
# layered round-tripping (money routed through 2+ intermediary accounts
# before coming back) needs an actual cycle search over the transaction
# graph, respecting chronological order and amount conservation at every
# hop so unrelated coincidental edges don't get chained together.
ROUND_TRIP_CYCLE_MIN_HOPS = 3          # edges; 3 = A->B->C->A (1+ intermediaries beyond the direct pair)
ROUND_TRIP_CYCLE_MAX_HOPS = 6
ROUND_TRIP_CYCLE_MAX_DAYS = 45         # total elapsed time allowed start->close of the cycle
ROUND_TRIP_CYCLE_MAX_HOP_HOURS = 96    # max gap between any two consecutive hops
ROUND_TRIP_CYCLE_MIN_KEEP_RATIO = 0.55 # each hop must retain >=55% of the previous hop's amount
ROUND_TRIP_CYCLE_CLOSE_MIN_RATIO = 0.55  # closing leg vs. the ORIGINAL seed amount
ROUND_TRIP_CYCLE_CLOSE_MAX_RATIO = 1.45
ROUND_TRIP_CYCLE_TOP_QUANTILE = 0.20   # low floor: the per-hop decay ratio + closing ratio
                                        # band below do the real noise filtering here. A high
                                        # floor mostly costs recall, because a single account
                                        # with one large unrelated transaction otherwise raises
                                        # its own per-account floor enough to hide a genuine,
                                        # smaller round-trip it also took part in.
ROUND_TRIP_CYCLE_MAX_FINDINGS = 500

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
    "is_round_trip", "is_round_trip_cycle", "is_layering", "is_fan_in", "is_fan_out",
    "is_smurfing", "is_odd_hour", "analytics_flags",
]