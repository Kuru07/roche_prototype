import json
import xml.etree.ElementTree as ET
from app.ingestion.adapters.base_adapter import BaseAdapter
from app.source_policy import safe_request_get

HEADERS = {
    "User-Agent": "Roche-ReferencePricingIntelligence/1.0 (Research & Prototype Ingestion)",
    "Accept": "application/json, application/xml, text/xml, */*"
}

class RestApiAdapter(BaseAdapter):
    """Universal adapter for REST APIs returning JSON or XML."""
    
    def extract(self, source_meta: dict) -> list[dict]:
        url = source_meta.get("url")
        parser_config = source_meta.get("parser_config") or {}
        if isinstance(parser_config, str):
            try:
                parser_config = json.loads(parser_config)
            except Exception:
                parser_config = {}
        
        headers = dict(HEADERS)
        if parser_config.get("headers"):
            headers.update(parser_config["headers"])
        
        params = parser_config.get("params", {})
        timeout = parser_config.get("timeout", 25)
        
        resp = safe_request_get(url, headers=headers, params=params, timeout=timeout)
        resp.raise_for_status()
        
        content_type = resp.headers.get("content-type", "").lower()
        
        # JSON response
        if "json" in content_type or url.lower().endswith(".json"):
            data = resp.json()
            if isinstance(data, list):
                return [d for d in data if isinstance(d, dict)]
            elif isinstance(data, dict):
                # Search for list property (e.g. "results", "data", "records")
                for key in ("data", "results", "records", "items", "rows"):
                    if key in data and isinstance(data[key], list):
                        return [d for d in data[key] if isinstance(d, dict)]
                return [data]
        
        # XML response
        if "xml" in content_type or url.lower().endswith(".xml"):
            root = ET.fromstring(resp.content)
            records = []
            for item in root.iter():
                if len(item) > 1:  # Likely a record node
                    record = {child.tag: child.text.strip() for child in item if child.text}
                    if record:
                        records.append(record)
            return records
        
        return []

