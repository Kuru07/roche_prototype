import unittest
import io
import os
import json
import duckdb
import pandas as pd
from app.source_policy import (
    safe_request_get,
    validate_roche_source_url,
    is_roche_approved_url,
    UnapprovedSourceError,
    ROCHE_APPROVED_URLS
)
from app.ingestion.adapters.cmed_adapter import CmedAdapter
from app.ingestion.adapters.spreadsheet_adapter import SpreadsheetAdapter
from app.ingestion.normalizer import map_raw_record, _parse_price_value, extract_cmed_tax_tier_prices
from app.ingestion.universal_engine import get_adapter, ingest_source
from app.agents.sql_agent import execute_sql_query, generate_sql
from app.state import get_db

CMED_APPROVED_URL = "https://www.gov.br/anvisa/pt-br/assuntos/medicamentos/cmed/precos"
CMED_SECONDARY_DOWNLOAD_URL = "https://www.gov.br/anvisa/pt-br/assuntos/medicamentos/cmed/precos/arquivos/lista_pmc_20261109_111901660.xlsx/@@download/file"
SCRATCH_SAMPLE_PATH = r"C:\Users\Dell\.gemini\antigravity\brain\e01a0210-badc-4e57-b104-38195cfa9c4f\scratch\cmed_sample.xlsx"

