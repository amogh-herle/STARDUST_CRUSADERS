"""
Router: /api/v1/upload

Accepts multi-file upload of bank statements (CSV/Excel/PDF/PNG),
runs Phase 6 ingestion + Phase 7 cleaning, and loads results into
PostgreSQL via bulk insert.

POST /                Upload one or more statement files
GET  /status/{id}     Check async processing status
"""

import os
import uuid
import subprocess
import shutil
import pandas as pd

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, BackgroundTasks
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from dependencies import get_db
from models import Account, Transaction
from schemas import UploadResponse
from config import settings

router = APIRouter(prefix="/upload", tags=["Upload"])

# In-memory job status store (replace with Redis in production)
_job_status: dict[str, dict] = {}

SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".pdf", ".png", ".jpg", ".jpeg"}


@router.get("/analytics-status")
async def get_analytics_status():
    """
    Return a summary of the latest Phase 8 analytics run so the frontend
    can show a quick overview without calling the full assistant.
    """
    import json
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[2]
    analytics_dir = project_root / "data" / "analytics_v2"
    report_path = analytics_dir / "analytics_report.json"
    risk_path = analytics_dir / "risk_scores.csv"

    if not report_path.exists():
        return {"status": "no_data", "message": "No analytics run yet. Upload a statement first."}

    with report_path.open() as f:
        report = json.load(f)

    top_accounts = []
    if risk_path.exists():
        try:
            df = pd.read_csv(risk_path, dtype=str)
            df["risk_score"] = pd.to_numeric(df["risk_score"], errors="coerce").fillna(0)
            top_accounts = (
                df.sort_values("risk_score", ascending=False)
                  .head(5)[["account_id", "account_holder", "risk_score", "risk_tier", "active_patterns"]]
                  .to_dict(orient="records")
            )
        except Exception:
            pass

    return {
        "status": "ready",
        "run_timestamp": report.get("run_timestamp"),
        "accounts": report.get("accounts", 0),
        "critical_accounts": report.get("critical_accounts", 0),
        "high_accounts": report.get("high_accounts", 0),
        "medium_accounts": report.get("medium_accounts", 0),
        "round_trips": report.get("round_trips", 0),
        "layering_chains": report.get("layering_chains", 0),
        "fan_in": report.get("fan_in", 0),
        "fan_out": report.get("fan_out", 0),
        "smurfing": report.get("smurfing", 0),
        "odd_hours": report.get("odd_hours", 0),
        "communities": report.get("communities", 0),
        "top_accounts": top_accounts,
    }


