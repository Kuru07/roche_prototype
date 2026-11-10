"""
Compatibility shim forwarding to Universal Ingestion Engine.
"""
from app.ingestion.universal_engine import ingest_source

def run_scrape(source_id: int):
    return ingest_source(source_id)
