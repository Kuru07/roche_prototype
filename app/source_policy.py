import re
from urllib.parse import urlparse, urljoin
from pathlib import Path
import requests

class RocheSecurityPolicyError(Exception):
    """Raised when an unapproved, substituted, or unlisted source is attempted."""
    pass

class UnapprovedSourceError(RocheSecurityPolicyError):
    pass

# Authoritative Roche-approved URLs extracted verbatim from roche_source_inventory.md
# Only URLs in this allowlist are permitted anywhere in the system.
ROCHE_APPROVED_URLS = [
    # Argentina (Preserved in inventory; excluded from calculation due to currency volatility)
    "https://arg.kairosweb.com/",
    # Brasil
    "https://www.gov.br/anvisa/pt-br/assuntos/medicamentos/cmed/precos",
    "https://bra.kairosweb.com/",
    # Bolivia
    "https://www.agemed.gob.bo/#dtu/contenido",
    # Paraguay
    "https://dinavisa.gov.py/precio-maximo-rep-fijado-por-la-dinavisa/",
    "https://cafapar.com.py/buscador_productos.php?search=",
    "https://dinavisa.gov.py/wp-content/uploads/2024/11/GUIA_DINAVISA_PARA_BUSQUEDA_DE_PRECIOS_REFERENCIALES.pdf",
    # Nicaragua
    "https://www.mific.gob.ni/Inicio/Servicios/Regulaci%C3%B3n-de-Precios-de-Medicamentos",
    # Chile
    "https://www.remediosmasbaratos.cl/",
    "https://chi.kairosweb.com/",
    "https://www.cenabast.cl/lista-de-medicamentos-ley-cenabast/?tribe_event_display=past&tribe_paged=3#:~:text=Navegador%20de%20p%C3%A1ginas:,-Tablero%20de%20precios&text=Nota:%20Los%20precios%20indicados%20corresponden,fecha%20de%20actualizaci%C3%B3n%20del%20reporte.",
    # Perú
    "https://opm-digemid.minsa.gob.pe/#/consulta-producto",
    "https://per.kairosweb.com/",
    # Estados Unidos
    "https://www.drugs.com/price-guide/",
    "https://www.pharmacychecker.com/#!",
    # Alemania
    "https://www.bfarm.de/EN/Medicinal-products/Information-on-medicinal-products/Reference-Pricing/_node.html",
    # Bélgica
    "https://www.inami.fgov.be/fr/themes/soins-de-sante-cout-et-remboursement/les-prestations-de-sante-que-vous-rembourse-votre-mutualite/medicaments/remboursement-d-un-medicament/specialites-pharmaceutiques-remboursables/specialites-pharmaceutiques-remboursables-listes-et-fichiers-de-reference",
    "https://webappsa.riziv-inami.fgov.be/ssp/ProductSearch",
    # España
    "https://www.sanidad.gob.es/areas/farmacia/infoIndustria/SPRAH/preciosMenoresAH/home.htm",
    "https://nomenclator.org/#gsc.tab=0",
    "https://www.sanidad.gob.es/campannas/campanas10/medicamentosGenericosEFG/conoce-precio-medicamento.html",
    "https://prvademecum.es/",
    "https://www.sanidad.gob.es/areas/farmacia/infoMedicamentos/home.htm",
    "https://www.sanidad.gob.es/profesionales/medicamentos.do",
    # Francia
    "https://base-donnees-publique.medicaments.gouv.fr/",
    # Holanda
    "https://www.medicijnkosten.nl/zoeken",
    # Italia
    "https://www.aifa.gov.it/liste-di-trasparenza",
    # Grecia
    "https://www.eof.gr/anazitisi-proionton/",
    "https://services.eof.gr/human-search/home.xhtml",
    # Letonia
    "https://dati.zva.gov.lv/zalu-registrs/en",
    # Portugal
    "https://www.infarmed.pt/web/infarmed/precos-de-medicamentos",
    # Suecia
    "https://www.tlv.se/in-english/prices-in-our-database.html",
    # República Checa
    "https://prehledy.sukl.cz/prehled_leciv.html#/",
    # Australia
    "https://www.pbs.gov.au/info/about-the-pbs#:~:text=Following%20a%20reduction%20in%20the,and%20certain%20other%20allowable%20charges).",
    # Arabia Saudita
    "https://www.sfda.gov.sa/en/drugs-list",
    # Canadá
    "https://www.formulary.health.gov.on.ca/formulary/",
    "https://www.canadapharmacy.com/es/blog/donde-puedo-encontrar-medicinas-mas-baratas",
    "https://www.pharmacychecker.com/es/accredited-online-pharmacies/",
    # Chipre
    "https://www.gov.cy/moh/en/",
    # El Salvador
    "https://info.srs.gob.sv/"
]

