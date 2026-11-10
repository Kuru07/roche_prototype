import re
import json
import hashlib
import unicodedata
import math
from datetime import datetime, timezone

# Standard semantic aliases for canonical schema mapping.
ALIASES = {
    "molecule_name": [
        "molecule", "active ingredient", "generic name", "principio activo",
        "denominacion comun", "denominacion comun internacional", "dci", "drug",
        "medicine", "medicamento", "substance", "sustancia", "substancia",
        "substancia ativa"
    ],
    "brand_name": [
        "brand", "brand name", "trade name", "product name", "nombre comercial",
        "nombre comercial del innovador", "producto", "produto", "marca", "medicamento comercial"
    ],
    "active_ingredient": [
        "active ingredient", "principio activo", "sustancia activa",
        "substancia ativa", "substancia", "componente"
    ],
    "strength": [
        "strength", "concentration", "potency", "concentracion", "concentracao",
        "dosis", "potencia"
    ],
    "dosage_form": [
        "dosage form", "form", "pharmaceutical form", "forma farmaceutica",
        "via de administracion"
    ],
    "pack_size": [
        "pack size", "package", "presentation size", "units", "content",
        "envase", "tamano de empaque", "unidades", "presentacion"
    ],
    "manufacturer": [
        "manufacturer", "laboratory", "lab", "titular", "fabricante",
        "laboratorio", "empresa"
    ],
    "currency": [
        "currency", "moneda", "divisa", "iso_currency"
    ],
    "price_retail": [
        "retail price", "pharmacy price", "precio farmacia", "pvp", "retail",
        "precio venta al publico", "precio consumidor final"
    ],
    "price_consumer": [
        "consumer price", "patient price", "precio consumidor", "precio venta publico",
        "precio paciente"
    ],
    "price_wholesale": [
        "wholesale price", "precio mayorista", "distributor price", "precio distribuidor"
    ],
    "price_public": [
        "public price", "government price", "institutional price", "precio publico",
        "precio institucional", "precio maximo regulado", "pmv", "precio techo"
    ],
    "price_value": [
        "price", "precio", "amount", "valor", "importe", "cost", "costo"
    ],
    "price_type": [
        "price type", "tipo precio", "tipo de precio", "nivel de precio"
    ],
    "price_per_unit": [
        "price per unit", "unit price", "precio por unidad", "precio unitario"
    ],
    "published_at": [
        "published at", "publication date", "fecha publicacion", "fecha emision"
    ],
    "effective_date": [
        "effective date", "valid from", "vigencia desde", "fecha vigencia", "inicio vigencia"
    ]
}

def _normalize_string(val: str) -> str:
    """Normalize text by stripping accents (NFKD) and non-alphanumeric chars."""
    if not val:
        return ""
    unaccented = unicodedata.normalize("NFKD", str(val)).encode("ASCII", "ignore").decode("utf-8")
    return re.sub(r"[^a-z0-9]+", " ", unaccented.lower()).strip()

