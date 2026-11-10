"""
Compatibility shim for in-memory DuckDB singleton.
All runtime data is strictly in memory (:memory:).
"""
from app.state import get_db

def get_conn():
    """Returns singleton in-memory DuckDB connection."""
    return get_db()

def schema_summary():
    from app.agents.sql_agent import schema_summary as _ss
    return _ss()
