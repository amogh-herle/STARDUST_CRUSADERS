"""
Phase 6 - Ingestion Pipeline

Main entry point. Takes a single file OR a directory of mixed-format
bank statements, routes each file to the correct parser, normalizes
to the unified schema, and writes:
    - ingested_transactions.csv   (all statements merged, unified schema)
  - ingestion_report.csv        (per-file summary: rows parsed, warnings, bank detected)

Usage:
    python ingest.py --input output/sample_bank_statements/ --out-dir ingested/ --workers [WORKERS]
    python ingest.py --input statement_ACC000042_SBI.csv   --out-dir ingested/ --workers [WORKERS]
"""

import os
import argparse
import multiprocessing as mp
import pandas as pd
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED

try:
    from phase6.ingestion_config import SUPPORTED_EXTENSIONS
    from phase6.format_parsers import parse_csv, parse_xlsx, parse_pdf, parse_image, parse_txt
    from phase6.normalizer import normalize
except ImportError:
    from ingestion_config import SUPPORTED_EXTENSIONS
    from format_parsers import parse_csv, parse_xlsx, parse_pdf, parse_image, parse_txt
    from normalizer import normalize


def ingest_file(file_path: str, pdf_password: str = None,
                 pdf_password_candidates: list = None) -> tuple[pd.DataFrame, dict]:
    """
    Ingest a single file. Returns (normalized_df, report_row).
    pdf_password / pdf_password_candidates are only used for .pdf files
    that turn out to be encrypted - ignored otherwise.
    """
    ext = Path(file_path).suffix.lower()
    fname = os.path.basename(file_path)

    report = {
        "file": fname,
        "extension": ext,
        "bank_detected": "",
        "rows_parsed": 0,
        "rows_after_clean": 0,
        "parse_warnings": "",
        "status": "ok",
    }

    if ext not in SUPPORTED_EXTENSIONS:
        report["status"] = "skipped"
        report["parse_warnings"] = f"Unsupported extension: {ext}"
        return pd.DataFrame(), report

    # --- Route to correct parser ---
    try:
        if ext == ".csv":
            raw_df, header_text, source_format, parse_warnings = parse_csv(file_path)
        elif ext in (".xlsx", ".xls"):
            raw_df, header_text, source_format, parse_warnings = parse_xlsx(file_path)
        elif ext == ".pdf":
            raw_df, header_text, source_format, parse_warnings = parse_pdf(
                file_path, password=pdf_password, password_candidates=pdf_password_candidates
            )
        elif ext in (".png", ".jpg", ".jpeg", ".tiff", ".tif"):
            raw_df, header_text, source_format, parse_warnings = parse_image(file_path)
        elif ext == ".json":
            from format_parsers import parse_json
            raw_df, header_text, source_format, parse_warnings = parse_json(file_path)
        elif ext == ".txt":
            raw_df, header_text, source_format, parse_warnings = parse_txt(file_path)
        elif ext == ".tsv":
            from format_parsers import parse_tsv
            raw_df, header_text, source_format, parse_warnings = parse_tsv(file_path)
        else:
            report["status"] = "skipped"
            return pd.DataFrame(), report
    except MemoryError:
        report["status"] = "error"
        report["parse_warnings"] = (
            "Parser crash: ran out of memory (a genuine allocation "
            "failure, not an artificial cap — this file likely needs "
            "fewer concurrent workers, or more system RAM)"
        )
        return pd.DataFrame(), report
    except Exception as e:
        report["status"] = "error"
        report["parse_warnings"] = f"Parser crash: {e}"
        return pd.DataFrame(), report

    report["rows_parsed"] = len(raw_df)
    if parse_warnings:
        report["parse_warnings"] = " | ".join(parse_warnings)
        # PDF parser flags this distinctly so it doesn't get lumped in
        # with generic crashes - an investigator should see at a glance
        # "this one needs a password", not "something went wrong".
        if any(str(w).startswith("PASSWORD_PROTECTED") for w in parse_warnings):
            report["status"] = "password_protected"
            return pd.DataFrame(), report

    if raw_df.empty:
        report["status"] = "empty"
        return pd.DataFrame(), report

    # --- Normalize to unified schema ---
    try:
        normalized_df, norm_warnings = normalize(
            raw_df, header_text, file_path, source_format
        )
    except Exception as e:
        report["status"] = "normalization_error"
        report["parse_warnings"] += f" | Normalization crash: {e}"
        return pd.DataFrame(), report

    report["rows_after_clean"] = len(normalized_df)
    report["bank_detected"] = normalized_df["bank_name"].iloc[0] if not normalized_df.empty else ""
    if norm_warnings:
        report["parse_warnings"] += " | " + " | ".join(norm_warnings)

    return normalized_df, report


