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
EXACT_DEDUP_KEYS  = ["account_id", "date", "narration", "debit", "credit", "balance"]
# NOTE ON WHY `time` IS NOT IN THIS KEY:
# Two problems make time unusable as a discriminator here:
#   1. Many source statements don't carry a time field at all — it defaults
#      to 00:00:00 for every row, so it adds zero discriminating power.
#   2. Even when time IS present, templated bulk-payment narrations
#      ("BLKNEFT/BLKPAY_YYYYMMDD/123") can repeat across genuinely
#      different transactions with matching amount + a coincidentally-equal
#      running balance — narration+amount+balance alone is not a safe key,
#      with or without time.
# The actual fix lives in deduplicator.py: instead of a flat key match,
# duplicate rows are only dropped when they are (a) immediately adjacent
# in original row order within the SAME source_file (a parsing/OCR
# artifact literally repeating a line), or (b) matching rows that appear
# in TWO DIFFERENT source_files (a genuine re-uploaded/overlapping
# statement). Same-file, non-adjacent matches on this key are real,
# distinct transactions and are deliberately left alone.
NEAR_DEDUP_KEYS   = ["account_id", "date", "debit", "credit"]
# NOTE: `balance` deliberately excluded (bug fix). Rule 3 says balance must
# never be used to detect duplicates — but requiring balance equality in
# the NEAR key meant two rows with the same amount/date/narration status
# but a DIFFERENT resulting balance could never match the key, so the
# doc's own worked example ("same amount, same narration, different
# balance → flag") could never actually fire. account+date+debit+credit
# is enough to catch near-duplicate candidates; balance is still reported
# alongside in the flagged output for the human reviewer to judge.
UTR_DEDUP_ENABLED = True   # flag (not drop) rows sharing a UTR/ref across files

# Same-file EXACT-duplicate candidates are only dropped if they sit within
# this many rows of each other in original extraction order. 1 = strictly
# back-to-back rows only (safest — a parser/OCR literally re-emitting the
# same line). Raise cautiously; widening this risks dropping real
# transactions again (the original bug).
EXACT_DEDUP_SAME_FILE_MAX_GAP = 10

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
DATE_VALID_YEAR_MAX   = 2035   # was 2030 — mismatched the architecture doc's stated 2000-2035 range

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

# An account whose balance never moves despite real debit/credit movement
# almost certainly means the source never populated a real running balance
# for that account (not that every single transaction was tampered with).
# Rather than let that flood suspect_accounts with meaningless 95-100%
# "breach" rates, detect the flatline up front and exclude the account
# from per-row BALANCE_BREACH scoring entirely — flagged once instead,
# as BALANCE_COLUMN_NOT_POPULATED.
BALANCE_FLATLINE_RATIO_THRESHOLD = 0.90   # >=90% of real-movement rows show balance unchanged
BALANCE_FLATLINE_MIN_TXNS        = 5      # need at least this many real-movement rows to judge

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
# Account number normalisation
# ---------------------------------------------------------------------------
ACCOUNT_ID_STRIP_SPACES = True   # "1234 5678 9012" → "123456789012"
                                   # OCR/bank exports frequently insert
                                   # grouping spaces into account numbers;
                                   # left in, the same real account looks
                                   # like N different accounts to every
                                   # downstream groupby("account_id").

# ---------------------------------------------------------------------------
# Narration normalisation
# ---------------------------------------------------------------------------
NARRATION_STRIP_CHARS      = r"[|\\<>{}[\]~`°]"
NARRATION_SEPARATOR_CHARS  = r"[/\-]"   # "UPI/AMAZON" / "UPI-AMAZON" → "UPI AMAZON"
                                          # applied BEFORE NARRATION_STRIP_CHARS
                                          # and whitespace collapse, so the
                                          # tokens end up space-separated
                                          # rather than glued together.
NARRATION_MIN_LEN_FLAG   = 3     # narration shorter than this after cleaning → flag

# ---------------------------------------------------------------------------
# Transaction-type structural validation
# ---------------------------------------------------------------------------
FLAG_BOTH_DEBIT_AND_CREDIT = True   # debit>0 AND credit>0 on one row → INVALID_TRANSACTION
FLAG_BOTH_NEGATIVE         = True   # debit<0 AND credit<0 on one row → BOTH_NEGATIVE_AMOUNTS
# Structural per the doc's own rule of thumb, but per the "nothing is
# silently dropped" design principle these are FLAGGED for investigator
# review rather than auto-dropped — the row may still carry real forensic
# signal (e.g. a mis-mapped column) worth keeping visible in the output.

# ---------------------------------------------------------------------------
# Missing-value handling (Module 4)
# ---------------------------------------------------------------------------
MISSING_TIME_DEFAULT          = "00:00:00"
MISSING_NARRATION_FILL        = "UNKNOWN NARRATION"
MISSING_AMOUNT_FILL           = 0.0
# account_id and date have NO safe fill — they are flagged only, never
# imputed, since guessing either would corrupt grouping/ordering logic
# used everywhere downstream (dedup, balance continuity, velocity).

# ---------------------------------------------------------------------------
# Quality scoring (Module 5)
# ---------------------------------------------------------------------------
# Every row starts at 100 and loses points for each issue found anywhere
# in the pipeline. Two penalty tables: one keyed by tokens that appear in
# the `clean_flags` string, one keyed by boolean flag columns already on
# the frame. A row can accumulate multiple penalties; score floors at 0.
QUALITY_SCORE_START = 100

QUALITY_PENALTIES_BY_FLAG_TOKEN = {
    "NULL_DATE":                 30,
    "MISSING_DATE":               30,
    "BAD_DATE_FORMAT":            25,
    "DATE_OUT_OF_RANGE":          15,
    "ZERO_DEBIT_AND_CREDIT":      10,
    "INVALID_TRANSACTION":        30,
    "BOTH_NEGATIVE_AMOUNTS":      20,
    "MISSING_ACCOUNT_ID":         30,
    "MISSING_NARRATION_FILLED":    8,
    "MISSING_AMOUNT_FILLED":      10,
    "MISSING_BALANCE":            15,
    "MISSING_UTR":                 3,
    "MISSING_TIME_DEFAULTED":      2,
    "EMPTY_OR_SHORT_NARRATION":   10,
    "BALANCE_COLUMN_NOT_POPULATED": 8,
}

QUALITY_PENALTIES_BY_BOOL_COLUMN = {
    "is_duplicate":        15,   # near-duplicate flagged (exact dupes are removed, not scored)
    "is_balance_breach":   25,
    "is_high_value_flag":   5,
    "is_velocity_flag":     5,
    "is_self_transfer":     5,
    "is_malformed_ifsc":   10,
    "is_utr_collision":     5,
}

QUALITY_BAND_THRESHOLDS = {"HIGH": 80, "MEDIUM": 50}   # score>=80 HIGH, >=50 MEDIUM, else LOW

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
    "is_multi_file_collision",
    "is_self_transfer",
    "is_malformed_ifsc",
    "is_invalid_transaction",
    "quality_score",
    "quality_band",
]