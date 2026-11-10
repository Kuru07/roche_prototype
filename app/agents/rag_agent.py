import re
import json
from app.config import GROQ_API_KEY, GROQ_MODEL, TOP_K_RAG, DEMO_LABEL
from app.state import search_faiss
from app.llm_client import get_groq_client, execute_groq_completion

_get_groq_client = get_groq_client

def query_rag(query: str, top_k: int = TOP_K_RAG) -> dict:
    """
    Retrieves in-memory regulatory and methodological documents from FAISS.
    Synthesizes a grounded answer with explicit citations and DEMO / SYNTHETIC flags.
    """
    matches = search_faiss(query, top_k=top_k)
    
    if not matches:
        return {
            "answer": "No relevant regulatory, decree, or methodological documents found in the in-memory index.",
            "citations": [],
            "has_synthetic_citations": False
        }
    
    # Process citations
    citations = []
    has_synthetic = False
    
    context_blocks = []
    for m in matches:
        is_synth = m.get("is_synthetic", False) or m.get("verification_status") == "DEMO_SYNTHETIC"
        if is_synth:
            has_synthetic = True
            display_title = f"{DEMO_LABEL} {m.get('title', 'Document')}"
        else:
            display_title = m.get("title", "Document")
        
        cit = {
            "doc_id": m.get("doc_id"),
            "title": display_title,
            "country": m.get("country", "Global"),
            "authority": m.get("source_authority", "Regulatory Body"),
            "url": m.get("source_url", ""),
            "section": m.get("section", ""),
            "publication_date": m.get("publication_date", "N/A"),
            "verification_status": m.get("verification_status", "UNVERIFIED"),
            "is_synthetic": is_synth,
            "similarity_score": round(m.get("similarity_score", 0.0), 3)
        }
        citations.append(cit)
        
        context_blocks.append(
            f"--- Document: {display_title} | Authority: {cit['authority']} | Country: {cit['country']} ---\n"
            f"{m.get('content', '')}\n"
        )
    
    context_str = "\n".join(context_blocks)
    
    system_prompt = f"""You are a specialized Regulatory and Pricing Methodology Assistant for Roche.
You answer user questions using ONLY the provided regulatory documents.

MANDATORY RULES:
1. If documents are marked with '{DEMO_LABEL}', you must clearly explain that this information comes from DEMO / SYNTHETIC reference data, NOT official verified government decree.
2. Under no circumstance state that an official Roche reference price formula exists unless explicitly stated in verified docs.
3. Be objective, concise, and cite the authority and country for each principle explained.
4. If the context does not answer the question, state that clearly."""

    user_prompt = f"Context Documents:\n{context_str}\n\nUser Question: {query}"
    
    answer = execute_groq_completion(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        model=GROQ_MODEL,
        temperature=0.1
    )
    if answer:
        return {
            "answer": answer,
            "citations": citations,
            "has_synthetic_citations": has_synthetic
        }
    
    # Deterministic fallback synthesis
    top_doc = matches[0]
    lead_summary = top_doc.get("content", "")[:350]
    badge_note = f"⚠️ Note: Responses are synthesized from {DEMO_LABEL}." if has_synthetic else ""
    
    answer = (
        f"**Regulatory Knowledge Summary:**\n\n"
        f"{lead_summary}...\n\n"
        f"{badge_note}\n\n"
        f"Refer to the citation cards below for full authority provenance."
    )
    
    return {
        "answer": answer,
        "citations": citations,
        "has_synthetic_citations": has_synthetic
    }

