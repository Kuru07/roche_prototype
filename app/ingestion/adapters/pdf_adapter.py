import io
import re
import json
from pypdf import PdfReader
from app.ingestion.adapters.base_adapter import BaseAdapter
from app.source_policy import safe_request_get

HEADERS = {
    "User-Agent": "Roche-ReferencePricingIntelligence/1.0"
}

class PdfAdapter(BaseAdapter):
    """Universal adapter for PDF official price lists and decrees."""
    
    def extract(self, source_meta: dict) -> list[dict]:
        url = source_meta.get("url", "")
        raw_bytes = source_meta.get("raw_bytes")
        
        if not raw_bytes:
            resp = safe_request_get(url, headers=HEADERS, timeout=40)
            resp.raise_for_status()
            raw_bytes = resp.content
        
        reader = PdfReader(io.BytesIO(raw_bytes))
        records = []
        
        for page_idx, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            
            # Identify candidate tabular rows (space-separated or tab-separated columns)
            for line in lines:
                parts = re.split(r"\s{2,}|\t|\|", line)
                if len(parts) >= 3:
                    records.append({
                        f"col_{i}": part.strip() for i, part in enumerate(parts)
                    })
        
        return records

    def extract_text_chunks(self, source_meta: dict, raw_bytes: bytes = None) -> list[dict]:
        """Extracts text sections for in-memory FAISS indexing."""
        url = source_meta.get("url", "")
        if not raw_bytes:
            resp = safe_request_get(url, headers=HEADERS, timeout=40)
            resp.raise_for_status()
            raw_bytes = resp.content
        
        reader = PdfReader(io.BytesIO(raw_bytes))
        chunks = []
        
        for idx, page in enumerate(reader.pages):
            text = (page.extract_text() or "").strip()
            if len(text) > 80:
                chunks.append({
                    "doc_id": f"pdf_{source_meta.get('source_id', 'custom')}_p{idx+1}",
                    "title": source_meta.get("source_name", "Uploaded Regulatory Document"),
                    "country": source_meta.get("country", "Unknown"),
                    "source_authority": source_meta.get("source_authority", "External Body"),
                    "source_url": url,
                    "verification_status": source_meta.get("verification_status", "UNVERIFIED"),
                    "is_synthetic": bool(source_meta.get("is_synthetic", False)),
                    "publication_date": source_meta.get("publication_date", "2024-01-01"),
                    "section": f"Page {idx+1}",
                    "content": text
                })
        return chunks

