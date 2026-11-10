import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Base Paths
BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"

# LLM Configuration
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
GROQ_TIMEOUT = float(os.getenv("GROQ_TIMEOUT", "20.0"))

# Embeddings & Vector Search
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5").strip()
AUTO_LOAD_DEMO_DATA = os.getenv("AUTO_LOAD_DEMO_DATA", "false").lower() in ("true", "1", "yes")

# Safety Caps & Limits
MAX_SQL_ROWS = 200
PAGINATION_PAGE_SIZE = 15
TOP_K_RAG = 4

# Mandatory Disclaimers & Labels
DEMO_LABEL = "[DEMO / SYNTHETIC DATA]"
CALCULATION_PENDING_MSG = "NOT CALCULATED — Official Roche methodology pending."
CALCULATION_PENDING_DETAILS = (
    "No official Roche reference pricing methodology has been configured in this prototype. "
    "To prevent regulatory and commercial discrepancies, assumed formulas (such as lowest, median, "
    "average of 3, or PPP adjustments) are strictly prohibited. Once the approved Roche methodology "
    "is registered, certified calculations will execute here."
)

IN_MEMORY_WARNING = (
    "⚠️ IN-MEMORY VOLATILE RUNTIME: All DuckDB tables, FAISS vector indices, document chunks, "
    "and scheduler states reside exclusively in RAM. Restarting or terminating the application "
    "clears all runtime data completely."
)

SUPPORTED_COUNTRIES = [
    "Colombia", "Ecuador", "Brazil", "Spain", "France",
    "Germany", "Italy", "United Kingdom", "United States",
    "Mexico", "Chile", "Argentina", "Saudi Arabia", "Canada"
]