def _ingest_file_worker(file_path: str, pdf_password: str = None,
                         pdf_password_candidates: list = None,
                         mem_limit_mb: float = None):
    # mem_limit_mb is accepted but unused here — it's still passed through
    # from the scheduler for weighting/admission-control purposes (how
    # many files run concurrently), but no longer enforced as a hard
    # RLIMIT_AS on this process. That was capping *virtual* address space,
    # which Python + numpy + pandas + pdfplumber + openpyxl reserve far
    # more of than actual resident memory just from imports (BLAS/shared
    # library mmaps) — so it was killing every file, including trivial
    # ones, before they could even finish importing their parser.
    return ingest_file(file_path, pdf_password, pdf_password_candidates)


def ingest_directory(input_path: str, out_dir: str, pdf_password: str = None,
                      pdf_password_candidates: list = None, workers: int = 1):
    """Ingest all supported files in a directory tree."""
    files = []
    for root, _, filenames in os.walk(input_path):
        root_path = Path(root)
        for f in sorted(filenames):
            file_path = root_path / f
            if file_path.suffix.lower() in SUPPORTED_EXTENSIONS:
                files.append(file_path)
    return _run_pipeline(files, out_dir, pdf_password, pdf_password_candidates, workers)


def ingest_single(file_path: str, out_dir: str, pdf_password: str = None,
                   pdf_password_candidates: list = None, workers: int = 1):
    """Ingest a single file."""
    return _run_pipeline([file_path], out_dir, pdf_password, pdf_password_candidates, workers)


