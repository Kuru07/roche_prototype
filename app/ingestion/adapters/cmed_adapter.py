import io
import json
from bs4 import BeautifulSoup
import pandas as pd
from app.ingestion.adapters.base_adapter import BaseAdapter
from app.source_policy import safe_request_get, validate_roche_source_url, is_roche_approved_url, UnapprovedSourceError

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7"
}

class CmedAdapter(BaseAdapter):
    """
    Controlled extraction adapter for Brazil ANVISA CMED price publications.
    Inspects the approved CMED portal landing page to discover downloadable price lists.
    Enforces strict Roche source policy:
    - Landing page is inspected for metadata only.
    - Discovered links are stamped PENDING_ROCHE_APPROVAL and blocked from automated retrieval.
    - Extracts structured drug pricing records from provided bytes while preserving raw columns and unconfirmed semantics.
    """
    
    def inspect_approved_page(self, url: str) -> dict:
        """
        Inspects the approved Roche landing page for ANVISA CMED.
        Identifies downloadable files, tables, or endpoints without following unapproved URLs.
        Treats all discovered links strictly as metadata requiring Roche approval before retrieval.
        """
        validate_roche_source_url(url)
        resp = safe_request_get(url, headers=HEADERS, timeout=25)
        soup = BeautifulSoup(resp.content, "lxml")
        
        tables = soup.find_all("table")
        links = soup.find_all("a", href=True)
        
        discovered_files = []
        for a in links:
            href = a["href"]
            text = a.get_text(strip=True)
            if any(ext in href.lower() for ext in [".xls", ".xlsx", ".csv", ".pdf", "@@download/file"]):
                full_url = href if href.startswith("http") else f"https://www.gov.br{href}"
                is_approved = is_roche_approved_url(full_url)
                discovered_files.append({
                    "title": text,
                    "url": full_url,
                    "is_on_roche_allowlist": is_approved,
                    "approval_status": "APPROVED_ROCHE_SOURCE" if is_approved else "PENDING_ROCHE_APPROVAL",
                    "policy_note": "Approved in Roche Inventory" if is_approved else "Discovered download endpoint on approved landing page; requires explicit Roche authorization before retrieval."
                })
        
        return {
            "status_code": resp.status_code,
            "page_title": soup.title.string.strip() if soup.title else "",
            "html_tables_count": len(tables),
            "discovered_files": discovered_files
        }
    
    def extract(self, source_meta: dict) -> list[dict]:
        url = source_meta.get("url", "")
        validate_roche_source_url(url)
        
        parser_config = source_meta.get("parser_config") or {}
        if isinstance(parser_config, str):
            try:
                parser_config = json.loads(parser_config)
            except Exception:
                parser_config = {}
        
        raw_bytes = source_meta.get("raw_bytes")
        sheet_name = parser_config.get("sheet_name", "Lista PMC")
        header = parser_config.get("header", 0)
        nrows = parser_config.get("nrows", None)
        
        # If raw_bytes not supplied, inspect landing page and enforce policy block on secondary URLs
        if not raw_bytes:
            inspection = self.inspect_approved_page(url)
            discovered = inspection.get("discovered_files", [])
            pmc_target = next((f["url"] for f in discovered if "lista_pmc" in f["url"].lower()), None)
            
            # Policy enforcement: secondary download URL is not in Roche allowlist
            if pmc_target and not is_roche_approved_url(pmc_target):
                raise UnapprovedSourceError(
                    f"Secondary download URL discovered on approved landing page is not in Roche allowlist: '{pmc_target}'. "
                    f"Status: PENDING_ROCHE_APPROVAL. Automated unapproved scraping without explicit Roche inventory entry is blocked."
                )
            raise ValueError(f"No valid data payload available for approved source URL {url}")
        
        buffer = io.BytesIO(raw_bytes)
        df = pd.read_excel(buffer, sheet_name=sheet_name, header=header, nrows=nrows)
        
        if df.empty:
            return []
        
        df.columns = [str(c).strip() for c in df.columns]
        return df.dropna(how="all").to_dict(orient="records")