class TestPhase3BrazilCmedIngestion(unittest.TestCase):
    
    def setUp(self):
        self.meta = {
            "source_id": 999,
            "country": "Brazil",
            "source_name": "ANVISA CMED Preços de Medicamentos",
            "source_authority": "ANVISA",
            "url": CMED_APPROVED_URL,
            "adapter_type": "cmed",
            "default_currency": "BRL",
            "verification_status": "PENDING_ROCHE_APPROVAL",
            "is_secondary_file": True,
            "is_synthetic": False,
            "parser_config": {
                "sheet_name": "Lista PMC",
                "header": 0
            }
        }
    
    def test_01_approved_url_access(self):
        """1. Test that the Roche-approved URL for Brazil is accessible and returns HTTP 200."""
        self.assertTrue(is_roche_approved_url(CMED_APPROVED_URL))
        resp = safe_request_get(CMED_APPROVED_URL, timeout=25)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers.get("Content-Type", ""))
        self.assertGreater(len(resp.content), 50000)
    
    def test_02_rejection_of_unapproved_secondary_url(self):
        """2. Test that unapproved secondary download URL is strictly rejected by the policy gate."""
        self.assertFalse(is_roche_approved_url(CMED_SECONDARY_DOWNLOAD_URL))
        with self.assertRaises(UnapprovedSourceError):
            validate_roche_source_url(CMED_SECONDARY_DOWNLOAD_URL)
        
        with self.assertRaises(UnapprovedSourceError):
            safe_request_get(CMED_SECONDARY_DOWNLOAD_URL)
    
    def test_03_landing_page_inspection_and_discovery(self):
        """3. Inspect approved landing page and verify discovered links are stamped PENDING_ROCHE_APPROVAL."""
        adapter = CmedAdapter()
        inspection = adapter.inspect_approved_page(CMED_APPROVED_URL)
        self.assertEqual(inspection["status_code"], 200)
        self.assertEqual(inspection["html_tables_count"], 0)  # No HTML tables in landing page body
        self.assertGreater(len(inspection["discovered_files"]), 0)
        
        # Verify discovered file links are marked as NOT on the allowlist and PENDING_ROCHE_APPROVAL
        for f in inspection["discovered_files"]:
            self.assertFalse(f["is_on_roche_allowlist"], f"Discovered link {f['url']} should not be on verbatim allowlist")
            self.assertEqual(f["approval_status"], "PENDING_ROCHE_APPROVAL")
    
    def test_04_adapter_blocks_unapproved_scraping_without_raw_bytes(self):
        """4. CmedAdapter must refuse automated extraction of unapproved secondary URLs."""
        adapter = CmedAdapter()
        with self.assertRaises(UnapprovedSourceError):
            adapter.extract({"url": CMED_APPROVED_URL, "raw_bytes": None})
    
    def test_05_extraction_on_actual_source_data(self):
        """5. Extract records from actual ANVISA CMED source spreadsheet in memory."""
        self.assertTrue(os.path.exists(SCRATCH_SAMPLE_PATH), "Scratch sample file must exist")
        with open(SCRATCH_SAMPLE_PATH, "rb") as f:
            raw_bytes = f.read()
        
        adapter = CmedAdapter()
        records = adapter.extract({
            "url": CMED_APPROVED_URL,
            "raw_bytes": raw_bytes,
            "parser_config": {"sheet_name": "Lista PMC", "nrows": 50}
        })
        self.assertGreater(len(records), 0)
        sample = records[0]
        self.assertIn("SUBSTÂNCIA", sample)
        self.assertIn("PRODUTO", sample)
        self.assertIn("LABORATÓRIO", sample)
        self.assertIn("PF Sem Impostos", sample)
        self.assertIn("PMC 0 %", sample)
    
    def test_06_field_mapping_and_semantics(self):
        """6. Test price-field semantics: separate tax tiers, preserve price_regime, never infer categories."""
        sample_raw = {
            "SUBSTÂNCIA": "TRASTUZUMABE",
            "CNPJ": "33.009.945/0001-23",
            "LABORATÓRIO": "PRODUTOS ROCHE QUÍMICOS E FARMACÊUTICOS S.A.",
            "PRODUTO": "HERCEPTIN",
            "APRESENTAÇÃO": "150 MG PO LIOF INJ CT FA VD INC",
            "REGIME DE PREÇO": "Regulado",
            "PF Sem Impostos": "2.500,50",
            "PMC 0 %": "2.800,00",
            "PMC 18 %": "3.200,75"
        }
        res = map_raw_record(sample_raw, self.meta)
        self.assertIsNotNone(res)
        self.assertEqual(res["country"], "Brazil")
        self.assertEqual(res["molecule_name"], "TRASTUZUMABE")
        self.assertEqual(res["brand_name"], "HERCEPTIN")
        self.assertEqual(res["manufacturer"], "PRODUTOS ROCHE QUÍMICOS E FARMACÊUTICOS S.A.")
        self.assertEqual(res["dosage_form"], "150 MG PO LIOF INJ CT FA VD INC")
        self.assertEqual(res["currency"], "BRL")
        
        # Requirement B: Do not map PMC 0% to consumer or PMC 18% to retail, and do not classify PF as wholesale
        self.assertIsNone(res["price_consumer"], "PMC 0% must NOT be inferred as consumer price category")
        self.assertIsNone(res["price_retail"], "PMC 18% must NOT be inferred as retail price category")
        self.assertIsNone(res["price_wholesale"], "PF Sem Impostos must NOT be inferred as wholesale price category")
        self.assertIsNone(res["price_type"], "price_type must NOT be inferred from REGIME DE PREÇO")
        self.assertEqual(res["price_regime"], "Regulado", "REGIME DE PREÇO must be preserved as price_regime")
        self.assertTrue(res["needs_review"], "Unconfirmed semantics must be flagged needs_review")
        self.assertIn("PENDING_REVIEW", res["mapping_notes"])
        
        # Verify tax tiers are separated
        self.assertEqual(res["tax_tier_prices"]["PF"]["Sem Impostos"], 2500.50)
        self.assertEqual(res["tax_tier_prices"]["PMC"]["0 %"], 2800.00)
        self.assertEqual(res["tax_tier_prices"]["PMC"]["18 %"], 3200.75)
        
        # Requirement C: Must be marked PENDING_ROCHE_APPROVAL because it comes from secondary file
        self.assertEqual(res["verification_status"], "PENDING_ROCHE_APPROVAL")
        self.assertEqual(res["is_synthetic"], False)
        self.assertEqual(res["source_url"], CMED_APPROVED_URL)
        
        # Raw row preserved verbatim
        self.assertEqual(res["source_row"]["CNPJ"], "33.009.945/0001-23")
    
    def test_07_duplicate_handling(self):
        """7. Verify deterministic provenance hashing and DuckDB INSERT OR REPLACE idempotency."""
        sample_raw = {
            "SUBSTÂNCIA": "BEVACIZUMABE",
            "LABORATÓRIO": "PRODUTOS ROCHE QUÍMICOS E FARMACÊUTICOS S.A.",
            "PRODUTO": "AVASTIN",
            "APRESENTAÇÃO": "25 MG/ML SOL DIL INFUS IV CT FA VD TRANS X 4 ML",
            "REGIME DE PREÇO": "Regulado",
            "PF Sem Impostos": "1.956,45"
        }
        res1 = map_raw_record(sample_raw, self.meta)
        res2 = map_raw_record(sample_raw, self.meta)
        self.assertEqual(res1["observation_id"], res2["observation_id"])
        
        con = duckdb.connect(":memory:")
        con.execute("""
            CREATE TABLE test_obs (
                observation_id VARCHAR PRIMARY KEY,
                molecule_name VARCHAR,
                price_regime VARCHAR
            )
        """)
        con.execute("INSERT OR REPLACE INTO test_obs VALUES (?, ?, ?)", [res1["observation_id"], res1["molecule_name"], res1["price_regime"]])
        con.execute("INSERT OR REPLACE INTO test_obs VALUES (?, ?, ?)", [res2["observation_id"], res2["molecule_name"], res2["price_regime"]])
        count = con.execute("SELECT count(*) FROM test_obs").fetchone()[0]
        self.assertEqual(count, 1, "Duplicate records must be de-duplicated by observation_id")
    
    def test_08_incomplete_and_ambiguous_records_no_inference(self):
        """8. Never infer missing prices or values; flag incomplete/ambiguous records for review."""
        sample_hospital = {
            "SUBSTÂNCIA": "ATEZOLIZUMABE",
            "LABORATÓRIO": "PRODUTOS ROCHE QUÍMICOS E FARMACÊUTICOS S.A.",
            "PRODUTO": "TECENTRIQ",
            "APRESENTAÇÃO": "1200 MG SOL DIL INFUS IV CT FA VD TRANS X 20 ML",
            "REGIME DE PREÇO": "Regulado",
            "PF Sem Impostos": "25.354,79",
            "PMC 0 %": "nan",
            "PMC 18 %": "nan"
        }
        res = map_raw_record(sample_hospital, self.meta)
        self.assertIsNotNone(res)
        self.assertIsNone(res["price_consumer"])
        self.assertIsNone(res["price_retail"])
        self.assertIsNone(res["price_wholesale"])
        self.assertEqual(res["tax_tier_prices"]["PF"]["Sem Impostos"], 25354.79)
        self.assertEqual(res["tax_tier_prices"]["PMC"], {}, "Hospital drugs without PMC must have empty PMC tiers")
        
        # Test record without molecule or brand name -> Rejected (returns None)
        unidentifiable = {"LABORATÓRIO": "BAYER", "PF Sem Impostos": "100,00"}
        res_bad = map_raw_record(unidentifiable, self.meta)
        self.assertIsNone(res_bad, "Records lacking active ingredient or brand must be rejected")
    
    def test_09_international_number_parsing(self):
        """9. Test safe conversion of international pricing formats without guessing."""
        self.assertEqual(_parse_price_value("2.500,50"), 2500.50)
        self.assertEqual(_parse_price_value("25.354,79"), 25354.79)
        self.assertEqual(_parse_price_value("27,44"), 27.44)
        self.assertEqual(_parse_price_value("2,500.50"), 2500.50)
        self.assertEqual(_parse_price_value("1200.00"), 1200.00)
        self.assertIsNone(_parse_price_value("nan"))
        self.assertIsNone(_parse_price_value("-"))
        self.assertIsNone(_parse_price_value(""))
        self.assertIsNone(_parse_price_value(None))
    
    def test_10_duckdb_memory_ingestion_and_sql_exclusion(self):
        """10. Verify DuckDB :memory: storage, PENDING_ROCHE_APPROVAL status, and SQL exclusion."""
        con = get_db()
        test_source_id = 888
        con.execute("DELETE FROM sources WHERE source_id = ?", [test_source_id])
        con.execute(
            """
            INSERT INTO sources VALUES (
                ?, 'Brazil', 'ANVISA CMED Preços de Medicamentos', 'ANVISA',
                ?, 'cmed', 'PENDING_ROCHE_APPROVAL', false, 24, '{}', NULL, 'ready', 'Phase 3 Controlled Validation'
            )
            """,
            [test_source_id, CMED_APPROVED_URL]
        )
        
        # Read sample bytes and execute ingestion
        with open(SCRATCH_SAMPLE_PATH, "rb") as f:
            raw_bytes = f.read()
        
        adapter = get_adapter("cmed")
        records = adapter.extract({
            "url": CMED_APPROVED_URL,
            "raw_bytes": raw_bytes,
            "parser_config": self.meta["parser_config"]
        })
        
        saved = 0
        for r in records[:25]:
            norm = map_raw_record(r, {
                "source_id": test_source_id,
                "country": "Brazil",
                "source_name": "ANVISA CMED Preços de Medicamentos",
                "source_authority": "ANVISA",
                "url": CMED_APPROVED_URL,
                "verification_status": "PENDING_ROCHE_APPROVAL",
                "is_secondary_file": True,
                "is_synthetic": False,
                "default_currency": "BRL"
            })
            if norm:
                con.execute(
                    """
                    INSERT OR REPLACE INTO price_observations VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, CURRENT_TIMESTAMP, ?, ?
                    )
                    """,
                    [
                        norm["observation_id"], norm["country"], norm["source_id"],
                        norm["source_name"], norm["source_authority"], norm["source_url"],
                        norm["verification_status"], norm["is_synthetic"], norm["extraction_confidence"],
                        norm["needs_review"], norm["mapping_notes"], norm["molecule_name"],
                        norm["brand_name"], norm["active_ingredient"], norm["strength"],
                        norm["dosage_form"], norm["pack_size"], norm["manufacturer"],
                        norm["currency"], norm["price_retail"], norm["price_consumer"],
                        norm["price_wholesale"], norm["price_public"], norm["price_type"],
                        norm["price_regime"], norm["price_value"], norm["price_per_unit"],
                        norm["published_at"], norm["effective_date"], json.dumps(norm["source_row"], default=str),
                        norm["search_text"]
                    ]
                )
                saved += 1
        
        self.assertGreater(saved, 0)
        
        # Verify stored rows in DuckDB :memory:
        rows = con.execute(
            """
            SELECT observation_id, country, source_url, verification_status, is_synthetic,
                   molecule_name, brand_name, price_regime, currency
            FROM price_observations
            WHERE source_id = ?
            """,
            [test_source_id]
        ).fetchall()
        
        self.assertEqual(len(rows), saved)
        for r in rows:
            self.assertEqual(r[1], "Brazil")
            self.assertEqual(r[2], CMED_APPROVED_URL)
            self.assertEqual(r[3], "PENDING_ROCHE_APPROVAL", "Secondary file data must be PENDING_ROCHE_APPROVAL")
            self.assertEqual(r[4], False, "Must be explicitly marked is_synthetic = False")
            self.assertEqual(r[7], "Regulado")
            self.assertEqual(r[8], "BRL")
        
        # Requirement C.5: Verify that SQL queries strictly exclude PENDING_ROCHE_APPROVAL records
        sql_query = generate_sql("Show prices for HERCEPTIN")
        sql_result = execute_sql_query(sql_query)
        # Ensure none of the pending approval rows from source_id 888 appear in the SQL query results
        matching_pending = [rec for rec in sql_result["records"] if rec.get("verification_status") == "PENDING_ROCHE_APPROVAL"]
        self.assertEqual(len(matching_pending), 0, "Unapproved secondary records must be excluded from SQL query results")

if __name__ == "__main__":
    unittest.main()
