import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from app.state import get_memory_stats, get_db, reset_in_memory_db
from app.agents import handle_user_query, FormulaRegistry
from app.ingestion.universal_engine import import_dynamic_sources_csv, ingest_source
from app.ingestion.adapters.html_adapter import HtmlAdapter
from app.source_policy import (
    is_roche_approved_url,
    validate_roche_source_url,
    get_country_policy_info,
    UnapprovedSourceError,
    ROCHE_APPROVED_URLS
)

class TestIntegrationAndPolicyGates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reset_in_memory_db()
    
    def test_01_in_memory_telemetry(self):
        """Gate 1: Checking In-Memory Telemetry."""
        stats = get_memory_stats()
        self.assertTrue(stats["is_pure_in_memory"])
        self.assertEqual(stats["synthetic_observations"], 0)
        self.assertEqual(stats["verified_observations"], 0)
        self.assertEqual(stats["faiss_chunks_indexed"], 0)
        print(f"  PASS: In-memory stores initialized ({stats['total_observations']} observations, {stats['faiss_chunks_indexed']} FAISS docs).")

    def test_02_calculation_guardrail(self):
        """Gate 2: Testing Calculation Guardrail."""
        calc_res = handle_user_query("Calculate reference price for Treanda in Paraguay")
        self.assertIn("NOT CALCULATED — Official Roche methodology pending.", calc_res["answer"])
        self.assertIsNone(calc_res["calculation"].reference_price)
        self.assertFalse(calc_res["calculation"].assumed_formula_applied)
        print("  PASS: Calculation strictly adheres to pending status with no assumed formula.")

    def test_03_sql_agent_and_bounded_result(self):
        """Gate 3: Testing SQL Agent & Bounded Result on Real Ingested Source."""
        ingest_source(101)
        sql_res = handle_user_query("Show prices for Treanda in Paraguay")
        obs_count = len(sql_res["data"])
        self.assertGreater(obs_count, 0)
        self.assertTrue(all(r["is_synthetic"] is False for r in sql_res["data"]))
        self.assertTrue(all(r["country"] == "Paraguay" for r in sql_res["data"]))
        print(f"  PASS: Retrieved {obs_count} real price observations (0 synthetic).")

    def test_04_rag_agent(self):
        """Gate 4: Testing RAG Agent."""
        from app.state import add_documents_to_faiss
        rag_empty = handle_user_query("Explain Ecuador Pharmaceutical Reference Pricing Framework decree")
        self.assertEqual(len(rag_empty["citations"]), 0)
        self.assertIn("No relevant regulatory", rag_empty["answer"])

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
        rag_res = handle_user_query("What decree governs pricing in Paraguay?")
        self.assertGreater(len(rag_res["citations"]), 0)
        self.assertFalse(rag_res["citations"][0]["is_synthetic"])
        print(f"  PASS: RAG retrieved {len(rag_res['citations'])} verified citations (0 synthetic).")

    def test_05_hybrid_agent(self):
        """Gate 5: Testing Hybrid Agent."""
        hyb_res = handle_user_query("What are Treanda prices in Paraguay and what decree governs pricing there?")
        self.assertGreater(len(hyb_res["data"]), 0)
        self.assertGreater(len(hyb_res["citations"]), 0)
        self.assertTrue(all(r["is_synthetic"] is False for r in hyb_res["data"]))
        print(f"  PASS: Hybrid pipeline harmonized {len(hyb_res['data'])} records and {len(hyb_res['citations'])} citations.")

    def test_06_dynamic_source_csv_import(self):
        """Gate 6: Testing Dynamic Source CSV Import."""
        csv_path = Path("data/dynamic_sources_template.csv")
        with open(csv_path, "rb") as f:
            cnt = import_dynamic_sources_csv(f.read())
        self.assertGreater(cnt, 0)
        print(f"  PASS: Dynamic source import registered {cnt} sources.")

    def test_07_no_disk_backed_database(self):
        """Gate 7: Verifying No Disk-Backed Database Exists."""
        self.assertFalse(Path("data/pricing.duckdb").exists())
        self.assertFalse(Path("pricing.duckdb").exists())
        self.assertFalse(Path("data/pricing.sqlite").exists())
        print("  PASS: Pure 100% in-memory runtime verified.")

    def test_08_strict_roche_allowlist_and_rejection(self):
        """Gate 8: Testing Strict Roche Source Allowlist & Rejection."""
        colombia_minsalud = "https://www.minsalud.gov.co/salud/MT/Paginas/medicamentos-regulacion-precios.aspx"
        self.assertFalse(is_roche_approved_url(colombia_minsalud))
        with self.assertRaises(UnapprovedSourceError):
            validate_roche_source_url(colombia_minsalud, country="Colombia")
        
        self.assertFalse(is_roche_approved_url("https://www.fda.gov"))
        self.assertFalse(is_roche_approved_url("https://who.int"))
        
        self.assertTrue(is_roche_approved_url("https://www.gov.br/anvisa/pt-br/assuntos/medicamentos/cmed/precos"))
        self.assertTrue(is_roche_approved_url("https://www.sanidad.gob.es/areas/farmacia/infoIndustria/SPRAH/preciosMenoresAH/home.htm"))
        self.assertTrue(is_roche_approved_url("https://base-donnees-publique.medicaments.gouv.fr/"))
        print(f"  PASS: Strict Roche source allowlist enforced across {len(ROCHE_APPROVED_URLS)} approved URLs.")

    def test_09_policy_statuses(self):
        """Gate 9: Testing Policy Statuses."""
        col_info = get_country_policy_info("Colombia")
        self.assertEqual(col_info["status"], "NO_ROCHE_APPROVED_SOURCE")
        self.assertFalse(col_info["has_approved_url"])

        arg_info = get_country_policy_info("Argentina")
        self.assertEqual(arg_info["status"], "EXCLUDED_BY_TEAM")
        self.assertTrue(arg_info["is_excluded"])

        ven_info = get_country_policy_info("Venezuela")
        self.assertEqual(ven_info["status"], "EXCLUDED_BY_TEAM")
        self.assertTrue(ven_info["is_excluded"])

        ecu_info = get_country_policy_info("Ecuador")
        self.assertEqual(ecu_info["status"], "NO_ROCHE_APPROVED_SOURCE")
        self.assertFalse(ecu_info["is_excluded"])

        self.assertEqual(get_country_policy_info("Bolivia")["status"], "PENDING_REVIEW")
        self.assertEqual(get_country_policy_info("Canadá")["status"], "PENDING_REVIEW")

        con = get_db()
        sid = con.execute("SELECT nextval('source_seq')").fetchone()[0]
        con.execute(
            "INSERT INTO sources VALUES (?, 'Paraguay', 'DINAVISA', 'DINAVISA', 'https://dinavisa.gov.py/precio-maximo-rep-fijado-por-la-dinavisa/', 'html_table', 'APPROVED_ROCHE_SOURCE', false, 24, '{}', NULL, 'ready', 'test')",
            [sid]
        )
        with patch.object(HtmlAdapter, "extract", side_effect=ConnectionError("Endpoint down")):
            try:
                ingest_source(sid)
            except ConnectionError:
                pass
        status_in_db = con.execute("SELECT last_status FROM sources WHERE source_id = ?", [sid]).fetchone()[0]
        self.assertEqual(status_in_db, "ACCESS_FAILED")
        print("  PASS: Inaccessible sources marked ACCESS_FAILED; ambiguous sources marked PENDING_REVIEW; gap countries marked NO_ROCHE_APPROVED_SOURCE.")

if __name__ == "__main__":
    unittest.main()
