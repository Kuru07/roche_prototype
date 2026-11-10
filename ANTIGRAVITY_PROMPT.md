You are a senior AI architect and full-stack engineer.

Build a production-minded prototype called
Roche Pricing Intelligence Assistant.

First inspect the existing repository, understand the
current files, and identify what is implemented and missing.
Do not blindly overwrite working code.

The application must be a CHAT-FIRST conversational AI
assistant, not a SQL dashboard.

Users must be able to ask both structured pricing questions
and general knowledge questions about pharmaceutical
pricing, regulations, government databases, PPP and
medicine pricing methodologies.

ARCHITECTURE:

1. Streamlit chat UI.
2. Orchestrator agent for intent detection, context
   management and tool routing.
3. SQL agent using DuckDB in-memory for structured
   medicine pricing.
4. RAG agent using FAISS in-memory and embeddings
   for unstructured documents.
5. Calculation agent with configurable, replaceable
   pricing formula.
6. Document ingestion agent for PDF, HTML and tables.
7. Scraper and APScheduler for country-specific updates.
8. Formatting agent for conversational answers,
   HTML tables, charts and source citations.

All runtime data must be in memory:
DuckDB :memory:, FAISS index, embeddings, source registry,
conversation history, context summaries and cached results.

No SQLite, persistent vector database or disk-backed DB.

Environment variables may hold configuration and API keys.
Explain that restarting clears runtime data.

Use Groq API with configurable model and API key.

Build modular, maintainable Python code with a small
number of logical modules.

Maintain README, requirements.txt and .env.example.

Do not implement everything yet.
First return the repository audit, proposed file structure,
dependencies and implementation plan.
Wait for the next implementation instruction.


PROMPT 2 — Implementation (Phase 1 & Phase 2)

Implement the data acquisition and database layer.

A. COUNTRY/SOURCE MANAGEMENT UI

- Select a country.
- Display its official URLs from the provided Roche
  Google Sheet repository or imported CSV.
- Allow adding, editing, enabling and disabling URLs.
- Store country, source name, URL, source type, data
  format, enabled status, scrape frequency and last
  successful scrape time.
- Support HTML, CSV, XLSX, PDF and JavaScript pages.

B. SCRAPING FRAMEWORK

- Generic scraper with source-specific adapters.
- Requests/BeautifulSoup for static HTML.
- Playwright for JavaScript pages.
- Pandas for CSV/XLSX.
- PyMuPDF/pdfplumber for PDFs.
- Normalize extracted data into common records.
- Log failures, extraction counts, timestamps and
  source provenance.
- Never fabricate missing prices or treat extraction
  failures as zero.

C. DUCKDB SCHEMA

All in memory.

Create tables:
countries, sources, medicines, drug_combinations,
pricing_records, scrape_runs, currency_rates,
ppp_indices, documents and ingestion_metadata.

Pricing records should include:

record_id, country_id, source_id, molecule_name,
molecule_aliases, brand_name, combination_name,
active_ingredients, strength, dosage_form, route,
pack_size, unit_of_measure, manufacturer,
marketing_authorization_holder, price_type, currency,
original_price, normalized_price, price_per_unit,
tax_included, effective_date, publication_date,
retrieval_timestamp, source_url, source_document
and extraction_confidence.

Distinguish:
ex-factory, wholesale, pharmacy/retail,
public and consumer prices.

Never mix different price types without explicit filtering.

D. Preserve historical prices as versioned records.
Detect duplicates using source, medicine, strength,
pack, price type and effective date.

E. Implement manual scrape and scheduler integration.
Add retries, timeouts, rate limiting and respectful crawling.
Show per-source scrape status.

Write unit tests using synthetic fixtures.
Never claim a government source works until actual
extraction has passed.