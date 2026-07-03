"""
Phase 7 — Data Cleaning Engine Configuration  (v2)

Upgraded to match Phase 6 v2:
  • New UNIFIED_SCHEMA columns: counterparty_account, counterparty_ifsc
  • Multi-format date acceptance (Phase 6 v2 normalizer output is YYYY-MM-DD,
    but defensive parsing covers any upstream drift)
  • Lakh/crore comma + multi-currency symbol stripping
  • UTR/reference duplicate detection (cross-statement re-upload signal)
  • Counterparty self-transfer validation
  • Richer per-account statistics in the audit trail

Rule of thumb (unchanged):
  Structural rules  → always wrong (null date, zero debit+credit)
  Statistical rules → suspicious relative to each account's own
                      distribution — these FLAG, never auto-drop
"""

# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------
EXACT_DEDUP_KEYS  = ["account_id", "date", "time", "narration", "debit", "credit", "balance"]
NEAR_DEDUP_KEYS   = ["account_id", "date", "time", "debit", "credit", "balance"]
UTR_DEDUP_ENABLED = True   # flag (not drop) rows sharing a UTR/ref across files

# ---------------------------------------------------------------------------
# Date validation — accepts multiple formats defensively even though
# Phase 6 v2 normalizer should already emit YYYY-MM-DD
# ---------------------------------------------------------------------------
DATE_FORMATS_ACCEPTED = [
    "%Y-%m-%d",
    "%d/%m/%Y", "%d/%m/%y",
    "%d-%m-%Y", "%d-%m-%y",
    "%d-%b-%Y", "%d-%b-%y",
    "%d %b %Y", "%d %B %Y",
    "%m/%d/%Y",
]
DATE_FORMAT_CANONICAL = "%Y-%m-%d"   # output is always normalised to this
DATE_VALID_YEAR_MIN   = 2000
DATE_VALID_YEAR_MAX   = 2030

# ---------------------------------------------------------------------------
# Amount cleaning
# ---------------------------------------------------------------------------
AMOUNT_BRACKET_NEGATIVE  = True    # (1234.56) → -1234.56
AMOUNT_STRIP_CURRENCY    = True    # ₹, INR, Rs., $, £, €
AMOUNT_HANDLE_CR_DR      = True    # "1234.56CR" / "1234.56DR" / leading CR/DR
AMOUNT_HANDLE_LAKH_WORDS = True    # "2.5 Lakh", "1 Cr" → 250000, 10000000

CURRENCY_SYMBOLS = r"[₹$£€]|INR|Rs\.?|USD|GBP|EUR"

LAKH_CRORE_MULTIPLIERS = {
    "lakh": 100_000,
    "lac":  100_000,
    "crore": 10_000_000,
    "cr":    10_000_000,   # NOTE: collides with "Cr" credit suffix —
                             # only applied when followed by a unit word,
                             # never to a bare trailing "Cr"/"Dr"
}

# ---------------------------------------------------------------------------
# Balance continuity
# ---------------------------------------------------------------------------
BALANCE_TOLERANCE                      = 1.0    # ₹1 rounding allowance
BALANCE_BREACH_ACCOUNT_FLAG_THRESHOLD  = 0.05   # flag account if >5% rows breach

# ---------------------------------------------------------------------------
# Statistical outlier flagging (per-account IQR)
# ---------------------------------------------------------------------------
OUTLIER_IQR_MULTIPLIER   = 3.0
OUTLIER_MIN_TXN_COUNT    = 10     # minimum rows before IQR is computed

# ---------------------------------------------------------------------------
# Velocity check (rapid successive large transfers — fraud signal)
# ---------------------------------------------------------------------------
VELOCITY_WINDOW_MINUTES  = 30      # check for N+ debits within this window
VELOCITY_MIN_TXNS        = 3       # minimum txns in window to flag
VELOCITY_MIN_AMOUNT      = 0       # minimum amount per txn (0 = any amount)

# ---------------------------------------------------------------------------
# Counterparty validation (new — leverages Phase 6 v2's counterparty cols)
# ---------------------------------------------------------------------------
FLAG_SELF_TRANSFER_SAME_ACCOUNT = True   # counterparty_account == account_id
FLAG_MALFORMED_IFSC             = True   # IFSC not 11-char alnum, 5th char 0

# ---------------------------------------------------------------------------
# Narration normalisation
# ---------------------------------------------------------------------------
NARRATION_STRIP_CHARS    = r"[|\\<>{}[\]~`°]"
NARRATION_MIN_LEN_FLAG   = 3     # narration shorter than this after cleaning → flag

# ---------------------------------------------------------------------------
# Channel normalisation map  (extended to match Phase 6 v2 CHANNEL_KEYWORDS)
# ---------------------------------------------------------------------------
CHANNEL_NORMALISE = {
    "UPI/IMPS":   "UPI",
    "UPI-IMPS":   "UPI",
    "IMPS/UPI":   "IMPS",
    "NEFT/RTGS":  "NEFT",
    "NFT":        "NEFT",
    "ATM WDL":    "ATM",
    "ATM-WDL":    "ATM",
    "NFS CASH":   "ATM",
    "NACH":       "ECS",
    "SI":         "ECS",
    "CHQ":        "CHEQUE",
    "CLG":        "CHEQUE",
    "BB/CHQ":     "CHEQUE",
    "SWIFT":      "WIRE",
    "NOSTRO":     "WIRE",
    "EMI":        "LOAN",
    "SBINT":      "INTEREST",
    "GST":        "CHARGES",
}

# ---------------------------------------------------------------------------
# Final output columns — includes Phase 6 v2's counterparty_account /
# counterparty_ifsc so Phase 8/9 graph layer can build edges with real
# account-to-account links wherever the source bank provided them.
# ---------------------------------------------------------------------------
CLEANED_OUTPUT_COLS = [
    "account_id", "account_holder", "bank_name",
    "date", "time", "narration", "channel",
    "debit", "credit", "balance",
    "utr_ref", "counterparty_name", "counterparty_account", "counterparty_ifsc",
    "source_file", "source_format",
    "clean_flags",
    "is_duplicate",
    "is_balance_breach",
    "is_high_value_flag",
    "is_ocr_row",
    "is_velocity_flag",
    "is_utr_collision",
    "is_self_transfer",
    "is_malformed_ifsc",
]
