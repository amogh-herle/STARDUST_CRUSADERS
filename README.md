# STARDUST CRUSADERS: Anti-Money Laundering (AML) Analysis Platform

An advanced platform for analyzing bank statements, tracking money flow, and detecting suspicious activities using Machine Learning and Graph Analytics.

## 🚀 Project Overview
Stardust Crusaders provides an automated pipeline for ingesting bank statements, cleaning the data, building money trail graphs, and scoring accounts for money laundering risks. It includes a frontend dashboard to visualize the insights and a backend API to serve the analytics.

## 📂 Repository Structure

- `aml_model/`: Machine Learning models (Isolation Forest) and graph-based heuristics for anomaly detection in transactions.
- `backend/`: FastAPI backend serving the dashboard, managing data ingestion, and providing API endpoints.
- `frontend/`: Original React frontend.
- `figma_frontend/bank-statement-dashboard/`: Next.js frontend (dashboard UI) built for visualizing the money trails and risk reports.
- `phase5/` - `phase8/`: Sequential data processing pipelines:
  - Phase 5 & 6: Data ingestion, normalization, and behavioral engine.
  - Phase 7: Data cleaning, deduplication, and quality assessment.
  - Phase 8: Advanced graph analytics, relationship network building, and risk scoring.

## 🛠️ Tech Stack

- **Backend:** FastAPI, Python
- **Frontend:** Next.js, React, Tailwind CSS (in `figma_frontend`)
- **Machine Learning & Graph:** scikit-learn (Isolation Forest), NetworkX
- **Database:** SQLite (local development)

## ⚙️ Setup Instructions

### 1. Backend Setup
```bash
cd backend
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

### 2. Frontend Setup (Next.js Dashboard)
```bash
cd figma_frontend/bank-statement-dashboard
npm install
npm run dev
```

## ⚠️ Notes on Git and Data
- **Do not commit sensitive data**, `.env` files, or large machine learning models (`.pkl`, `.h5`) to this repository.
- Outputs from pipelines (e.g., generated graphs, HTML reports, CSVs) are ignored via `.gitignore` to keep the repository clean.