def _run_pipeline(files: list, out_dir: str, pdf_password: str = None,
                   pdf_password_candidates: list = None, workers: int = 1):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_rows = []
    out_csv = out_dir / "ingested_transactions.csv"
    dup_csv = out_dir / "removed_duplicate_rows.csv"
    report_csv = out_dir / "ingestion_report.csv"

    for path in (out_csv, dup_csv, report_csv):
        if path.exists():
            path.unlink()

    # Seed the main output file so downstream steps always see a valid CSV,
    # even if the run only produces warnings or empty inputs.
    pd.DataFrame(columns=[
        "account_id", "account_holder", "bank_name",
        "date", "time", "narration", "channel",
        "debit", "credit", "balance",
        "utr_ref", "counterparty_name", "counterparty_account",
        "counterparty_ifsc", "source_file", "source_format",
        "ingestion_warnings",
    ]).to_csv(out_csv, index=False)

    transactions_written = 0
    duplicate_rows_removed = 0
    seen_transaction_keys = set()
    banks_detected = set()
    formats_ingested = set()

    print(f"\n{'='*60}")
    print(f"Phase 6 — Ingestion Pipeline")
    print(f"Files to process: {len(files)}")
    if workers and workers != 1:
        print(f"Parallel workers  : {workers}")
    print(f"{'='*60}")

    def _append_df_to_csv(df: pd.DataFrame, path: Path):
        if df is None or df.empty:
            return
        df.to_csv(path, mode="a", index=False, header=False)

    def _transaction_keys(df: pd.DataFrame):
        key_frame = df[["account_id", "date", "narration", "debit", "credit"]].copy()
        for column in ("account_id", "date", "narration"):
            key_frame[column] = key_frame[column].fillna("").astype(str)
        for column in ("debit", "credit"):
            key_frame[column] = pd.to_numeric(key_frame[column], errors="coerce").fillna(0.0).round(2)
        return list(key_frame.itertuples(index=False, name=None))

    # Generic name fallback: if a PDF's account holder couldn't be extracted
    # from the statement header, give it a stable "Person N" label instead
    # of leaving it blank. Keyed by account_id (falls back to source_file
    # if account_id is also missing) so every row from the same account
    # gets the SAME label, and each new unlabeled account gets the next
    # number. This runs in the main process only (results are handled
    # sequentially in _emit_ready_results), so it's safe without locks
    # even though parsing itself happens in parallel worker processes.
    _person_label_map: dict = {}
    _person_counter = [0]

    def _assign_generic_names(df: pd.DataFrame) -> pd.DataFrame:
        if "account_holder" not in df.columns or df.empty:
            return df
        holder = df["account_holder"].astype(str).str.strip()
        blank_mask = df["account_holder"].isna() | holder.eq("") | holder.str.lower().eq("nan")
        if not blank_mask.any():
            return df

        acct_key = df["account_id"].astype(str).str.strip()
        fname_key = df["source_file"].astype(str).str.strip()
        key_series = acct_key.where(acct_key.ne(""), fname_key)

        for key in key_series[blank_mask].unique():
            if key not in _person_label_map:
                _person_counter[0] += 1
                _person_label_map[key] = f"Person {_person_counter[0]}"

        df.loc[blank_mask, "account_holder"] = key_series[blank_mask].map(_person_label_map)
        return df

    def _write_normalized_rows(df: pd.DataFrame):
        nonlocal transactions_written, duplicate_rows_removed

        if df is None or df.empty:
            return

        df = _assign_generic_names(df)

        banks_detected.update(str(v) for v in df["bank_name"].dropna().unique() if str(v))
        formats_ingested.update(str(v) for v in df["source_format"].dropna().unique() if str(v))

        keys = _transaction_keys(df)
        keep_mask = []
        for key in keys:
            if key in seen_transaction_keys:
                keep_mask.append(False)
                duplicate_rows_removed += 1
            else:
                seen_transaction_keys.add(key)
                keep_mask.append(True)

        keep_mask_series = pd.Series(keep_mask, index=df.index)
        kept_rows = df.loc[keep_mask_series].reset_index(drop=True)
        duplicate_rows = df.loc[~keep_mask_series].reset_index(drop=True)

        _append_df_to_csv(kept_rows, out_csv)
        transactions_written += len(kept_rows)

        if not duplicate_rows.empty:
            _append_df_to_csv(duplicate_rows, dup_csv)

    if workers is None or workers <= 1:
        iterable = ((idx + 1, file_path, ingest_file(file_path, pdf_password, pdf_password_candidates))
                    for idx, file_path in enumerate(files))
        for idx, file_path, result in iterable:
            fname = Path(file_path).name
            df, report = result
            print(f"\n  [{idx}/{len(files)}] {fname}")

            report_rows.append(report)

            if not df.empty:
                _write_normalized_rows(df)
                print(f"    ✓ Bank    : {report['bank_detected']}")
                print(f"    ✓ Rows    : {report['rows_parsed']} parsed → {report['rows_after_clean']} clean")
                if report["parse_warnings"]:
                    print(f"    ⚠ Warnings: {report['parse_warnings'][:120]}")
            else:
                print(f"    ✗ Status  : {report['status']}")
                if report["parse_warnings"]:
                    print(f"    ✗ Reason  : {report['parse_warnings'][:120]}")
    else:
        max_workers = workers if workers > 0 else max(1, os.cpu_count() - 1)
        memory_safe_cap = max(1, int(os.getenv("PHASE6_MAX_PARALLEL_WORKERS", "8")))
        if max_workers > memory_safe_cap:
            print(f"Capping workers to {memory_safe_cap} to keep memory bounded (requested {max_workers})")
            max_workers = memory_safe_cap

        # ── Memory-aware scheduling (admission control only) ────────────
        # File SIZE is a bad proxy for PDF memory use here — a 1000-page
        # statement can be a 1-4MB file (measured on this dataset) while
        # needing >2.5GB peak RSS to parse, because pdfplumber builds a
        # full layout graph per page. PAGE COUNT is what actually predicts
        # memory use. Measured on real files from this dataset:
        #   373 pages  -> ~864MB peak RSS
        #   1011 pages -> ~2454MB peak RSS
        # -> roughly linear at ~2.6MB/page + ~150MB baseline. This estimate
        # is used ONLY to decide how many files run *concurrently* (so we
        # don't accidentally admit 8x 900-page PDFs at once) — it is NOT
        # enforced as a hard per-process memory cap (an earlier version of
        # this did that via RLIMIT_AS, which caps *virtual* address space
        # rather than actual usage; numpy/pandas/pdfplumber/openpyxl
        # reserve far more of that from imports alone than any reasonable
        # per-file budget, so it ended up killing every file, including
        # trivial ones, before they finished importing their parser).
        mb_per_page = float(os.getenv("PHASE6_MB_PER_PAGE", "2.6"))
        base_overhead_mb = float(os.getenv("PHASE6_BASE_OVERHEAD_MB", "150"))
        base_worker_mem_mb = float(os.getenv("PHASE6_BASE_WORKER_MEM_MB", "700"))
        # Page count alone undershoots for statements with a lot of rows
        # per page (measured: a 234-page file with ~46 rows/page peaked at
        # 1.36GB vs. ~0.86GB for a 194-page file with far fewer rows/page)
        # — row density varies enough between banks that a flat per-page
        # rate isn't precise. 2.2x safety margin absorbs that variance
        # without needing per-bank tuning.
        safety_mult = float(os.getenv("PHASE6_MEM_SAFETY_MULT", "2.2"))
        max_worker_mem_mb = float(os.getenv("PHASE6_MAX_WORKER_MEM_MB", "7000"))

        def _pdf_page_count(path):
            try:
                import pypdf
                return len(pypdf.PdfReader(str(path)).pages)
            except Exception:
                return None

        def _estimate_mem_mb(path) -> float:
            path = Path(path)
            if path.suffix.lower() == ".pdf":
                pages = _pdf_page_count(path)
                if pages:
                    est = base_overhead_mb + mb_per_page * pages
                    return min(max_worker_mem_mb, max(base_worker_mem_mb, est * safety_mult))
            return base_worker_mem_mb

        # Total concurrent-admission budget, in MB, based on actual system
        # RAM rather than a magic worker-count constant.
        def _total_system_mb() -> float:
            try:
                with open("/proc/meminfo") as fh:
                    for line in fh:
                        if line.startswith("MemTotal:"):
                            return int(line.split()[1]) / 1024
            except Exception:
                pass
            return 8192.0  # conservative guess if we can't read /proc/meminfo

        mem_budget_mb = float(os.getenv(
            "PHASE6_TOTAL_MEM_BUDGET_MB", str(int(_total_system_mb() * 0.70))
        ))

        weight_budget = mem_budget_mb
        current_weight = 0.0
        in_flight_weight = {}  # future -> (weight, its own rlimit) for release + bookkeeping

        print(f"Using up to {max_workers} worker processes "
              f"(system mem budget ~{mem_budget_mb:.0f}MB, "
              f"per-file cap {base_worker_mem_mb:.0f}-{max_worker_mem_mb:.0f}MB)")
        pending = {}
        completed = {}
        next_emit_idx = 1

        def _handle_result(idx, file_path, result):
            fname = Path(file_path).name
            df, report = result
            completed[idx] = (fname, df, report)

        def _emit_ready_results():
            nonlocal next_emit_idx
            while next_emit_idx in completed:
                fname, df, report = completed.pop(next_emit_idx)
                print(f"\n  [{next_emit_idx}/{len(files)}] {fname}")

                report_rows.append(report)

                if not df.empty:
                    _write_normalized_rows(df)
                    print(f"    ✓ Bank    : {report['bank_detected']}")
                    print(f"    ✓ Rows    : {report['rows_parsed']} parsed → {report['rows_after_clean']} clean")
                    if report["parse_warnings"]:
                        print(f"    ⚠ Warnings: {report['parse_warnings'][:120]}")
                else:
                    print(f"    ✗ Status  : {report['status']}")
                    if report["parse_warnings"]:
                        print(f"    ✗ Reason  : {report['parse_warnings'][:120]}")

                next_emit_idx += 1

        def _submit_next(executor, next_idx):
            file_path = files[next_idx]
            w = _estimate_mem_mb(file_path)
            future = executor.submit(
                _ingest_file_worker,
                str(file_path),
                pdf_password,
                pdf_password_candidates,
                w,
            )
            pending[future] = (next_idx + 1, file_path)
            in_flight_weight[future] = w
            return w

        executor_kwargs = {"max_workers": max_workers}
        try:
            executor_kwargs["mp_context"] = mp.get_context("spawn")
        except ValueError:
            pass

        try:
            executor_kwargs["max_tasks_per_child"] = 1
            executor = ProcessPoolExecutor(**executor_kwargs)
        except TypeError:
            executor_kwargs.pop("max_tasks_per_child", None)
            executor = ProcessPoolExecutor(**executor_kwargs)

        with executor:
            next_submit_idx = 0

            # Admit files while both budgets allow it: process-count (CPU
            # cores, via max_workers) AND estimated memory (mem_budget_mb,
            # via current_weight). A single file whose own estimate alone
            # exceeds the memory budget is still admitted once nothing
            # else is in flight, so we never deadlock — it just runs solo.
            while next_submit_idx < len(files) and len(pending) < max_workers and (
                current_weight < weight_budget or not pending
            ):
                current_weight += _submit_next(executor, next_submit_idx)
                next_submit_idx += 1

            while pending:
                done, _ = wait(pending.keys(), return_when=FIRST_COMPLETED)
                for future in done:
                    idx, file_path = pending.pop(future)
                    current_weight -= in_flight_weight.pop(future, 1)
                    try:
                        result = future.result()
                    except Exception as e:
                        # Covers a genuine worker-process crash (real OOM
                        # kill, segfault in a native library, etc.) that
                        # ingest_file()'s own try/except couldn't catch
                        # because the whole process died.
                        result = (pd.DataFrame(), {
                            "file": Path(file_path).name,
                            "extension": Path(file_path).suffix.lower(),
                            "bank_detected": "",
                            "rows_parsed": 0,
                            "rows_after_clean": 0,
                            "parse_warnings": f"Parallel worker crash: {e}",
                            "status": "error",
                        })

                    _handle_result(idx, file_path, result)
                    _emit_ready_results()

                    while next_submit_idx < len(files) and (
                        current_weight < weight_budget or not pending
                    ):
                        current_weight += _submit_next(executor, next_submit_idx)
                        next_submit_idx += 1

            _emit_ready_results()

    if transactions_written == 0:
        print("\n  ✗ No data was successfully ingested.")

    report_df = pd.DataFrame(report_rows)
    report_df.to_csv(report_csv, index=False)

    _print_summary(
        report_rows,
        transactions_written,
        duplicate_rows_removed,
        banks_detected,
        formats_ingested,
        out_dir,
        out_csv,
        dup_csv if duplicate_rows_removed else None,
    )
    return report_df


