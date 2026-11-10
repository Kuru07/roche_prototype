# Roche Reference Pricing Intelligence — Conversational AI Prototype

A production-minded, **chat-first conversational AI assistant** for pharmaceutical reference pricing intelligence, regulatory compliance, and international price benchmarking.

---

## ⚡ Important Architecture & Prototype Guarantees

1. **100% In-Memory Runtime (Volatile)**:
   - **No persistent disk databases or SQLite**.
   - All runtime data—DuckDB (`:memory:`), in-memory FAISS vector index, document chunks, source registry, conversation context, and APScheduler jobs—reside exclusively in RAM.
   - **Restarting or terminating the process completely purges all runtime data**. Re-ingestion or demo seed loading occurs automatically on startup.

2. **Official Roche Pricing Formula (Decoupled Framework)**:
   - **No assumed formulas**: The prototype strictly avoids assuming Lowest, Median, Average of 3, or unapproved PPP-adjusted formulas as Roche's official methodology.
   - All calculation requests display:
     > **`NOT CALCULATED — Official Roche methodology pending.`**
   - Certified reference pricing calculations will only execute once an approved Roche methodology plugin is registered in `FormulaRegistry`.

3. **Strict Roche-Approved Source Policy & Allowlist**:
   - **Roche approval is the only authority**: Only sources explicitly mentioned in the original Roche source inventory (`roche_source_inventory.md`) are permitted.
   - **Zero unapproved sources**: The system completely rejects unlisted sources. It never adds, suggests, substitutes, discovers, or automatically includes sources from web searches, general knowledge, or other country databases.
   - **Colombia MinSalud rejected**: Any suggested Colombia MinSalud / SISMED URL is strictly blocked because Roche did not provide it.
   - **`NO_ROCHE_APPROVED_SOURCE` status**: Countries with no approved sources in the inventory (Colombia, Uruguay, Cuba, Dominica, St. Vincent, Antigua, Saint Lucia, Guyana, Surinam, Libano, Noruega, Ecuador) are explicitly stamped `NO_ROCHE_APPROVED_SOURCE`. No alternatives are searched.
   - **`ACCESS_FAILED` status**: If an approved source is inaccessible or times out, it is marked `ACCESS_FAILED` with zero substitution.
   - **`PENDING_REVIEW` status**: If a listed source has unclear pricing definitions or extraction requirements (e.g., Bolivia LINAME reference prices, Canada unit of dispensing), it is marked `PENDING_REVIEW` without guessing or substituting formulas.
   - **Verbatim preservation**: Exact Roche-provided URLs, institutions, country names, and observations are preserved verbatim.
   - **Allowlist enforcement**: Ingestion, dynamic CSV imports, and scrapers enforce the allowlist. Unlisted URLs raise `UnapprovedSourceError`.
   - **Automated test suite**: Validated by `test_roche_source_policy.py` (13 unit tests) and `test_integration.py` (9 integration gates).

4. **Explicit DEMO / SYNTHETIC Data Labeling**:
   - All sample pricing records and mock regulatory circulars are permanently tagged at the database level (`is_synthetic = TRUE`, `verification_status = 'DEMO_SYNTHETIC'`) and prominently labeled in the UI (`[DEMO / SYNTHETIC DATA]`).
   - Synthetic data is never represented as actual government gazetted prices or official regulations.

4. **Universal Country-Agnostic Ingestion Engine**:
   - Single ingestion framework supporting reusable protocol adapters:
     - **REST APIs / JSON / XML**
     - **HTML Tables & Structured Lists**
     - **Spreadsheets (CSV, TSV, XLS, XLSX)**
     - **PDF Documents** (tabular extraction and regulatory text chunking)
   - Dynamic source configuration imported from CSV or Google Sheet exports.
   - Full provenance tracking: raw payload JSON, retrieval timestamps, source authority, and confidence scoring. Ambiguous field mappings are flagged with `needs_review = True` for human audit.

5. **Chat-First UI with Direct Large Result Pagination**:
   - Conversational AI interface front-and-center.
   - Bounded queries (`LIMIT 200`). Results exceeding 15 rows are summarized statistically for the LLM to prevent context overflow, while the complete bounded dataset is rendered directly in Streamlit with interactive pagination, column sorting, search filters, and CSV export.

---

## 🏛️ System Architecture

```text
User Prompt (Streamlit Chat UI)
       │
       ▼
Orchestrator Agent (Intent Classification & Context Router)
       ├── SQL_QUERY ───────► SQL Agent (DuckDB :memory: + RapidFuzz Entity Matching)
       ├── RAG_QUERY ───────► RAG Agent (In-Memory FAISS Vector Search + Citation Stamping)
       ├── CALCULATION ─────► Calculation Framework (Pluggable Formula Registry; Pending by default)
       ├── HYBRID_QUERY ────► SQL Agent + RAG Agent Harmonization
       └── FORMATTING ──────► Formatting Agent (Plotly Charts, HTML Tables, CSV Export)
```

---

## 🚀 Quick Start

### 1. Prerequisites
- Python 3.10+ (Tested on Python 3.12)
- Optional: Groq API Key (If omitted, the system falls back to robust deterministic SQL generation and rule-based knowledge synthesis)

### 2. Installation
```bash
# Clone or navigate to the repository
cd roche_pricing_prototype

# Create and activate virtual environment (optional but recommended)
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configuration
Copy `.env.example` to `.env`:
```bash
# Windows:
copy .env.example .env
# Linux/macOS:
cp .env.example .env
```
Optionally configure `GROQ_API_KEY` in `.env`:
```env
GROQ_API_KEY=gsk_your_groq_api_key_here
GROQ_MODEL=llama-3.3-70b-versatile
```

### 4. Run the Test Suite
```bash
# Run strict Roche source policy unit tests (13 tests)
python test_roche_source_policy.py

# Run full integration verification (9 gates)
python test_integration.py
```

### 5. Run the Application
```bash
streamlit run streamlit_app.py
```
Or simply double-click `run.bat` on Windows.

---

## 💬 Example Questions to Try

- **Structured Price Search**:
  - *"Show prices for Trastuzumab across countries"*
  - *"Find retail and public prices for Bevacizumab in Colombia"*
  - *"Compare Herceptin price observations in Brazil and Spain"*
- **Calculation Agent**:
  - *"Calculate the reference price cap for Trastuzumab in Ecuador"*  
    *(Demonstrates the strict `NOT CALCULATED — Official Roche methodology pending.` response)*
- **Regulatory Knowledge (RAG)**:
  - *"Explain the Ecuador Pharmaceutical Reference Pricing Framework decree"*
  - *"What are the principles of Purchasing Power Parity (PPP) in medicine pricing?"*
  - *"What are the WHO guidelines on external reference pricing?"*
- **Hybrid Query**:
  - *"What are Trastuzumab prices in Colombia and what decree governs pricing there?"*
- **Formatting Follow-up**:
  - *"Make it a table"*
  - *"Chart this"*
