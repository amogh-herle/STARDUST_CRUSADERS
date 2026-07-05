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

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, BackgroundTasks, Query
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
    workers: int = Query(4, description="Number of worker processes for parallel ingestion"),
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
    ingested_path, cleaned_path, analytics_out, pipeline_report = _run_pipeline(
        upload_dir, upload_id, warnings, workers=workers
    )

    if not cleaned_path or not os.path.exists(cleaned_path):
        raise HTTPException(status_code=500, detail="Pipeline failed: " + str(warnings))

    # Bulk load into PostgreSQL and sync analytics
    rows_loaded, banks_detected = await _load_into_db(cleaned_path, analytics_out, db)

    # Automatically generate graph.json files from the newly uploaded transaction dataset
    _generate_frontend_graph(cleaned_path)

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


def _run_pipeline(upload_dir: str, upload_id: str, warnings: list, workers: int = 4) -> tuple:
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
             "--out-dir", ingested_dir,
             "--workers", str(workers)],
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
        return None, None, None, {}
    except Exception as e:
        warnings.append(f"Ingestion error: {e}")
        return None, None, None, {}

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
        return None, None, None, {}

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
        return ingested_csv, None, None, {}

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

    return ingested_csv, cleaned_csv, analytics_out, {"rows_ingested": rows_ingested}


async def _load_into_db(
    cleaned_csv: str, analytics_out: str, db: AsyncSession
) -> tuple[int, list[str]]:
    """
    Bulk-load cleaned_transactions.csv into PostgreSQL and sync analytics outputs.
    """
    from seed import sync_analytics_to_db
    
    risk_scores_csv = os.path.join(analytics_out, "risk_scores.csv")
    community_summaries_csv = os.path.join(analytics_out, "community_summaries.csv")
    
    rows_loaded = await sync_analytics_to_db(
        db=db,
        risk_scores_csv=risk_scores_csv,
        community_summaries_csv=community_summaries_csv,
        cleaned_csv=cleaned_csv,
        is_seed=False
    )
    
    banks_detected = []
    if os.path.exists(cleaned_csv):
        df = pd.read_csv(cleaned_csv, dtype=str).fillna("")
        if "bank_name" in df.columns:
            banks_detected = [b for b in df["bank_name"].unique().tolist() if b]

    return rows_loaded, banks_detected


def _generate_frontend_graph(cleaned_csv: str):
    """
    Generate graph.json from the newly uploaded cleaned CSV file and save it
    to both aml_model/graph.json and public/graph.json.
    """
    import json
    import sys
    from pathlib import Path
    
    project_root = Path(__file__).resolve().parents[2]
    aml_model_path = project_root / "aml_model"
    
    # Target file paths
    aml_graph_path = aml_model_path / "graph.json"
    public_graph_path = project_root / "figma_frontend" / "bank-statement-dashboard" / "public" / "graph.json"
    
    if str(aml_model_path) not in sys.path:
        sys.path.append(str(aml_model_path))
        
    try:
        import importlib.util
        gb_path = aml_model_path / "graph" / "graph_builder.py"
        spec = importlib.util.spec_from_file_location("graph_builder", str(gb_path))
        graph_builder_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(graph_builder_module)
        GraphBuilder = graph_builder_module.GraphBuilder
        
        builder = GraphBuilder(cleaned_csv)
        
        # Seed with all primary accounts detected in statement
        seeds = builder.raw_df["account_id"].dropna().unique().tolist()
        if seeds:
            graph_data = builder.build_incremental_subgraph(
                seed=seeds,
                min_amount=0,
                max_hops=1,
                incremental_threshold=30,
            )
            
            # Create directories if they do not exist
            os.makedirs(os.path.dirname(aml_graph_path), exist_ok=True)
            os.makedirs(os.path.dirname(public_graph_path), exist_ok=True)

            # Write to aml_model/graph.json
            with open(aml_graph_path, "w", encoding="utf-8") as f:
                json.dump(graph_data, f, indent=4)
                
            # Write to public/graph.json
            with open(public_graph_path, "w", encoding="utf-8") as f:
                json.dump(graph_data, f, indent=4)
                
            print(f"[Graph Generation] Successfully built graph from cleaned CSV. Saved to {public_graph_path}")
        else:
            print("[Graph Generation] No seed accounts found in uploaded CSV.")
    except Exception as e:
        print(f"[Graph Generation] Failed to generate graph: {e}")
