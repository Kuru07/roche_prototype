import atexit
import json
import os
import re
import threading
import numpy as np
import duckdb
import faiss
from app.config import (
    DATA_DIR,
    EMBEDDING_MODEL_NAME
)
from app.source_policy import (
    validate_roche_source_url,
    is_roche_approved_url,
    ROCHE_APPROVED_URLS,
    COUNTRY_INVENTORY_METADATA,
    UnapprovedSourceError
)

_LOCK = threading.RLock()
_CONN = None
_FAISS_INDEX = None
_DOCUMENT_CHUNKS = []
_EMBEDDER = None
_EMBED_DIM = 384

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sources (
    source_id INTEGER PRIMARY KEY,
    country VARCHAR NOT NULL,
    source_name VARCHAR NOT NULL,
    source_authority VARCHAR NOT NULL,
    url VARCHAR NOT NULL,
    adapter_type VARCHAR NOT NULL,
    verification_status VARCHAR NOT NULL,
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE,
    scrape_interval_hours INTEGER DEFAULT 24,
    parser_config JSON,
    last_scraped_at TIMESTAMP,
    last_status VARCHAR,
    notes VARCHAR
);

CREATE SEQUENCE IF NOT EXISTS source_seq START 1;

CREATE TABLE IF NOT EXISTS price_observations (
    observation_id VARCHAR PRIMARY KEY,
    country VARCHAR NOT NULL,
    source_id INTEGER NOT NULL,
    source_name VARCHAR NOT NULL,
    source_authority VARCHAR NOT NULL,
    source_url VARCHAR NOT NULL,
    verification_status VARCHAR NOT NULL,
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE,
    extraction_confidence DOUBLE NOT NULL,
    needs_review BOOLEAN DEFAULT FALSE,
    mapping_notes VARCHAR,
    molecule_name VARCHAR,
    brand_name VARCHAR,
    active_ingredient VARCHAR,
    strength VARCHAR,
    dosage_form VARCHAR,
    pack_size VARCHAR,
    presentation VARCHAR,
    manufacturer VARCHAR,
    currency VARCHAR NOT NULL,
    price_retail DOUBLE,
    price_consumer DOUBLE,
    price_wholesale DOUBLE,
    price_public DOUBLE,
    price_type VARCHAR,
    price_regime VARCHAR,
    price_value DOUBLE,
    price_per_unit DOUBLE,
    published_at DATE,
    effective_date DATE,
    retrieved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source_row JSON,
    search_text VARCHAR
);

