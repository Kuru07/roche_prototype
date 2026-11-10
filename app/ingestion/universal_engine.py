import uuid
import json
from datetime import datetime, timezone
import pandas as pd
from app.state import get_db, add_documents_to_faiss
from app.source_policy import validate_roche_source_url, is_roche_approved_url, get_country_policy_info, UnapprovedSourceError
from app.ingestion.normalizer import map_raw_record
from app.ingestion.adapters.rest_api_adapter import RestApiAdapter
from app.ingestion.adapters.html_adapter import HtmlAdapter
from app.ingestion.adapters.spreadsheet_adapter import SpreadsheetAdapter
from app.ingestion.adapters.pdf_adapter import PdfAdapter
from app.ingestion.adapters.cmed_adapter import CmedAdapter

_ADAPTERS = {
    "rest_api": RestApiAdapter(),
    "html_table": HtmlAdapter(),
    "html": HtmlAdapter(),
    "spreadsheet": SpreadsheetAdapter(),
    "csv": SpreadsheetAdapter(),
    "xlsx": SpreadsheetAdapter(),
    "xls": SpreadsheetAdapter(),
    "pdf": PdfAdapter(),
    "cmed": CmedAdapter(),
    "brazil_cmed": CmedAdapter()
}

_CUSTOM_PLUGINS = {}

def register_custom_plugin(name: str, plugin_instance):
    """Registers a source-specific custom plugin for non-standard endpoints."""
    _CUSTOM_PLUGINS[name] = plugin_instance

def get_adapter(adapter_type: str, url: str = ""):
    """Selects the appropriate protocol adapter."""
    norm_type = (adapter_type or "").lower().strip()
    if norm_type in _CUSTOM_PLUGINS:
        return _CUSTOM_PLUGINS[norm_type]
    if norm_type in _ADAPTERS:
        return _ADAPTERS[norm_type]
    
    # Infer from URL
    u = url.lower()
    if u.endswith((".csv", ".tsv", ".xlsx", ".xls")):
        return _ADAPTERS["spreadsheet"]
    elif u.endswith(".pdf"):
        return _ADAPTERS["pdf"]
    elif u.endswith((".json", ".xml")) or "api" in u:
        return _ADAPTERS["rest_api"]
    return _ADAPTERS["html_table"]

def ingest_source(source_id: int) -> dict:
    """
    Executes universal ingestion for a configured source in DuckDB :memory:.
    STRICT ENFORCEMENT: Only Roche-approved sources are ingested.
    If inaccessible, marks ACCESS_FAILED without substitution.
    If definitions are ambiguous, marks PENDING_REVIEW without guessing.
    """
    con = get_db()
    row = con.execute(
        """
        SELECT source_id, country, source_name, source_authority, url,
               adapter_type, verification_status, is_synthetic, parser_config
        FROM sources WHERE source_id = ?
        """,
        [source_id]
    ).fetchone()
    
    if not row:
        raise ValueError(f"Source ID {source_id} not found in in-memory registry")
    
    source_meta = {
        "source_id": row[0],
        "country": row[1],
        "source_name": row[2],
        "source_authority": row[3],
        "url": row[4],
        "adapter_type": row[5],
        "verification_status": row[6],
        "is_synthetic": bool(row[7]),
        "parser_config": row[8]
    }
    
    # Strict Policy Gate 1: Reject unapproved URLs
    url = source_meta["url"]
    if not is_roche_approved_url(url):
        error_msg = f"Security Policy Rejection: URL '{url}' for country '{source_meta['country']}' is not on the Roche-approved inventory (roche_source_inventory.md). Substitution and unapproved ingestion are strictly blocked."
        con.execute(
            "UPDATE sources SET last_status = 'REJECTED_UNAPPROVED', notes = ? WHERE source_id = ?",
            [error_msg, source_id]
        )
        raise UnapprovedSourceError(error_msg)
    
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    
    con.execute(
        "INSERT INTO scrape_runs VALUES (?, ?, ?, ?, NULL, 'running', 0, 0, 'Universal Ingestion Started')",
        [run_id, source_id, source_meta["country"], started_at]
    )
    
    adapter = get_adapter(source_meta["adapter_type"], source_meta["url"])
    
    try:
        raw_records = adapter.extract(source_meta)
        saved_count = 0
        
        for idx, raw in enumerate(raw_records):
            norm = map_raw_record(raw, source_meta, row_index=idx)
            if not norm:
                continue
            
            raw_json = json.dumps(norm["source_row"], default=str, ensure_ascii=False)
            
            con.execute(
                """
                INSERT OR REPLACE INTO price_observations VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, CURRENT_TIMESTAMP, ?, ?
                )
                """,
                [
                    norm["observation_id"],
                    norm["country"],
                    norm["source_id"],
                    norm["source_name"],
                    norm["source_authority"],
                    norm["source_url"],
                    norm["verification_status"],
                    norm["is_synthetic"],
                    norm["extraction_confidence"],
                    norm["needs_review"],
                    norm["mapping_notes"],
                    norm["molecule_name"],
                    norm["brand_name"],
                    norm["active_ingredient"],
                    norm["strength"],
                    norm["dosage_form"],
                    norm["pack_size"],
                    norm.get("presentation") or norm.get("pack_size"),
                    norm["manufacturer"],
                    norm["currency"],
                    norm["price_retail"],
                    norm["price_consumer"],
                    norm["price_wholesale"],
                    norm["price_public"],
                    norm["price_type"],
                    norm.get("price_regime"),
                    norm["price_value"],
                    norm["price_per_unit"],
                    norm["published_at"],
                    norm["effective_date"],
                    raw_json,
                    norm["search_text"]
                ]
            )
            saved_count += 1
        
        finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
        
        # Check if country has ambiguous definitions requiring PENDING_REVIEW
        country_pol = get_country_policy_info(source_meta["country"])
        final_status = "PENDING_REVIEW" if country_pol.get("status") == "PENDING_REVIEW" else "success"
        
        con.execute(
            "UPDATE sources SET last_scraped_at = ?, last_status = ? WHERE source_id = ?",
            [finished_at, final_status, source_id]
        )
        con.execute(
            "UPDATE scrape_runs SET finished_at = ?, status = ?, rows_found = ?, rows_saved = ?, message = 'Completed' WHERE run_id = ?",
            [finished_at, final_status, len(raw_records), saved_count, run_id]
        )
        return {
            "status": final_status,
            "rows_found": len(raw_records),
            "rows_saved": saved_count,
            "run_id": run_id
        }
    except Exception as exc:
        finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
        # Strict Policy Gate 4: Mark ACCESS_FAILED without substitution
        con.execute(
            "UPDATE sources SET last_scraped_at = ?, last_status = 'ACCESS_FAILED', notes = ? WHERE source_id = ?",
            [finished_at, f"ACCESS_FAILED: {str(exc)[:400]}", source_id]
        )
        con.execute(
            "UPDATE scrape_runs SET finished_at = ?, status = 'ACCESS_FAILED', message = ? WHERE run_id = ?",
            [finished_at, f"ACCESS_FAILED: {str(exc)[:500]}", run_id]
        )
        raise

