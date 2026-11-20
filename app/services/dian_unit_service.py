import os
import re
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("DIANUnitService")

CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "dian_units_catalog.json"


class DIANUnitService:
    """
    Servicio centralizado de Unidades de Medida Oficiales de la DIAN y Motor de Conversión.
    Mapea, normaliza y realiza conversión matemática entre unidades de factura DIAN
    y unidades del catálogo e inventario Fruver / POS.
    """

    _catalog: Optional[Dict[str, Dict[str, Any]]] = None

    @classmethod
    def get_catalog(cls) -> Dict[str, Dict[str, Any]]:
        """Carga el catálogo de unidades DIAN en memoria (singleton/cache)."""
        if cls._catalog is None:
            cls._catalog = cls._load_catalog()
        return cls._catalog

    @classmethod
    def _load_catalog(cls) -> Dict[str, Dict[str, Any]]:
        if CATALOG_PATH.exists():
            try:
                with open(CATALOG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    logger.info(f"✅ Catálogo maestro DIAN cargado: {len(data)} unidades de medida.")
                    return data
            except Exception as e:
                logger.error(f"Error cargando catálogo de unidades DIAN: {e}")
        return {}

    @classmethod
    def get_unit(cls, code_or_name: str) -> Optional[Dict[str, Any]]:
        """Busca una unidad por su código DIAN oficial o alias común."""
        if not code_or_name:
            return None
        catalog = cls.get_catalog()
        key = str(code_or_name).strip().upper()
        if key in catalog:
            return catalog[key]

        # Búsqueda secundaria insensible a mayúsculas/minúsculas por nombre DIAN
        key_lower = str(code_or_name).strip().lower()
        for item in catalog.values():
            if item.get("dian_name", "").lower() == key_lower or item.get("standard_unit", "").lower() == key_lower:
                return item

        return None

    @classmethod
    def search_units(cls, query: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Búsqueda rápida omnidireccional de códigos DIAN por código o descripción."""
        catalog = cls.get_catalog()
        if not query:
            return list(catalog.values())[:limit]

        q = query.strip().lower()
        results = []
        for item in catalog.values():
            code = item.get("code", "").lower()
            name = item.get("dian_name", "").lower()
            std = item.get("standard_unit", "").lower()
            if q == code:
                results.insert(0, item)
            elif q in code or q in name or q in std:
                results.append(item)
            if len(results) >= limit:
                break
        return results

    @classmethod
    def resolve_unit(cls, raw_unit: str, description: str = "") -> Dict[str, Any]:
        """
        Resuelve una unidad cruda proveniente de factura DIAN (XML UBL 2.1 o PDF oficial VPFE)
        y analiza el texto de descripción para identificar presentaciones, empaques y factores.
        """
        clean_raw = str(raw_unit or "").strip().upper()
        clean_desc = str(description or "").strip()

        unit_info = cls.get_unit(clean_raw)
        
        codigo_dian = unit_info.get("code", clean_raw) if unit_info else clean_raw
        nombre_dian = unit_info.get("dian_name", clean_raw) if unit_info else clean_raw
        magnitud = unit_info.get("magnitude", "UNIDAD") if unit_info else "UNIDAD"
        unidad_estandar = unit_info.get("standard_unit", clean_raw.capitalize() or "Und") if unit_info else (clean_raw.capitalize() or "Und")
        factor_base = float(unit_info.get("factor_to_base", 1.0)) if unit_info else 1.0
        unidad_base = unit_info.get("base_unit", "UN") if unit_info else "UN"

        # Heurística inicial de presentación y multiplicador por empaque
        presentacion, unidades_por_presentacion, detected_pres_type = cls.detect_presentation_and_equivalence(
            clean_desc,
            codigo_dian=codigo_dian,
            standard_unit=unidad_estandar
        )

        # Reglas especiales de conteo inherente al código DIAN:
        # DZN = Docena (12 unidades)
        if codigo_dian == "DZN":
            presentacion = "Docena"
            unidades_por_presentacion = 12.0
        elif codigo_dian == "QD":
            presentacion = "1/4 Docena"
            unidades_por_presentacion = 3.0
        elif codigo_dian == "PR":
            presentacion = "Par"
            unidades_por_presentacion = 2.0
        elif codigo_dian in ("CL", "CEN") and "cien" in nombre_dian.lower():
            presentacion = "Ciento"
            unidades_por_presentacion = 100.0

        es_multipack = unidades_por_presentacion > 1.0

        return {
            "codigo_dian": codigo_dian,
            "nombre_dian": nombre_dian,
            "unidad_estandar": unidad_estandar,
            "magnitud": magnitud,
            "unidad_base": unidad_base,
            "factor_base": factor_base,
            "presentacion": presentacion,
            "unidades_por_presentacion": unidades_por_presentacion,
            "es_multipack": es_multipack,
            "etiqueta_dian": f"DIAN: {codigo_dian} ({nombre_dian.capitalize()})" if unit_info else f"DIAN: {codigo_dian}"
        }

    @classmethod
    def detect_presentation_and_equivalence(
        cls,
        description: str,
        codigo_dian: str = "",
        standard_unit: str = "Und"
    ) -> Tuple[str, float, Optional[str]]:
        """
        Extrae heurísticamente la presentación comercial (Caja, Display, Paquete, Bolsa, Bulto, etc.)
        y la cantidad de unidades por empaque a partir de la descripción del producto y código DIAN.
        """
        desc_upper = str(description or "").upper()
        c_u = str(codigo_dian or "").upper()

        # REGLA CRÍTICA: Códigos DIAN explícitos de UNIDAD individual (94, C62, NAR, NIU, H87, EA, PCE, UN, UND)
        # En facturas comerciales colombianas, si el código DIAN es '94' o unidad individual, la línea se factura
        # por unidad suelta (ej. 12 barras de jabón a $3.269), aun cuando el nombre comercial del fabricante cite
        # el empaque master de fábrica (ej. 'x72Un x110gr'). En estos casos NUNCA se debe multiplicar por 72.
        INDIVIDUAL_CODES = {"94", "C62", "NAR", "NIU", "H87", "EA", "PCE", "UN", "UND"}
        if c_u in INDIVIDUAL_CODES:
            m_disp = re.search(r"\bDISPLAY\s*[Xx]\s*(\d+)", desc_upper)
            if m_disp and float(m_disp.group(1)) <= 24:
                return "Display", float(m_disp.group(1)), "DISPLAY"
            if any(k in desc_upper for k in ["TRIOPACK", "TRI-PACK", "3PACK", "X3UN"]):
                return "Paquete", 3.0, "MULTIPACK"
            if any(k in desc_upper for k in ["DUPACK", "DUO-PACK", "2PACK", "X2UN"]):
                return "Paquete", 2.0, "MULTIPACK"
            return "Unidad", 1.0, "INDIVIDUAL_UNIT"

        # 1. Patrón Display explícito: "DISPLAY X 6 UND", "DISPLAY X 12"
        m_display = re.search(r"\bDISPLAY\s*[Xx]\s*(\d+)", desc_upper)
        if m_display:
            qty = float(m_display.group(1))
            if 1 < qty <= 500:
                return "Display", qty, "DISPLAY"

        # 2. Patrón de matriz múltiple: "12X10 X 54GR" -> 12 unidades por display/caja
        m_mult = re.search(r"(\d+)\s*[Xx]\s*(\d+)\s*[Xx]\s*(\d+)\s*(?:G|GR|ML)?\b", desc_upper)
        if m_mult:
            qty = float(m_mult.group(1))
            if 1 < qty <= 500:
                pres_name = "Caja" if c_u in ("BX", "CA", "CR", "CT", "WSD") else "Paquete"
                return pres_name, qty, "MATRIZ_MULT"

        # 3. Patrón multiplicador de empaque: "12 X 10 X 54GR" o "24 X 250ML" o "12X10"
        m_pack = re.search(r"(\d+)\s*[Xx]\s*(\d+)\s*(?:G|GR|ML|UN|UND|K|KG)?\b", desc_upper)
        if m_pack:
            qty = float(m_pack.group(1))
            if 1 < qty <= 500:
                pres_name = "Caja" if c_u in ("BX", "CA", "CR", "CT", "WSD") else "Paquete"
                return pres_name, qty, "PACK"

        # 4. Patrón por unidades: "X 6 UND", "X 12 UNIDADES", "X 24 UN", "X 10 PZA"
        m_x_und = re.search(r"[Xx]\s*(\d+)\s*(?:UND|UNIDADES|UN|PZA|PCS|U)?\b", desc_upper)
        if m_x_und:
            qty = float(m_x_und.group(1))
            if 1 < qty <= 500:
                if "CAJA" in desc_upper or c_u in ("BX", "CA", "CR", "CT"):
                    return "Caja", qty, "X_UND"
                if "BOLSA" in desc_upper or c_u in ("BG", "BLS"):
                    return "Bolsa", qty, "X_UND"
                if "DISPLAY" in desc_upper or c_u == "DS":
                    return "Display", qty, "X_UND"
                return "Paquete", qty, "X_UND"

        # 5. Paréntesis con cantidad: "(12 UND X 50GR)", "(24 X 100ML)"
        m_bracket = re.search(r"\((\d+)\s*(?:UND|UN)?\s*[Xx]\s*\d+\s*(?:G|GR|ML)?\)", desc_upper)
        if m_bracket:
            qty = float(m_bracket.group(1))
            if 1 < qty <= 500:
                return "Paquete", qty, "BRACKET"

        # 6. Detección por código DIAN de empaque
        if c_u in ("BX", "CA", "CR", "CT", "CS", "BB", "CJA"):
            return "Caja", 1.0, "DIAN_EMPAQUE"
        if c_u in ("PK", "PA", "PQT", "PC", "PAQ"):
            return "Paquete", 1.0, "DIAN_EMPAQUE"
        if c_u in ("BG", "BLS"):
            return "Bolsa", 1.0, "DIAN_EMPAQUE"
        if c_u in ("SA", "BULT"):
            return "Bulto", 1.0, "DIAN_EMPAQUE"
        if c_u in ("CH", "CAN"):
            return "Canastilla", 1.0, "DIAN_EMPAQUE"
        if c_u in ("BO", "BOT"):
            return "Botella", 1.0, "DIAN_EMPAQUE"
        if c_u == "PU":
            return "Bandeja", 1.0, "DIAN_EMPAQUE"
        if c_u == "DS":
            return "Display", 1.0, "DIAN_EMPAQUE"
        if c_u == "BE":
            return "Atado", 1.0, "DIAN_EMPAQUE"

        # 7. Palabras clave en descripción
        if "BANDEJA" in desc_upper:
            m_b = re.search(r"(\d+)\s+BANDEJAS?", desc_upper)
            qty = float(m_b.group(1)) if m_b else 1.0
            return "Bandeja", max(1.0, qty), "KEYWORD"
        if "CANASTILLA" in desc_upper:
            return "Canastilla", 1.0, "KEYWORD"
        if "BULTO" in desc_upper or "SACO" in desc_upper:
            return "Bulto", 1.0, "KEYWORD"
        if "ATADO" in desc_upper:
            return "Atado", 1.0, "KEYWORD"

        # 8. Retorno por defecto según la unidad estándar
        return standard_unit or "Und", 1.0, None

    @classmethod
    def calculate_cross_unit_conversion(
        cls,
        from_unit: str,
        to_unit: str,
        quantity: float,
        unit_cost: float,
        units_per_pack: float = 1.0
    ) -> Dict[str, Any]:
        """
        Realiza la conversión matemática bidireccional entre la unidad de la factura (DIAN)
        y la unidad configurada en el producto del catálogo/POS (ej. Libras a Kilos, Kilos a Libras,
        Docenas a Unidades, Cajas a Unidades, Gramos a Kilos).
        
        Garantiza que el valor monetario total de compra se mantenga idéntico:
        Valor Total = (Cantidad Original * Costo Original) == (Cantidad Convertida * Costo Convertido)
        """
        u_from = str(from_unit or "").strip().upper()
        u_to = str(to_unit or "").strip().upper()
        qty = float(quantity or 0.0)
        cost = float(unit_cost or 0.0)
        pack_mult = max(1.0, float(units_per_pack or 1.0))

        # Normalizar códigos a claves estándar
        norm_map = {
            "KGM": "KG", "KILO": "KG", "KILOS": "KG", "KG": "KG",
            "GRM": "GR", "GRAMO": "GR", "GRAMOS": "GR", "G": "GR", "GR": "GR",
            "LBR": "LB", "LIBRA": "LB", "LIBRAS": "LB", "LB": "LB",
            "ONZ": "OZ", "ONZA": "OZ", "OZ": "OZ",
            "TNE": "TON", "TONELADA": "TON", "TON": "TON",
            "EA": "UN", "NIU": "UN", "C62": "UN", "94": "UN", "UND": "UN", "UNIDAD": "UN", "UN": "UN",
            "DZN": "DZN", "DOCENA": "DZN",
            "QD": "QD", "PR": "PR",
            "BX": "CAJA", "CA": "CAJA", "CJA": "CAJA", "CAJA": "CAJA",
            "PK": "PAQ", "PA": "PAQ", "PQT": "PAQ", "PAQUETE": "PAQ", "PAQ": "PAQ",
            "LTR": "L", "LT": "L", "LITRO": "L", "L": "L",
            "MLT": "ML", "ML": "ML", "CC": "ML"
        }

        key_from = norm_map.get(u_from, u_from)
        key_to = norm_map.get(u_to, u_to)

        # Si son la misma unidad base o no hay conversión requerida
        if key_from == key_to:
            return {
                "conversion_requerida": False,
                "de_unidad": from_unit,
                "a_unidad": to_unit,
                "factor": 1.0,
                "cantidad_convertida": qty,
                "costo_convertido": cost,
                "mensaje": "Misma unidad de medida, sin conversión necesaria."
            }

        factor = 1.0
        formula = ""

        # --- A. CONVERSIONES DE PESO ---
        # 1 Libra colombiana = 0.5 Kg (500g)
        if key_from == "LB" and key_to == "KG":
            # Factura viene en Libras (ej. 10 lb a $2.000/lb = $20.000). Inventario en KG (5 kg a $4.000/kg = $20.000)
            factor = 0.5
            formula = "1 Libra = 0.5 Kg"
        elif key_from == "KG" and key_to == "LB":
            # Factura viene en Kilos (ej. 5 kg a $4.000/kg = $20.000). Inventario en LB (10 lb a $2.000/lb = $20.000)
            factor = 2.0
            formula = "1 Kg = 2.0 Libras"
        elif key_from == "GR" and key_to == "KG":
            # Factura en Gramos (ej. 500 g a $5.000). Inventario en KG (0.5 kg a $10.000/kg)
            factor = 0.001
            formula = "1.000 Gramos = 1.0 Kg"
        elif key_from == "KG" and key_to == "GR":
            factor = 1000.0
            formula = "1.0 Kg = 1.000 Gramos"
        elif key_from == "LB" and key_to == "GR":
            factor = 500.0
            formula = "1 Libra = 500 Gramos"
        elif key_from == "GR" and key_to == "LB":
            factor = 1.0 / 500.0
            formula = "500 Gramos = 1 Libra"
        elif key_from == "TON" and key_to == "KG":
            factor = 1000.0
            formula = "1 Tonelada = 1.000 Kg"
        elif key_from == "KG" and key_to == "TON":
            factor = 0.001
            formula = "1.000 Kg = 1 Tonelada"

        # --- B. CONVERSIONES DE CONTEO / MULTIPACK ---
        elif key_from == "DZN" and key_to == "UN":
            # Factura en Docena (ej. 3 docenas a $12.000 = $36.000). Inventario en UN (36 un a $1.000 = $36.000)
            factor = 12.0
            formula = "1 Docena = 12 Unidades"
        elif key_from == "UN" and key_to == "DZN":
            factor = 1.0 / 12.0
            formula = "12 Unidades = 1 Docena"
        elif key_from == "QD" and key_to == "UN":
            factor = 3.0
            formula = "1/4 Docena = 3 Unidades"
        elif key_from == "PR" and key_to == "UN":
            factor = 2.0
            formula = "1 Par = 2 Unidades"

        # --- C. CONVERSIONES DE EMPAQUE A UNIDADES (CAJA / PAQUETE -> UN) ---
        elif key_from in ("CAJA", "PAQ", "BULTO", "DISPLAY") and key_to == "UN":
            factor = pack_mult
            formula = f"1 {from_unit.capitalize()} = {int(pack_mult) if pack_mult.is_integer() else pack_mult} Unidades"
        elif key_from == "UN" and key_to in ("CAJA", "PAQ", "BULTO", "DISPLAY"):
            factor = 1.0 / pack_mult if pack_mult > 0 else 1.0
            formula = f"{int(pack_mult) if pack_mult.is_integer() else pack_mult} Unidades = 1 {to_unit.capitalize()}"

        # --- D. CONVERSIONES DE VOLUMEN ---
        elif key_from == "ML" and key_to == "L":
            factor = 0.001
            formula = "1.000 Ml = 1.0 Litro"
        elif key_from == "L" and key_to == "ML":
            factor = 1000.0
            formula = "1.0 Litro = 1.000 Ml"
        elif key_from == "GLN" and key_to == "L":
            factor = 3.78541
            formula = "1 Galón = 3.785 Litros"

        else:
            # Unidades incompatibles o sin factor directo
            return {
                "conversion_requerida": False,
                "de_unidad": from_unit,
                "a_unidad": to_unit,
                "factor": 1.0,
                "cantidad_convertida": qty,
                "costo_convertido": cost,
                "mensaje": f"No hay conversión automática directa entre {from_unit} y {to_unit}."
            }

        # Calcular valores convertidos
        converted_qty = round(qty * factor, 4)
        converted_cost = round(cost / factor, 4) if factor > 0 else cost

        return {
            "conversion_requerida": True,
            "de_unidad": from_unit,
            "a_unidad": to_unit,
            "factor": factor,
            "formula": formula,
            "cantidad_convertida": converted_qty,
            "costo_convertido": converted_cost,
            "mensaje": f"Conversión automática: {formula}. Cantidad: {qty} {from_unit} ➔ {converted_qty} {to_unit} (Costo: ${cost:,.2f} ➔ ${converted_cost:,.2f}/{to_unit})"
        }

    @classmethod
    def get_all_dian_codes(cls) -> List[str]:
        """Retorna la lista de todos los códigos DIAN conocidos en mayúsculas."""
        return list(cls.get_catalog().keys())

    @classmethod
    def get_known_unit_tokens(cls) -> Dict[str, str]:
        """
        Retorna diccionario unificado para reconocimiento de unidades en OCR y motores de parsing.
        """
        catalog = cls.get_catalog()
        tokens: Dict[str, str] = {}
        for code, info in catalog.items():
            tokens[code.upper()] = info.get("standard_unit", code)
            # Agregar el nombre oficial también como token si es una o dos palabras cortas
            name = info.get("dian_name", "").upper()
            if name and len(name.split()) <= 2:
                tokens[name] = info.get("standard_unit", code)

        return tokens
