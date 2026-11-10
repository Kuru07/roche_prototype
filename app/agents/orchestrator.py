import os
import re
from app.config import GROQ_API_KEY, GROQ_MODEL
from app.source_policy import get_country_policy_info, is_roche_approved_url, UnapprovedSourceError
from app.agents.sql_agent import generate_sql, execute_sql_query, get_fuzzy_suggestions, SQLAgentError
from app.agents.rag_agent import query_rag
from app.agents.calculation_framework import execute_pricing_calculation
from app.agents.formatting_agent import format_pricing_answer, build_price_comparison_chart
from app.llm_client import get_groq_client, execute_groq_completion

_get_groq_client = get_groq_client

def detect_country_in_query(query: str) -> str | None:
    """Identifies if a country from the Roche inventory is mentioned in the prompt."""
    from app.source_policy import COUNTRY_INVENTORY_METADATA
    q_lower = query.lower()
    for country in COUNTRY_INVENTORY_METADATA:
        # Match word boundary
        if re.search(rf"\b{re.escape(country.lower())}\b", q_lower):
            return country
    return None

# Known medicine brand names and active molecules in the system
KNOWN_MEDICINES = {
    "treanda", "trastuzumab", "xarelto", "herceptin", "nebivolol", 
    "bendamustina", "rivaroxaban", "tegretol", "symbicort", "carbamazepina", 
    "budesonida", "formoterol", "rituximab", "mabthera", "avastin", 
    "bevacizumab", "perjeta", "pertuzumab", "kadcyla", "alecensa"
}

# Explicit price lookup indicators
PRICE_LOOKUP_PATTERNS = [
    r"\bprice\s+of\b",
    r"\bprices\s+of\b",
    r"\bprice\s+for\b",
    r"\bprices\s+for\b",
    r"\bcost\s+of\b",
    r"\bhow\s+much\s+does\b",
    r"\bshow\s+prices\b",
    r"\bfind\s+prices\b",
    r"\blist\s+prices\b",
    r"\bmaximum\s+(rep\s+)?price\b",
    r"\bprecio\s+m[aá]ximo\b",
    r"\bprecios\s+de\b",
    r"\ball\s+medicines\b",
    r"\bcompare\s+prices\b"
]

GENERAL_QA_PHRASES = [
    "pharmaceutical reference pricing",
    "reference pricing",
    "international reference pricing",
    "external reference pricing",
    "reference price basket",
    "reference basket",
    "pricing basket",
    "internal reference pricing",
    "price referencing",
    "what is irp",
    "what is erp"
]

def get_general_qa_deterministic_fallback(query: str) -> str:
    """
    Transparent, high-accuracy deterministic domain fallback for conceptual reference pricing questions
    when the Groq LLM service is offline or unreachable.
    """
    q = query.lower()
    if "basket" in q:
        return (
            "### 🧺 Reference Price Baskets in International Pharmaceutical Pricing\n\n"
            "A **Reference Price Basket** is a statutory list of comparator countries established by a national health authority "
            "or price-setting body to benchmark domestic pharmaceutical prices.\n\n"
            "**Key Characteristics:**\n"
            "- **Country Selection:** Authorities select basket countries based on economic parity (e.g., GDP per capita, purchasing power), geographic proximity, or regulatory rigor.\n"
            "- **Basket Composition:** Baskets range from small regional groups (e.g., 3–5 neighboring countries) to broad baskets (e.g., 15–25 countries across the European Union or OECD).\n"
            "- **Benchmarking Metric:** Statutory formulas dictate whether the price ceiling is set to the lowest price in the basket, the average of the lowest three countries, or the basket median."
        )
    if "internal" in q and ("external" in q or "difference" in q):
        return (
            "### ⚖️ Internal vs. External Reference Pricing\n\n"
            "**1. External Reference Pricing (ERP / International Reference Pricing - IRP):**\n"
            "- Benchmarks the domestic price of a medicine against prices for the **same medicine across a defined basket of other countries**.\n"
            "- Typically applied to on-patent, innovator, or single-source medicines where no domestic therapeutic competition exists.\n\n"
            "**2. Internal Reference Pricing:**\n"
            "- Benchmarks prices of medicines **within the same domestic market** against therapeutically comparable, identical, or bioequivalent products (e.g., originators vs. generic alternatives).\n"
            "- Establishes a maximum reimbursement level or cluster price group domestically."
        )
    return (
        "### 💊 What is Pharmaceutical Reference Pricing?\n\n"
        "**Pharmaceutical Reference Pricing** (also referred to as **External Reference Pricing (ERP)** or **International Reference Pricing (IRP)**) "
        "is a regulatory price-control mechanism used by health authorities, ministries of health, and public payers to benchmark and set statutory price ceilings "
        "for medicines based on prices reported in other countries.\n\n"
        "#### Core Principles & Mechanisms:\n\n"
        "1. **The Reference Basket:**\n"
        "   - The national regulator establishes a formal basket of comparator countries based on economic criteria, geographic proximity, or healthcare system structure.\n\n"
        "2. **Benchmarking Rule (Calculation Metric):**\n"
        "   - A legally defined formula is applied across the reference basket. Common metrics include:\n"
        "     - **Lowest price** in the basket.\n"
        "     - **Average of the lowest three** prices.\n"
        "     - **Median or average** across the entire comparator basket.\n\n"
        "3. **Statutory Price Ceiling:**\n"
        "   - The calculated benchmark establishes the maximum allowable price at a defined commercialization level (e.g., Ex-Factory Price, Wholesale Price, or Maximum Retail Price / PVP).\n\n"
        "4. **External vs. Internal Benchmarking:**\n"
        "   - **External (IRP):** Cross-border comparison of the *same medicine* in *other countries*.\n"
        "   - **Internal:** Domestic comparison of *therapeutically equivalent or substitutable drugs* within the *same national market*.\n\n"
        "This regulatory mechanism aims to ensure public affordability and healthcare budget sustainability while establishing transparent price ceilings for innovator therapies."
    )

