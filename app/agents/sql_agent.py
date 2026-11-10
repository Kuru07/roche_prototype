import os
import re
import json
from rapidfuzz import process, fuzz
from app.config import GROQ_API_KEY, GROQ_MODEL, MAX_SQL_ROWS
from app.state import get_db
from app.llm_client import get_groq_client, execute_groq_completion

class SQLAgentError(Exception):
    pass

_get_groq_client = get_groq_client

def schema_summary() -> str:
    """Returns columns and types for in-memory price_observations."""
    con = get_db()
    cols = con.execute("DESCRIBE price_observations").fetchall()
    col_str = ", ".join(f"{c[0]} ({c[1]})" for c in cols)
    countries = [r[0] for r in con.execute("SELECT DISTINCT country FROM price_observations").fetchall()]
    return f"Columns in price_observations: {col_str}. Countries in DB: {countries}."

def extract_drug_entity(query: str) -> list[str]:
    """Uses RapidFuzz to identify drug or molecule mentions in query."""
    con = get_db()
    names = con.execute(
        """
        SELECT DISTINCT molecule_name FROM price_observations WHERE molecule_name IS NOT NULL
        UNION
        SELECT DISTINCT brand_name FROM price_observations WHERE brand_name IS NOT NULL
        UNION
        SELECT DISTINCT active_ingredient FROM price_observations WHERE active_ingredient IS NOT NULL
        """
    ).fetchall()
    
    candidates = [r[0] for r in names if r[0]]
    if not candidates:
        return []
    
    # Tokenize user query
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9-]{3,}", query)
    matches = []
    for token in tokens:
        best = process.extractOne(token, candidates, scorer=fuzz.WRatio)
        if best and best[1] >= 75:
            matches.append(best[0])
    return list(dict.fromkeys(matches))

def validate_sql(sql: str) -> str:
    """Enforces safety guardrails: SELECT only, approved table, limit cap."""
    s = sql.strip().rstrip(";")
    if ";" in s or not re.match(r"(?is)^select\b", s):
        raise SQLAgentError("Safety violation: Only a single SELECT query is permitted.")
    
    forbidden = r"(?i)\b(insert|update|delete|drop|alter|create|attach|detach|copy|pragma|install|load|export|import|call)\b"
    if re.search(forbidden, s):
        raise SQLAgentError("Safety violation: Query contains forbidden SQL modification keywords.")
    
    if not re.search(r"(?i)\bfrom\s+price_observations\b", s):
        raise SQLAgentError("Safety violation: Queries must target the price_observations table.")
    
    # Enforce limit
    m = re.search(r"(?i)\blimit\s+(\d+)", s)
    if not m:
        s += f" LIMIT {MAX_SQL_ROWS}"
    else:
        limit_val = int(m.group(1))
        if limit_val > MAX_SQL_ROWS:
            s = s[:m.start(1)] + str(MAX_SQL_ROWS) + s[m.end(1):]
    return s

