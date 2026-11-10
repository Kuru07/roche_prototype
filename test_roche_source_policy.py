import unittest
from unittest.mock import patch
from app.source_policy import (
    validate_roche_source_url,
    is_roche_approved_url,
    get_country_policy_info,
    UnapprovedSourceError,
    ROCHE_APPROVED_URLS,
    COUNTRY_INVENTORY_METADATA
)
from app.ingestion.universal_engine import import_dynamic_sources_csv, ingest_source
from app.agents.orchestrator import handle_user_query
from app.state import get_db

class TestRocheStrictSourcePolicy(unittest.TestCase):
    
    def test_01_approved_urls_accepted(self):
        """Proves that URLs explicitly present in roche_source_inventory.md are accepted."""
        approved_samples = [
            "https://www.gov.br/anvisa/pt-br/assuntos/medicamentos/cmed/precos",
            "https://www.sanidad.gob.es/areas/farmacia/infoIndustria/SPRAH/preciosMenoresAH/home.htm",
            "https://base-donnees-publique.medicaments.gouv.fr/",
            "https://www.aifa.gov.it/liste-di-trasparenza",
            "https://www.remediosmasbaratos.cl/",
            "https://dinavisa.gov.py/precio-maximo-rep-fijado-por-la-dinavisa/",
            "https://www.bfarm.de/EN/Medicinal-products/Information-on-medicinal-products/Reference-Pricing/_node.html",
            "https://www.formulary.health.gov.on.ca/formulary/"
        ]
        for url in approved_samples:
            self.assertTrue(is_roche_approved_url(url), f"Approved URL rejected: {url}")
            validated = validate_roche_source_url(url)
            self.assertEqual(validated, url)

    def test_02_colombia_suggested_minsalud_url_strictly_rejected(self):
        """Proves that Colombia's suggested MinSalud URL is strictly rejected because Roche did not provide it."""
        colombia_unapproved_urls = [
            "https://www.minsalud.gov.co/salud/MT/Paginas/medicamentos-regulacion-precios.aspx",
            "https://www.datos.gov.co/resource/c8rr-q2ca.json",
            "https://www.minsalud.gov.co/sismed"
        ]
        for url in colombia_unapproved_urls:
            self.assertFalse(is_roche_approved_url(url))
            with self.assertRaises(UnapprovedSourceError):
                validate_roche_source_url(url, country="Colombia")

    def test_03_arbitrary_unlisted_sources_strictly_rejected(self):
        """Proves that unlisted government or web sources are rejected even if official."""
        unapproved_external = [
            "https://www.fda.gov/drugs/drug-approvals-and-databases",
            "https://who.int/guidelines/pharmaceutical-pricing",
            "https://www.ema.europa.eu/en/medicines",
            "https://www.controlsanitario.gob.ec/precios-medicamentos/",
            "https://custom-unapproved-crawler.com/prices"
        ]
        for url in unapproved_external:
            self.assertFalse(is_roche_approved_url(url))
            with self.assertRaises(UnapprovedSourceError):
                validate_roche_source_url(url)

    def test_04_countries_with_no_source_marked_correctly(self):
        """Proves that countries without sources in the Roche inventory are marked NO_ROCHE_APPROVED_SOURCE."""
        no_source_countries = [
            "Colombia", "Uruguay", "Cuba", "Mancomunidad de Dominica",
            "San Vicente y las Granadinas", "Antigua y Barbuda",
            "Santa Lucía", "Guyana", "Surinam", "Líbano", "Noruega", "Ecuador"
        ]
        for c in no_source_countries:
            info = get_country_policy_info(c)
            self.assertEqual(info["status"], "NO_ROCHE_APPROVED_SOURCE", f"{c} was not marked NO_ROCHE_APPROVED_SOURCE")
            self.assertFalse(info["has_approved_url"])
        
        # Verify Ecuador specifically: confirm missing reference-source status, NOT excluded from project
        ecuador_info = get_country_policy_info("Ecuador")
        self.assertEqual(ecuador_info["status"], "NO_ROCHE_APPROVED_SOURCE")
        self.assertFalse(ecuador_info["is_excluded"], "Ecuador must NOT be excluded from the project")

    def test_05_currency_volatility_exclusions_marked_correctly(self):
        """Proves that Argentina and Venezuela are marked EXCLUDED_BY_TEAM per team observations."""
        for c in ["Argentina", "Venezuela"]:
            info = get_country_policy_info(c)
            self.assertEqual(info["status"], "EXCLUDED_BY_TEAM")
            self.assertTrue(info["is_excluded"])
            self.assertIn("volatilidad de su moneda", info["observation"])

    def test_06_csv_importer_rejects_unapproved_urls(self):
        """Proves that uploading a CSV with an unlisted URL raises UnapprovedSourceError and blocks ingestion."""
        bad_csv = (
            "country,source_name,url\n"
            "Colombia,MinSalud SISMED,https://www.minsalud.gov.co/unapproved\n"
        )
        with self.assertRaises(UnapprovedSourceError):
            import_dynamic_sources_csv(bad_csv.encode("utf-8"))

    def test_07_csv_importer_accepts_approved_urls(self):
        """Proves that uploading a CSV with approved Roche inventory URLs succeeds."""
        good_csv = (
            "country,source_name,url\n"
            "Brasil,ANVISA CMED,https://www.gov.br/anvisa/pt-br/assuntos/medicamentos/cmed/precos\n"
            "España,MSSSI Precios Menores,https://www.sanidad.gob.es/areas/farmacia/infoIndustria/SPRAH/preciosMenoresAH/home.htm\n"
        )
        count = import_dynamic_sources_csv(good_csv.encode("utf-8"))
        self.assertEqual(count, 2)

    def test_08_ingestion_blocks_unapproved_source_execution(self):
        """Proves that ingest_source blocks any unapproved URL before executing."""
        con = get_db()
        sid = con.execute("SELECT nextval('source_seq')").fetchone()[0]
        # Insert a rogue source manually into DB
        con.execute(
            "INSERT INTO sources VALUES (?, 'RogueCountry', 'RogueSource', 'RogueAuth', 'https://rogue-source.org/api', 'rest_api', 'UNVERIFIED', false, 24, '{}', NULL, 'ready', 'unapproved test')",
            [sid]
        )
        with self.assertRaises(UnapprovedSourceError):
            ingest_source(sid)

    def test_09_chat_orchestrator_enforces_policy_transparency(self):
        """Proves that chat queries about Colombia clearly communicate the NO_ROCHE_APPROVED_SOURCE status."""
        res_colombia = handle_user_query("What is the pricing source status for Colombia?")
        self.assertIn("NO_ROCHE_APPROVED_SOURCE", res_colombia["answer"])
        self.assertIn("roche_source_inventory.md", res_colombia["answer"])

        res_argentina = handle_user_query("Show source status for Argentina")
        self.assertIn("EXCLUDED_BY_TEAM", res_argentina["answer"])
        self.assertIn("volatilidad de su moneda", res_argentina["answer"])

    def test_10_inaccessible_source_marked_access_failed_without_substitution(self):
        """
        Proves Requirement 4: If a listed source is inaccessible, it is marked ACCESS_FAILED.
        The system does NOT replace it with any fallback or alternative source.
        """
        from unittest.mock import patch
        from app.ingestion.adapters.html_adapter import HtmlAdapter
        
        con = get_db()
        sid = con.execute("SELECT nextval('source_seq')").fetchone()[0]
        # Insert a valid, approved Roche URL
        approved_url = "https://www.gov.cy/moh/en/"
        con.execute(
            "INSERT INTO sources VALUES (?, 'Chipre', 'Ministerio de Salud de Chipre', 'MOH', ?, 'html_table', 'APPROVED_ROCHE_SOURCE', false, 24, '{}', NULL, 'ready', 'test source')",
            [sid, approved_url]
        )
        
        # Simulate network failure / inaccessibility
        with patch.object(HtmlAdapter, "extract", side_effect=ConnectionError("Host unreachable: 503 Service Unavailable")):
            with self.assertRaises(ConnectionError):
                ingest_source(sid)
        
        # Verify status in DuckDB sources table is strictly 'ACCESS_FAILED'
        row = con.execute("SELECT last_status, notes FROM sources WHERE source_id = ?", [sid]).fetchone()
        self.assertEqual(row[0], "ACCESS_FAILED")
        self.assertIn("ACCESS_FAILED", row[1])
        
        # Verify scrape_runs status is strictly 'ACCESS_FAILED'
        run_row = con.execute("SELECT status, message FROM scrape_runs WHERE source_id = ? ORDER BY started_at DESC LIMIT 1", [sid]).fetchone()
        self.assertEqual(run_row[0], "ACCESS_FAILED")
        self.assertIn("ACCESS_FAILED", run_row[1])

    def test_11_unclear_definitions_marked_pending_review_without_guessing(self):
        """
        Proves Requirement 5: If a listed source has unclear pricing definitions or extraction requirements,
        it is marked PENDING_REVIEW; the system does not guess or substitute.
        """
        from app.ingestion.adapters.pdf_adapter import PdfAdapter
        
        # Bolivia has unclear pricing definitions noted by Roche:
        bolivia_info = get_country_policy_info("Bolivia")
        self.assertEqual(bolivia_info["status"], "PENDING_REVIEW")
        self.assertIn("LINAME", bolivia_info["observation"])
        
        # Canada has dispensing unit ambiguity noted by Roche:
        canada_info = get_country_policy_info("Canadá")
        self.assertEqual(canada_info["status"], "PENDING_REVIEW")
        self.assertIn("unidad de dispensación", canada_info["observation"])
        
        # Test ingestion of an approved source from a PENDING_REVIEW country
        con = get_db()
        sid = con.execute("SELECT nextval('source_seq')").fetchone()[0]
        con.execute(
            "INSERT INTO sources VALUES (?, 'Bolivia', 'AGEMED Precios Referenciales', 'AGEMED', 'https://www.agemed.gob.bo/#dtu/contenido', 'pdf', 'APPROVED_ROCHE_SOURCE', false, 24, '{}', NULL, 'ready', 'Bolivia test')",
            [sid]
        )
        
        with patch.object(PdfAdapter, "extract", return_value=[]):
            res = ingest_source(sid)
            self.assertEqual(res["status"], "PENDING_REVIEW")
        
        row = con.execute("SELECT last_status FROM sources WHERE source_id = ?", [sid]).fetchone()
        self.assertEqual(row[0], "PENDING_REVIEW")

    def test_12_exact_preservation_of_roche_inventory(self):
        """
        Proves Requirement 6: The exact Roche-provided URLs, institutions, country names,
        and observations from roche_source_inventory.md are preserved verbatim.
        """
        from pathlib import Path
        md_path = Path("roche_source_inventory.md")
        self.assertTrue(md_path.exists())
        with open(md_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        # Check verbatim observations
        exact_observations = [
            "No se toma en cuenta el precio de este país debido a la volatilidad de su moneda",
            "Son precios referenciales de LINAME por lo tanto son PVP o más bajos?",
            "Fija precios máximo al consumidor final en base a precios de referencia internacional.",
            "Ya no hay kairos",
            "No hay fuente de búsqueda",
            "Regula precios maximos al consumidor final",
            "Fijan precios máximos de venta al público",
            "Fijan precios máximos al distribuidor",
            "El precio PVL indica el precio máximo industrial o el precio laboratorio y, el precio PVP IVA correspondiente al Precio de Venta al Público con IVA. Se debe considerar el PVP con IVA como precio de referencia",
            "Tomar el precio “Prix hors honoraiire de dispensation”",
            "Una vez descargada la base de datos, buscar la presentación y el principio activo de interés. El precio de referencia será el que aparezca en la columna “Prezzo Pubblico”.",
            "Tener en cuenta que el precio que se visualiza es por unidad de dispensación"
        ]
        for obs in exact_observations:
            self.assertIn(obs, content, f"Exact observation missing from inventory: {obs}")
        
        # Check every URL in allowlist originates from the markdown
        for url in ROCHE_APPROVED_URLS:
            self.assertTrue(
                (url in content) or (url.rstrip("/") in content),
                f"URL {url} in allowlist does not originate verbatim from roche_source_inventory.md"
            )

    def test_13_no_external_sources_as_fallback(self):
        """
        Proves Requirement 9: External search engines, general knowledge portals,
        and unapproved third-party aggregators are prohibited from serving as fallback sources.
        """
        unauthorized_fallbacks = [
            "https://www.google.com/search?q=drug+prices",
            "https://duckduckgo.com/?q=trastuzumab+price",
            "https://en.wikipedia.org/wiki/List_of_pharmaceutical_prices",
            "https://global-price-finder.org/api/prices",
            "https://minsalud.gov.co/fallback-price"
        ]
        for fb in unauthorized_fallbacks:
            self.assertFalse(is_roche_approved_url(fb))
            with self.assertRaises(UnapprovedSourceError):
                validate_roche_source_url(fb)

    def test_14a_redirect_to_unapproved_domain_blocked_before_request(self):
        """
        Proves Requirement 2: Manual redirect validation blocks unapproved external domains
        BEFORE following. Never sends a request to an unapproved destination.
        """
        import requests
        from app.source_policy import safe_request_get
        
        approved_url = "https://www.sanidad.gob.es/areas/farmacia/infoIndustria/SPRAH/preciosMenoresAH/home.htm"
        
        # Mock 302 response pointing to unapproved external domain
        mock_redirect = requests.Response()
        mock_redirect.status_code = 302
        mock_redirect.url = approved_url
        mock_redirect.headers["Location"] = "https://unapproved-portal.org/login"
        
        with patch("requests.get", return_value=mock_redirect) as mock_get:
            with self.assertRaises(UnapprovedSourceError) as ctx:
                safe_request_get(approved_url)
            self.assertIn("unapproved host/domain", str(ctx.exception))
            # Verify requests.get was called with allow_redirects=False and NEVER called on the unapproved domain
            mock_get.assert_called_once()
            called_args, called_kwargs = mock_get.call_args
            self.assertEqual(called_args[0], approved_url)
            self.assertFalse(called_kwargs.get("allow_redirects", True))

    def test_14b_redirect_to_unapproved_subdomain_blocked_before_request(self):
        """
        Proves Requirement 2: Redirect to an unapproved subdomain of an approved domain
        is blocked BEFORE following.
        """
        import requests
        from app.source_policy import safe_request_get
        
        approved_url = "https://www.sanidad.gob.es/areas/farmacia/infoIndustria/SPRAH/preciosMenoresAH/home.htm"
        
        mock_redirect = requests.Response()
        mock_redirect.status_code = 302
        mock_redirect.url = approved_url
        mock_redirect.headers["Location"] = "https://sub.sanidad.gob.es/unapproved-endpoint"
        
        with patch("requests.get", return_value=mock_redirect) as mock_get:
            with self.assertRaises(UnapprovedSourceError) as ctx:
                safe_request_get(approved_url)
            self.assertIn("unapproved host/domain", str(ctx.exception))
            # Verify no request was sent to the subdomain
            mock_get.assert_called_once()
            self.assertEqual(mock_get.call_args[0][0], approved_url)

    def test_14c_redirect_http_downgrade_blocked_before_request(self):
        """
        Proves Requirement 2: An HTTP protocol downgrade (https -> http) in a redirect
        is blocked BEFORE following.
        """
        import requests
        from app.source_policy import safe_request_get
        
        approved_url = "https://www.sanidad.gob.es/areas/farmacia/infoIndustria/SPRAH/preciosMenoresAH/home.htm"
        
        mock_redirect = requests.Response()
        mock_redirect.status_code = 301
        mock_redirect.url = approved_url
        mock_redirect.headers["Location"] = "http://www.sanidad.gob.es/areas/farmacia/infoIndustria/SPRAH/preciosMenoresAH/home.htm"
        
        with patch("requests.get", return_value=mock_redirect) as mock_get:
            with self.assertRaises(UnapprovedSourceError) as ctx:
                safe_request_get(approved_url)
            self.assertIn("protocol downgrade", str(ctx.exception))
            # Verify no request was sent to http
            mock_get.assert_called_once()
            self.assertEqual(mock_get.call_args[0][0], approved_url)

    def test_15_canonicalization_blocks_subdomain_and_altered_query(self):
        """
        Proves that canonicalization prevents bypassing the allowlist via
        subdomains, altered query parameters, or domain variations.
        """
        tampered_urls = [
            # Subdomain variation of approved domain
            "https://sub.sanidad.gob.es/areas/farmacia/infoIndustria/SPRAH/preciosMenoresAH/home.htm",
            # Path variation / traversal
            "https://www.gov.br/anvisa/pt-br/assuntos/medicamentos/cmed/precos/unapproved-subpath",
            # Altered query parameter
            "https://cafapar.com.py/buscador_productos.php?search=aspirin&admin_bypass=1",
            "https://www.cenabast.cl/lista-de-medicamentos-ley-cenabast/?tribe_event_display=unapproved",
            # Attacker domain with approved domain in path
            "https://attacker.com/https://www.gov.br/anvisa/pt-br/assuntos/medicamentos/cmed/precos",
            # Protocol downgrade
            "http://www.sanidad.gob.es/areas/farmacia/infoIndustria/SPRAH/preciosMenoresAH/home.htm"
        ]
        for url in tampered_urls:
            self.assertFalse(is_roche_approved_url(url), f"Should have rejected tampered URL: {url}")
            with self.assertRaises(UnapprovedSourceError):
                validate_roche_source_url(url)

if __name__ == "__main__":
    unittest.main()
