# Phase 7 — Data Cleaning Engine

## Overview

Phase 7 is the data-cleaning pipeline that processes Phase 6's ingested transactions. It standardizes, deduplicates, validates, handles missing values, and scores data quality.

**Status:** Production-ready with performance optimizations and comprehensive test coverage.

---

## Quick Start

### Run Cleaning Pipeline
```bash
python clean.py --input ../phase6/ingested/ingested_transactions.csv --out-dir cleaned/
```

### Run Tests
```bash
pytest test_phase7.py -v
```

### Run Performance Benchmark
```bash
python benchmark_performance.py
```

---

## Files

### Core Modules
- `clean.py` — Main orchestrator (runs all 5 modules in sequence)
- `validator.py` — Data standardizer & validator (dates, amounts, balance continuity, etc.)
- `missing_handler.py` — Missing value handler (flags/fills based on safety rules)
- `quality_assessor.py` — Quality scorer (penalty-based scoring system)
- `deduplicator.py` — Duplicate detector (exact removal, near/UTR flagging)
- `cleaning_config.py` — Configuration (thresholds, penalties, flags)
- `audit_utils.py` — Shared utilities (action logging, flag merging)

### Testing & Benchmarking
- `test_phase7.py` — Test suite (10 tests covering edge cases)
- `benchmark_performance.py` — Performance benchmark (tests on 500k rows)

### Documentation
- `HARDENING_CHANGES.md` — Complete change log with before/after diffs
- `README.md` — This file

---

## Pipeline Flow

```
Input: ingested_transactions.csv (from Phase 6)
  ↓
[1] Data Standardizer — dates, amounts, text normalization
  ↓
[2] Duplicate Detector — exact removal, near/UTR/multi-file flagging
  ↓
[3] Data Validator — transaction types, failed txns, balance continuity,
                     counterparties, outliers, velocity, narration integrity
  ↓
[4] Missing Value Handler — flag/fill based on safety rules
  ↓
[5] Quality Assessor — penalty-based quality scoring
  ↓
Output: cleaned_transactions.csv + audit files
```

---

## Output Files

| File | Description |
|------|-------------|
| `cleaned_transactions.csv` | Main output → feed to Phase 8/9/10 |
| `removed_data.csv` | All removed rows with reasons |
| `flagged_data.csv` | All flagged rows with reasons |
| `near_duplicates_flagged.csv` | Near-duplicates for review |
| `utr_collisions_flagged.csv` | UTR collisions for review |
| `all_actions.csv` | Row-level audit log of every change |
| `suspect_accounts.csv` | Accounts with balance integrity issues |
| `cleaning_report.json` | Machine-readable full audit trail |
| `quality_report.json` | Quality assessment + duplicate-rate stats |
| `cleaning_summary.txt` | Human-readable narrative summary |

---

## Recent Improvements

### ✅ Performance (3-10x speedup)
- Vectorized all row-by-row loops
- 500k rows now process in ~15 seconds (vs 45-150s before)

### ✅ Code Quality
- Extracted shared utilities to `audit_utils.py`
- Comprehensive test suite (10/10 tests passing)
- Schema validation at pipeline entry

### ✅ Production Ready
- Migrated to `logging` module (from print statements)
- Complete audit trail (fixed ZERO_DEBIT_AND_CREDIT gap)
- Tightened coupling (loud failures instead of silent bugs)

See `HARDENING_CHANGES.md` for complete details.

---

## Design Principles

1. **Never silently drop or guess** — Every removal/flag/change is logged
2. **Complete audit trail** — Reconstructable by investigators
3. **Fail-fast** — Clear error messages for invalid input
4. **Performance** — Vectorized operations for large datasets

---

## Configuration

Edit `cleaning_config.py` to adjust:
- Deduplication thresholds
- Balance tolerance levels
- Quality penalty weights
- Flag definitions

---

## Support

- Review `HARDENING_CHANGES.md` for detailed change explanations
- Check inline comments in code (they explain the "why")
- Run tests with `-v` flag: `pytest test_phase7.py -v`

---

**Version:** v3 (hardened)  
**Test Coverage:** 10/10 passing  
**Performance:** Benchmarked on 500k rows
