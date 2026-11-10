import json
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from app.config import DEMO_LABEL, GROQ_API_KEY, GROQ_MODEL
from app.llm_client import get_groq_client, execute_groq_completion

_get_groq_client = get_groq_client

def format_pricing_answer(query: str, records: list[dict], summary: dict = None) -> str:
    """
    Synthesizes a conversational response for structured price observations.
    Preserves currency, price types, and explicitly tags DEMO / SYNTHETIC data.
    """
    if not records:
        return "No matching price observations were found in the currently ingested sources."
    
    total = len(records)
    has_demo = any(r.get("is_synthetic", False) or r.get("verification_status") == "DEMO_SYNTHETIC" for r in records)
    has_review = any(r.get("needs_review", False) for r in records)
    
    # Large result set (> 15 rows): do not send massive JSON to LLM
    if total > 15:
        demo_str = f" **({summary.get('synthetic_count', 0)} marked as {DEMO_LABEL})**" if has_demo else ""
        review_str = f" ⚠️ *{summary.get('review_needed_count', 0)} record(s) flagged for human review due to ambiguous field mapping.*" if has_review else ""
        
        return (
            f"Retrieved **{total} matching price observations** across {summary.get('countries_represented', 'multiple')} countries.{demo_str}\n\n"
            f"- **Molecules Found:** {', '.join(str(m) for m in summary.get('molecules_found', []))}\n"
            f"- **Currencies:** {', '.join(str(c) for c in summary.get('currencies', []))}\n"
            f"{review_str}\n\n"
            f"The full bounded dataset is rendered directly in the interactive paginated table below with sorting and CSV export."
        )
    
    # Small result set (<= 15 rows): conversational synthesis via Groq or structured template
    system_prompt = f"""You are a pharmaceutical pricing analyst.
Format retrieved pricing observations clearly.
Rules:
1. If records have is_synthetic=True, explicitly state they are {DEMO_LABEL}.
2. Never calculate, infer, or invent missing prices or currency conversions.
3. Preserve reported currency, price type, dosage, and source authority.
4. Emphasize that source-reported prices are NOT Ecuador regulated price caps.
5. If needs_review=True on any record, highlight the mapping note warning."""

    payload = json.dumps(records, default=str, ensure_ascii=False)
    user_msg = f"User Request: {query}\nDatabase Records: {payload}"
    
    answer = execute_groq_completion(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg}
        ],
        model=GROQ_MODEL,
        temperature=0.1
    )
    if answer:
        return answer
    
    # Deterministic fallback response
    lines = [f"Found **{total} matching record(s)**:"]
    if has_demo:
        lines.append(f"> ⚠️ **Notice:** Contains records labeled **{DEMO_LABEL}**.")
    if has_review:
        lines.append("> ⚠️ **Notice:** One or more records have ambiguous column mappings flagged for human review.")
    
    lines.append("\nSummary of key observations:")
    for r in records[:5]:
        mol = r.get("molecule_name") or r.get("brand_name") or "Medicine"
        country = r.get("country", "Unknown")
        price = r.get("price_value") or r.get("price_public") or r.get("price_retail")
        curr = r.get("currency", "")
        ptype = r.get("price_type", "Reported Price")
        auth = r.get("source_authority", "Source")
        synth = f" *{DEMO_LABEL}*" if r.get("is_synthetic") else ""
        lines.append(f"- **{country}** ({mol}): **{price:,.2f} {curr}** [{ptype}] via *{auth}*{synth}")
    
    if total > 5:
        lines.append(f"\n*(Showing 5 of {total} records. View complete details in the table below)*")
    
    return "\n".join(lines)

def build_price_comparison_chart(records: list[dict]):
    """Creates an interactive Plotly comparison chart if multiple countries/prices exist."""
    if not records or len(records) < 2:
        return None
    
    df = pd.DataFrame(records)
    if "price_value" not in df.columns or df["price_value"].dropna().empty:
        return None
    
    clean_df = df.dropna(subset=["price_value"]).copy()
    if clean_df.empty:
        return None
    
    clean_df["Label"] = clean_df.apply(
        lambda r: f"{r.get('country', '')} - {r.get('brand_name') or r.get('molecule_name', '')} ({r.get('currency', '')})",
        axis=1
    )
    clean_df["Status"] = clean_df["is_synthetic"].apply(lambda s: "Demo / Synthetic" if s else "Verified Official")
    
    fig = px.bar(
        clean_df,
        x="Label",
        y="price_value",
        color="Status",
        color_discrete_map={"Demo / Synthetic": "#f59e0b", "Verified Official": "#10b981"},
        title="Reported Price Observations by Country & Product",
        labels={"price_value": "Reported Price (Local Currency)", "Label": "Country / Molecule"},
        hover_data=["country", "molecule_name", "currency", "price_type", "source_authority"]
    )
    fig.update_layout(
        template="plotly_white",
        margin=dict(l=20, r=20, t=40, b=60),
        xaxis_tickangle=-30
    )
    return fig

