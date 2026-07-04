"""
graph_api.py
FastAPI backend serving the money-flow graph to a Next.js frontend as JSON.
NO HTML, NO Pyvis. Works with ANY uploaded CSV, not just the model's
trained/scored output.

Run:
    uvicorn api.graph_api:app --reload --port 8000

Workflow:
    1. POST /dataset/upload         (multipart file upload)
       -> returns {"dataset_id": "..."}  -- use this id in every call below

    2. GET  /graph/bounds?dataset_id=...
    3. GET  /graph/build?dataset_id=...&seed=ACC001&min_amount=500
    4. GET  /graph/expand?dataset_id=...&node=ACC001
    5. POST /graph/from-prompt      {"dataset_id": "...", "account_id": "..."}

If dataset_id is omitted, falls back to the model's default scored CSV
(outputs/reports/isolation_forest_scored_transactions.csv) for backward
compatibility.
"""

from fastapi import FastAPI, Query, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import os
import sys
import uuid
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from graph.graph_builder import GraphBuilder

app = FastAPI(title="Money Flow Graph API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CSV = os.path.join(BASE_DIR, "outputs", "reports", "isolation_forest_scored_transactions.csv")
UPLOAD_DIR = os.path.join(BASE_DIR, "outputs", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# dataset_id -> csv filepath  (survives across requests, resets on server restart)
_dataset_registry: dict = {}
# csv filepath -> GraphBuilder instance (avoids re-parsing the same file repeatedly)
_builder_cache: dict = {}

DEFAULT_DATASET_ID = "__default__"
_dataset_registry[DEFAULT_DATASET_ID] = DEFAULT_CSV


def get_builder(dataset_id: Optional[str] = None) -> GraphBuilder:
    dsid = dataset_id or DEFAULT_DATASET_ID
    if dsid not in _dataset_registry:
        raise HTTPException(status_code=404,
                             detail=f"Unknown dataset_id '{dsid}'. Upload a file first via /dataset/upload.")

    csv_path = _dataset_registry[dsid]
    if not os.path.exists(csv_path):
        raise HTTPException(status_code=404, detail=f"File not found on disk: {csv_path}")

    if csv_path not in _builder_cache:
        try:
            _builder_cache[csv_path] = GraphBuilder(csv_path)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to parse CSV: {e}")

    return _builder_cache[csv_path]


class PromptGraphRequest(BaseModel):
    account_id: str
    dataset_id: Optional[str] = None
    min_amount: float = 0
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    incremental_threshold: int = 30


# ── dataset upload ────────────────────────────────────────────────────────────

@app.post("/dataset/upload")
async def upload_dataset(file: UploadFile = File(...)):
    """
    Accepts ANY transaction CSV (schema-flexible — see graph_builder.py's
    _load() for supported column name variants). Returns a dataset_id to
    use in all subsequent /graph/* calls.
    """
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv files are supported")

    dataset_id = str(uuid.uuid4())
    dest_path = os.path.join(UPLOAD_DIR, f"{dataset_id}.csv")

    with open(dest_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    _dataset_registry[dataset_id] = dest_path

    # Validate immediately so the frontend gets a clear error right away,
    # not on the first /graph/build call
    try:
        builder = GraphBuilder(dest_path)
    except Exception as e:
        os.remove(dest_path)
        del _dataset_registry[dataset_id]
        raise HTTPException(status_code=400, detail=f"Could not process this CSV: {e}")

    _builder_cache[dest_path] = builder
    bounds = builder.get_filter_bounds()

    return {
        "dataset_id": dataset_id,
        "filename": file.filename,
        "bounds": bounds,
    }


@app.get("/dataset/list")
def list_datasets():
    return {"datasets": list(_dataset_registry.keys())}


@app.delete("/dataset/{dataset_id}")
def delete_dataset(dataset_id: str):
    if dataset_id == DEFAULT_DATASET_ID:
        raise HTTPException(status_code=400, detail="Cannot delete the default dataset")
    if dataset_id not in _dataset_registry:
        raise HTTPException(status_code=404, detail="Unknown dataset_id")

    path = _dataset_registry.pop(dataset_id)
    _builder_cache.pop(path, None)
    if os.path.exists(path):
        os.remove(path)
    return {"deleted": dataset_id}


# ── graph endpoints (all accept an optional dataset_id) ──────────────────────

@app.get("/graph/bounds")
def graph_bounds(dataset_id: Optional[str] = Query(None)):
    builder = get_builder(dataset_id)
    return builder.get_filter_bounds()


@app.get("/graph/build")
def build_graph(
    seed: str = Query(..., description="Account ID to start the graph from"),
    dataset_id: Optional[str] = Query(None),
    min_amount: float = Query(0),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    incremental_threshold: int = Query(30),
    max_hops: int = Query(3),
    max_nodes: int = Query(200),
):
    builder = get_builder(dataset_id)
    return builder.build_incremental_subgraph(
        seed=seed, min_amount=min_amount, date_from=date_from, date_to=date_to,
        incremental_threshold=incremental_threshold, max_hops=max_hops, max_nodes=max_nodes,
    )


@app.get("/graph/expand")
def expand_node(
    node: str = Query(...),
    dataset_id: Optional[str] = Query(None),
    min_amount: float = Query(0),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    builder = get_builder(dataset_id)
    return builder.expand_node(node=node, min_amount=min_amount, date_from=date_from, date_to=date_to)


@app.post("/graph/from-prompt")
def graph_from_prompt(req: PromptGraphRequest):
    builder = get_builder(req.dataset_id)
    return builder.build_incremental_subgraph(
        seed=req.account_id, min_amount=req.min_amount, date_from=req.date_from,
        date_to=req.date_to, incremental_threshold=req.incremental_threshold,
    )


@app.get("/health")
def health():
    return {"status": "ok"}