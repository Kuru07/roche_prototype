import io
import json
import pandas as pd
from app.ingestion.adapters.base_adapter import BaseAdapter
from app.source_policy import safe_request_get

HEADERS = {
    "User-Agent": "Roche-ReferencePricingIntelligence/1.0"
}

class SpreadsheetAdapter(BaseAdapter):
    """Universal adapter for CSV, TSV, XLS, and XLSX price matrices."""
    
    def extract(self, source_meta: dict) -> list[dict]:
        url = source_meta.get("url", "")
        raw_bytes = source_meta.get("raw_bytes")
        parser_config = source_meta.get("parser_config") or {}
        if isinstance(parser_config, str):
            try:
                parser_config = json.loads(parser_config)
            except Exception:
                parser_config = {}
        
        if not raw_bytes:
            resp = safe_request_get(url, headers=HEADERS, timeout=35)
            resp.raise_for_status()
            raw_bytes = resp.content
        
        buffer = io.BytesIO(raw_bytes)
        format_hint = parser_config.get("format", "").lower()
        sheet = parser_config.get("sheet_name", 0)
        header = parser_config.get("header", 0)
        nrows = parser_config.get("nrows", None)
        
        if format_hint in ("xlsx", "xls") or url.lower().endswith((".xlsx", ".xls")):
            df = pd.read_excel(buffer, sheet_name=sheet, header=header, nrows=nrows)
        else:
            sep = parser_config.get("separator", ",")
            try:
                df = pd.read_csv(buffer, sep=sep, header=header, nrows=nrows)
            except Exception:
                buffer.seek(0)
                df = pd.read_csv(buffer, sep=";", header=header, nrows=nrows)
        
        if df.empty:
            return []
        
        df.columns = [str(c).strip() for c in df.columns]
        return df.dropna(how="all").to_dict(orient="records")

