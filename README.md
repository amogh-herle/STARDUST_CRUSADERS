<div align="center">
  <h1>🌌 STARDUST CRUSADERS</h1>
  <p><strong>Advanced Anti-Money Laundering (AML) Analysis & Graph Analytics Platform</strong></p>

  <!-- Badges -->
  <img src="https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/Next.js-000000?style=for-the-badge&logo=next.js&logoColor=white" alt="Next.js" />
  <img src="https://img.shields.io/badge/React-20232A?style=for-the-badge&logo=react&logoColor=61DAFB" alt="React" />
  <img src="https://img.shields.io/badge/Tailwind_CSS-38B2AC?style=for-the-badge&logo=tailwind-css&logoColor=white" alt="Tailwind" />
  <img src="https://img.shields.io/badge/scikit_learn-F7931E?style=for-the-badge&logo=scikit-learn&logoColor=white" alt="Scikit-Learn" />
</div>

<br />

## 📖 Overview

**Stardust Crusaders** is an end-to-end Machine Learning and Graph Analytics platform designed to combat financial fraud. By automating the ingestion of complex bank statements, the system cleans data, constructs deep relationship networks (money trails), and utilizes advanced anomaly detection to flag suspicious financial activities. 

It empowers financial investigators with a comprehensive dashboard to visualize transaction flows and trace illicit money laundering operations effortlessly.

---

## ✨ Key Features

- 📑 **Automated Ingestion (Phases 5-6):** Ingests raw bank statements (PDFs, CSVs, TXTs) and normalizes them into a unified, structured ledger format.
- 🧹 **Intelligent Data Cleaning (Phase 7):** Deduplicates records, handles missing data, and assesses data quality before analytics.
- 🧠 **Machine Learning Detection:** Uses `Isolation Forest` models to identify outlier transactions and anomalous behavior.
- 🕸️ **Graph Analytics (Phase 8):** Constructs comprehensive directed graphs (using NetworkX) to trace multi-hop money trails and build relationship networks between accounts.
- 📊 **Interactive Dashboard:** A sleek Next.js & TailwindCSS frontend designed for investigators to visualize high-risk accounts and generated ML reports.

---

## 🏗️ Architecture & Repository Structure

The repository is modularly designed, separating the core ML pipelines from the web architecture.

```text
STARDUST_CRUSADERS/
├── aml_model/           # Machine Learning models (Isolation Forest) & training scripts
├── backend/             # FastAPI Backend serving endpoints, dashboards, & analytics
├── figma_frontend/      # Next.js Dashboard (Frontend application UI)
├── frontend/            # Legacy React Web Application
├── phase5/ & phase6/    # Data Ingestion, Behavior Engine, & Normalization pipelines
├── phase7/              # Data Cleaning, Deduplication, & Quality assessment
└── phase8/              # Advanced Graph Analytics, Risk Scoring, & Suspicious Networks
```

---

## 🚀 Getting Started

Follow these steps to get the platform running on your local machine.

### 1. Clone the Repository
```bash
git clone https://github.com/pestechnology/Stadust-Crusaders.git
cd Stadust-Crusaders
```

### 2. Backend Setup (FastAPI)
The backend requires Python and serves the core API endpoints that power the dashboard.

```bash
cd backend
# Create a virtual environment
python -m venv venv

# Activate the virtual environment
# On Windows:
venv\Scripts\activate
# On Mac/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start the FastAPI server
python main.py
```
> **Note:** The API will be available at `http://localhost:8000`. You can view the interactive API docs at `http://localhost:8000/docs`.

### 3. Frontend Setup (Next.js Dashboard)
The primary user interface for financial investigators.

```bash
# Open a new terminal and navigate to the frontend directory
cd figma_frontend/bank-statement-dashboard

# Install Node modules
npm install

# Start the development server
npm run dev
```
> **Note:** The dashboard will be available at `http://localhost:3000`.

---

## ⚠️ Important Guidelines for Contributors

- **Sensitive Data:** Never commit real, unanonymized bank statements to the repository. Use dummy data for testing.
- **Large Files & ML Models:** Machine Learning models (`*.pkl`, `*.h5`) and large dataset exports (`*.csv`, `*.json`, `*.gexf`) should not be pushed to GitHub. The `.gitignore` is configured to prevent this. 
- **Environment Variables:** Never commit `.env` files. Ensure you duplicate `.env.example` to `.env.local` for your specific API keys.

---
<div align="center">
  <i>Built with ❤️ for advanced financial security and analytics.</i>
</div>