def _parse_price_value(val) -> float | None:
    """
    Safely converts international price numbers (handling comma/dot decimals and thousand separators).
    Never infers missing values; returns None on blank, '-' or NaN.
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return None if math.isnan(val) else float(val)
    
    s = str(val).strip()
    if not s or s.lower() in ("nan", "none", "-", "null", "n/a"):
        return None
    
    cleaned = re.sub(r"[^0-9,.-]", "", s)
    if not cleaned or cleaned in ("-", "."):
        return None
    
    if "." in cleaned and "," in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            # e.g. 2.500,50 (Latin America / Europe)
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            # e.g. 2,500.50 (US / UK)
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    
    try:
        res = float(cleaned)
        return None if math.isnan(res) else res
    except Exception:
        return None

def parse_paraguay_rep_price(raw_val) -> tuple[float | None, str | None, bool]:
    r"""
    Parses Paraguay DINAVISA 'Precio máximo REP' string.
    Validates format: ^[0-9.]+\s*(gs\.?|guaran[ií]es)?$
    Removes period thousands separators; converts to integer float.
    Returns: (parsed_float, notation_string, is_valid)
    """
    if raw_val is None or str(raw_val).strip().lower() in ("", "nan", "none", "-", "null"):
        return None, None, False
    s = str(raw_val).strip()
    m = re.match(r"^([0-9.]+)\s*(gs\.?|guaran[ií]es)?$", s, re.IGNORECASE)
    if not m:
        return None, None, False
    num_str = m.group(1).replace(".", "")
    notation = (m.group(2) or "gs").strip().lower()
    try:
        val = float(num_str)
        return val, notation, True
    except Exception:
        return None, None, False

def extract_cmed_tax_tier_prices(raw_dict: dict) -> dict:
    """
    Extracts distinct PF and PMC price categories and their respective ICMS tax tiers.
    Strictly preserves tax rate and price category distinct without conflating them.
    """
    tiers = {
        "PF": {},
        "PMC": {}
    }
    for col, val in raw_dict.items():
        if not col:
            continue
        parsed_val = _parse_price_value(val)
        if parsed_val is None:
            continue
        
        col_clean = str(col).strip()
        if re.match(r"^PF\b", col_clean, re.IGNORECASE):
            tier_name = re.sub(r"^PF\s*", "", col_clean, flags=re.IGNORECASE).strip()
            tiers["PF"][tier_name] = parsed_val
        elif re.match(r"^PMC\b", col_clean, re.IGNORECASE):
            tier_name = re.sub(r"^PMC\s*", "", col_clean, flags=re.IGNORECASE).strip()
            tiers["PMC"][tier_name] = parsed_val
    
    return tiers

def map_raw_record(raw_dict: dict, source_meta: dict, row_index: int | None = None) -> dict:
    """
    Maps a raw record into the canonical schema while strictly enforcing Roche source policy.
    Supports:
    1. Paraguay DINAVISA HTML table extraction (Precio máximo REP).
    2. Brazil ANVISA CMED pricing matrices (tax-tier separation).
    3. Generic multi-lingual sources with strict allowlist and provenance enforcement.
    """
    country = source_meta.get("country", "Unknown")
    source_url = source_meta.get("url", "")
    
    # -------------------------------------------------------------
    # ROUTE 1: PARAGUAY DINAVISA TABLE MAPPING
    # -------------------------------------------------------------
    is_paraguay_dinavisa = (
        country == "Paraguay"
        or "Precio máximo REP" in raw_dict
        or "Nombre comercial del INNOVADOR" in raw_dict
        or "dinavisa.gov.py/precio-maximo-rep-fijado-por-la-dinavisa" in source_url.lower()
    )
    
    if is_paraguay_dinavisa:
        raw_preserved = dict(raw_dict)
        
        # Determine source row index for collision-safe identification
        idx_val = row_index if row_index is not None else raw_dict.get("_row_index", 0)
        
        # Clean helper
        def _clean_field(v):
            if v is None:
                return None
            s = str(v).strip()
            if not s or s.lower() in ("nan", "none", "null"):
                return None
            # Normalize internal newlines/multiple spaces
            return re.sub(r"\s+", " ", s).strip()
        
        brand_name = _clean_field(raw_dict.get("Nombre comercial del INNOVADOR"))
        active_ingredient = _clean_field(raw_dict.get("Principio activo"))
        strength = _clean_field(raw_dict.get("Dosis"))
        dosage_form = _clean_field(raw_dict.get("Forma farmacéutica"))
        presentation = _clean_field(raw_dict.get("Presentación"))
        raw_price_str = raw_dict.get("Precio máximo REP")
        
        # Validate medicine identity
        if not brand_name and not active_ingredient:
            return None  # Cannot identify drug presentation
        
        # Parse and validate Paraguay REP price
        parsed_price, notation, is_valid = parse_paraguay_rep_price(raw_price_str)
        
        # Audit missing/incomplete values
        missing_fields = []
        if not brand_name:
            missing_fields.append("Nombre comercial del INNOVADOR")
        if not active_ingredient:
            missing_fields.append("Principio activo")
        if not strength:
            missing_fields.append("Dosis")
        if not dosage_form:
            missing_fields.append("Forma farmacéutica")
        if not presentation:
            missing_fields.append("Presentación")
        if parsed_price is None:
            missing_fields.append("Precio máximo REP")
        
        # CORRECTION 1: Separate source approval from record verification
        if missing_fields:
            needs_review = True
            mapping_notes = f"Incomplete row in approved source: Missing {', '.join(missing_fields)}"
            confidence = 0.60
            record_verification_status = "PENDING_REVIEW"
            parsed_price = None
        else:
            needs_review = False
            mapping_notes = "Standard DINAVISA REP mapping; currency notation 'gs' mapped to ISO 'PYG'"
            confidence = 1.0
            record_verification_status = "APPROVED_ROCHE_SOURCE"
        
        # Preserve original row and metadata
        raw_preserved["_source_approval_status"] = "APPROVED_ROCHE_SOURCE"
        raw_preserved["_record_verification_status"] = record_verification_status
        raw_preserved["_row_index"] = idx_val
        raw_preserved["_original_currency_notation"] = notation or "gs"
        raw_preserved["_iso_currency"] = "PYG"
        raw_preserved["_original_price_string"] = str(raw_price_str) if raw_price_str is not None else ""
        raw_preserved["_maximum_rep_price"] = parsed_price
        raw_preserved["_currency_mapping_documentation"] = (
            "Original currency notation 'gs' mapped to ISO 4217 code 'PYG'. "
            "No currency conversion or PPP adjustments applied."
        )
        if source_meta.get("page_title"):
            raw_preserved["_source_page_title"] = source_meta.get("page_title")
        
        # CORRECTION 2: Collision-safe deterministic observation ID
        # Includes exact source URL, original source row index, and original raw row contents
        raw_json_str = json.dumps(raw_dict, default=str, ensure_ascii=False, sort_keys=True)
        obs_id = hashlib.sha256(f"Paraguay_{source_url}_{idx_val}_{raw_json_str}".encode()).hexdigest()[:28]
        
        search_parts = [
            str(brand_name or ""),
            str(active_ingredient or ""),
            str(strength or ""),
            str(dosage_form or ""),
            str(presentation or ""),
            "Paraguay",
            "DINAVISA",
            "Precio máximo REP"
        ]
        
        return {
            "observation_id": f"obs_{obs_id}",
            "country": "Paraguay",
            "source_id": source_meta.get("source_id", 24),
            "source_name": source_meta.get("source_name", "Precio máximo REP Fijado por la DINAVISA"),
            "source_authority": "DINAVISA",
            "source_url": source_url or "https://dinavisa.gov.py/precio-maximo-rep-fijado-por-la-dinavisa/",
            "verification_status": record_verification_status,
            "source_approval_status": "APPROVED_ROCHE_SOURCE",
            "is_synthetic": False,
            "extraction_confidence": confidence,
            "needs_review": needs_review,
            "mapping_notes": mapping_notes,
            "molecule_name": active_ingredient or brand_name,
            "brand_name": brand_name,
            "active_ingredient": active_ingredient,
            "strength": strength,
            "dosage_form": dosage_form,
            "pack_size": presentation,
            "presentation": presentation,
            "manufacturer": None,
            "currency": "PYG",
            "price_retail": None,
            "price_consumer": None,
            "price_wholesale": None,
            "price_public": None,
            "price_type": "Precio máximo REP",
            "price_regime": None,
            "price_value": parsed_price,
            "maximum_rep_price": parsed_price,
            "price_per_unit": None,
            "published_at": None,
            "effective_date": None,
            "source_row": raw_preserved,
            "search_text": " ".join(search_parts)
        }
    
    # -------------------------------------------------------------
    # ROUTE 2: BRAZIL CMED MATRIX & GENERIC SOURCES
    # -------------------------------------------------------------
    mapped = {}
    unmapped = {}
    ambiguous = []
    col_scores = {}
    
    is_cmed_matrix = any(k.startswith(("PF ", "PMC ", "PF", "PMC")) for k in raw_dict.keys()) or "REGIME DE PREÇO" in raw_dict
    
    parser_config = source_meta.get("parser_config") or {}
    if isinstance(parser_config, str):
        try:
            parser_config = json.loads(parser_config)
        except Exception:
            parser_config = {}
    
    explicit_mapping = parser_config.get("column_mapping", {})
    if explicit_mapping:
        for target, source_col in explicit_mapping.items():
            if source_col in raw_dict and raw_dict[source_col] is not None:
                val = raw_dict[source_col]
                if isinstance(val, float) and math.isnan(val):
                    val = None
                mapped[target] = val
                col_scores[target] = 1.0
    
    for raw_col, val in raw_dict.items():
        if explicit_mapping and raw_col in explicit_mapping.values():
            continue
        if isinstance(val, float) and math.isnan(val):
            continue
        if str(val).strip().lower() in ("nan", "none", "null"):
            continue
        if is_cmed_matrix and any(raw_col.strip().startswith(prefix) for prefix in ("PF", "PMC", "PMVG")):
            continue
        
        norm_col = _normalize_string(raw_col)
        matched_target = None
        best_score = 0.0
        
        for target, alias_list in ALIASES.items():
            if target in mapped and mapped[target] is not None:
                continue
            for alias in alias_list:
                norm_alias = _normalize_string(alias)
                if norm_col == norm_alias:
                    score = 1.0
                elif norm_alias in norm_col or norm_col in norm_alias:
                    score = 0.75
                else:
                    score = 0.0
                
                if score > best_score:
                    best_score = score
                    matched_target = target
        
        if matched_target and best_score >= 0.75:
            if matched_target in mapped and mapped[matched_target] is not None:
                ambiguous.append(f"Collision on {matched_target}: '{raw_col}' vs prior match")
            else:
                mapped[matched_target] = val
                col_scores[matched_target] = best_score
        else:
            unmapped[raw_col] = str(val)[:200]
    
    molecule = mapped.get("molecule_name") or mapped.get("active_ingredient") or ""
    brand = mapped.get("brand_name") or ""
    
    if not molecule and not brand:
        return None
    
    price_regime = raw_dict.get("REGIME DE PREÇO") or raw_dict.get("regime_de_preco") or mapped.get("price_regime")
    if price_regime is not None:
        price_regime = str(price_regime).strip()
    
    tax_tier_prices = extract_cmed_tax_tier_prices(raw_dict) if is_cmed_matrix else {}
    raw_preserved = dict(raw_dict)
    if tax_tier_prices:
        raw_preserved["_tax_tier_prices"] = tax_tier_prices
    
    if is_cmed_matrix:
        price_retail = None
        price_consumer = None
        price_wholesale = None
        price_public = None
        price_type = None
        price_value = None
        needs_review = True
        mapping_notes = "PENDING_REVIEW: Price category semantics unconfirmed by Roche. Storing raw PF/PMC tax tiers separately without canonical category inference."
        confidence = 0.70
    else:
        price_retail = _parse_price_value(mapped.get("price_retail"))
        price_consumer = _parse_price_value(mapped.get("price_consumer"))
        price_wholesale = _parse_price_value(mapped.get("price_wholesale"))
        price_public = _parse_price_value(mapped.get("price_public"))
        price_type = str(mapped.get("price_type") or "Reported Price").strip() if mapped.get("price_type") else None
        price_value = mapped.get("price_value") or price_consumer or price_public or price_retail or price_wholesale
        needs_review = bool(ambiguous)
        mapping_notes = "; ".join(ambiguous) if ambiguous else "Standard mapping"
        confidence = round(sum(col_scores.values()) / len(col_scores), 3) if col_scores else 0.8
    
    configured_status = source_meta.get("verification_status", "UNVERIFIED")
    is_secondary = source_meta.get("is_secondary_file", False) or "arquivos" in source_url.lower()
    
    if is_secondary or configured_status == "PENDING_ROCHE_APPROVAL":
        verification_status = "PENDING_ROCHE_APPROVAL"
    elif configured_status == "APPROVED_ROCHE_SOURCE" and not is_secondary:
        verification_status = "APPROVED_ROCHE_SOURCE"
    else:
        verification_status = configured_status
    
    raw_json_str = json.dumps(raw_dict, default=str, ensure_ascii=False, sort_keys=True)
    obs_id = hashlib.sha256(f"{country}_{source_url}_{raw_json_str}".encode()).hexdigest()[:28]
    
    search_parts = [
        str(mapped.get("molecule_name") or molecule or ""),
        str(mapped.get("brand_name") or brand or ""),
        str(mapped.get("active_ingredient") or molecule or ""),
        str(mapped.get("strength") or ""),
        str(mapped.get("dosage_form") or ""),
        str(mapped.get("manufacturer") or ""),
        str(price_regime or ""),
        country,
        source_meta.get("source_authority", "")
    ]
    
    return {
        "observation_id": f"obs_{obs_id}",
        "country": country,
        "source_id": source_meta.get("source_id", 0),
        "source_name": source_meta.get("source_name", "Universal Ingestion Source"),
        "source_authority": source_meta.get("source_authority", "External Authority"),
        "source_url": source_url,
        "verification_status": verification_status,
        "is_synthetic": bool(source_meta.get("is_synthetic", False)),
        "extraction_confidence": confidence,
        "needs_review": needs_review,
        "mapping_notes": mapping_notes,
        "molecule_name": str(mapped.get("molecule_name") or molecule or "").strip() or None,
        "brand_name": str(mapped.get("brand_name") or brand or "").strip() or None,
        "active_ingredient": str(mapped.get("active_ingredient") or molecule or "").strip() or None,
        "strength": str(mapped.get("strength") or "").strip() or None,
        "dosage_form": str(mapped.get("dosage_form") or "").strip() or None,
        "pack_size": str(mapped.get("pack_size") or "").strip() or None,
        "presentation": str(mapped.get("pack_size") or "").strip() or None,
        "manufacturer": str(mapped.get("manufacturer") or "").strip() or None,
        "currency": str(mapped.get("currency") or source_meta.get("default_currency") or "USD").upper().strip(),
        "price_retail": price_retail,
        "price_consumer": price_consumer,
        "price_wholesale": price_wholesale,
        "price_public": price_public,
        "price_type": price_type,
        "price_regime": price_regime,
        "price_value": price_value,
        "maximum_rep_price": price_value,
        "price_per_unit": mapped.get("price_per_unit"),
        "published_at": mapped.get("published_at"),
        "effective_date": mapped.get("effective_date"),
        "source_row": raw_preserved,
        "tax_tier_prices": tax_tier_prices,
        "search_text": " ".join(search_parts)
    }
