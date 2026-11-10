@echo off
echo ========================================================
echo  Roche Reference Pricing Intelligence Assistant
echo  100% In-Memory Runtime (DuckDB :memory: + FAISS)
echo ========================================================

if not exist .env (
    echo Creating .env from .env.example...
    copy .env.example .env
)

echo Starting Streamlit Chat Interface...
streamlit run streamlit_app.py
pause
