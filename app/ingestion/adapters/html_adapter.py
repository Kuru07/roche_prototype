import json
import os
import pandas as pd
from bs4 import BeautifulSoup
from io import StringIO
from app.ingestion.adapters.base_adapter import BaseAdapter
from app.source_policy import safe_request_get, validate_roche_source_url

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-PY,es;q=0.9,en;q=0.8"
}

class HtmlAdapter(BaseAdapter):
    """Universal adapter for HTML tables, price bulletins, and lists."""
    
    def extract(self, source_meta: dict) -> list[dict]:
        url = source_meta.get("url", "")
        if url:
            validate_roche_source_url(url)
        
        parser_config = source_meta.get("parser_config") or {}
        if isinstance(parser_config, str):
            try:
                parser_config = json.loads(parser_config)
            except Exception:
                parser_config = {}
        
        timeout = parser_config.get("timeout", 30)
        raw_content = source_meta.get("raw_bytes") or source_meta.get("raw_html")
        
        if not raw_content:
            fixture_path = parser_config.get("fixture_path")
            if fixture_path and os.path.exists(fixture_path):
                with open(fixture_path, "rb") as f:
                    raw_content = f.read()
            else:
                resp = safe_request_get(url, headers=HEADERS, timeout=timeout)
                resp.raise_for_status()
                raw_content = resp.content
        
        soup = BeautifulSoup(raw_content, "lxml")
        
        # Capture page title if present
        if soup.title and soup.title.string:
            source_meta["page_title"] = soup.title.string.strip()
        
        # Check if a custom table selector was specified
        table_selector = parser_config.get("table_selector")
        if table_selector and soup.select(table_selector):
            html_to_parse = str(soup.select(table_selector)[0])
        else:
            tbl = soup.find("table")
            html_to_parse = str(tbl) if tbl else str(soup)
        
        try:
            dfs = pd.read_html(StringIO(html_to_parse))
        except Exception:
            dfs = []
        
        records = []
        for df in dfs:
            if df.empty or len(df.columns) < 2:
                continue
            
            # Flatten multi-index columns if present
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = ["_".join(str(c).strip() for c in col if str(c) and not str(c).startswith("Unnamed")) for col in df.columns]
            else:
                df.columns = [str(c).strip() for c in df.columns]
            
            dict_rows = df.dropna(how="all").to_dict(orient="records")
            records.extend(dict_rows)
        
        return records