def _normalize_url(url: str) -> str:
    """
    Canonical normalization of URL for strict validation comparison only.
    Preserves exact query parameters, fragments (e.g. text fragments), and paths.
    Does NOT modify the stored inventory strings.
    """
    if not url:
        return ""
    u = (url or "").strip()
    # Clean markdown artifacts and escape sequences
    u = u.replace("\\)", ")").replace("\\-", "-")
    
    parsed = urlparse(u)
    if not parsed.scheme or not parsed.netloc:
        return u.lower()
    
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    
    # Preserve exact path (only normalize multiple consecutive slashes, e.g. // -> /)
    path = re.sub(r"/+", "/", parsed.path) if parsed.path else "/"
    # Only trim trailing slash if path is longer than '/' and there is no query or fragment
    if not parsed.query and not parsed.fragment and len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    
    normalized = f"{scheme}://{netloc}{path}"
    if parsed.query:
        normalized += f"?{parsed.query}"
    if parsed.fragment:
        normalized += f"#{parsed.fragment}"
    return normalized

# Precompute normalized allowlist
_NORMALIZED_ALLOWLIST = set(_normalize_url(u) for u in ROCHE_APPROVED_URLS)

def is_roche_approved_url(url: str) -> bool:
    """Returns True if the URL is strictly present in the approved Roche inventory."""
    if not url:
        return False
    norm = _normalize_url(url)
    return norm in _NORMALIZED_ALLOWLIST

def validate_roche_source_url(url: str, country: str = "") -> str:
    """
    Enforces the strict Roche business requirement:
    Only sources explicitly provided in roche_source_inventory.md are permitted.
    All unlisted, suggested, or substituted URLs are strictly rejected.
    """
    if not is_roche_approved_url(url):
        raise UnapprovedSourceError(
            f"REJECTED BY STRICT ROCHE SOURCE POLICY: The URL '{url}' is not on the "
            f"official Roche Source Inventory (roche_source_inventory.md). "
            f"Adding, suggesting, discovering, or substituting unapproved sources is strictly prohibited."
        )
    # Secondary guideline PDF links (e.g. from observation notes) are rejected for price data ingestion
    if "guia_dinavisa" in url.lower() or (country and country.lower() == "paraguay" and url.lower().endswith(".pdf")):
        raise UnapprovedSourceError(
            f"REJECTED BY STRICT ROCHE SOURCE POLICY: URL '{url}' is an observation reference document, "
            f"not an approved price data ingestion source for Paraguay. Only the exact HTML table endpoint is approved."
        )
    return url

def _validate_redirect_destination(initial_url: str, destination_url: str) -> None:
    """
    Enforces strict redirect security:
    Validates redirect destinations BEFORE following.
    Blocks unapproved domains, unapproved subdomains, and HTTP protocol downgrades.
    """
    if not destination_url:
        return
    
    init_parsed = urlparse(initial_url)
    dest_parsed = urlparse(destination_url)
    
    # 1. Block HTTP protocol downgrade (https -> http)
    if init_parsed.scheme == "https" and dest_parsed.scheme == "http":
        raise UnapprovedSourceError(
            f"REJECTED BY STRICT ROCHE SOURCE POLICY: Insecure HTTP protocol downgrade in redirect "
            f"from '{initial_url}' to '{destination_url}'."
        )
    
    init_host = init_parsed.netloc.lower().split(":")[0]
    dest_host = dest_parsed.netloc.lower().split(":")[0]
    
    # 2. Block redirect if destination host is different (unapproved domain or subdomain)
    if init_host != dest_host:
        # Check if the destination host/URL is explicitly in the approved allowlist
        if not is_roche_approved_url(destination_url):
            raise UnapprovedSourceError(
                f"REJECTED BY STRICT ROCHE SOURCE POLICY: Redirect from approved URL '{initial_url}' "
                f"to unapproved host/domain '{dest_host}' ({destination_url}) is strictly blocked."
            )
    
    # 3. If on the same host, ensure the destination URL itself is approved
    if not is_roche_approved_url(destination_url):
        if _normalize_url(destination_url) not in _NORMALIZED_ALLOWLIST:
            raise UnapprovedSourceError(
                f"REJECTED BY STRICT ROCHE SOURCE POLICY: Redirect from approved URL '{initial_url}' "
                f"to unapproved destination '{destination_url}' is strictly blocked."
            )