def classify_intent(query: str, history: list = None) -> str:
    """
    Classifies user intent into one of the required categories:
    1. GREETING — Hello, Hi, Hey.
    2. HELP — Help, capabilities, how to use.
    3. GENERAL_QA — Conceptual questions and explanations.
    4. SQL_QUERY — Actual medicine price lookups and structured price comparisons.
    5. RAG_QUERY — Regulatory documents, pricing regulations and methodology explanations.
    6. CALCULATION — Pricing calculations (strictly pending Roche methodology).
    Plus:
    - HYBRID_QUERY (combined SQL pricing + regulatory decree)
    - FORMATTING_FOLLOWUP (context-based reformatting)
    """
    q = query.lower().strip()
    
    # Check formatting follow-up first
    format_triggers = ["as a table", "make it a table", "show as table", "summarize this", "sort this", "rank these", "chart this", "plot this"]
    if history and any(t in q for t in format_triggers):
        return "FORMATTING_FOLLOWUP"
    
    cleaned_q = re.sub(r"[^\w\s]", "", q).strip()
    words = cleaned_q.split()
    
    # 1. GREETING
    greeting_exact = {"hello", "hi", "hey", "hola", "buenas", "greetings", "good morning", "good afternoon", "good evening"}
    if cleaned_q in greeting_exact or (words and words[0] in {"hello", "hi", "hey", "hola", "greetings"} and len(words) <= 3):
        return "GREETING"
    
    # 2. HELP
    help_exact = {"help", "guide", "usage", "commands", "how to use", "what can you do", "capabilities", "instructions"}
    if cleaned_q in help_exact or (words and words[0] == "help" and len(words) <= 3) or cleaned_q in {"how to use this", "how does this work", "what can i ask"}:
        return "HELP"
    
    # 6. CALCULATION
    calc_triggers = ["calculate", "compute", "reference price cap", "formula", "irp calculation", "price ceiling calculation", "apply formula"]
    if any(t in q for t in calc_triggers):
        return "CALCULATION"

    has_medicine = any(re.search(rf"\b{re.escape(med)}\b", q) for med in KNOWN_MEDICINES)
    has_price_lookup = any(re.search(pat, q) for pat in PRICE_LOOKUP_PATTERNS)
    
    regulatory_words = ["decree", "decreto", "regulation", "reglamento", "law", "ley", "normativa", "resolución", "sismed", "pvp", "pvl", "iva", "who guideline", "purchasing power parity", "ppp", "framework"]
    has_regulatory = any(w in q for w in regulatory_words)

    # Hybrid Query Check (SQL data + RAG regulations)
    if (has_medicine or has_price_lookup) and has_regulatory and ("decree" in q or "regulation" in q or "law" in q):
        return "HYBRID_QUERY"

    # 5. RAG_QUERY (Regulatory documents, pricing regulations, legal methodology)
    if has_regulatory and not has_medicine:
        return "RAG_QUERY"

    # 3. GENERAL_QA (Conceptual questions, explanations, definitions)
    has_concept = any(p in q for p in GENERAL_QA_PHRASES)
    is_question = any(q.startswith(starter) for starter in ["what is ", "what are ", "explain ", "how does ", "how do ", "why do ", "define ", "tell me about "])
    
    if has_concept or (is_question and "pricing" in q and not has_medicine and not has_price_lookup):
        return "GENERAL_QA"

    # 4. SQL_QUERY (Actual medicine lookups, database records)
    return "SQL_QUERY"

