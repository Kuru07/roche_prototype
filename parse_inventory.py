import re
import json

with open("roche_source_inventory.md", "r", encoding="utf-8") as f:
    text = f.read()

# Let's verify our complete structured extraction
inventory = [
    {
        "country": "Argentina",
        "regional_bloc": "Mercosur",
        "observation": "No se toma en cuenta el precio de este país debido a la volatilidad de su moneda",
        "exclusion": True,
        "exclusion_type": "Currency Volatility Excluded",
        "sources": [
            {
                "institution": "Revista K@iros",
                "category": "Commercial Price Portal",
                "url": "https://arg.kairosweb.com/",
                "price_types": ["PVP", "Comercial de Farmacia"],
                "adapter": "HtmlTableListAdapter / Web Scraper",
                "review_required": True,
                "review_reason": "Excluded country (currency volatility); paywalled / auth protected portal"
            }
        ]
    },
    {
        "country": "Brasil",
        "regional_bloc": "Mercosur",
        "observation": "-",
        "exclusion": False,
        "sources": [
            {
                "institution": "ANVISA / CMED",
                "category": "Government / Regulatory",
                "url": "https://www.gov.br/anvisa/pt-br/assuntos/medicamentos/cmed/precos",
                "price_types": ["PMF (Preço Máximo ao Fabricante)", "PMC (Preço Máximo ao Consumidor)"],
                "adapter": "SpreadsheetAdapter (XLSX / CSV)",
                "review_required": False,
                "review_reason": "Official CMED monthly price table download"
            },
            {
                "institution": "Revista K@iros",
                "category": "Commercial Price Portal",
                "url": "https://bra.kairosweb.com/",
                "price_types": ["PMC", "Preço Farmácia"],
                "adapter": "HtmlTableListAdapter / Web Scraper",
                "review_required": True,
                "review_reason": "Subscription portal / search form"
            }
        ]
    },
    {
        "country": "Bolivia",
        "regional_bloc": "Mercosur / ALBA / UNASUR",
        "observation": "Son precios referenciales de LINAME por lo tanto son PVP o más bajos?",
        "exclusion": False,
        "sources": [
            {
                "institution": "Agencia Estatal de Medicamentos y Tecnología en Salud (AGEMED)",
                "category": "Government / Regulatory",
                "url": "https://www.agemed.gob.bo/#dtu/contenido",
                "price_types": ["Precios Referenciales LINAME (Lista Nacional de Medicamentos Esenciales)"],
                "adapter": "PdfDocumentAdapter / HtmlTableListAdapter",
                "review_required": True,
                "review_reason": "Ambiguity noted by team: verify whether LINAME prices represent PVP or institutional public tender ceilings"
            }
        ]
    },
    {
        "country": "Paraguay",
        "regional_bloc": "Mercosur / UNASUR",
        "observation": "Fija precios máximo al consumidor final en base a precios de referencia internacional. https://dinavisa.gov.py/wp-content/uploads/2024/11/GUIA_DINAVISA_PARA_BUSQUEDA_DE_PRECIOS_REFERENCIALES.pdf",
        "exclusion": False,
        "sources": [
            {
                "institution": "Dirección Nacional de Vigilancia Sanitaria (DNVS / DINAVISA)",
                "category": "Government / Regulatory",
                "url": "https://dinavisa.gov.py/precio-maximo-rep-fijado-por-la-dinavisa/",
                "price_types": ["Precio Máximo al Consumidor Final (Fijado por DINAVISA)"],
                "adapter": "HtmlTableListAdapter / PdfDocumentAdapter",
                "review_required": False,
                "review_reason": "Official statutory price ceiling portal"
            },
            {
                "institution": "Cámara de Farmacias de Paraguay (CAFAPAR)",
                "category": "Commercial / Trade Association",
                "url": "https://cafapar.com.py/buscador_productos.php?search=",
                "price_types": ["Precio de Venta en Farmacia"],
                "adapter": "HtmlTableListAdapter / RestApiAdapter",
                "review_required": True,
                "review_reason": "Dynamic search form querying private pharmacy retail prices"
            },
            {
                "institution": "DINAVISA (Guía de Búsqueda de Precios Referenciales)",
                "category": "Government Regulatory Guideline (RAG)",
                "url": "https://dinavisa.gov.py/wp-content/uploads/2024/11/GUIA_DINAVISA_PARA_BUSQUEDA_DE_PRECIOS_REFERENCIALES.pdf",
                "price_types": ["Methodology / Regulatory Search Guide"],
                "adapter": "PdfDocumentAdapter (FAISS RAG)",
                "review_required": False,
                "review_reason": "Official regulatory search methodology manual"
            }
        ]
    },
    {
        "country": "Uruguay",
        "regional_bloc": "Mercosur / UNASUR",
        "observation": "Ya no hay kairos",
        "exclusion": True,
        "exclusion_type": "NO_ROCHE_APPROVED_SOURCE",
        "status": "NO_ROCHE_APPROVED_SOURCE",
        "sources": []
    },
    {
        "country": "Venezuela",
        "regional_bloc": "Mercosur / ALBA / UNASUR",
        "observation": "No se toma en cuenta el precio de este país debido a la volatilidad de su moneda",
        "exclusion": True,
        "exclusion_type": "NO_ROCHE_APPROVED_SOURCE",
        "status": "NO_ROCHE_APPROVED_SOURCE",
        "sources": []
    },
    {
        "country": "Cuba",
        "regional_bloc": "ALBA",
        "observation": "No hay fuente de búsqueda",
        "exclusion": True,
        "exclusion_type": "NO_ROCHE_APPROVED_SOURCE",
        "status": "NO_ROCHE_APPROVED_SOURCE",
        "sources": []
    },
    {
        "country": "Nicaragua",
        "regional_bloc": "ALBA",
        "observation": "Regula precios maximos al consumidor final",
        "exclusion": False,
        "sources": [
            {
                "institution": "Ministerio de Fomento, Industria y Comercio (MIFIC)",
                "category": "Government / Regulatory",
                "url": "https://www.mific.gob.ni/Inicio/Servicios/Regulaci%C3%B3n-de-Precios-de-Medicamentos",
                "price_types": ["Precio Máximo al Consumidor Final"],
                "adapter": "HtmlTableListAdapter / PdfDocumentAdapter",
                "review_required": True,
                "review_reason": "MIFIC regulations published in periodic PDF circulars"
            }
        ]
    },
    {
        "country": "Mancomunidad de Dominica",
        "regional_bloc": "ALBA",
        "observation": "No hay fuente de búsqueda",
        "exclusion": True,
        "exclusion_type": "NO_ROCHE_APPROVED_SOURCE",
        "status": "NO_ROCHE_APPROVED_SOURCE",
        "sources": []
    },
    {
        "country": "San Vicente y las Granadinas",
        "regional_bloc": "ALBA",
        "observation": "No hay fuente de búsqueda",
        "exclusion": True,
        "exclusion_type": "NO_ROCHE_APPROVED_SOURCE",
        "status": "NO_ROCHE_APPROVED_SOURCE",
        "sources": []
    },
    {
        "country": "Antigua y Barbuda",
        "regional_bloc": "ALBA",
        "observation": "No hay fuente de búsqueda",
        "exclusion": True,
        "exclusion_type": "NO_ROCHE_APPROVED_SOURCE",
        "status": "NO_ROCHE_APPROVED_SOURCE",
        "sources": []
    },
    {
        "country": "Santa Lucía",
        "regional_bloc": "ALBA",
        "observation": "No hay fuente de búsqueda",
        "exclusion": True,
        "exclusion_type": "NO_ROCHE_APPROVED_SOURCE",
        "status": "NO_ROCHE_APPROVED_SOURCE",
        "sources": []
    },
    {
        "country": "Chile",
        "regional_bloc": "UNASUR",
        "observation": "Fijan precios máximos de venta al público",
        "exclusion": False,
        "sources": [
            {
                "institution": "CENABAST - Remedios Más Baratos",
                "category": "Government / Public Transparency Portal",
                "url": "https://www.remediosmasbaratos.cl/",
                "price_types": ["Precio Máximo de Venta al Público (Ley Cenabast)"],
                "adapter": "RestApiAdapter / HtmlTableListAdapter",
                "review_required": True,
                "review_reason": "Client-rendered web search application querying CENABAST ceiling prices"
            },
            {
                "institution": "CENABAST - Lista de Medicamentos Ley Cenabast",
                "category": "Institutional Procurement / Public Price Ceiling",
                "url": "https://www.cenabast.cl/lista-de-medicamentos-ley-cenabast/?tribe_event_display=past&tribe_paged=3#:~:text=Navegador%20de%20p%C3%A1ginas:,-Tablero%20de%20precios&text=Nota:%20Los%20precios%20indicados%20corresponden,fecha%20de%20actualizaci%C3%B3n%20del%20reporte.",
                "price_types": ["Precio Máximo Ley Cenabast / Precio Adquisición Institucional"],
                "adapter": "HtmlTableListAdapter / SpreadsheetAdapter",
                "review_required": True,
                "review_reason": "Tablero / report with pagination parameters"
            },
            {
                "institution": "Revista K@iros Chile",
                "category": "Commercial Price Portal",
                "url": "https://chi.kairosweb.com/",
                "price_types": ["PVP Farmacia"],
                "adapter": "HtmlTableListAdapter / Web Scraper",
                "review_required": True,
                "review_reason": "Commercial pharmacy list price directory"
            }
        ]
    },
    {
        "country": "Colombia",
        "regional_bloc": "UNASUR",
        "observation": "Fijan precios máximos al distribuidor",
        "exclusion": True,
        "exclusion_type": "NO_ROCHE_APPROVED_SOURCE",
        "status": "NO_ROCHE_APPROVED_SOURCE",
        "sources": []
    },
    {
        "country": "Guyana",
        "regional_bloc": "UNASUR",
        "observation": "-",
        "exclusion": True,
        "exclusion_type": "NO_ROCHE_APPROVED_SOURCE",
        "status": "NO_ROCHE_APPROVED_SOURCE",
        "sources": []
    },
    {
        "country": "Peru",
        "regional_bloc": "UNASUR",
        "observation": "-",
        "exclusion": False,
        "sources": [
            {
                "institution": "Dirección General de Medicamentos, Insumos y Drogas (DIGEMID)",
                "category": "Government / Regulatory",
                "url": "https://opm-digemid.minsa.gob.pe/#/consulta-producto",
                "price_types": ["Observatorio de Precios de Medicamentos (PVP / Institucional)"],
                "adapter": "RestApiAdapter / Web Scraper",
                "review_required": True,
                "review_reason": "Single Page Application (SPA / Angular hash router) backed by internal REST endpoint"
            },
            {
                "institution": "Revista K@iros Peru",
                "category": "Commercial Price Portal",
                "url": "https://per.kairosweb.com/",
                "price_types": ["PVP Farmacia"],
                "adapter": "HtmlTableListAdapter / Web Scraper",
                "review_required": True,
                "review_reason": "Commercial pharmacy database"
            }
        ]
    },
    {
        "country": "Surinam",
        "regional_bloc": "UNASUR",
        "observation": "-",
        "exclusion": True,
        "exclusion_type": "NO_ROCHE_APPROVED_SOURCE",
        "status": "NO_ROCHE_APPROVED_SOURCE",
        "sources": []
    },
    {
        "country": "Estados Unidos",
        "regional_bloc": "Norteamérica",
        "observation": "-",
        "exclusion": False,
        "sources": [
            {
                "institution": "Drugs.com Price Guide",
                "category": "Commercial Medicine Price Portal",
                "url": "https://www.drugs.com/price-guide/",
                "price_types": ["U.S. Cash Price / Retail Pharmacy Estimates"],
                "adapter": "HtmlTableListAdapter / Web Scraper",
                "review_required": True,
                "review_reason": "Commercial cash discount/coupon prices; highly fragmented and unrepresentative of statutory net prices"
            },
            {
                "institution": "PharmacyChecker",
                "category": "Commercial Medicine Price Portal",
                "url": "https://www.pharmacychecker.com/#!",
                "price_types": ["Online International & U.S. Mail Order Price Comparison"],
                "adapter": "HtmlTableListAdapter / Web Scraper",
                "review_required": True,
                "review_reason": "International mail-order price comparison aggregator"
            }
        ]
    },
    {
        "country": "Alemania",
        "regional_bloc": "Unión Europea",
        "observation": "-",
        "exclusion": False,
        "sources": [
            {
                "institution": "Institute of Medical Documentation and Information (DIMDI / BfArM)",
                "category": "Government / Regulatory",
                "url": "https://www.bfarm.de/EN/Medicinal-products/Information-on-medicinal-products/Reference-Pricing/_node.html",
                "price_types": ["Festbeträge (Reference Price Groups / Reimbursement Ceilings)"],
                "adapter": "HtmlTableListAdapter / SpreadsheetAdapter",
                "review_required": False,
                "review_reason": "Official statutory reference price clusters"
            }
        ]
    },
    {
        "country": "Bélgica",
        "regional_bloc": "Unión Europea",
        "observation": "-",
        "exclusion": False,
        "sources": [
            {
                "institution": "Institut National d´assurance maladie - invalidité (INAMI) - Fichiers de Référence",
                "category": "Government / Regulatory & Reimbursement",
                "url": "https://www.inami.fgov.be/fr/themes/soins-de-sante-cout-et-remboursement/les-prestations-de-sante-que-vous-rembourse-votre-mutualite/medicaments/remboursement-d-un-medicament/specialites-pharmaceutiques-remboursables/specialites-pharmaceutiques-remboursables-listes-et-fichiers-de-reference",
                "price_types": ["Prix Ex-Factory / Base de Remboursement"],
                "adapter": "SpreadsheetAdapter (CSV / XLSX / TXT reference files)",
                "review_required": False,
                "review_reason": "Downloadable official pharmaceutical reimbursement database"
            },
            {
                "institution": "INAMI / RIZIV - ProductSearch WebApp",
                "category": "Government / Public Search Portal",
                "url": "https://webappsa.riziv-inami.fgov.be/ssp/ProductSearch",
                "price_types": ["Prix Public / Prix Base de Remboursement"],
                "adapter": "RestApiAdapter / HtmlTableListAdapter",
                "review_required": True,
                "review_reason": "Interactive web application for drug search"
            }
        ]
    },
    {
        "country": "España",
        "regional_bloc": "Unión Europea",
        "observation": "El precio PVL indica el precio máximo industrial o el precio laboratorio y, el precio PVP IVA correspondiente al Precio de Venta al Público con IVA. Se debe considerar el PVP con IVA como precio de referencia",
        "exclusion": False,
        "sources": [
            {
                "institution": "Ministerio de Sanidad (MSSSI) - Precios Menores AH",
                "category": "Government / Regulatory",
                "url": "https://www.sanidad.gob.es/areas/farmacia/infoIndustria/SPRAH/preciosMenoresAH/home.htm",
                "price_types": ["PVL (Precio Venta Laboratorio)", "PVP IVA (Precio Venta Público con IVA)"],
                "adapter": "SpreadsheetAdapter (Excel/CSV monthly tables)",
                "review_required": False,
                "review_reason": "Official reference price grouping and lower price tables"
            },
            {
                "institution": "Nomenclatura de Medicamentos (Nomenclator.org)",
                "category": "Commercial / Public Portal",
                "url": "https://nomenclator.org/#gsc.tab=0",
                "price_types": ["PVL", "PVP IVA"],
                "adapter": "HtmlTableListAdapter / Web Scraper",
                "review_required": True,
                "review_reason": "Community / commercial mirror of official SNS formulary"
            },
            {
                "institution": "Ministerio de Sanidad - Medicamentos Genéricos EFG",
                "category": "Government / Information Campaign",
                "url": "https://www.sanidad.gob.es/campannas/campanas10/medicamentosGenericosEFG/conoce-precio-medicamento.html",
                "price_types": ["PVP Informativo"],
                "adapter": "HtmlTableListAdapter",
                "review_required": True,
                "review_reason": "Educational campaign portal"
            },
            {
                "institution": "Vademecum Internacional España",
                "category": "Commercial Medicine Price Portal",
                "url": "https://prvademecum.es/",
                "price_types": ["PVL", "PVP IVA"],
                "adapter": "HtmlTableListAdapter / Web Scraper",
                "review_required": True,
                "review_reason": "Commercial drug directory (paywalled / auth required for advanced prices)"
            },
            {
                "institution": "Ministerio de Sanidad - Info Medicamentos",
                "category": "Government / Information Portal",
                "url": "https://www.sanidad.gob.es/areas/farmacia/infoMedicamentos/home.htm",
                "price_types": ["Precios de Referencia y Financiación"],
                "adapter": "HtmlTableListAdapter",
                "review_required": False,
                "review_reason": "Informational portal linking to monthly circulars"
            },
            {
                "institution": "Ministerio de Sanidad - Portal Profesionales",
                "category": "Government / Professional Portal",
                "url": "https://www.sanidad.gob.es/profesionales/medicamentos.do",
                "price_types": ["Nomenclátor Oficial de Facturación de Medicamentos"],
                "adapter": "HtmlTableListAdapter / SpreadsheetAdapter",
                "review_required": False,
                "review_reason": "Direct access point to monthly billing nomenclator files"
            }
        ]
    },
    {
        "country": "Francia",
        "regional_bloc": "Unión Europea",
        "observation": "Tomar el precio “Prix hors honoraiire de dispensation”",
        "exclusion": False,
        "sources": [
            {
                "institution": "Ministère des Affaires sociales et de la Santé - Base de Données Publique des Médicaments",
                "category": "Government / Regulatory",
                "url": "https://base-donnees-publique.medicaments.gouv.fr/",
                "price_types": ["Prix hors honoraire de dispensation (Mandatory per team observation)"],
                "adapter": "SpreadsheetAdapter (Downloadable raw text files) / RestApiAdapter",
                "review_required": False,
                "review_reason": "Official public drug database. Extraction must strictly select 'Prix hors honoraire de dispensation' excluding pharmacist margin."
            }
        ]
    },
    {
        "country": "Holanda",
        "regional_bloc": "Unión Europea",
        "observation": "-",
        "exclusion": False,
        "sources": [
            {
                "institution": "Zorginstituut Nederland (Medicijnkosten)",
                "category": "Government / Health Technology Assessment & Reimbursement",
                "url": "https://www.medicijnkosten.nl/zoeken",
                "price_types": ["Apotheekinkoopprijs (AIP / Pharmacy Purchase Price)", "Vergoeding (Reimbursement limit)"],
                "adapter": "RestApiAdapter / Web Scraper",
                "review_required": True,
                "review_reason": "Interactive search tool backed by Zorginstituut pricing API"
            }
        ]
    },
    {
        "country": "Italia",
        "regional_bloc": "Unión Europea",
        "observation": "Una vez descargada la base de datos, buscar la presentación y el principio activo de interés. El precio de referencia será el que aparezca en la columna “Prezzo Pubblico”.",
        "exclusion": False,
        "sources": [
            {
                "institution": "Agenzia Italiana del Farmaco (AIFA) - Liste di Trasparenza",
                "category": "Government / Regulatory",
                "url": "https://www.aifa.gov.it/liste-di-trasparenza",
                "price_types": ["Prezzo Pubblico (Mandatory column per team observation)", "Prezzo di Riferimento AIFA"],
                "adapter": "SpreadsheetAdapter (Monthly Excel / CSV transparency lists)",
                "review_required": False,
                "review_reason": "Downloadable spreadsheet database. Extraction must isolate column 'Prezzo Pubblico'."
            }
        ]
    },
    {
        "country": "Grecia",
        "regional_bloc": "Unión Europea",
        "observation": "-",
        "exclusion": False,
        "sources": [
            {
                "institution": "National Organisation for Medicines (EOF) - Anazitisi Proionton",
                "category": "Government / Regulatory",
                "url": "https://www.eof.gr/anazitisi-proionton/",
                "price_types": ["Hospital Price", "Wholesale Price", "Retail Price (PVP)"],
                "adapter": "HtmlTableListAdapter / SpreadsheetAdapter",
                "review_required": True,
                "review_reason": "Official drug bulletin and price bulletins"
            },
            {
                "institution": "EOF - Human Search Service",
                "category": "Government / Search Portal",
                "url": "https://services.eof.gr/human-search/home.xhtml",
                "price_types": ["Registered Prices"],
                "adapter": "Web Scraper / RestApiAdapter",
                "review_required": True,
                "review_reason": "JSF / PrimeFaces stateful search portal (session viewstate required)"
            }
        ]
    },
    {
        "country": "Letonia",
        "regional_bloc": "Unión Europea",
        "observation": "-",
        "exclusion": False,
        "sources": [
            {
                "institution": "Medicinal Product Register of Latvia (ZVA)",
                "category": "Government / Regulatory",
                "url": "https://dati.zva.gov.lv/zalu-registrs/en",
                "price_types": ["Maximum Wholesale Price", "Maximum Pharmacy Price (PVP)"],
                "adapter": "RestApiAdapter / SpreadsheetAdapter",
                "review_required": False,
                "review_reason": "Public register with open dataset downloads and search API"
            }
        ]
    },
    {
        "country": "Portugal",
        "regional_bloc": "Unión Europea",
        "observation": "-",
        "exclusion": False,
        "sources": [
            {
                "institution": "INFARMED - Preços de Medicamentos",
                "category": "Government / Regulatory",
                "url": "https://www.infarmed.pt/web/infarmed/precos-de-medicamentos",
                "price_types": ["PVA (Preço de Venda ao Armazenista / Wholesale)", "PVP (Preço de Venta ao Público)"],
                "adapter": "HtmlTableListAdapter / SpreadsheetAdapter",
                "review_required": False,
                "review_reason": "INFARMED monthly price ceiling tables and formulary"
            }
        ]
    },
    {
        "country": "Suecia",
        "regional_bloc": "Unión Europea",
        "observation": "-",
        "exclusion": False,
        "sources": [
            {
                "institution": "TLV (Tandvårds- och läkemedelsförmånsverket)",
                "category": "Government / Regulatory & Reimbursement",
                "url": "https://www.tlv.se/in-english/prices-in-our-database.html",
                "price_types": ["AIP (Apotekens inköpspris / Pharmacy Purchase Price)", "AUP (Apotekens utförsäljningspris / Pharmacy Retail Price)"],
                "adapter": "SpreadsheetAdapter (Downloadable Excel database) / RestApiAdapter",
                "review_required": False,
                "review_reason": "Direct download of complete national price database (Swedish Krone)"
            }
        ]
    },
    {
        "country": "República Checa",
        "regional_bloc": "Unión Europea",
        "observation": "-",
        "exclusion": False,
        "sources": [
            {
                "institution": "State Institute for Drug Control (SUKL)",
                "category": "Government / Regulatory",
                "url": "https://prehledy.sukl.cz/prehled_leciv.html#/",
                "price_types": ["Maximum Price (MFC)", "Maximum Reimbursement (UHR)"],
                "adapter": "RestApiAdapter / SpreadsheetAdapter",
                "review_required": True,
                "review_reason": "Client-side SPA portal; raw open data files available via SUKL open data directory"
            }
        ]
    },
    {
        "country": "Australia",
        "regional_bloc": "Resto del mundo",
        "observation": "-",
        "exclusion": False,
        "sources": [
            {
                "institution": "Pharmaceutical Benefits Scheme (PBS / Department of Health)",
                "category": "Government / Statutory Reimbursement",
                "url": "https://www.pbs.gov.au/info/about-the-pbs#:~:text=Following%20a%20reduction%20in%20the,and%20certain%20other%20allowable%20charges).",
                "price_types": ["Ex-Manufacturer Price", "Price to Pharmacist (PTP)", "Dispensed Price for Maximum Quantity (DPMQ)"],
                "adapter": "SpreadsheetAdapter (Monthly PBS text/CSV downloads) / HtmlTableListAdapter",
                "review_required": False,
                "review_reason": "Authoritative monthly PBS schedule downloads"
            }
        ]
    },
    {
        "country": "Arabia Saudita",
        "regional_bloc": "Resto del mundo",
        "observation": "-",
        "exclusion": False,
        "sources": [
            {
                "institution": "Saudi Food & Drug Authority (SFDA)",
                "category": "Government / Regulatory",
                "url": "https://www.sfda.gov.sa/en/drugs-list",
                "price_types": ["Registered Public Retail Price (SAR)"],
                "adapter": "RestApiAdapter / SpreadsheetAdapter",
                "review_required": True,
                "review_reason": "Online drugs list portal; searchable by trade name / active ingredient"
            }
        ]
    },
    {
        "country": "Canadá",
        "regional_bloc": "Resto del mundo",
        "observation": "Tener en cuenta que el precio que se visualiza es por unidad de dispensación",
        "exclusion": False,
        "sources": [
            {
                "institution": "Ontario Drug Benefit Formulary (ODB) / Comparative Drug Index",
                "category": "Government / Provincial Formulary",
                "url": "https://www.formulary.health.gov.on.ca/formulary/",
                "price_types": ["Drug Benefit Price per Dispensing Unit (Mandatory per team observation)"],
                "adapter": "HtmlTableListAdapter / SpreadsheetAdapter",
                "review_required": True,
                "review_reason": "Prices are strictly per dispensing unit (tablet, mL, vial); normalization must convert to pack price if pack size is present."
            },
            {
                "institution": "Canada Pharmacy Blog",
                "category": "Commercial Pharmacy Blog",
                "url": "https://www.canadapharmacy.com/es/blog/donde-puedo-encontrar-medicinas-mas-baratas",
                "price_types": ["Informational Blog Post"],
                "adapter": "HtmlTableListAdapter / Text Parser",
                "review_required": True,
                "review_reason": "Commercial blog post, NOT a structured pricing database"
            },
            {
                "institution": "PharmacyChecker (Accredited Online Pharmacies)",
                "category": "Commercial Aggregator",
                "url": "https://www.pharmacychecker.com/es/accredited-online-pharmacies/",
                "price_types": ["Online Pharmacy Directory"],
                "adapter": "HtmlTableListAdapter / Web Scraper",
                "review_required": True,
                "review_reason": "Accreditation directory / commercial marketing portal"
            }
        ]
    },
    {
        "country": "Chipre",
        "regional_bloc": "Resto del mundo",
        "observation": "-",
        "exclusion": False,
        "sources": [
            {
                "institution": "Ministerio de Salud de Chipre (MOH)",
                "category": "Government / Regulatory",
                "url": "https://www.gov.cy/moh/en/",
                "price_types": ["Pricelist of Medicinal Products for Human Use (Wholesale & Retail)"],
                "adapter": "PdfDocumentAdapter / SpreadsheetAdapter",
                "review_required": True,
                "review_reason": "Ministry homepage; price lists are published as downloadable PDFs and Excel sheets under Pharmaceutical Services section"
            }
        ]
    },
    {
        "country": "El Salvador",
        "regional_bloc": "Resto del mundo",
        "observation": "-",
        "exclusion": False,
        "sources": [
            {
                "institution": "Ministerio de Salud El Salvador / Superintendencia de Regulación Sanitaria (SRS)",
                "category": "Government / Regulatory",
                "url": "https://info.srs.gob.sv/",
                "price_types": ["Precios Máximos de Venta al Público (Ley de Medicamentos)"],
                "adapter": "HtmlTableListAdapter / PdfDocumentAdapter",
                "review_required": True,
                "review_reason": "SRS regulatory transparency portal; publishes maximum public price list"
            }
        ]
    },
    {
        "country": "Libano",
        "regional_bloc": "Resto del mundo",
        "observation": "-",
        "exclusion": True,
        "exclusion_type": "NO_ROCHE_APPROVED_SOURCE",
        "status": "NO_ROCHE_APPROVED_SOURCE",
        "sources": []
    },
    {
        "country": "Noruega",
        "regional_bloc": "Resto del mundo",
        "observation": "-",
        "exclusion": True,
        "exclusion_type": "NO_ROCHE_APPROVED_SOURCE",
        "status": "NO_ROCHE_APPROVED_SOURCE",
        "sources": []
    }
]

print(f"Total Countries extracted: {len(inventory)}")
total_sources = sum(len(c["sources"]) for c in inventory)
print(f"Total Sources registered: {total_sources}")
excluded_countries = [c["country"] for c in inventory if c.get("exclusion")]
print(f"Countries with explicit exclusions or no search source ({len(excluded_countries)}): {excluded_countries}")
gov_sources = sum(1 for c in inventory for s in c["sources"] if "Government" in s["category"] or "Regulatory" in s["category"] or "Procurement" in s["category"])
com_sources = sum(1 for c in inventory for s in c["sources"] if "Commercial" in s["category"] or "Portal" in s["category"] or "Aggregator" in s["category"])
print(f"Government/Regulatory/Procurement sources: {gov_sources}")
print(f"Commercial/Portal sources: {com_sources}")