def safe_request_get(url: str, max_redirects: int = 5, **kwargs) -> requests.Response:
    """
    Performs an HTTP GET request with strict Roche source policy enforcement:
    1. Validates that the requested URL is present in the approved Roche allowlist.
    2. Disables automatic redirects (allow_redirects=False).
    3. Manually inspects and validates every redirect destination against the allowlist
       BEFORE following each redirect hop. Never sends a request to an unapproved destination.
    """
    validate_roche_source_url(url)
    
    current_url = url
    kwargs_copy = dict(kwargs)
    kwargs_copy["allow_redirects"] = False
    
    history = []
    redirect_count = 0
    
    while True:
        resp = requests.get(current_url, **kwargs_copy)
        
        # Check if response is an HTTP redirect (301, 302, 303, 307, 308)
        if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
            redirect_count += 1
            if redirect_count > max_redirects:
                raise UnapprovedSourceError(
                    f"Security Policy Rejection: Exceeded maximum allowed redirects ({max_redirects}) from '{url}'."
                )
            
            location = resp.headers.get("Location")
            if not location:
                resp.history = history
                return resp
            
            # Resolve relative redirect URLs against current URL
            next_url = urljoin(current_url, location)
            
            # CRITICAL SECURITY GATE: Validate redirect destination BEFORE making request
            _validate_redirect_destination(url, next_url)
            
            history.append(resp)
            current_url = next_url
        else:
            resp.history = history
            return resp