def handle_user_query(query: str, history: list = None, last_result: dict = None) -> dict:
    """
    Main orchestration router. Enforces strict Roche source policy:
    If a country has no approved source in roche_source_inventory.md, reports NO_ROCHE_APPROVED_SOURCE.
    """
    country_mentioned = detect_country_in_query(query)
    policy_banner = ""
    
    if country_mentioned:
        c_info = get_country_policy_info(country_mentioned)
        if c_info["status"] == "NO_ROCHE_APPROVED_SOURCE":
            policy_banner = (
                f"> ⛔ **STRICT ROCHE SOURCE POLICY:** `{country_mentioned}` has status **`NO_ROCHE_APPROVED_SOURCE`**.\n"
                f"> In the Roche Source Inventory (`roche_source_inventory.md`), no approved source or URL was provided "
                f"> *(Observation: \"{c_info.get('observation', 'Sin fuente disponible')}\")*.\n"
                f"> Per strict mandate, **no external, suggested, or substituted sources (such as web search or government portals) are permitted.**\n\n"
            )
        elif c_info["status"] == "EXCLUDED_BY_TEAM":
            policy_banner = (
                f"> ⚠️ **ROCHE POLICY EXCLUSION:** `{country_mentioned}` is **`EXCLUDED_BY_TEAM`**.\n"
                f"> Official Roche Observation: *\"{c_info.get('observation')}\"*.\n"
                f"> This country is excluded by team decision from reference pricing calculations.\n\n"
            )
        elif c_info["status"] == "PENDING_REVIEW":
            policy_banner = (
                f"> ⚠️ **ROCHE INVENTORY NOTICE:** `{country_mentioned}` source definitions are **`PENDING_REVIEW`**.\n"
                f"> Official Roche Observation: *\"{c_info.get('observation')}\"*.\n\n"
            )

    intent = classify_intent(query, history)
    
    # 1. Conversational Greeting Flow
    if intent in ("GREETING", "GREETING_QUERY"):
        answer = (
            f"{policy_banner}"
            "### 👋 Hello! Welcome to the Roche Reference Pricing Assistant\n\n"
            "I am an analytical assistant designed for international pharmaceutical reference pricing, regulatory intelligence, and strict Roche source policy auditing.\n\n"
            "**Key Capabilities:**\n"
            "- 💊 **Medicine Price Lookups:** Query official extracted prices for approved innovator medicines across reference countries (e.g., *Trastuzumab* in Spain or *Xarelto* in Paraguay).\n"
            "- 🔒 **Strict Roche Source Policy Auditing:** Verify country source status and approved registry endpoints against the Roche inventory. Unapproved sources are strictly blocked.\n"
            "- 📜 **Regulatory Intelligence (RAG):** Explore pricing decrees, PVP/PVL structures, and international reference pricing methodologies.\n"
            "- 📊 **In-Memory Analytics:** Review bounded data tables, visualize comparative charts, and export CSV extracts from volatile RAM (DuckDB `:memory:`).\n\n"
            "*Try asking a medicine pricing question or type **\"Help\"** to view example prompts!*"
        )
        return {
            "intent": "GREETING",
            "flow_title": "Conversational Greeting",
            "answer": answer,
            "data": [],
            "sql": None,
            "citations": [],
            "chart": None,
            "calculation": None
        }

    # 2. Usage Guidance / Help Flow
    if intent in ("HELP", "HELP_QUERY"):
        answer = (
            f"{policy_banner}"
            "### 📖 Roche Reference Pricing Assistant — Usage Guide\n\n"
            "This assistant operates under **strict Roche source governance** and a **100% in-memory runtime** (DuckDB `:memory:` + FAISS).\n\n"
            "**Supported Query Types & Examples:**\n\n"
            "1. **Medicine Pricing Lookups (SQL Engine):**\n"
            "   - *\"Show prices for Trastuzumab in España and Brasil\"*\n"
            "   - *\"What is the maximum REP price for Treanda in Paraguay?\"*\n\n"
            "2. **Source Policy & Country Status:**\n"
            "   - *\"What is the pricing source status for Colombia?\"*\n"
            "   - *\"Show source status for Argentina and Venezuela\"*\n\n"
            "3. **Regulatory Context & Pricing Decrees (RAG Engine):**\n"
            "   - *\"Explain the Spanish PVP IVA pricing structure\"*\n"
            "   - *\"What are DINAVISA's rules for Precio Máximo REP in Paraguay?\"*\n\n"
            "4. **Conceptual Pharmaceutical Pricing (General Q&A):**\n"
            "   - *\"Explain what pharmaceutical reference pricing is in simple terms\"*\n"
            "   - *\"What is an international reference basket?\"*\n\n"
            "5. **Reformatting & Visualizations:**\n"
            "   - *\"Show as a table\"*\n"
            "   - *\"Plot this chart\"*\n\n"
            "**Policy Guardrails:**\n"
            "- **Zero-Hallucination Sources:** Only official Roche-approved URLs are queried or ingested.\n"
            "- **Pending Methodology:** Reference price cap calculations strictly display: `NOT CALCULATED — Official Roche methodology pending.`"
        )
        return {
            "intent": "HELP",
            "flow_title": "Usage Guidance & Assistant Help",
            "answer": answer,
            "data": [],
            "sql": None,
            "citations": [],
            "chart": None,
            "calculation": None
        }

    # 3. General Q&A Flow (Conceptual & Educational)
    if intent == "GENERAL_QA":
        groq_messages = [
            {
                "role": "system",
                "content": (
                    "You are an analytical assistant for the Roche International Reference Pricing System. "
                    "Provide clear, accurate, and concise explanations of pharmaceutical pricing concepts, "
                    "international reference pricing (IRP), external reference pricing (ERP), reference price baskets, "
                    "and price benchmarking. Keep explanations simple, professional, and accessible. "
                    "Do not fabricate country-specific regulatory decrees, official formulas, or price data."
                )
            },
            {
                "role": "user",
                "content": query
            }
        ]
        
        llm_response = execute_groq_completion(groq_messages)
        
        if llm_response:
            answer = (
                f"{policy_banner}"
                f"{llm_response}\n\n"
                f"> ℹ️ *Response generated via Groq LLM (`{GROQ_MODEL}`) for conceptual guidance.*"
            )
            flow_title = "General Q&A (Groq LLM Assistant)"
        else:
            fallback_text = get_general_qa_deterministic_fallback(query)
            answer = (
                f"{policy_banner}"
                f"> ℹ️ **Notice: LLM Service Unavailable (Transparent Fallback)**\n"
                f"> The configured Groq LLM service is currently offline or unreachable. "
                f"The following explanation is provided deterministically by the Roche Pricing Intelligence engine:\n\n"
                f"{fallback_text}"
            )
            flow_title = "General Q&A (Deterministic Domain Fallback)"

        return {
            "intent": "GENERAL_QA",
            "flow_title": flow_title,
            "answer": answer,
            "data": [],
            "sql": None,
            "citations": [],
            "chart": None,
            "calculation": None
        }

    # Flow: Formatting Follow-Up
    if intent == "FORMATTING_FOLLOWUP":
        if last_result and last_result.get("data"):
            rows = last_result["data"]
            answer = policy_banner + format_pricing_answer(query, rows, last_result.get("summary"))
            chart = build_price_comparison_chart(rows) if ("chart" in query.lower() or "plot" in query.lower()) else last_result.get("chart")
            return {
                "intent": "FORMATTING_FOLLOWUP",
                "flow_title": "Formatting Follow-Up (Reusing Session Cache)",
                "answer": answer,
                "data": rows,
                "summary": last_result.get("summary"),
                "sql": last_result.get("sql"),
                "citations": last_result.get("citations", []),
                "chart": chart,
                "calculation": None
            }
        else:
            return {
                "intent": "FORMATTING_FOLLOWUP",
                "flow_title": "Formatting Follow-Up",
                "answer": "No prior data in memory to reformat. Please ask a pricing or regulatory question first.",
                "data": [],
                "chart": None,
                "calculation": None
            }
    
    # 6. Calculation Query
    if intent in ("CALCULATION", "CALCULATION_QUERY"):
        sql = generate_sql(query)
        sql_res = execute_sql_query(sql)
        calc_res = execute_pricing_calculation(query, sql_res["records"])
        
        answer = (
            f"{policy_banner}"
            f"### 🧮 {calc_res.message}\n\n"
            f"{calc_res.details}\n\n"
            f"**Audit & Status Details:**\n"
            f"- **Formula Status:** `{calc_res.formula_name}`\n"
            f"- **Assumed Formula Applied:** `{calc_res.assumed_formula_applied}`\n"
            f"- **Candidate Basket Records Retrieved:** `{len(sql_res['records'])}`\n"
        )
        
        chart = build_price_comparison_chart(sql_res["records"])
        return {
            "intent": "CALCULATION",
            "flow_title": "Calculation Agent (Formula Framework)",
            "answer": answer,
            "data": sql_res["records"],
            "summary": sql_res.get("summary"),
            "sql": sql,
            "citations": [],
            "chart": chart,
            "calculation": calc_res
        }
    
    # RAG Knowledge Query
    if intent == "RAG_QUERY":
        rag_res = query_rag(query)
        return {
            "intent": "RAG_QUERY",
            "flow_title": "RAG Agent (FAISS In-Memory Regulatory Knowledge)",
            "answer": policy_banner + rag_res["answer"],
            "data": [],
            "sql": None,
            "citations": rag_res["citations"],
            "chart": None,
            "calculation": None
        }
    
    # Hybrid Query (SQL Data + RAG Regulations)
    if intent == "HYBRID_QUERY":
        sql = generate_sql(query)
        sql_res = execute_sql_query(sql)
        rag_res = query_rag(query)
        
        data_summary = format_pricing_answer(query, sql_res["records"], sql_res.get("summary"))
        combined_answer = (
            f"{policy_banner}"
            f"### 📊 Structured Pricing Findings\n{data_summary}\n\n"
            f"### 📜 Regulatory & Methodological Context\n{rag_res['answer']}"
        )
        
        chart = build_price_comparison_chart(sql_res["records"])
        return {
            "intent": "HYBRID_QUERY",
            "flow_title": "Hybrid Agent (DuckDB :memory: + FAISS Vector Store)",
            "answer": combined_answer,
            "data": sql_res["records"],
            "summary": sql_res.get("summary"),
            "sql": sql,
            "citations": rag_res["citations"],
            "chart": chart,
            "calculation": None
        }
    
    # Default: SQL Structured Query
    try:
        sql = generate_sql(query)
        sql_res = execute_sql_query(sql)
        answer = policy_banner + format_pricing_answer(query, sql_res["records"], sql_res.get("summary"))
        
        suggestions = []
        if not sql_res["records"]:
            last_token = query.split()[-1] if query.split() else ""
            suggestions = get_fuzzy_suggestions(last_token)
        
        chart = build_price_comparison_chart(sql_res["records"])
        return {
            "intent": "SQL_QUERY",
            "flow_title": "SQL Agent (DuckDB :memory: Structured Lookup)",
            "answer": answer,
            "data": sql_res["records"],
            "summary": sql_res.get("summary"),
            "sql": sql,
            "citations": [],
            "suggestions": suggestions,
            "chart": chart,
            "calculation": None
        }
    except SQLAgentError as err:
        return {
            "intent": "SQL_QUERY",
            "flow_title": "SQL Safety Guard",
            "answer": f"⚠️ **Query Safety Check:** {err}",
            "data": [],
            "chart": None,
            "calculation": None
        }
    except Exception as exc:
        return {
            "intent": "SQL_QUERY",
            "flow_title": "Execution Error",
            "answer": f"Unable to complete query: {exc}",
            "data": [],
            "chart": None,
            "calculation": None
        }