def import_dynamic_sources_csv(csv_content: str | bytes) -> int:
    """
    Imports dynamic source configurations from CSV / Sheet export into DuckDB :memory:.
    STRICT ENFORCEMENT: Every imported URL must be on the Roche approved allowlist.
    Unapproved URLs are rejected completely.
    """
    con = get_db()
    if isinstance(csv_content, bytes):
        import io
        df = pd.read_csv(io.BytesIO(csv_content))
    else:
        import io
        df = pd.read_csv(io.StringIO(csv_content))
    
    required = {"country"}
    if not required.issubset(set(df.columns)):
        raise ValueError("CSV must contain at least a 'country' column")
    
    imported = 0
    rejected = []
    max_id = con.execute("SELECT COALESCE(MAX(source_id), 0) FROM sources").fetchone()[0]
    
    for _, row in df.iterrows():
        country = str(row["country"]).strip()
        url = str(row.get("url") or "").strip()
        
        # Policy Check: If country has no URL, record as NO_ROCHE_APPROVED_SOURCE
        if not url or url.lower() in ("nan", "none", "-", ""):
            max_id += 1
            con.execute(
                """
                INSERT INTO sources VALUES (?, ?, ?, ?, '', 'none_gapped', 'NO_ROCHE_APPROVED_SOURCE', false, 0, '{}', NULL, 'NO_ROCHE_APPROVED_SOURCE', 'Country in Roche inventory with no approved URL')
                """,
                [max_id, country, f"{country} (No Approved Source)", "No Approved Authority"]
            )
            imported += 1
            continue
        
        # Strict Allowlist Check
        if not is_roche_approved_url(url):
            rejected.append(f"{country} -> '{url}'")
            continue
        
        max_id += 1
        name = str(row.get("source_name") or f"{country} Approved Source").strip()
        auth = str(row.get("source_authority") or f"{country} Approved Authority").strip()
        adapter = str(row.get("adapter_type") or "html_table").strip()
        verif = str(row.get("verification_status") or "APPROVED_ROCHE_SOURCE").strip()
        synth = bool(row.get("is_synthetic", False))
        interval = int(row.get("scrape_interval_hours", 24))
        pconfig = str(row.get("parser_config") or "{}")
        
        con.execute(
            """
            INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'ready', 'Approved Roche Inventory Source')
            """,
            [max_id, country, name, auth, url, adapter, verif, synth, interval, pconfig]
        )
        imported += 1
    
    if rejected:
        raise UnapprovedSourceError(
            f"REJECTED {len(rejected)} UNAPPROVED SOURCE(S): Roche policy strictly blocks unlisted URLs. "
            f"Rejected: {', '.join(rejected)}"
        )
    
    return imported
