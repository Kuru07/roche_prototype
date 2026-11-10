"""
Compatibility shim forwarding to modular agents.
"""
from app.agents.orchestrator import handle_user_query, classify_intent
from app.agents.sql_agent import generate_sql, execute_sql_query, get_fuzzy_suggestions, SQLAgentError, schema_summary
from app.agents.rag_agent import query_rag
from app.agents.formatting_agent import format_pricing_answer, build_price_comparison_chart
from app.agents.calculation_framework import execute_pricing_calculation, FormulaRegistry, BasePricingCalculator

# Legacy aliases
def orchestrate(query, history=None):
    return {"intent": classify_intent(query, history)}

def execute_sql(sql):
    res = execute_sql_query(sql)
    import pandas as pd
    return pd.DataFrame(res["records"])

def format_answer(query, rows):
    return format_pricing_answer(query, rows)

def fuzzy_suggestions(term, limit=8):
    return get_fuzzy_suggestions(term, limit)

class AgentError(SQLAgentError):
    pass
