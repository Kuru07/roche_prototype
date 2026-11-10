import unittest
import io
import os
import re
import json
import duckdb
import pandas as pd
from bs4 import BeautifulSoup
from app.source_policy import (
    safe_request_get,
    validate_roche_source_url,
    is_roche_approved_url,
    UnapprovedSourceError,
    ROCHE_APPROVED_URLS
)
from app.ingestion.adapters.html_adapter import HtmlAdapter
from app.ingestion.normalizer import map_raw_record, parse_paraguay_rep_price
from app.ingestion.universal_engine import get_adapter, ingest_source
from app.agents.sql_agent import execute_sql_query, generate_sql
from app.agents.calculation_framework import execute_pricing_calculation
from app.state import get_db, get_memory_stats

PARAGUAY_APPROVED_URL = "https://dinavisa.gov.py/precio-maximo-rep-fijado-por-la-dinavisa/"
UNAPPROVED_EXTERNAL_URL = "https://www.minsalud.gov.co/salud/MT/Paginas/medicamentos-regulacion-precios.aspx"
UNAPPROVED_SECONDARY_URL = "https://dinavisa.gov.py/wp-content/uploads/2024/11/GUIA_DINAVISA_PARA_BUSQUEDA_DE_PRECIOS_REFERENCIALES.pdf"

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "data", "paraguay_dinavisa_fixture.html")