CREATE TABLE IF NOT EXISTS scrape_runs (
    run_id VARCHAR PRIMARY KEY,
    source_id INTEGER,
    country VARCHAR,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    status VARCHAR,
    rows_found INTEGER DEFAULT 0,
    rows_saved INTEGER DEFAULT 0,
    message VARCHAR
);
"""

class ResilientSemanticEmbedder:
    """Fast, deterministic in-memory subword n-gram embedder for FAISS."""
    def __init__(self, dim: int = 384):
        self.dim = dim

    def embed(self, texts: list[str]) -> np.ndarray:
        vecs = []
        for text in texts:
            v = np.zeros(self.dim, dtype=np.float32)
            cleaned = re.sub(r"[^\w\s-]", " ", text.lower())
            words = [w for w in cleaned.split() if len(w) > 1]
            if not words:
                vecs.append(v)
                continue
            unigrams = words
            bigrams = [f"{words[i]}_{words[i+1]}" for i in range(len(words)-1)]
            trigrams = [f"{words[i]}_{words[i+1]}_{words[i+2]}" for i in range(len(words)-2)]
            tokens = unigrams + bigrams + trigrams
            
            for tok in tokens:
                idx = abs(hash(tok)) % self.dim
                v[idx] += 1.0
            
            norm = np.linalg.norm(v)
            if norm > 0:
                v = v / norm
            vecs.append(v)
        return np.array(vecs, dtype=np.float32)

def _get_embedder():
    global _EMBEDDER
    if _EMBEDDER is not None:
        return _EMBEDDER
    
    use_fastembed = os.getenv("USE_FASTEMBED", "false").lower() in ("true", "1")
    if use_fastembed:
        try:
            from fastembed import TextEmbedding
            _EMBEDDER = TextEmbedding(model_name=EMBEDDING_MODEL_NAME)
            return _EMBEDDER
        except Exception:
            pass
    
    _EMBEDDER = ResilientSemanticEmbedder(dim=_EMBED_DIM)
    return _EMBEDDER

def reset_in_memory_faiss():
    """Resets the singleton in-memory FAISS index and chunk store for test isolation."""
    global _FAISS_INDEX, _DOCUMENT_CHUNKS
    with _LOCK:
        _FAISS_INDEX = None
        _DOCUMENT_CHUNKS = []

def reset_in_memory_db():
    """Resets the singleton in-memory DuckDB connection and FAISS stores for test isolation."""
    global _CONN
    with _LOCK:
        if _CONN is not None:
            try:
                _CONN.close()
            except Exception:
                pass
            _CONN = None
        reset_in_memory_faiss()

# Safely close DuckDB connection at process exit to prevent nanobind leak warnings
atexit.register(reset_in_memory_db)

def get_db():
    """Returns the singleton in-memory DuckDB connection."""
    global _CONN
    with _LOCK:
        if _CONN is None:
            _CONN = duckdb.connect(":memory:")
            _CONN.execute(SCHEMA_SQL)
            _initialize_roche_source_registry(_CONN)
        return _CONN

def get_faiss_index():
    """Returns the in-memory FAISS index and chunk store."""
    global _FAISS_INDEX
    with _LOCK:
        if _FAISS_INDEX is None:
            _FAISS_INDEX = faiss.IndexFlatIP(_EMBED_DIM)
        return _FAISS_INDEX, _DOCUMENT_CHUNKS

def embed_texts(texts: list[str]) -> np.ndarray:
    embedder = _get_embedder()
    try:
        embeddings = embedder.embed(texts)
        arr = np.array(embeddings, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1e-9
        return arr / norms
    except Exception:
        fallback = ResilientSemanticEmbedder(_EMBED_DIM)
        return fallback.embed(texts)

def add_documents_to_faiss(docs: list[dict]):
    index, chunks = get_faiss_index()
    if not docs:
        return
    
    texts = [f"{d.get('title', '')} {d.get('section', '')}\n{d.get('content', '')}" for d in docs]
    vectors = embed_texts(texts)
    
    with _LOCK:
        index.add(vectors)
        chunks.extend(docs)

def search_faiss(query: str, top_k: int = 4) -> list[dict]:
    index, chunks = get_faiss_index()
    if not chunks or index.ntotal == 0:
        return []
    
    q_vec = embed_texts([query])
    scores, indices = index.search(q_vec, min(top_k, index.ntotal))
    
    results = []
    for score, idx in zip(scores[0], indices[0]):
        if idx >= 0 and idx < len(chunks):
            chunk_copy = dict(chunks[idx])
            chunk_copy["similarity_score"] = float(score)
            results.append(chunk_copy)
    return results

def _initialize_roche_source_registry(con):
    """
    Seeds the in-memory sources table strictly from the 36 countries in roche_source_inventory.md.
    Enforces that countries with no approved URL are recorded as NO_ROCHE_APPROVED_SOURCE.
    """
    sid = 0
    # Register all countries from authoritative metadata
    for country, meta in COUNTRY_INVENTORY_METADATA.items():
        sid += 1
        status = meta["status"]
        obs = meta["observation"]
        
        # If country has approved URLs, we find them in allowlist
        # If country has NO approved URL, record as NO_ROCHE_APPROVED_SOURCE or EXCLUDED
        if not meta["has_approved_url"]:
            con.execute(
                """
                INSERT INTO sources VALUES (?, ?, ?, ?, '', 'none_gapped', ?, false, 0, '{}', NULL, ?, ?)
                """,
                [
                    sid,
                    country,
                    f"{country} (No Approved Source)",
                    "No Approved Authority",
                    status,
                    status,
                    f"Roche Inventory Observation: {obs}"
                ]
            )
    
    # Pre-register approved pilot source: Paraguay DINAVISA
    con.execute(
        """
        INSERT OR REPLACE INTO sources VALUES (
            101, 'Paraguay', 'Precio máximo REP Fijado por la DINAVISA', 'DINAVISA',
            'https://dinavisa.gov.py/precio-maximo-rep-fijado-por-la-dinavisa/',
            'html_table', 'APPROVED_ROCHE_SOURCE', false, 24,
            '{"table_selector": "table", "default_currency": "PYG", "fixture_path": "data/paraguay_dinavisa_fixture.html"}',
            NULL, 'ready', 'Official Roche-Approved DINAVISA Ingestion Source'
        )
        """
    )

def get_memory_stats() -> dict:
    """Returns live in-memory telemetry including Roche source policy metrics."""
    con = get_db()
    obs_count = con.execute("SELECT COUNT(*) FROM price_observations").fetchone()[0]
    sources_count = con.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    verified_obs = con.execute("SELECT COUNT(*) FROM price_observations WHERE verification_status = 'APPROVED_ROCHE_SOURCE'").fetchone()[0]
    synthetic_obs = con.execute("SELECT COUNT(*) FROM price_observations WHERE is_synthetic = TRUE").fetchone()[0]
    review_needed = con.execute("SELECT COUNT(*) FROM price_observations WHERE needs_review = TRUE").fetchone()[0]
    no_source_count = con.execute("SELECT COUNT(*) FROM sources WHERE verification_status = 'NO_ROCHE_APPROVED_SOURCE'").fetchone()[0]
    runs_count = con.execute("SELECT COUNT(*) FROM scrape_runs").fetchone()[0]
    
    index, chunks = get_faiss_index()
    doc_count = len(chunks)
    
    return {
        "total_observations": obs_count,
        "verified_observations": verified_obs,
        "synthetic_observations": synthetic_obs,
        "needs_review_count": review_needed,
        "configured_sources": sources_count,
        "no_roche_approved_source_count": no_source_count,
        "scrape_runs": runs_count,
        "faiss_chunks_indexed": doc_count,
        "is_pure_in_memory": True
    }