def _print_summary(report_rows, transactions_written, dedup_removed, banks_detected, formats_ingested, out_dir, out_csv, dup_csv=None):
    total = len(report_rows)
    # status can stay "ok" even when normalize() extracted zero usable
    # rows (it only flips to "normalization_error"/"empty" on an actual
    # exception or an empty parse, not on "parsed fine but everything got
    # filtered out") — counting on rows_after_clean catches that case
    # instead of silently reporting a file as successful with no data.
    ok = sum(1 for r in report_rows if r["rows_after_clean"] > 0)
    errored = sum(
        1 for r in report_rows
        if r["status"] in ("error", "normalization_error", "empty")
        or (r["status"] == "ok" and r["rows_after_clean"] == 0)
    )
    skipped = sum(1 for r in report_rows if r["status"] == "skipped")
    password_protected = [r["file"] for r in report_rows if r["status"] == "password_protected"]

    print(f"\n{'='*60}")
    print("INGESTION SUMMARY")
    print(f"{'='*60}")
    print(f"Files processed    : {total}")
    print(f"  Successfully      : {ok}")
    print(f"  Errors/empty      : {errored}")
    print(f"  Skipped           : {skipped}")
    print(f"  Password-protected: {len(password_protected)}")
    if transactions_written > 0:
        print(f"Total rows ingested : {transactions_written + dedup_removed}")
        print(f"Duplicates removed  : {dedup_removed}")
        print(f"Final clean rows    : {transactions_written}")
        print(f"Banks detected      : {', '.join(sorted(banks_detected))}")
        print(f"Formats ingested    : {', '.join(sorted(formats_ingested))}")
        if dedup_removed:
            print(f"Duplicate rows saved: {os.path.join(out_dir, 'removed_duplicate_rows.csv')}")
    if password_protected:
        print(f"\n  ⚠ These files need a password — re-run with --pdf-password")
        print(f"    or --pdf-passwords to supply one or more candidates:")
        for f in password_protected:
            print(f"      • {f}")
    print(f"\nOutputs written to: {os.path.abspath(out_dir)}")
    print(f"  ingested_transactions.csv")
    print(f"  ingestion_report.csv")
    if dup_csv is not None:
        print(f"  removed_duplicate_rows.csv")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase 6 — Bank Statement Ingestion Pipeline")
    parser.add_argument("--input", required=True, help="File or directory to ingest")
    parser.add_argument("--out-dir", default="ingested", help="Output directory")
    parser.add_argument("--pdf-password", default=None,
                         help="Password to try if a PDF turns out to be encrypted")
    parser.add_argument("--pdf-passwords", default=None,
                         help="Comma-separated list of candidate passwords to try in order "
                              "(e.g. likely DOB/PAN/account-number-based formulas)")
    parser.add_argument("--workers", type=int, default=1,
                         help="Number of worker processes for parallel ingestion. Use 0 for auto.")
    args = parser.parse_args()

    candidates = [p.strip() for p in args.pdf_passwords.split(",")] if args.pdf_passwords else None

    if os.path.isdir(args.input):
        ingest_directory(args.input, args.out_dir, args.pdf_password, candidates, args.workers)
    elif os.path.isfile(args.input):
        ingest_single(args.input, args.out_dir, args.pdf_password, candidates, args.workers)
    else:
        print(f"Error: {args.input} is not a valid file or directory")