class TestPhase3ParaguayDinavisaIngestion(unittest.TestCase):
    
    @classmethod
    def setUpClass(cls):
        # CORRECTION 4: Remove live network dependency. Use saved HTML fixture derived from pilot response.
        with open(FIXTURE_PATH, "rb") as f:
            cls.html_content = f.read()
        cls.soup = BeautifulSoup(cls.html_content, "lxml")
        cls.table = cls.soup.find("table")
        
        cls.source_meta = {
            "source_id": 101,
            "country": "Paraguay",
            "source_name": "Precio máximo REP Fijado por la DINAVISA",
            "source_authority": "DINAVISA",
            "url": PARAGUAY_APPROVED_URL,
            "adapter_type": "html_table",
            "verification_status": "APPROVED_ROCHE_SOURCE",
            "is_synthetic": False,
            "parser_config": {
                "table_selector": "table",
                "default_currency": "PYG",
                "fixture_path": FIXTURE_PATH
            }
        }

    @classmethod
    def tearDownClass(cls):
        from app.state import reset_in_memory_db
        reset_in_memory_db()

    def test_01_exact_approved_url_access(self):
        """1. Test that the exact Roche-approved URL for Paraguay DINAVISA is verified and valid."""
        self.assertTrue(is_roche_approved_url(PARAGUAY_APPROVED_URL))
        self.assertEqual(validate_roche_source_url(PARAGUAY_APPROVED_URL), PARAGUAY_APPROVED_URL)
        self.assertGreater(len(self.html_content), 50000)
        self.assertIn("DINAVISA", self.soup.title.string if self.soup.title else "")

    def test_02_rejection_of_unapproved_urls(self):
        """2. Test that unapproved external URLs and unapproved secondary endpoints are strictly rejected."""
        self.assertFalse(is_roche_approved_url(UNAPPROVED_EXTERNAL_URL))
        with self.assertRaises(UnapprovedSourceError):
            validate_roche_source_url(UNAPPROVED_EXTERNAL_URL)
        with self.assertRaises(UnapprovedSourceError):
            safe_request_get(UNAPPROVED_EXTERNAL_URL)
        
        # Unapproved arbitrary portal
        with self.assertRaises(UnapprovedSourceError):
            safe_request_get("https://www.google.com/search?q=dinavisa+precios")

    def test_03_correct_html_table_identification(self):
        """3. Correctly identifies the single HTML table with 207 total rows (1 header + 206 data rows)."""
        tables = self.soup.find_all("table")
        self.assertEqual(len(tables), 1, "Expected exactly 1 HTML table on the DINAVISA page")
        rows = self.table.find_all("tr")
        self.assertEqual(len(rows), 207, "Expected 207 tr elements (1 header + 206 data rows)")
        headers = self.table.find("tr").find_all(["th", "td"])
        self.assertEqual(len(headers), 6, "Expected exactly 6 header columns")

    def test_04_extraction_of_all_six_original_columns(self):
        """4. Extracts all six original columns with exact Spanish column names preserved for ALL 206 records."""
        adapter = HtmlAdapter()
        records = adapter.extract({
            "url": PARAGUAY_APPROVED_URL,
            "raw_bytes": self.html_content,
            "parser_config": {"table_selector": "table"}
        })
        self.assertEqual(len(records), 206)
        
        expected_columns = {
            "Nombre comercial del INNOVADOR",
            "Principio activo",
            "Dosis",
            "Forma farmacéutica",
            "Presentación",
            "Precio máximo REP"
        }
        # CORRECTION 4: Verify all 206 records contain all six original column keys
        for idx, rec in enumerate(records):
            self.assertEqual(set(rec.keys()), expected_columns, f"Row {idx} missing expected keys")
        
        self.assertIn("Treanda", records[0]["Nombre comercial del INNOVADOR"])

    def test_05_multiline_cell_handling(self):
        """5. Handles multi-line cells (<br> / newlines) by normalizing to clean single-spaced text."""
        adapter = HtmlAdapter()
        records = adapter.extract({
            "url": PARAGUAY_APPROVED_URL,
            "raw_bytes": self.html_content,
            "parser_config": {"table_selector": "table"}
        })
        # Record index 2 is Exjade (originally contained <br/> in raw HTML)
        exjade = records[2]
        self.assertEqual(exjade["Nombre comercial del INNOVADOR"], "Exjade")
        self.assertEqual(exjade["Forma farmacéutica"], "Comprimidos dispersables")
        self.assertEqual(exjade["Presentación"], "Por comprimido dispersable")

    def test_06_spanish_text_and_accent_preservation(self):
        """6. Verifies preservation of Spanish characters, accents, and tildes without corruption."""
        adapter = HtmlAdapter()
        records = adapter.extract({
            "url": PARAGUAY_APPROVED_URL,
            "raw_bytes": self.html_content,
            "parser_config": {"table_selector": "table"}
        })
        # Record index 7 is Pregnyl
        pregnyl = records[7]
        self.assertEqual(pregnyl["Principio activo"], "Gonadotropina coriónica humana")
        self.assertEqual(pregnyl["Forma farmacéutica"], "Polvo liofilizado para solución inyectable")
        self.assertIn("ó", pregnyl["Principio activo"])
        self.assertIn("ó", pregnyl["Forma farmacéutica"])

    def test_07_price_parsing_and_gs_notation_preservation(self):
        """7. Validates numeric price parsing, period thousands separation, and gs currency notation across all 205 nonblank prices."""
        adapter = HtmlAdapter()
        records = adapter.extract({
            "url": PARAGUAY_APPROVED_URL,
            "raw_bytes": self.html_content,
            "parser_config": {"table_selector": "table"}
        })
        # CORRECTION 4: Validate all 205 nonblank source prices against parsed numeric values
        nonblank_verified = 0
        blank_verified = 0
        for idx, r in enumerate(records):
            raw_val = r.get("Precio máximo REP")
            if str(raw_val).strip() and str(raw_val).strip().lower() not in ("nan", "none", ""):
                val, notat, is_v = parse_paraguay_rep_price(raw_val)
                self.assertTrue(is_v, f"Failed parsing on row {idx}: {raw_val}")
                self.assertEqual(notat, "gs")
                self.assertIsNotNone(val)
                # Verify period thousands stripped matches clean digits
                clean_num = float(re.sub(r"[^\d]", "", str(raw_val)))
                self.assertEqual(val, clean_num, f"Numeric mismatch on row {idx}: parsed {val} vs expected {clean_num}")
                nonblank_verified += 1
            else:
                val, notat, is_v = parse_paraguay_rep_price(raw_val)
                self.assertFalse(is_v)
                self.assertIsNone(val)
                blank_verified += 1
        
        self.assertEqual(nonblank_verified, 205, "Expected exactly 205 valid nonblank prices")
        self.assertEqual(blank_verified, 1, "Expected exactly 1 blank price (Row 195)")

    def test_08_duplicate_and_incomplete_row_handling(self):
        """8. Verifies duplicate presentations are preserved with distinct IDs and incomplete rows receive PENDING_REVIEW."""
        adapter = HtmlAdapter()
        records = adapter.extract({
            "url": PARAGUAY_APPROVED_URL,
            "raw_bytes": self.html_content,
            "parser_config": {"table_selector": "table"}
        })
        
        # CORRECTION 2: Duplicate presentations: Xarelto rows have unique collision-safe IDs
        xarelto_rows = [(idx, r) for idx, r in enumerate(records) if "xarelto" in str(r.get("Nombre comercial del INNOVADOR")).lower()]
        self.assertEqual(len(xarelto_rows), 6, "Expected 6 total Xarelto entries")
        
        xarelto_obs_ids = []
        for idx, r in xarelto_rows:
            norm = map_raw_record(r, self.source_meta, row_index=idx)
            xarelto_obs_ids.append(norm["observation_id"])
        self.assertEqual(len(set(xarelto_obs_ids)), 6, "All 6 Xarelto observations must have distinct collision-safe IDs")
        
        # CORRECTION 1: Incomplete row (Row index 194 / Nebivolol) receives PENDING_REVIEW
        nebivolol_raw = records[194]
        mapped_neb = map_raw_record(nebivolol_raw, self.source_meta, row_index=194)
        self.assertIsNotNone(mapped_neb, "Incomplete row must NOT be silently discarded")
        self.assertTrue(mapped_neb["needs_review"])
        self.assertEqual(mapped_neb["verification_status"], "PENDING_REVIEW")
        self.assertEqual(mapped_neb["source_approval_status"], "APPROVED_ROCHE_SOURCE")
        self.assertIn("Incomplete row", mapped_neb["mapping_notes"])
        self.assertIsNone(mapped_neb["price_value"])
        self.assertIsNone(mapped_neb["brand_name"])
        self.assertEqual(mapped_neb["active_ingredient"], "Nebivolol")

    def test_09_correct_medicine_matching_fields(self):
        """9. Verifies brand name is not confused with active ingredient and combinations are handled."""
        adapter = HtmlAdapter()
        records = adapter.extract({
            "url": PARAGUAY_APPROVED_URL,
            "raw_bytes": self.html_content,
            "parser_config": {"table_selector": "table"}
        })
        
        # Test Treanda
        treanda_norm = map_raw_record(records[0], self.source_meta, row_index=0)
        self.assertEqual(treanda_norm["brand_name"], "Treanda")
        self.assertEqual(treanda_norm["active_ingredient"], "Bendamustina clorhidrato")
        self.assertNotEqual(treanda_norm["brand_name"], treanda_norm["active_ingredient"])

        # Test Fixed-dose combination (Zavicefta)
        zavicefta_raw = next(r for r in records if r["Nombre comercial del INNOVADOR"] == "Zavicefta")
        zav_norm = map_raw_record(zavicefta_raw, self.source_meta, row_index=1)
        self.assertEqual(zav_norm["brand_name"], "Zavicefta")
        self.assertIn("+", zav_norm["active_ingredient"])
        self.assertIn("avibactam", zav_norm["active_ingredient"])

    def test_10_provenance_and_source_approval_status(self):
        """10. Confirms complete provenance, SHA-256 hash, and separated source vs record status."""
        adapter = HtmlAdapter()
        records = adapter.extract({
            "url": PARAGUAY_APPROVED_URL,
            "raw_bytes": self.html_content,
            "parser_config": {"table_selector": "table"}
        })
        norm = map_raw_record(records[0], self.source_meta, row_index=0)
        self.assertEqual(norm["country"], "Paraguay")
        self.assertEqual(norm["source_authority"], "DINAVISA")
        self.assertEqual(norm["source_url"], PARAGUAY_APPROVED_URL)
        self.assertEqual(norm["verification_status"], "APPROVED_ROCHE_SOURCE")
        self.assertEqual(norm["source_approval_status"], "APPROVED_ROCHE_SOURCE")
        self.assertFalse(norm["is_synthetic"])
        self.assertTrue(norm["observation_id"].startswith("obs_"))
        self.assertEqual(len(norm["observation_id"]), 32)
        
        # Check raw preserved row
        self.assertIn("Nombre comercial del INNOVADOR", norm["source_row"])
        self.assertEqual(norm["source_row"]["_original_currency_notation"], "gs")
        self.assertEqual(norm["source_row"]["_iso_currency"], "PYG")
        self.assertEqual(norm["source_row"]["_source_approval_status"], "APPROVED_ROCHE_SOURCE")
        self.assertEqual(norm["source_row"]["_record_verification_status"], "APPROVED_ROCHE_SOURCE")
        self.assertIn("No currency conversion", norm["source_row"]["_currency_mapping_documentation"])

    def test_11_in_memory_duckdb_ingestion(self):
        """11. End-to-end ingestion of all 206 Paraguay records into DuckDB :memory:."""
        con = get_db()
        # Seed source 101 in DuckDB pointing to fixture
        con.execute(
            """
            INSERT OR REPLACE INTO sources VALUES (
                101, 'Paraguay', 'Precio máximo REP Fijado por la DINAVISA', 'DINAVISA',
                ?, 'html_table', 'APPROVED_ROCHE_SOURCE', false, 24,
                ?, NULL, 'ready', 'Paraguay Pilot'
            )
            """,
            [PARAGUAY_APPROVED_URL, json.dumps({"table_selector": "table", "fixture_path": FIXTURE_PATH})]
        )
        
        # Ingest using universal engine
        result = ingest_source(101)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["rows_found"], 206)
        self.assertEqual(result["rows_saved"], 206)
        
        # Verify count in DuckDB price_observations
        saved_count = con.execute("SELECT COUNT(*) FROM price_observations WHERE source_id = 101").fetchone()[0]
        self.assertEqual(saved_count, 206)

    def test_12_sql_retrieval_default_pricing_excludes_nebivolol(self):
        """12. Verifies SQL Agent default pricing queries exclude Nebivolol, but audit queries retrieve it."""
        # A. Default pricing query for Treanda succeeds
        sql_treanda = generate_sql("Show prices in Paraguay for Treanda")
        self.assertIn("price_observations", sql_treanda)
        res_treanda = execute_sql_query(sql_treanda)
        self.assertGreater(res_treanda["count"], 0)
        sample = res_treanda["records"][0]
        self.assertEqual(sample["verification_status"], "APPROVED_ROCHE_SOURCE")
        self.assertEqual(sample["price_type"], "Precio máximo REP")
        self.assertFalse(sample["is_synthetic"])
        
        # B. Default pricing query for Nebivolol strictly excludes unpriced/review row
        sql_nebivolol_pricing = generate_sql("Show prices in Paraguay for Nebivolol")
        res_nebivolol_pricing = execute_sql_query(sql_nebivolol_pricing)
        self.assertEqual(res_nebivolol_pricing["count"], 0, "Default pricing query must NEVER return unpriced Nebivolol")
        
        # C. Explicit audit query retrieves Nebivolol clearly labelled as pending review
        sql_audit = generate_sql("Show pending review records for Paraguay")
        res_audit = execute_sql_query(sql_audit)
        self.assertGreater(res_audit["count"], 0)
        neb_audit_rows = [r for r in res_audit["records"] if r.get("active_ingredient") == "Nebivolol" or r.get("molecule_name") == "Nebivolol"]
        self.assertEqual(len(neb_audit_rows), 1)
        self.assertTrue(neb_audit_rows[0]["needs_review"])
        self.assertEqual(neb_audit_rows[0]["verification_status"], "PENDING_REVIEW")
        self.assertTrue(pd.isna(neb_audit_rows[0]["price_value"]) or neb_audit_rows[0]["price_value"] is None)

    def test_13_no_synthetic_data_in_pricing_summaries(self):
        """13. Real Paraguay observations have is_synthetic = False and default pricing queries exclude demo records."""
        con = get_db()
        # Verify all 206 observations from real DINAVISA source 101 are is_synthetic = False
        rows = con.execute("SELECT is_synthetic, verification_status FROM price_observations WHERE source_id = 101").fetchall()
        self.assertEqual(len(rows), 206)
        for is_synth, verif in rows:
            self.assertFalse(is_synth, "Real Paraguay records must NEVER have is_synthetic = True")
        
        # Verify default pricing query excludes the demo record
        sql_all_paraguay = generate_sql("Show prices in Paraguay")
        res_all = execute_sql_query(sql_all_paraguay)
        for r in res_all["records"]:
            self.assertFalse(r["is_synthetic"], "Default pricing query must strictly exclude synthetic demo records")
            self.assertIsNotNone(r["price_value"], "Default pricing query must strictly exclude null prices")
            self.assertFalse(r["needs_review"], "Default pricing query must strictly exclude needs_review rows")

    def test_14_rejection_of_actual_dinavisa_secondary_pdf(self):
        """14. Confirms that the actual secondary DINAVISA PDF URL is strictly rejected by the source policy without requesting it."""
        # CORRECTION 4: Test actual secondary PDF URL without network request
        with self.assertRaises(UnapprovedSourceError):
            validate_roche_source_url(UNAPPROVED_SECONDARY_URL, country="Paraguay")

    def test_15_calculation_agent_guardrail_intact(self):
        """15. Confirms calculation agent returns NOT_CALCULATED with official methodology pending message."""
        res = execute_pricing_calculation(
            query="Calculate reference price",
            basket_records=[{"country": "Paraguay", "price_value": 1154532.0, "currency": "PYG"}]
        )
        self.assertEqual(res.status, "NOT_CALCULATED")
        self.assertIn("Official Roche methodology pending", res.message)
        self.assertFalse(res.assumed_formula_applied)

if __name__ == "__main__":
    unittest.main()
