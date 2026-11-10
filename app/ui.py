import streamlit as st
import pandas as pd
from app.config import (
    DEMO_LABEL,
    IN_MEMORY_WARNING,
    PAGINATION_PAGE_SIZE,
    GROQ_MODEL,
    GROQ_API_KEY
)
from app.source_policy import (
    COUNTRY_INVENTORY_METADATA,
    is_roche_approved_url,
    ROCHE_APPROVED_URLS,
    UnapprovedSourceError
)
from app.state import get_memory_stats, get_db, add_documents_to_faiss
from app.agents import handle_user_query, FormulaRegistry
from app.ingestion.universal_engine import ingest_source, import_dynamic_sources_csv
from app.ingestion.adapters.pdf_adapter import PdfAdapter
from app.scheduler import sync_in_memory_jobs


# Initialize in-memory scheduler once per runtime
@st.cache_resource
def init_scheduler():
    try:
        return sync_in_memory_jobs()
    except Exception:
        return 0


def main():
    st.set_page_config(
        page_title="Roche | Pricing Intelligence Assistant",
        page_icon="💊",
        layout="wide",
        initial_sidebar_state="expanded"
    )

    init_scheduler()

    # Custom Styling for Badges & Policy Indicators
    st.markdown("""
    <style>
        .badge-demo {
            background-color: #fef3c7;
            color: #92400e;
            padding: 3px 8px;
            border-radius: 4px;
            font-weight: 600;
            font-size: 0.75rem;
            display: inline-block;
            margin-right: 4px;
        }
        .badge-approved {
            background-color: #d1fae5;
            color: #065f46;
            padding: 3px 8px;
            border-radius: 4px;
            font-weight: 600;
            font-size: 0.75rem;
            display: inline-block;
            margin-right: 4px;
        }
        .badge-nosource {
            background-color: #f1f5f9;
            color: #475569;
            padding: 3px 8px;
            border-radius: 4px;
            font-weight: 600;
            font-size: 0.75rem;
            display: inline-block;
            margin-right: 4px;
        }
        .badge-excluded {
            background-color: #fee2e2;
            color: #991b1b;
            padding: 3px 8px;
            border-radius: 4px;
            font-weight: 600;
            font-size: 0.75rem;
            display: inline-block;
            margin-right: 4px;
        }
    </style>
    """, unsafe_allow_html=True)

    # Application Header
    c_head1, c_head2 = st.columns([3, 1])
    with c_head1:
        st.title("💊 Roche Reference Pricing Intelligence")
        st.caption("Chat-First Conversational Assistant · 100% In-Memory Runtime · Strict Roche-Approved Source Policy")
    with c_head2:
        st.info(f"**Runtime:** 100% In-Memory (Volatile)\n\n**Policy:** Strict Roche Inventory Allowlist ({len(ROCHE_APPROVED_URLS)} Approved URLs)")

    st.warning(IN_MEMORY_WARNING, icon="⚠️")

    # Session State Initialization
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
    if "last_result" not in st.session_state:
        st.session_state.last_result = None

    # Quick Starter Prompt Chips
    st.markdown("**💡 Quick Starters:**")
    chips = [
        "Show prices for Treanda in Paraguay",
        "Find prices for Xarelto in Paraguay",
        "What is the pricing source status for Colombia?",
        "Show source status for Argentina and Venezuela",
        "Explain what pharmaceutical reference pricing is in simple terms"
    ]
    chip_cols = st.columns(len(chips))
    selected_chip = None
    for i, chip in enumerate(chips):
        if chip_cols[i].button(chip, key=f"chip_{i}", width="stretch"):
            selected_chip = chip

    # Chat Container
    chat_container = st.container()

    with chat_container:
        for idx, msg in enumerate(st.session_state.chat_history):
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

                # Show SQL details if available
                if msg.get("sql"):
                    with st.expander("🔍 Executed In-Memory SQL Query", expanded=False):
                        st.code(msg["sql"], language="sql")

                # Show interactive Plotly chart if present
                if msg.get("chart"):
                    st.plotly_chart(msg["chart"], width="stretch", key=f"chart_{idx}")

                # Show paginated data table
                if msg.get("data") and len(msg["data"]) > 0:
                    records = msg["data"]
                    total_records = len(records)

                    # Check badges
                    has_demo = any(r.get("is_synthetic", False) for r in records)
                    badge_html = ""
                    if has_demo:
                        badge_html += f'<span class="badge-demo">{DEMO_LABEL}</span>'
                    badge_html += '<span class="badge-approved">ROCHE APPROVED SOURCE DATA</span>'
                    st.markdown(badge_html, unsafe_allow_html=True)

                    df_all = pd.DataFrame(records)
                    preferred_cols = [
                        "country", "molecule_name", "brand_name", "strength", "dosage_form",
                        "currency", "price_value", "price_type", "source_authority",
                        "verification_status", "is_synthetic", "needs_review", "effective_date"
                    ]
                    existing_cols = [c for c in preferred_cols if c in df_all.columns] + [c for c in df_all.columns if c not in preferred_cols and c not in ("source_row", "search_text")]
                    df_display = df_all[existing_cols]

                    if total_records > PAGINATION_PAGE_SIZE:
                        num_pages = max(1, (total_records + PAGINATION_PAGE_SIZE - 1) // PAGINATION_PAGE_SIZE)
                        page_col1, page_col2 = st.columns([1, 4])
                        page_idx = page_col1.number_input(f"Page (of {num_pages})", min_value=1, max_value=num_pages, value=1, key=f"page_{idx}")
                        start_i = (page_idx - 1) * PAGINATION_PAGE_SIZE
                        end_i = min(start_i + PAGINATION_PAGE_SIZE, total_records)

                        st.dataframe(df_display.iloc[start_i:end_i], width="stretch", hide_index=True)
                        st.caption(f"Showing rows {start_i+1}–{end_i} of {total_records} bounded records.")
                    else:
                        st.dataframe(df_display, width="stretch", hide_index=True)

                    csv_bytes = df_display.to_csv(index=False).encode("utf-8")
                    st.download_button("📥 Download Result CSV", csv_bytes, f"price_observations_{idx}.csv", "text/csv", key=f"dl_{idx}")

                # Show Citation Cards
                if msg.get("citations") and len(msg["citations"]) > 0:
                    with st.expander(f"📚 Regulatory & Methodological Citations ({len(msg['citations'])})", expanded=True):
                        for cit in msg["citations"]:
                            synth_tag = f" · **{DEMO_LABEL}**" if cit.get("is_synthetic") else ""
                            st.markdown(
                                f"**{cit['title']}**{synth_tag}\n\n"
                                f"- **Authority:** {cit.get('authority')} | **Country:** {cit.get('country')} | **Section:** {cit.get('section')}\n"
                                f"- **Status:** `{cit.get('verification_status')}` | **Similarity Score:** `{cit.get('similarity_score')}`\n"
                                f"- **Source URL:** {cit.get('url')}\n"
                            )
                            st.divider()

    # Chat Input & Routing
    user_prompt = st.chat_input("Ask a pricing, regulatory, or source status question...")
    prompt_to_run = selected_chip if selected_chip else user_prompt

    if prompt_to_run:
        st.session_state.chat_history.append({"role": "user", "content": prompt_to_run})
        with st.chat_message("user"):
            st.markdown(prompt_to_run)

        with st.chat_message("assistant"):
            with st.spinner("Analyzing intent and checking Roche source policies..."):
                response_pkg = handle_user_query(
                    prompt_to_run,
                    history=st.session_state.chat_history,
                    last_result=st.session_state.last_result
                )

                st.caption(f"⚡ Flow: {response_pkg['flow_title']}")
                st.markdown(response_pkg["answer"])

                if response_pkg.get("sql"):
                    with st.expander("🔍 Executed In-Memory SQL Query", expanded=False):
                        st.code(response_pkg["sql"], language="sql")

                if response_pkg.get("chart"):
                    st.plotly_chart(response_pkg["chart"], width="stretch")

                if response_pkg.get("data") and len(response_pkg["data"]) > 0:
                    records = response_pkg["data"]
                    df_all = pd.DataFrame(records)
                    preferred_cols = [
                        "country", "molecule_name", "brand_name", "strength", "dosage_form",
                        "currency", "price_value", "price_type", "source_authority",
                        "verification_status", "is_synthetic", "needs_review", "effective_date"
                    ]
                    existing_cols = [c for c in preferred_cols if c in df_all.columns] + [c for c in df_all.columns if c not in preferred_cols and c not in ("source_row", "search_text")]
                    st.dataframe(df_all[existing_cols].head(PAGINATION_PAGE_SIZE), width="stretch", hide_index=True)

                if response_pkg.get("citations") and len(response_pkg["citations"]) > 0:
                    with st.expander(f"📚 Regulatory & Methodological Citations ({len(response_pkg['citations'])})", expanded=True):
                        for cit in response_pkg["citations"]:
                            synth_tag = f" · **{DEMO_LABEL}**" if cit.get("is_synthetic") else ""
                            st.markdown(
                                f"**{cit['title']}**{synth_tag}\n\n"
                                f"- **Authority:** {cit.get('authority')} | **Country:** {cit.get('country')} | **Section:** {cit.get('section')}\n"
                                f"- **Status:** `{cit.get('verification_status')}` | **Similarity Score:** `{cit.get('similarity_score')}`\n"
                                f"- **Source URL:** {cit.get('url')}\n"
                            )

                # Save into conversation context
                st.session_state.last_result = response_pkg
                st.session_state.chat_history.append({
                    "role": "assistant",
                    "content": response_pkg["answer"],
                    "data": response_pkg.get("data"),
                    "sql": response_pkg.get("sql"),
                    "citations": response_pkg.get("citations"),
                    "chart": response_pkg.get("chart")
                })

    # SIDEBAR: Strict Roche Policy & Runtime Controls
    with st.sidebar:
        st.header("🔒 Roche Source Policy Controls")
        stats = get_memory_stats()

        col_m1, col_m2 = st.columns(2)
        col_m1.metric("Total Records", stats["total_observations"])
        col_m2.metric("FAISS Docs", stats["faiss_chunks_indexed"])

        col_m3, col_m4 = st.columns(2)
        col_m3.metric("Approved Sources", stats["configured_sources"])
        col_m4.metric("No Source Listed", stats["no_roche_approved_source_count"])

        st.divider()
        st.subheader("📋 Roche Country Source Registry")
        selected_status = st.selectbox(
            "Filter by Roche Status",
            ["ALL", "APPROVED_ROCHE_SOURCE", "NO_ROCHE_APPROVED_SOURCE", "EXCLUDED_BY_TEAM", "PENDING_REVIEW"]
        )

        reg_rows = []
        for c_name, c_meta in COUNTRY_INVENTORY_METADATA.items():
            if selected_status == "ALL" or c_meta["status"] == selected_status:
                reg_rows.append({
                    "Country": c_name,
                    "Status": c_meta["status"],
                    "Observation": c_meta["observation"][:45] + "..." if len(c_meta["observation"]) > 45 else c_meta["observation"]
                })
        st.dataframe(pd.DataFrame(reg_rows), width="stretch", hide_index=True)

        st.divider()
        st.subheader("🌐 Ingest Roche-Approved Source")
        con = get_db()
        approved_sources_df = con.execute(
            "SELECT source_id, country, source_name, url, verification_status FROM sources WHERE url != '' ORDER BY source_id"
        ).fetchdf()

        if not approved_sources_df.empty:
            src_opts = approved_sources_df["source_id"].tolist()
            sel_sid = st.selectbox(
                "Select approved source",
                src_opts,
                format_func=lambda x: f"[{x}] {approved_sources_df[approved_sources_df['source_id']==x].iloc[0]['country']} · {approved_sources_df[approved_sources_df['source_id']==x].iloc[0]['source_name'][:30]}"
            )
            if st.button("▶ Ingest Selected Source", type="primary", width="stretch"):
                with st.spinner("Validating against Roche allowlist and ingesting into RAM..."):
                    try:
                        res = ingest_source(int(sel_sid))
                        st.success(f"Ingested {res['rows_saved']} records into DuckDB :memory:!")
                        st.rerun()
                    except UnapprovedSourceError as err:
                        st.error(f"⛔ POLICY REJECTION: {err}")
                    except Exception as e:
                        st.error(f"⚠️ ACCESS_FAILED: {e}")

        st.divider()
        st.subheader("📁 Dynamic Source Importer (Strict)")
        st.caption("Upload CSV / Google Sheet. **Every URL must be present in the approved Roche inventory** or it will be rejected.")
        src_file = st.file_uploader("Upload Sources CSV", type=["csv"], key="dyn_sources_csv")
        if src_file and st.button("Validate & Import"):
            try:
                cnt = import_dynamic_sources_csv(src_file.getvalue())
                st.success(f"Successfully registered {cnt} verified Roche sources!")
                st.rerun()
            except UnapprovedSourceError as err:
                st.error(f"⛔ STRICT POLICY REJECTION: {err}")
            except Exception as e:
                st.error(f"Import failed: {e}")

        st.divider()
        st.subheader("🧮 Formula Status")
        curr_calc = FormulaRegistry.get_calculator()
        if curr_calc is None:
            st.error("Status: **PENDING_ROCHE_APPROVAL**")
            st.caption("Calculations strictly display: 'NOT CALCULATED — Official Roche methodology pending.'")
        else:
            st.success(f"Status: **ACTIVE ({curr_calc.formula_name})**")

        st.divider()
        if st.button("🗑️ Clear Chat History", width="stretch"):
            st.session_state.chat_history = []
            st.session_state.last_result = None
            st.rerun()

if __name__ == '__main__':
    main()