@router.post("/", response_model=UploadResponse)
async def upload_statements(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Accept bank statement files, save to disk, run ingestion + cleaning
    pipeline, bulk-load results into PostgreSQL.

    Supports: CSV, Excel (xlsx/xls), PDF (text-layer), PNG/JPG (OCR via Tesseract)
    """
    upload_id = str(uuid.uuid4())[:8]
    upload_dir = os.path.join(settings.UPLOAD_DIR, upload_id)
    os.makedirs(upload_dir, exist_ok=True)

    # Validate and save uploaded files
    saved_files = []
    warnings = []

    for f in files:
        ext = os.path.splitext(f.filename or "")[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            warnings.append(f"{f.filename}: unsupported format, skipped")
            continue

        size_bytes = 0
        dest = os.path.join(upload_dir, f.filename)
        with open(dest, "wb") as out:
            while chunk := await f.read(1024 * 1024):
                size_bytes += len(chunk)
                if size_bytes > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
                    warnings.append(f"{f.filename}: exceeds {settings.MAX_UPLOAD_SIZE_MB}MB limit")
                    break
                out.write(chunk)
        saved_files.append(dest)

    if not saved_files:
        raise HTTPException(status_code=400, detail="No valid files uploaded")

    # Run pipeline synchronously (for hackathon demo; use BackgroundTasks for production)
    ingested_path, cleaned_path, pipeline_report = _run_pipeline(
        upload_dir, upload_id, warnings
    )

    if not cleaned_path or not os.path.exists(cleaned_path):
        raise HTTPException(status_code=500, detail="Pipeline failed: " + str(warnings))

    # Bulk load into PostgreSQL
    rows_loaded, banks_detected = await _load_into_db(cleaned_path, db)

    return UploadResponse(
        upload_id=upload_id,
        files_received=len(files),
        files_ingested=len(saved_files),
        rows_parsed=pipeline_report.get("rows_ingested", 0),
        rows_after_clean=rows_loaded,
        banks_detected=banks_detected,
        warnings=warnings[:20],   # cap warning list for response size
        status="success" if rows_loaded > 0 else "partial",
    )


def _run_pipeline(upload_dir: str, upload_id: str, warnings: list) -> tuple:
    """Run Phase 6 + Phase 7 + Phase 8 as subprocess calls."""
    import sys
    from pathlib import Path

    # Always use absolute paths — phase6/7/8 run with different cwd
    upload_dir   = str(Path(upload_dir).resolve())
    ingested_dir = str(Path(upload_dir) / "ingested")
    cleaned_dir  = str(Path(upload_dir) / "cleaned")

    # Resolve python executable (use same interpreter as current process)
    python_exe = sys.executable

    # Resolve project root (backend/ is one level below project root)
    project_root = Path(__file__).resolve().parents[2]

    phase6_script = str(project_root / "phase6" / "ingest.py")
    phase7_script = str(project_root / "phase7" / "clean.py")
    phase8_script = str(project_root / "phase8" / "analyse.py")
    analytics_out = str(project_root / "data" / "analytics_v2")

    # Phase 6: ingest
    try:
        result = subprocess.run(
            [python_exe, phase6_script,
             "--input", upload_dir,
             "--out-dir", ingested_dir],
            capture_output=True, text=True, timeout=120,
            cwd=str(project_root / "phase6"),
        )
        if result.returncode != 0:
            warnings.append(f"Ingestion warning: {result.stderr[:300]}")
        # Log stdout for debugging even on success
        if result.stdout:
            print(f"[Phase6 stdout] {result.stdout[-500:]}")
        if result.stderr:
            print(f"[Phase6 stderr] {result.stderr[-500:]}")
    except subprocess.TimeoutExpired:
        warnings.append("Ingestion timed out after 120s")
        return None, None, {}
    except Exception as e:
        warnings.append(f"Ingestion error: {e}")
        return None, None, {}

    ingested_csv = os.path.join(ingested_dir, "ingested_transactions.csv")
    if not os.path.exists(ingested_csv):
        # Check for a per-file report to understand why
        report_csv = os.path.join(ingested_dir, "ingestion_report.csv")
        if os.path.exists(report_csv):
            try:
                rep = pd.read_csv(report_csv)
                for _, row in rep.iterrows():
                    if row.get("status") != "ok" or row.get("rows_after_clean", 0) == 0:
                        warnings.append(
                            f"Phase6 [{row.get('file')}]: status={row.get('status')} "
                            f"reason={str(row.get('parse_warnings',''))[:200]}"
                        )
            except Exception:
                pass
        if not warnings or not any("Phase6" in w for w in warnings):
            warnings.append("Ingestion produced no output — file may be unsupported or parsing failed")
        return None, None, {}

    # Phase 7: clean
    try:
        result = subprocess.run(
            [python_exe, phase7_script,
             "--input", ingested_csv,
             "--out-dir", cleaned_dir],
            capture_output=True, text=True, timeout=120,
            cwd=str(project_root / "phase7"),
        )
        if result.returncode != 0:
            warnings.append(f"Cleaning warning: {result.stderr[:300]}")
    except Exception as e:
        warnings.append(f"Cleaning error: {e}")
        return ingested_csv, None, {}

    cleaned_csv = os.path.join(cleaned_dir, "cleaned_transactions.csv")

    rows_ingested = 0
    try:
        rows_ingested = len(pd.read_csv(ingested_csv))
    except Exception:
        pass

    # Phase 8: analytics — writes outputs to data/analytics_v2/
    if os.path.exists(cleaned_csv):
        try:
            os.makedirs(analytics_out, exist_ok=True)
            result = subprocess.run(
                [python_exe, phase8_script,
                 "--input", cleaned_csv,
                 "--out-dir", analytics_out],
                capture_output=True, text=True, timeout=300,
                cwd=str(project_root / "phase8"),
            )
            if result.returncode != 0:
                warnings.append(f"Analytics warning: {result.stderr[:300]}")
        except Exception as e:
            warnings.append(f"Analytics error: {e}")

    return ingested_csv, cleaned_csv, {"rows_ingested": rows_ingested}


async def _load_into_db(
    cleaned_csv: str, db: AsyncSession
) -> tuple[int, list[str]]:
    """
    Bulk-load cleaned_transactions.csv into PostgreSQL.
    Uses upsert logic: existing account_id rows are updated,
    new ones inserted. Transaction rows are appended (never updated).
    """
    df = pd.read_csv(cleaned_csv, dtype=str)
    df = df.fillna("")
    banks_detected = df["bank_name"].unique().tolist() if "bank_name" in df.columns else []

    # --- Upsert Accounts ---
    if "account_id" in df.columns:
        for _, row in df.drop_duplicates("account_id").iterrows():
            existing = await db.get(Account, row["account_id"])
            if not existing:
                acct = Account(
                    account_id=row.get("account_id", ""),
                    holder_name=row.get("account_holder", "Unknown"),
                    bank_name=row.get("bank_name", "Unknown"),
                    source_file=row.get("source_file", ""),
                )
                db.add(acct)

    await db.flush()

    # --- Insert Transactions (skip existing UTR refs to avoid exact dupes) ---
    rows_loaded = 0
    for _, row in df.iterrows():
        def _f(v, default=None):
            val = row.get(v, "")
            if val == "" or (isinstance(val, float) and pd.isna(val)):
                return default
            return val

        def _float(v):
            try:
                return float(_f(v, 0))
            except (ValueError, TypeError):
                return 0.0

        def _bool(v):
            val = str(_f(v, "False")).lower()
            return val in ("true", "1", "yes")

        txn = Transaction(
            account_id=_f("account_id", ""),
            date=_f("date"),
            time=_f("time", "00:00:00"),
            narration=_f("narration"),
            channel=_f("channel", "OTHER"),
            debit=_float("debit"),
            credit=_float("credit"),
            balance=_float("balance"),
            utr_ref=_f("utr_ref"),
            counterparty_account_id=_f("counterparty_account_id"),
            counterparty_name=_f("counterparty_name"),
            source_file=_f("source_file"),
            source_format=_f("source_format"),
            ingestion_warnings=_f("ingestion_warnings"),
            clean_flags=_f("clean_flags"),
            is_duplicate=_bool("is_duplicate"),
            is_balance_breach=_bool("is_balance_breach"),
            is_high_value_flag=_bool("is_high_value_flag"),
            is_ocr_row=_bool("is_ocr_row"),
        )
        db.add(txn)
        rows_loaded += 1

    await db.flush()
    return rows_loaded, [b for b in banks_detected if b]
