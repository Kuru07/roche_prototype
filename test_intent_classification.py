import unittest
from unittest.mock import patch
from app.agents.orchestrator import classify_intent, handle_user_query
from app.state import reset_in_memory_db, get_db

class TestIntentClassification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reset_in_memory_db()

    @classmethod
    def tearDownClass(cls):
        reset_in_memory_db()

    def test_01_greeting_intent(self):
        """1. Verifies GREETING category for conversational greetings."""
        queries = ["Hello", "Hi", "Hey", "Good morning", "Hola", "greetings"]
        for q in queries:
            intent = classify_intent(q)
            self.assertEqual(intent, "GREETING", f"Query '{q}' should classify as GREETING, got {intent}")
        
        # Verify execution does not invoke SQL or search medicines
        res = handle_user_query("Hello")
        self.assertEqual(res["intent"], "GREETING")
        self.assertIsNone(res["sql"], "GREETING must never generate or execute SQL")
        self.assertEqual(res["data"], [], "GREETING must return empty data array")
        self.assertIsNone(res["calculation"])
        self.assertIn("Hello! Welcome to the Roche Reference Pricing Assistant", res["answer"])
        self.assertIn("Key Capabilities", res["answer"])

    def test_02_help_intent(self):
        """2. Verifies HELP category for user guidance, usage instructions and capabilities."""
        queries = ["Help", "how to use this", "what can you do", "capabilities", "guide"]
        for q in queries:
            intent = classify_intent(q)
            self.assertEqual(intent, "HELP", f"Query '{q}' should classify as HELP, got {intent}")

        # Verify execution does not invoke SQL
        res = handle_user_query("Help")
        self.assertEqual(res["intent"], "HELP")
        self.assertIsNone(res["sql"], "HELP must never generate or execute SQL")
        self.assertEqual(res["data"], [])
        self.assertIsNone(res["calculation"])
        self.assertIn("Usage Guide", res["answer"])
        self.assertIn("Supported Query Types & Examples", res["answer"])

    def test_03_general_qa_intent(self):
        """3. Verifies GENERAL_QA category for conceptual and educational questions."""
        queries = [
            "Explain what pharmaceutical reference pricing is in simple terms.",
            "Explain pharmaceutical reference pricing",
            "What is international reference pricing?",
            "What is external reference pricing?",
            "What is a reference price basket?",
            "How does external reference pricing work?",
            "What is the difference between internal and external reference pricing?"
        ]
        for q in queries:
            intent = classify_intent(q)
            self.assertEqual(intent, "GENERAL_QA", f"Query '{q}' should classify as GENERAL_QA, got {intent}")

        # Test transparent fallback when Groq is unavailable
        with patch("app.agents.orchestrator.execute_groq_completion", return_value=None):
            res_fallback = handle_user_query("Explain what pharmaceutical reference pricing is in simple terms.")
            self.assertEqual(res_fallback["intent"], "GENERAL_QA")
            self.assertIsNone(res_fallback["sql"], "General QA must never be forced into SQL")
            self.assertEqual(res_fallback["data"], [], "General QA must return empty data array")
            self.assertEqual(res_fallback["citations"], [], "Must not fabricate citations")
            self.assertIsNone(res_fallback["calculation"])
            self.assertIn("Notice: LLM Service Unavailable (Transparent Fallback)", res_fallback["answer"])
            self.assertIn("Pharmaceutical Reference Pricing", res_fallback["answer"])
            self.assertIn("Reference Basket", res_fallback["answer"])
            self.assertNotIn("No matching price observations were found", res_fallback["answer"])

        # Test Groq response when available
        mock_llm_reply = "Pharmaceutical reference pricing is when a government caps local medicine prices based on prices in other countries."
        with patch("app.agents.orchestrator.execute_groq_completion", return_value=mock_llm_reply):
            res_llm = handle_user_query("Explain what pharmaceutical reference pricing is in simple terms.")
            self.assertEqual(res_llm["intent"], "GENERAL_QA")
            self.assertIsNone(res_llm["sql"])
            self.assertIn(mock_llm_reply, res_llm["answer"])
            self.assertIn("Response generated via Groq LLM", res_llm["answer"])

    def test_04_sql_query_intent(self):
        """4. Verifies SQL_QUERY category for actual medicine lookups and price records."""
        # Must route specific medicine lookups to SQL_QUERY
        self.assertEqual(
            classify_intent("What is the maximum REP price for Treanda in Paraguay?"),
            "SQL_QUERY"
        )
        self.assertEqual(
            classify_intent("Show prices for Trastuzumab in España and Brasil"),
            "SQL_QUERY"
        )
        self.assertEqual(
            classify_intent("Find prices for Xarelto"),
            "SQL_QUERY"
        )

        # Ingest Paraguay to test Treanda lookup
        from app.ingestion.universal_engine import ingest_source
        ingest_source(101)

        res = handle_user_query("What is the maximum REP price for Treanda in Paraguay?")
        self.assertEqual(res["intent"], "SQL_QUERY")
        self.assertIsNotNone(res["sql"], "Medicine pricing queries must generate and execute SQL")
        self.assertTrue(len(res["data"]) > 0, "Must return actual extracted Treanda records from DuckDB")
        # Treanda is present in Paraguay DINAVISA dataset
        has_treanda = any("Treanda" in str(r.get("brand_name", "")) for r in res["data"])
        self.assertTrue(has_treanda, "Should find Treanda in extracted Paraguay observations")

    def test_05_rag_query_intent(self):
        """5. Verifies RAG_QUERY category for regulatory documents, decrees, and pricing structures."""
        from app.state import add_documents_to_faiss
        self.assertEqual(
            classify_intent("Explain the Spanish PVP IVA pricing structure"),
            "RAG_QUERY"
        )
        self.assertEqual(
            classify_intent("Explain Ecuador Pharmaceutical Reference Pricing Framework decree"),
            "RAG_QUERY"
        )
        self.assertEqual(
            classify_intent("What decree governs pricing in España?"),
            "RAG_QUERY"
        )

        # Before documents are added, RAG handles query gracefully without SQL
        res_empty = handle_user_query("Explain the Spanish PVP IVA pricing structure")
        self.assertEqual(res_empty["intent"], "RAG_QUERY")
        self.assertIsNone(res_empty["sql"], "Regulatory RAG queries must not execute SQL")
        self.assertEqual(res_empty["data"], [])
        self.assertIn("No relevant regulatory", res_empty["answer"])

        # Add official document to verify retrieval
        add_documents_to_faiss([{
            "doc_id": "DINAVISA-RES-01",
            "title": "DINAVISA Resolution REP Paraguay",
            "country": "Paraguay",
            "source_authority": "DINAVISA",
            "source_url": "https://dinavisa.gov.py/precio-maximo-rep-fijado-por-la-dinavisa/",
            "section": "Article 1",
            "content": "Establece el precio maximo de referencia para medicamentos esenciales en Paraguay.",
            "publication_date": "2024-01-01",
            "verification_status": "APPROVED_ROCHE_SOURCE",
            "is_synthetic": False
        }])

        res = handle_user_query("What decree governs pricing in Paraguay?")
        self.assertEqual(res["intent"], "RAG_QUERY")
        self.assertIsNone(res["sql"], "Regulatory RAG queries must not execute SQL")
        self.assertTrue(len(res["citations"]) > 0, "RAG query must return citations from FAISS store")
        self.assertEqual(res["citations"][0]["country"], "Paraguay")
        self.assertFalse(res["citations"][0]["is_synthetic"])

    def test_06_calculation_intent(self):
        """6. Verifies CALCULATION category strictly checks approved Roche formulas."""
        self.assertEqual(
            classify_intent("Calculate reference price for Trastuzumab in Ecuador"),
            "CALCULATION"
        )
        self.assertEqual(
            classify_intent("Compute the reference price cap for Xarelto"),
            "CALCULATION"
        )

        res = handle_user_query("Calculate reference price for Trastuzumab in Ecuador")
        self.assertEqual(res["intent"], "CALCULATION")
        self.assertIsNotNone(res["calculation"])
        self.assertIsNone(res["calculation"].reference_price, "Calculations must not be performed without approved formula")
        self.assertFalse(res["calculation"].assumed_formula_applied, "Must not assume or invent unofficial formulas")
        self.assertIn("NOT CALCULATED", res["answer"])
        self.assertIn("Official Roche methodology pending", res["answer"])

if __name__ == "__main__":
    unittest.main()