def generate_sql(query: str) -> str:
    """Generates DuckDB SQL using Groq or deterministic entity fallback."""
    system_prompt = f"""You are a specialized SQL Generator for pharmaceutical reference pricing intelligence.
In-Memory DuckDB Schema:
{schema_summary()}

Rules:
1. Return ONLY a JSON object: {{"sql": "SELECT ...", "explanation": "..."}}
2. Target only price_observations.
3. For medicine name searches, use ILIKE on molecule_name, brand_name, active_ingredient, or search_text.
4. Select useful columns: observation_id, country, source_authority, molecule_name, brand_name, strength, dosage_form, pack_size, currency, price_value, price_public, price_wholesale, price_retail, price_type, price_regime, verification_status, is_synthetic, needs_review, effective_date, source_url.
5. Limit to at most {MAX_SQL_ROWS} rows.
6. Never write DDL, DML, PRAGMA, or multiple statements.
7. For default pricing summaries and reference-price retrieval, STRICTLY EXCLUDE:
   - needs_review = TRUE
   - is_synthetic = TRUE
   - Null price values (price_value IS NOT NULL)
   - Rejected or pending source approvals (verification_status NOT IN ('PENDING_ROCHE_APPROVAL', 'REJECTED_UNAPPROVED', 'PENDING_REVIEW'))
8. Only when the user explicitly requests an audit, review, data quality, or incomplete records check, include records with needs_review IS TRUE, is_synthetic IS TRUE, or verification_status = 'PENDING_REVIEW'."""

    raw = execute_groq_completion(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query}
        ],
        model=GROQ_MODEL,
        temperature=0.0
    )
    if raw:
        try:
            cleaned = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.I).strip()
            sql = json.loads(cleaned)["sql"]
            return validate_sql(sql)
        except Exception:
            pass  # Fallback to deterministic generator
    
    # Deterministic fallback
    is_audit_query = any(k in query.lower() for k in [
        "audit", "review", "pending", "incomplete", "missing", "unverified", 
        "data quality", "synthetic", "all records", "unpriced", "draft", "raw"
    ])
    
    entities = extract_drug_entity(query)
    term = entities[0].replace("'", "''") if entities else None
    
    if not term:
        # Extract general tokens
        tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9-]{3,}", query)
        stop = {"show", "find", "price", "prices", "drug", "medicine", "across", "country", "countries", "compare", "give", "list", "table", "what", "which", "with", "from"}
        candidates = [t for t in tokens if t.lower() not in stop]
        if candidates:
            term = candidates[-1].replace("'", "''")
    
    cols = "observation_id, country, source_authority, molecule_name, brand_name, strength, dosage_form, pack_size, currency, price_value, price_public, price_wholesale, price_retail, price_type, price_regime, verification_status, is_synthetic, needs_review, effective_date, source_url"
    
    # CORRECTION 3: Harden default SQL pricing filters
    if is_audit_query:
        # Explicit audit/data-quality query: allow retrieval of review-needed, pending, or synthetic records
        if any(k in query.lower() for k in ["pending", "incomplete", "review", "unpriced", "missing"]):
            approved_filter = "(needs_review IS TRUE OR verification_status = 'PENDING_REVIEW' OR price_value IS NULL)"
        elif "synthetic" in query.lower() or "demo" in query.lower():
            approved_filter = "is_synthetic IS TRUE"
        else:
            approved_filter = "1=1"
    else:
        # Default pricing query: strictly exclude synthetic records, unpriced rows, and review items
        approved_filter = (
            "verification_status NOT IN ('PENDING_ROCHE_APPROVAL', 'REJECTED_UNAPPROVED', 'PENDING_REVIEW') "
            "AND is_synthetic IS FALSE "
            "AND (needs_review IS FALSE OR needs_review IS NULL) "
            "AND price_value IS NOT NULL"
        )
    
    if term:
        sql = f"SELECT {cols} FROM price_observations WHERE ({approved_filter}) AND (molecule_name ILIKE '%{term}%' OR brand_name ILIKE '%{term}%' OR active_ingredient ILIKE '%{term}%' OR search_text ILIKE '%{term}%') ORDER BY country, price_value ASC LIMIT {MAX_SQL_ROWS}"
    else:
        sql = f"SELECT {cols} FROM price_observations WHERE {approved_filter} ORDER BY retrieved_at DESC LIMIT {MAX_SQL_ROWS}"
    
    return validate_sql(sql)

def execute_sql_query(sql: str) -> dict:
    """Executes safe SQL on DuckDB :memory: and summarizes large datasets."""
    valid_sql = validate_sql(sql)
    con = get_db()
    df = con.execute(valid_sql).fetchdf()
    records = df.to_dict(orient="records")
    
    # Requirement 6: Large results (> 15 rows) should NOT be dumped into the LLM context.
    # LLM receives a statistical summary; Streamlit paginates the full result directly.
    is_large = len(records) > 15
    summary = {}
    if is_large:
        summary = {
            "total_records": len(records),
            "countries_represented": df["country"].nunique() if "country" in df else 0,
            "molecules_found": df["molecule_name"].dropna().unique().tolist()[:5] if "molecule_name" in df else [],
            "currencies": df["currency"].unique().tolist() if "currency" in df else [],
            "synthetic_count": int(df["is_synthetic"].sum()) if "is_synthetic" in df else 0,
            "review_needed_count": int(df["needs_review"].sum()) if "needs_review" in df else 0
        }
    
    return {
        "sql": valid_sql,
        "records": records,
        "count": len(records),
        "is_large": is_large,
        "summary": summary
    }

def get_fuzzy_suggestions(term: str, limit: int = 5) -> list[tuple]:
    """Provides spelling/molecule suggestions when no results match."""
    con = get_db()
    rows = con.execute(
        "SELECT DISTINCT molecule_name FROM price_observations WHERE molecule_name IS NOT NULL UNION SELECT DISTINCT brand_name FROM price_observations WHERE brand_name IS NOT NULL"
    ).fetchall()
    choices = [r[0] for r in rows if r[0]]
    if not choices or not term:
        return []
    return process.extract(term, choices, scorer=fuzz.WRatio, limit=limit)