# Authoritative country statuses derived strictly from roche_source_inventory.md
# Countries with NO URL provided in the inventory are marked NO_ROCHE_APPROVED_SOURCE.
COUNTRY_INVENTORY_METADATA = {
    "Argentina": {
        "status": "EXCLUDED_BY_TEAM",
        "observation": "No se toma en cuenta el precio de este país debido a la volatilidad de su moneda",
        "has_approved_url": True,
        "is_excluded": True,
        "exclusion_reason": "Currency Volatility Excluded",
        "pricing_definition": "PVP / Farmacia"
    },
    "Brasil": {
        "status": "APPROVED_ROCHE_SOURCE",
        "observation": "-",
        "has_approved_url": True,
        "is_excluded": False,
        "pricing_definition": "PMF (Máximo Fabricante) & PMC (Máximo Consumidor)"
    },
    "Bolivia": {
        "status": "PENDING_REVIEW",
        "observation": "Son precios referenciales de LINAME por lo tanto son PVP o más bajos?",
        "has_approved_url": True,
        "is_excluded": False,
        "pricing_definition": "Precios Referenciales LINAME (Ambiguity: PVP vs Tender)"
    },
    "Paraguay": {
        "status": "APPROVED_ROCHE_SOURCE",
        "observation": "Fija precios máximo al consumidor final en base a precios de referencia internacional.",
        "has_approved_url": True,
        "is_excluded": False,
        "pricing_definition": "Precio Máximo Consumidor Final"
    },
    "Uruguay": {
        "status": "NO_ROCHE_APPROVED_SOURCE",
        "observation": "Ya no hay kairos",
        "has_approved_url": False,
        "is_excluded": True,
        "pricing_definition": None
    },
    "Venezuela": {
        "status": "EXCLUDED_BY_TEAM",
        "observation": "No se toma en cuenta el precio de este país debido a la volatilidad de su moneda",
        "has_approved_url": False,
        "is_excluded": True,
        "exclusion_reason": "Currency Volatility Excluded (No Source in Inventory)",
        "pricing_definition": None
    },
    "Cuba": {
        "status": "NO_ROCHE_APPROVED_SOURCE",
        "observation": "No hay fuente de búsqueda",
        "has_approved_url": False,
        "is_excluded": True,
        "pricing_definition": None
    },
    "Nicaragua": {
        "status": "APPROVED_ROCHE_SOURCE",
        "observation": "Regula precios maximos al consumidor final",
        "has_approved_url": True,
        "is_excluded": False,
        "pricing_definition": "Precio Máximo Consumidor Final"
    },
    "Mancomunidad de Dominica": {
        "status": "NO_ROCHE_APPROVED_SOURCE",
        "observation": "No hay fuente de búsqueda",
        "has_approved_url": False,
        "is_excluded": True,
        "pricing_definition": None
    },
    "San Vicente y las Granadinas": {
        "status": "NO_ROCHE_APPROVED_SOURCE",
        "observation": "No hay fuente de búsqueda",
        "has_approved_url": False,
        "is_excluded": True,
        "pricing_definition": None
    },
    "Antigua y Barbuda": {
        "status": "NO_ROCHE_APPROVED_SOURCE",
        "observation": "No hay fuente de búsqueda",
        "has_approved_url": False,
        "is_excluded": True,
        "pricing_definition": None
    },
    "Santa Lucía": {
        "status": "NO_ROCHE_APPROVED_SOURCE",
        "observation": "No hay fuente de búsqueda",
        "has_approved_url": False,
        "is_excluded": True,
        "pricing_definition": None
    },
    "Chile": {
        "status": "APPROVED_ROCHE_SOURCE",
        "observation": "Fijan precios máximos de venta al público",
        "has_approved_url": True,
        "is_excluded": False,
        "pricing_definition": "PVP Máximo (Ley Cenabast)"
    },
    "Colombia": {
        "status": "NO_ROCHE_APPROVED_SOURCE",
        "observation": "Fijan precios máximos al distribuidor",
        "has_approved_url": False,
        "is_excluded": False,
        "pricing_definition": "Precio Máximo al Distribuidor (No URL provided by Roche)"
    },
    "Guyana": {
        "status": "NO_ROCHE_APPROVED_SOURCE",
        "observation": "-",
        "has_approved_url": False,
        "is_excluded": True,
        "pricing_definition": None
    },
    "Perú": {
        "status": "APPROVED_ROCHE_SOURCE",
        "observation": "-",
        "has_approved_url": True,
        "is_excluded": False,
        "pricing_definition": "Observatorio DIGEMID & PVP K@iros"
    },
    "Surinam": {
        "status": "NO_ROCHE_APPROVED_SOURCE",
        "observation": "-",
        "has_approved_url": False,
        "is_excluded": True,
        "pricing_definition": None
    },
    "Estados Unidos": {
        "status": "APPROVED_ROCHE_SOURCE",
        "observation": "-",
        "has_approved_url": True,
        "is_excluded": False,
        "pricing_definition": "U.S. Cash Price & Mail-Order Aggregator"
    },
    "Alemania": {
        "status": "APPROVED_ROCHE_SOURCE",
        "observation": "-",
        "has_approved_url": True,
        "is_excluded": False,
        "pricing_definition": "Festbeträge (Reference Price Groups)"
    },
    "Bélgica": {
        "status": "APPROVED_ROCHE_SOURCE",
        "observation": "-",
        "has_approved_url": True,
        "is_excluded": False,
        "pricing_definition": "Prix Ex-Factory & Prix Base de Remboursement"
    },
    "España": {
        "status": "APPROVED_ROCHE_SOURCE",
        "observation": "El precio PVL indica el precio máximo industrial o el precio laboratorio y, el precio PVP IVA correspondiente al Precio de Venta al Público con IVA. Se debe considerar el PVP con IVA como precio de referencia",
        "has_approved_url": True,
        "is_excluded": False,
        "pricing_definition": "PVP IVA (Target) & PVL (Industrial)"
    },
    "Francia": {
        "status": "APPROVED_ROCHE_SOURCE",
        "observation": "Tomar el precio “Prix hors honoraiire de dispensation”",
        "has_approved_url": True,
        "is_excluded": False,
        "pricing_definition": "Prix hors honoraire de dispensation"
    },
    "Holanda": {
        "status": "APPROVED_ROCHE_SOURCE",
        "observation": "-",
        "has_approved_url": True,
        "is_excluded": False,
        "pricing_definition": "Apotheekinkoopprijs (AIP) & Vergoeding"
    },
    "Italia": {
        "status": "APPROVED_ROCHE_SOURCE",
        "observation": "Una vez descargada la base de datos, buscar la presentación y el principio activo de interés. El precio de referencia será el que aparezca en la columna “Prezzo Pubblico”.",
        "has_approved_url": True,
        "is_excluded": False,
        "pricing_definition": "Prezzo Pubblico (Isolate from transparency Excel)"
    },
    "Grecia": {
        "status": "APPROVED_ROCHE_SOURCE",
        "observation": "-",
        "has_approved_url": True,
        "is_excluded": False,
        "pricing_definition": "Hospital, Wholesale & Retail Prices"
    },
    "Letonia": {
        "status": "APPROVED_ROCHE_SOURCE",
        "observation": "-",
        "has_approved_url": True,
        "is_excluded": False,
        "pricing_definition": "Wholesale Price & Pharmacy Price"
    },
    "Portugal": {
        "status": "APPROVED_ROCHE_SOURCE",
        "observation": "-",
        "has_approved_url": True,
        "is_excluded": False,
        "pricing_definition": "PVA (Armazenista) & PVP (Público)"
    },
    "Suecia": {
        "status": "APPROVED_ROCHE_SOURCE",
        "observation": "-",
        "has_approved_url": True,
        "is_excluded": False,
        "pricing_definition": "AIP (Pharmacy Purchase) & AUP (Pharmacy Retail)"
    },
    "República Checa": {
        "status": "APPROVED_ROCHE_SOURCE",
        "observation": "-",
        "has_approved_url": True,
        "is_excluded": False,
        "pricing_definition": "Maximum Price (MFC) & Reimbursement (UHR)"
    },
    "Australia": {
        "status": "APPROVED_ROCHE_SOURCE",
        "observation": "-",
        "has_approved_url": True,
        "is_excluded": False,
        "pricing_definition": "Ex-Manufacturer, PTP & DPMQ"
    },
    "Arabia Saudita": {
        "status": "APPROVED_ROCHE_SOURCE",
        "observation": "-",
        "has_approved_url": True,
        "is_excluded": False,
        "pricing_definition": "Registered Public Retail Price (SAR)"
    },
    "Canadá": {
        "status": "PENDING_REVIEW",
        "observation": "Tener en cuenta que el precio que se visualiza es por unidad de dispensación",
        "has_approved_url": True,
        "is_excluded": False,
        "pricing_definition": "Price per Dispensing Unit (Unit normalizer required)"
    },
    "Chipre": {
        "status": "APPROVED_ROCHE_SOURCE",
        "observation": "-",
        "has_approved_url": True,
        "is_excluded": False,
        "pricing_definition": "Wholesale & Retail Pricelists"
    },
    "El Salvador": {
        "status": "APPROVED_ROCHE_SOURCE",
        "observation": "-",
        "has_approved_url": True,
        "is_excluded": False,
        "pricing_definition": "Precios Máximos de Venta al Público (PVP)"
    },
    "Líbano": {
        "status": "NO_ROCHE_APPROVED_SOURCE",
        "observation": "-",
        "has_approved_url": False,
        "is_excluded": True,
        "pricing_definition": None
    },
    "Noruega": {
        "status": "NO_ROCHE_APPROVED_SOURCE",
        "observation": "-",
        "has_approved_url": False,
        "is_excluded": True,
        "pricing_definition": None
    },
    "Ecuador": {
        "status": "NO_ROCHE_APPROVED_SOURCE",
        "observation": "País objetivo de regulación en el proyecto; sin fuente de precio de referencia provista en el inventario Roche",
        "has_approved_url": False,
        "is_excluded": False,
        "pricing_definition": "Target Country / Missing Reference-Source Status (Not Excluded from Project)"
    }
}

