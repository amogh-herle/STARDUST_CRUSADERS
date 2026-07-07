# CIDECODE — Financial Intelligence Platform

> **CIDECODE Bank Statement Analysis System** is a full-stack financial intelligence platform built for law enforcement and financial investigators. It ingests raw bank statements in any format, cleans and standardises the data, runs multi-layer AML (Anti-Money Laundering) analytics, and surfaces fraud networks through an interactive graph-based UI — all powered by a local LLM investigator assistant.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Feature Walkthrough](#feature-walkthrough)
  - [Phase 6 — Multi-Format Ingestion](#phase-6--multi-format-ingestion)
  - [Phase 7 — Data Cleaning Engine](#phase-7--data-cleaning-engine)
  - [Phase 8 — AML Analytics & Pattern Detection](#phase-8--aml-analytics--pattern-detection)
  - [AML Model — Isolation Forest](#aml-model--isolation-forest)
  - [Backend API](#backend-api)
  - [Frontend Dashboard](#frontend-dashboard)
- [Tech Stack](#tech-stack)
- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Backend Setup](#backend-setup)
  - [Frontend Setup](#frontend-setup)
  - [Project Structure](#project-structure)
- [API Reference](#api-reference)
- [Environment Variables](#environment-variables)

---

## Overview

CIDECODE was built for **CIDECODE 2026**, a hackathon focused on financial crime investigation tooling. The platform solves the core challenge investigators face: bank statements arrive in dozens of inconsistent formats, are riddled with OCR errors, duplicates, and missing fields, and manually tracing money flows across accounts is infeasible at scale.

The system automates the full pipeline from raw file upload to prioritised fraud alerts:

```
Raw Statements (PDF/CSV/Excel/Image)
        ↓
    Phase 6 — Ingestion
        ↓
    Phase 7 — Cleaning & Validation
        ↓
    Phase 8 — Pattern Detection & Risk Scoring
        ↓
    PostgreSQL + FastAPI Backend
        ↓
    Next.js Dashboard (Graph View, Money Trail, Reports)
        ↓
    Qwen3-8B Local LLM Investigator Assistant
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Next.js Frontend                     │
│   Upload → Reports → Graph View → Money Trail → Library │
└──────────────────────┬──────────────────────────────────┘
                       │ REST /api/v1/
┌──────────────────────▼──────────────────────────────────┐
│                   FastAPI Backend                       │
│   /upload  /accounts  /transactions  /graph             │
│   /investigations  /dashboard  /rings  /assistant       │
└────────────┬────────────────────────┬───────────────────┘
             │                        │
   ┌──────────▼─────────┐   ┌─────────▼──────────────────┐
   │   PostgreSQL DB    │   │   Phase 6/7/8 Pipeline     │
   │  (async SQLAlchemy)│   │  (subprocess calls on      │
   │  Accounts          │   │   upload, writes analytics │
   │  Transactions      │   │   to data/analytics_v2/)   │
   │  FraudRings        │   └────────────────────────────┘
   │  Investigations    │
   │  RiskScoreHistory  │    ┌───────────────────────────┐
   └────────────────────┘    │   Qwen3-8B (via Ollama)   │
                             │   Local LLM assistant     │
                             └───────────────────────────┘
```

---

## Feature Walkthrough

### Phase 6 — Multi-Format Ingestion

Handles the "last mile" problem of real-world bank statements that arrive in wildly different formats and layouts.

**Supported formats:** CSV, Excel (xlsx/xls), PDF (text-layer), scanned images (PNG/JPG/TIFF via OCR), JSON, TXT, TSV

**Key capabilities:**
- Auto-detects bank format and schema using `schema_detector.py`
- Parallel ingestion via `ProcessPoolExecutor` with memory-aware scheduling — estimates peak RAM per PDF from page count (~2.6 MB/page) to prevent OOM on large statements
- Password-protected PDF handling with candidate password list support
- Normalises all sources to a unified schema with 17 standard columns
- Outputs `ingested_transactions.csv` and `ingestion_report.csv` per run
- Generic name fallback: unnamed accounts get stable `Person N` labels
- Narration-derived pseudo-counterparty nodes for transactions with no formal account number

---

### Phase 7 — Data Cleaning Engine

A five-module pipeline that transforms messy raw data into court-ready, audit-traceable output. **Nothing is silently dropped** — every removal and change is logged.

**Module 1 — Data Standardiser**
- Date parsing with multi-format support and out-of-range detection
- Amount cleaning: handles `₹` symbols, bracket notation, CR/DR suffixes, lakh/crore formatting
- Narration and channel text normalisation
- Account ID whitespace correction

**Module 2 — Duplicate Detector**
- Exact duplicate removal (same account + date + narration + amounts) — first occurrence kept
- Near-duplicate flagging (≥95% narration similarity, same amounts/date)
- UTR reference collision detection
- Multi-file key collision detection (same key across 3+ source files)

**Module 3 — Data Validator**
- Transaction type validation (debit + credit both set = invalid)
- Failed/declined/reversed transaction detection via narration keywords
- Balance continuity checking: `prev_balance + credit - debit ≈ current_balance` per account
  - MINOR mismatches (≤₹5) and MAJOR mismatches (>₹5) flagged separately
  - Accounts with static/untracked balance columns excluded from breach scoring
- Counterparty validation: self-transfer detection, malformed IFSC flagging
- Statistical outlier detection (amount > 3×IQR per account)
- Velocity burst detection (rapid debit succession)
- Narration integrity checks

**Module 4 — Missing Value Handler**
- Smart imputation for narration, amount, time, and UTR fields

**Module 5 — Quality Assessor**
- Per-row quality score and band (A/B/C/D)

**Outputs:**
| File | Contents |
|---|---|
| `cleaned_transactions.csv` | Main output, ready for analytics |
| `removed_data.csv` | Every removed row with reason |
| `flagged_data.csv` | All kept-but-flagged rows with full flag descriptions |
| `near_duplicates_flagged.csv` | Near-dupe candidates for human review |
| `utr_collisions_flagged.csv` | UTR collision candidates |
| `all_actions.csv` | Row-level log of every value change |
| `suspect_accounts.csv` | Accounts with balance integrity issues |
| `cleaning_report.json` | Full machine-readable audit trail |
| `quality_report.json` | Quality assessment summary |
| `cleaning_summary.txt` | Human-readable narrative |

---

### Phase 8 — AML Analytics & Pattern Detection

The core fraud detection engine. Runs over cleaned transactions to identify known money laundering typologies using account-relative thresholds (not hardcoded rupee amounts).

**Pattern Detectors:**

| Pattern | Description |
|---|---|
| **Round Trip** | A sends funds to B; B returns a similar amount within N days — confirmed by same-counterparty evidence |
| **Round Trip Cycle** | Multi-hop cycles: A → B → C → ... → A, bounded DFS over the transaction graph with chronological ordering and amount conservation checks |
| **Layering** | Temporal chain of internal transfers A → B → C → D where each hop retains ≥ threshold ratio of prior amount, detected by DFS with per-hop hour gap limit |
| **Fan-In** | Account receives from many distinct senders within a time window (collector node pattern) |
| **Fan-Out** | Account distributes to many distinct receivers within a time window (distributor/mule pattern) |
| **Smurfing** | Structured similarly sized transfers to multiple destinations within N days, using account-relative upper quantile banding |
| **Odd-Hour Activity** | Real timestamped transactions between 00:00–05:00, only scored on accounts with sufficient timed-transaction coverage |

**Risk Scoring:**

Combines pattern intensity, Phase 7 flags, beneficiary novelty, and graph centrality metrics into a 0–100 investigator priority score:

- **Graph metrics:** Weighted PageRank, betweenness centrality, degree centrality via NetworkX
- **Pattern intensity:** Log-scaled count scores per typology
- **Behavioural flags:** Velocity, high-value outliers, balance breaches (rate-based via √mean)
- **Beneficiary analysis:** New high-value beneficiary detection (z-score based)
- **Risk tiers:** CRITICAL / HIGH / MEDIUM / LOW with fallback tier assignment

**Fraud Ring / Community Detection:**
- Louvain community detection over the account transaction graph
- Community summaries: total members, amount moved, active patterns per cluster

**Outputs:** `risk_scores.csv`, `community_summaries.csv`, `analytics_transactions.csv`, `analytics_report.json`

---

### AML Model — Isolation Forest

Unsupervised ML pipeline for anomaly detection at the transaction level. The report outputs served through the backend and frontend depend on this model — it is a core part of the platform, not a standalone tool.

**Training pipeline (`train.py`):**
1. Data loading and feature engineering (fit — freezes account/global statistics)
2. Optional feature pruning via `prune_features.py`
3. Isolation Forest training with optional hyperparameter tuning
4. Post-processing: entity segmentation (business vs. retail) + suppression rules
5. Saves model with embedded frozen `FeatureEngineer` to prevent inference leakage

**Inference pipeline (`predict.py`):**
- Uses frozen training-time statistics — new accounts cannot skew their own baseline
- Suppression rules silence known-safe patterns: frequent counterparty, salary narration, business normal-range amounts
- Outputs both raw `is_flagged` (ML) and `final_flag` (post-suppression) — investigators work from `final_flag`
- Risk tiers: CRITICAL / HIGH / MEDIUM / LOW per transaction

**Graph analysis (`graph/`):**
- `GraphBuilder`: incremental money-flow graph with slider-driven amount + date filters
  - Expansion stops at low-transaction-count leaf nodes; "click to expand" pattern for the frontend
  - Narration-derived pseudo-counterparty nodes for transactions without formal account numbers
  - Cytoscape.js-compatible JSON output (nodes + edges with full metadata)
- `MoneyFlowGraph`: investigation-focused, flagged-transactions-only view
- `MoneyTrailTracer`: traces fund flows for a specific account ID

---

### Backend API

Built with **FastAPI** and **async SQLAlchemy 2.0** on **PostgreSQL**.

**Route tree (`/api/v1/`):**

| Route | Description |
|---|---|
| `POST /upload/` | Upload bank statements — triggers Phase 6 → 7 → 8 pipeline, bulk-loads results into DB, auto-generates `graph.json` |
| `GET /upload/analytics-status` | Summary of latest Phase 8 analytics run |
| `GET /accounts/` | Paginated account list, sorted by risk score. Filters: bank, is_suspect, min_risk_score, search |
| `GET /accounts/{id}` | Full account detail with risk profile |
| `GET /accounts/{id}/transactions` | Paginated transactions for one account; filters: date range, flagged-only |
| `GET /accounts/{id}/risk-history` | ML scoring run history |
| `GET /accounts/{id}/counterparties` | All accounts this one transacted with |
| `GET /transactions/` | Transaction search with filters |
| `GET /graph/` | Cytoscape.js graph payload for the frontend |
| `GET /investigations/` | Case file management |
| `POST /investigations/` | Create new investigation |
| `GET /dashboard/stats` | Single-call overview stats (accounts, transactions, rings, risk amounts) |
| `GET /rings/` | All detected fraud rings, sorted by amount moved |
| `GET /rings/{id}/members` | Ring member list with roles and amounts |
| `POST /assistant/chat` | Qwen3-8B investigator assistant chat |

**Data models:** Accounts, Transactions, FraudRings, FraudRingMembers, Investigations, RiskScoreHistory, Reports, EvidenceItems — all UUID-keyed, UTC-timestamped.

---

### Frontend Dashboard

Built with **Next.js 16**, **TypeScript**, **Tailwind CSS 4**, and **Cytoscape.js**.

**Views:**

| View | Description |
|---|---|
| **Upload** | Drag-and-drop multi-file upload zone. Accepts CSV, Excel, PDF, PNG/JPG. Shows per-file ingestion feedback, bank detection, and row counts. |
| **Reports** | Post-upload analytics summary — flagged transactions, risk tier breakdown, active patterns, top suspect accounts. |
| **Graph View** | Interactive Cytoscape.js money-flow graph. Nodes are accounts; edges are transactions. Colour-coded by risk tier. Click a node for account dashboard (holder, risk score, net flow). Click an edge for transaction detail (mode, amount, narration, date, flag status). Amount and date range sliders for filtering. |
| **Money Trail** | Deep-dive fund tracing for a specific account ID — traces inflows, outflows, and counterparty chains. Integrates the credit trail panel. |
| **Library** | Saved investigation case files. Open a case to restore the full analysis context (uploaded files, graph state, reports). |

**Auth:** Local PostgreSQL/SQLite database-backed authentication.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 16, React 19, TypeScript, Tailwind CSS 4, Cytoscape.js, D3.js |
| Backend | Python 3.11+, FastAPI, SQLAlchemy 2.0 (async), Pydantic v2, Uvicorn |
| Database | PostgreSQL (asyncpg driver) |
| ML / Analytics | scikit-learn (Isolation Forest), XGBoost, NetworkX, pandas, numpy, scipy |
| LLM Assistant | Qwen3-8B via Ollama (tool-calling mode, local) |
| Ingestion | pdfplumber, pypdf, openpyxl, Tesseract OCR (via pytesseract) |
| Auth | Native Local PostgreSQL / SQLite |
| Containerisation | Docker / Docker Compose |

---

## Getting Started

You can choose to set up and run CIDECODE either using **Docker Compose** (recommended for quick and easy environment setup) or **locally** on your host machine.

---

### Track A — Docker Compose Setup (Recommended)

Running the entire stack with Docker Compose handles database initialization, dependency resolution, and routing automatically.

#### Prerequisites for Docker

- **Docker** (v20.10+) and **Docker Compose** installed on your system.
- **Ollama** installed on your host machine (for local LLM assistant).
  - Pull the required model: `ollama pull qwen3:8b`
  - Ensure Ollama is configured to accept external connections (by default, Docker containers communicate with the host via `host.docker.internal`). On Linux, run Ollama with the environment variable `OLLAMA_HOST=0.0.0.0` set (or update your systemd service config).

#### Step-by-Step Execution

1. **Clone the repository:**
   ```bash
   git clone https://github.com/your-org/STARDUST_CRUSADERS.git
   cd STARDUST_CRUSADERS
   ```

2. **Configure Frontend Environment variables:**
   The frontend communicates with the backend API. Make sure `figma_frontend/bank-statement-dashboard/.env.local` exists. If not, copy it:
   ```bash
   cp figma_frontend/bank-statement-dashboard/.env.local.example figma_frontend/bank-statement-dashboard/.env.local
   ```

3. **Build and start the containers:**
   From the root of the project, run:
   ```bash
   docker compose up --build
   ```

4. **Verify services are running:**
   - **Next.js Frontend:** [http://localhost:3000](http://localhost:3000)
   - **FastAPI Backend:** [http://localhost:8000](http://localhost:8000)
   - **API Swagger Documentation:** [http://localhost:8000/docs](http://localhost:8000/docs)
   - **PostgreSQL Database:** Port `5432` on localhost (user: `cidecode`, password: `cidecode`, database: `cidecode`)

5. **Automatic DB Migration & Seeding:**
   On startup, the backend container automatically checks for the database tables, creates them if missing, and seeds them using the historical Phase 7 & 8 outputs (CSVs from the repository). You do not need to perform manual migrations or import SQL files.

#### Managing the Docker Stack

- **Run in detached mode:** `docker compose up -d`
- **Stop the containers:** `docker compose down`
- **View logs:** `docker compose logs -f` (or target a specific service like `docker compose logs -f backend`)
- **Tear down volumes (reset DB):** `docker compose down -v`

---

### Track B — Local Development Setup

If you prefer to run the application components individually without Docker:

#### Prerequisites for Local

- **Python 3.11+**
- **Node.js 20+** (with npm/yarn)
- **PostgreSQL 15+** (running on port 5432)
- **Tesseract OCR** (required for processing scanned statement files via OCR):
  - Ubuntu/Debian: `sudo apt-get install tesseract-ocr`
  - macOS: `brew install tesseract`
  - Windows: Install from UB Mannheim binaries and add to PATH.
- **Ollama**:
  - Pull the model: `ollama pull qwen3:8b`

#### Backend Setup

1. **Navigate to the root directory and set up virtual environment:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # Windows: venv\Scripts\activate
   ```

2. **Install Python dependencies:**
   Install both global packages and backend-specific packages:
   ```bash
   pip install -r requirements.txt
   pip install -r backend/requirements.txt
   ```

3. **Configure Environment:**
   If using custom PostgreSQL configuration, create a `.env` file inside the `backend` folder:
   ```bash
   # Example backend/.env content:
   DATABASE_URL=postgresql+asyncpg://cidecode:cidecode@localhost:5432/cidecode
   DATABASE_URL_SYNC=postgresql+psycopg2://cidecode:cidecode@localhost:5432/cidecode
   ```
   *Note: If PostgreSQL is not detected on localhost:5432 at startup, the backend automatically falls back to an SQLite database (`backend/cidecode.db`).*

4. **Run the FastAPI server:**
   ```bash
   cd backend
   python main.py
   # Or run via uvicorn:
   # uvicorn main:app --reload --host 0.0.0.0 --port 8000
   ```
   The backend auto-migrates and seeds itself on startup.

#### Frontend Setup

1. **Navigate to Next.js project directory:**
   ```bash
   cd figma_frontend/bank-statement-dashboard
   ```

2. **Install Node.js dependencies:**
   ```bash
   npm install
   ```

3. **Set Environment variables:**
   Ensure `.env.local` is present (copy from `.env.local.example` if needed) and verify the parameters:
   ```env
   NEXT_PUBLIC_API_BASE=http://localhost:8000
   NEXT_PUBLIC_API_URL=http://localhost:8000
   ```

4. **Run Next.js Dev Server:**
   ```bash
   npm run dev
   ```
   The dashboard will be active at [http://localhost:3000](http://localhost:3000) (or `http://localhost:3001` depending on port occupancy).

---


## Project Structure

```
STARDUST_CRUSADERS/
├── backend/                   # FastAPI backend
│   ├── main.py                # App entry point, router registration
│   ├── models.py              # SQLAlchemy ORM models
│   ├── schemas.py             # Pydantic request/response schemas
│   ├── config.py              # Settings (env-driven)
│   ├── database.py            # Async engine + session factory
│   ├── seed.py                # DB seed + analytics sync
│   ├── dependencies.py        # FastAPI dependency injection
│   └── routers/
│       ├── upload.py          # File upload + pipeline trigger
│       ├── accounts.py        # Account management + counterparties
│       ├── transactions.py    # Transaction search
│       ├── graph.py           # Graph payload endpoints
│       ├── investigations.py  # Case file management
│       ├── dashboard.py       # Dashboard stats + fraud rings
│       └── assistant.py       # LLM assistant chat
│
├── phase6/                    # Ingestion pipeline
│   ├── ingest.py              # Main entry point
│   ├── format_parsers.py      # CSV/Excel/PDF/Image/JSON/TXT parsers
│   ├── schema_detector.py     # Bank format auto-detection
│   ├── normalizer.py          # Unified schema normalisation
│   └── ingestion_config.py    # Supported extensions + column mappings
│
├── phase7/                    # Data cleaning engine
│   ├── clean.py               # 5-module pipeline orchestrator
│   ├── deduplicator.py        # Exact + near + UTR deduplication
│   ├── validator.py           # All validation modules
│   ├── missing_handler.py     # Missing value imputation
│   ├── quality_assessor.py    # Per-row quality scoring
│   └── cleaning_config.py     # Output column definitions
│
├── phase8/                    # AML analytics engine
│   ├── analyse.py             # Pipeline orchestrator
│   ├── pattern_detectors.py   # Round-trip, layering, fan-in/out, smurfing, odd-hour
│   ├── risk_scorer.py         # Multi-factor risk scoring + graph metrics
│   ├── community.py           # Louvain community/fraud ring detection
│   ├── graph_builder.py       # NetworkX transaction graph construction
│   ├── relationship_engine.py # Account relationship mapping
│   ├── money_trail.py         # Fund flow tracing
│   ├── aml_inference.py       # Inference integration
│   └── reporting.py           # Analytics report generation
│
│
├── aml_model/                 # Standalone Isolation Forest pipeline
│   ├── train.py               # Training entry point
│   ├── predict.py             # Inference entry point
│   ├── features/              # Feature engineering
│   ├── models/                # IF trainer + post-processing
│   ├── graph/                 # GraphBuilder + money flow graph
│   ├── evaluation/            # Metrics + reports
│   └── outputs/               # Saved models + scored CSVs
│
├── figma_frontend/
│   └── bank-statement-dashboard/   # Next.js frontend
│       └── src/
│           ├── app/           # Next.js app router pages
│           ├── components/    # UI components
│           │   ├── UploadZone.tsx
│           │   ├── ReportView.tsx
│           │   ├── MoneyTrailView.tsx
│           │   ├── LibraryView.tsx
│           │   ├── Sidebar.tsx
│           │   └── Topbar.tsx
│           └── lib/           # API client, auth, constants
│
└── data/
    └── analytics_v2/          # Live analytics outputs (written on upload)
```

---

## API Reference

Full interactive docs available at `http://localhost:8000/docs` when the backend is running.

**Key request/response examples:**

```bash
# Upload bank statements
curl -X POST http://localhost:8000/api/v1/upload/ \
  -F "files=@statement.pdf" -F "files=@other_account.csv"

# Get top suspect accounts
curl "http://localhost:8000/api/v1/accounts/?is_suspect=true&page_size=20"

# Dashboard overview
curl http://localhost:8000/api/v1/dashboard/stats

# Ask the investigator assistant
curl -X POST http://localhost:8000/api/v1/assistant/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "Summarise the risk profile for ACC000042", "account_id": "ACC000042"}'
```

---

## Environment Variables

### Backend (`backend/.env`)

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://cidecode:cidecode@localhost:5432/cidecode` | Async DB connection string |
| `DATABASE_URL_SYNC` | `postgresql+psycopg2://...` | Sync DB connection string (for migrations) |
| `DEBUG` | `True` | Enable hot-reload |
| `CORS_ORIGINS` | `["http://localhost:5173", ...]` | Allowed frontend origins |
| `UPLOAD_DIR` | `uploads` | Directory for uploaded statement files |
| `MAX_UPLOAD_SIZE_MB` | `50` | Per-file upload size cap |
| `QWEN_BASE_URL` | `http://localhost:11434` | Ollama base URL for LLM assistant |
| `QWEN_MODEL_NAME` | `qwen3:8b` | Ollama model name |

### Frontend (`figma_frontend/bank-statement-dashboard/.env`)

| Variable | Description |
|---|---|
| `NEXT_PUBLIC_API_BASE` | Backend API URL |

---

## Team

- **Eshwar**
- **Gagan R**
- **Amogh Herle**
- **Aryan Nangarath**

---

🥉 **3rd Place** — `<CIDECODE/>` Hackathon 2026 · Problem Statement: *Automated Bank Statement Analysis System*

*Team Stardust Crusaders*