COUNTRY_ALIASES = {
    "spain": "España",
    "brazil": "Brasil",
    "germany": "Alemania",
    "france": "Francia",
    "italy": "Italia",
    "canada": "Canadá",
    "united states": "Estados Unidos",
    "usa": "Estados Unidos",
    "us": "Estados Unidos",
    "peru": "Perú",
    "czech republic": "República Checa",
    "cyprus": "Chipre",
    "netherlands": "Holanda",
    "the netherlands": "Holanda",
    "sweden": "Suecia",
    "latvia": "Letonia",
    "greece": "Grecia",
    "belgium": "Bélgica",
    "norway": "Noruega",
    "lebanon": "Líbano",
    "libano": "Líbano"
}

def get_country_policy_info(country: str) -> dict:
    """Returns the strict Roche policy status and metadata for a country."""
    c = country.strip()
    if c in COUNTRY_INVENTORY_METADATA:
        return dict(COUNTRY_INVENTORY_METADATA[c])
    
    # Check aliases
    c_lower = c.lower()
    if c_lower in COUNTRY_ALIASES:
        canonical = COUNTRY_ALIASES[c_lower]
        if canonical in COUNTRY_INVENTORY_METADATA:
            return dict(COUNTRY_INVENTORY_METADATA[canonical])
    
    # Try normalized match
    for k, v in COUNTRY_INVENTORY_METADATA.items():
        if k.lower() == c_lower:
            return dict(v)
            
    return {
        "status": "NO_ROCHE_APPROVED_SOURCE",
        "observation": "Country not recognized in Roche-provided source inventory",
        "has_approved_url": False,
        "is_excluded": True,
        "pricing_definition": None
    }
