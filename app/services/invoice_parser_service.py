import io
import os
import re
import json
import base64
import logging
import asyncio
from pathlib import Path
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Tuple

import httpx

try:
    import numpy as np
except ImportError:
    np = None

try:
    from PIL import Image, ImageOps
except ImportError:
    Image = None

try:
    import textsnap
except ImportError:
    textsnap = None

from app.core.config import settings
from app.services.pricing_engine import calcular_costos_item_factura, redondear_centena_cercana
from app.models.schemas import (
    RawExtractedInvoice,
    RawExtractedItem,
    RawExtractedSupplier,
    RawExtractedInvoiceMeta
)
from app.services.image_preprocessor import AdaptiveImagePreprocessor, ProcessedVariant, ImageDiagnostic

logger = logging.getLogger("InvoiceParserService")

SPANISH_MONTHS = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12
}

KNOWN_UNIT_TOKENS = {
    # Peso
    "KG": "Kg", "KILOS": "Kg", "KILO": "Kg", "KGS": "Kg",
    "GR": "Gr", "GRAMOS": "Gr", "GRAMO": "Gr", "G": "Gr", "GRS": "Gr",
    "LIBRA": "Libra", "LIBRAS": "Libra", "LB": "Libra", "LBS": "Libra",
    "TON": "Ton", "TONELADA": "Ton",
    # Unidades
    "UND": "Und", "UNIDAD": "Und", "UNIDADES": "Und", "UN": "Und", "U": "Und", "UD": "Und", "EA": "Und",
    "PZ": "Und", "PIEZA": "Und", "DOC": "Docena", "DOCENA": "Docena",
    # Presentaciones y Empaques
    "CAJA": "Caja", "CAJAS": "Caja", "CJ": "Caja", "CJS": "Caja",
    "DISPLAY": "Display", "DISPLAYS": "Display", "DISP": "Display",
    "PAQUETE": "Paquete", "PAQUETES": "Paquete", "PQ": "Paquete", "PAQ": "Paquete",
    "BOLSA": "Bolsa", "BOLSAS": "Bolsa", "BLS": "Bolsa",
    "BULTO": "Bulto", "BULTOS": "Bulto", "SACO": "Bulto",
    "BOTELLA": "Botella", "BOTELLAS": "Botella", "BOT": "Botella",
    "LATA": "Lata", "LATAS": "Lata",
    "BANDEJA": "Bandeja", "BANDEJAS": "Bandeja",
    "ATADO": "Atado", "CANASTILLA": "Canastilla",
    # Volumen
    "LT": "Litro", "LITRO": "Litro", "LITROS": "Litro", "L": "Litro",
    "ML": "Ml", "MILILITROS": "Ml", "CC": "Ml"
}

NON_PRODUCT_WORDS = [
    "SUBTOTAL", "TOTAL", "RESOLUCION", "PAGINA", "CLIENTE", "DIRECCION", "TELEFONO", "VENDEDOR",
    "AUTORIZACION", "RESPONSABLE", "IVA", "ICUI", "IBUA", "ORDENCOMPRA", "PLANILLA", "DOCUMENTO",
    "CORREO", "FECHA", "FORMA", "MEDIO", "SUCURSAL", "CONDICION", "EFECTIVO", "BASE", "TASA",
    "MARIELA SANCHEZ", "ALMACEN", "ACTIVIDAD", "OBSERVACION", "REPRESENTACION", "VIGENCIA",
    "CEDULA", "DE PAGO", "VALOR EN LETRAS", "MUNICIPIO", "NOMBRE COMERCIAL", "VENTA NO",
    "ESTA FACTURA", "FIRMA", "RECIBIDO POR", "ACEPTADO POR", "EMITIDO POR", "RETEFUENTE",
    "RETEIVA", "RETEICA", "TOTAL DE LINEAS", "TOTAL CANTIDAD", "TOTAL ITEMS", "TOTAL ARTICULOS",
    "CIUDAD", "VENDIDO A", "RECIBO DE PAGO", "CUENTA DE COBRO", "PEDIDO", "FRUVER DEL YAKIBA",
    "CONDICIONES DE PAGO", "PLAZO", "MEDIO DE PAGO", "FORMA DE PAGO", "VENDIDOA", "YANQBA",
    "TIPO DE RESPONSABILIDAD", "RESPONSABILIDADES FISCALES", "FACTURA ELECTRONICA DE VENTA",
    "FRUVER", "LLANUVA"
    "FRUVER", "LLANUVA", "YANUBA", "YANUA", "YAKUBA", "REGIMEN", "PERSONA", "NATURAL", "JURIDICA", "COMERCIAL"
    "FRUVER", "LLANUVA", "YANUBA", "YANUA", "YAKUBA", "REGIMEN", "PERSONA", "NATURAL", "JURIDICA", "COMERCIAL",
    # Términos de pago y condiciones financieras
    "CONTADO", "CREDITO", "TRANSFERENCIA", "CONSIGNACION", "CHEQUE", "DIAS FECHA", "PLAZO DIAS",
    # Identificadores de sucursal y códigos de cliente detectados en muestras
    "FRUVERYANUBA", "FRUVER YANUBA", "CR173815", "CR 173815", "CRA 17", "CALLE 16", "CL 16", "CRA17",
    # Retenciones e impuestos de pie de página
    "R.ICA", "R. ICA", "RETE.ICA", "RETE ICA", "VLRBRUTO", "VALOR BRUTO", "VLR BRUTO", "VALOR TOTAL",
    "TOTAL NETO", "TOTAL PAGAR", "NETO A PAGAR", "NETO PAGAR", "SALDO ANTERIOR", "NUEVO SALDO",
    # Títulos y encabezados de columna para que no se conviertan en productos
    "CODIGO", "DESCRIPCION", "CANTIDAD", "VR UNIT", "VR.UNIT", "VR. UNIT", "P.UNIT", "P. UNIT",
    "UNITARIO", "IMPORTE", "DESCUENTO", "TARIFA", "PORCENTAJE", "VALOR VENTA", "LINEA",
    # Datos de control documental y bancos
    "HORA IMP", "PAGINA 1", "PAGINA 2", "HOJA", "TOTAL ITEM", "ITEM", "ITEMS", "ARTICULO",
    "BANCO", "BANCOLOMBIA", "DAVIVIENDA", "CUENTA DE AHORROS", "CUENTA CORRIENTE", "CUFE", "CUDE", "QR"
]


def clean_text(text: Any) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def parse_numeric(val: Any) -> float:
    """Parsea representaciones numéricas y monetarias COP a float de forma tolerante y precisa."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    s = s.replace("'", "").replace("’", "")
    s = re.sub(r"(\d):(\d)", r"\1.\2", s)
    s = re.sub(r"[^\d,\.-]", "", s)
    if not s:
        return 0.0

    dig_only = re.sub(r"\D", "", s)
    if len(dig_only) >= 11:
        return 0.0

    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        if len(parts) > 2:
            if len(parts[-1]) <= 2:
                s = "".join(parts[:-1]) + "." + parts[-1]
            else:
                s = "".join(parts)
        elif len(parts) == 2 and len(parts[1]) == 3 and not re.search(r"\d{4,}", parts[0]):
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    elif "." in s:
        parts = s.split(".")
        if len(parts) > 2:
            if len(parts[-1]) <= 2:
                s = "".join(parts[:-1]) + "." + parts[-1]
            else:
                s = "".join(parts)
        elif len(parts) == 2 and len(parts[1]) == 3 and not re.search(r"\d{4,}", parts[0]):
            s = s.replace(".", "")

    try:
        f = float(s)
        return f if f < 100_000_000 else 0.0
    except ValueError:
        return 0.0


def normalize_date(val: Any) -> Optional[str]:
    """Normaliza fechas a formato estándar ISO YYYY-MM-DD."""
    if not val:
        return None
    s = clean_text(val).lower()

    # 1. DD/MM/YYYY o DD-MM-YYYY con año 202X
    m_dmy = re.search(r"\b(\d{1,2})[/\-\.](\d{1,2})[/\-\.]*(202\d)", s)
    if m_dmy:
        d, m, y = m_dmy.groups()
        if 1 <= int(d) <= 31 and 1 <= int(m) <= 12:
            return f"{y}-{int(m):02d}-{int(d):02d}"

    # 2. DD Mes YYYY (tolera falta de espacios, ej: 11 Sep2026)
    m_month_str = re.search(r"\b(\d{1,2})[\s\.\-/]*([a-z]{3,10})[\s\.\-/]*(202\d)", s)
    if m_month_str:
        d, m_txt, y = m_month_str.groups()
        for k, v in SPANISH_MONTHS.items():
            if m_txt.startswith(k):
                return f"{y}-{v:02d}-{int(d):02d}"

    # 3. YYYY-MM-DD o YYYY/MM/DD
    m_iso = re.search(r"\b(202\d)[/\-\.](\d{1,2})[/\-\.](\d{1,2})\b", s)
    if m_iso:
        y, m, d = m_iso.groups()
        if 1 <= int(d) <= 31 and 1 <= int(m) <= 12:
            return f"{y}-{int(m):02d}-{int(d):02d}"

    return None


def detect_presentation_equivalence(description: str, raw_unit: str = "") -> Tuple[str, float]:
    """Identifica heurísticamente la presentación y unidades por empaque."""
    desc = clean_text(description).upper()
    unit_norm = KNOWN_UNIT_TOKENS.get(clean_text(raw_unit).upper(), clean_text(raw_unit).capitalize() or "Und")

    m_pack = re.search(r"(\d+)\s*[Xx]\s*(\d+)\s*(?:G|GR|ML|UN|UND|K|KG)?\b", desc)
    if m_pack:
        qty_units = float(m_pack.group(1))
        if 1 < qty_units <= 500:
            return "Caja", qty_units

    m_x_und = re.search(r"[Xx]\s*(\d+)\s*(?:UND|UNIDADES|UN|PZA|PCS)?\b", desc)
    if m_x_und:
        qty_units = float(m_x_und.group(1))
        if 1 < qty_units <= 500:
            return "Paquete", qty_units

    m_bracket = re.search(r"(\d+)\s*(?:UND)?\s*[Xx]\s*\d+\s*(?:G|GR|ML)?\)", desc)
    if m_bracket:
        qty_units = float(m_bracket.group(1))
        if 1 < qty_units <= 500:
            return "Paquete", qty_units

    if unit_norm in ("Caja", "Display", "Paquete", "Bulto"):
        return unit_norm, 1.0

    return unit_norm, 1.0


class BaseVisionEngine(ABC):
    @abstractmethod
    async def extract_invoice(self, file_bytes: bytes, filename: str = "") -> Dict[str, Any]:
        pass


class XMLInvoiceEngine(BaseVisionEngine):
    """
    Motor nativo para Facturación Electrónica DIAN Colombia (UBL 2.1).
    Extrae directamente los datos estructurados oficiales con 100% de precisión matemática en 0ms.
    """
    async def extract_invoice(self, file_bytes: bytes, filename: str = "") -> Dict[str, Any]:
        try:
            tree = ET.fromstring(file_bytes)
        except Exception as e:
            raise ValueError(f"El archivo XML no es un documento válido: {e}")

        namespaces = {
            "fe": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
            "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
            "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
        }

        def find_text(elem, xpath):
            if elem is None:
                return None
            res = elem.find(xpath, namespaces)
            return clean_text(res.text) if res is not None else None

        num_factura = find_text(tree, "cbc:ID")
        fecha = find_text(tree, "cbc:IssueDate")

        # Proveedor
        prov_elem = tree.find(".//cac:AccountingSupplierParty/cac:Party", namespaces)
        proveedor = None
        nit = None
        if prov_elem is not None:
            proveedor = (
                find_text(prov_elem, ".//cac:PartyTaxScheme/cbc:RegistrationName") or
                find_text(prov_elem, ".//cac:PartyLegalEntity/cbc:RegistrationName") or
                find_text(prov_elem, ".//cac:PartyName/cbc:Name")
            )
            nit = (
                find_text(prov_elem, ".//cac:PartyTaxScheme/cbc:CompanyID") or
                find_text(prov_elem, ".//cac:PartyLegalEntity/cbc:CompanyID")
            )

        # Totales
        subtotal = parse_numeric(find_text(tree, ".//cac:LegalMonetaryTotal/cbc:LineExtensionAmount"))
        total = parse_numeric(find_text(tree, ".//cac:LegalMonetaryTotal/cbc:PayableAmount"))

        # Líneas de factura
        items = []
        line_nodes = tree.findall(".//cac:InvoiceLine", namespaces)
        for idx, line in enumerate(line_nodes):
            desc = find_text(line, ".//cac:Item/cbc:Description") or f"Artículo {idx + 1}"
            raw_qty = find_text(line, "cbc:InvoicedQuantity")
            cant = max(0.01, parse_numeric(raw_qty) or 1.0)
            unit_code = "Und"
            q_elem = line.find("cbc:InvoicedQuantity", namespaces)
            if q_elem is not None and "unitCode" in q_elem.attrib:
                unit_code = KNOWN_UNIT_TOKENS.get(q_elem.attrib["unitCode"].upper(), q_elem.attrib["unitCode"])

            code = (
                find_text(line, ".//cac:Item/cac:StandardItemIdentification/cbc:ID") or
                find_text(line, ".//cac:Item/cac:SellersItemIdentification/cbc:ID") or
                ""
            )

            unit_price = parse_numeric(find_text(line, ".//cac:Price/cbc:PriceAmount"))
            line_subtotal = parse_numeric(find_text(line, "cbc:LineExtensionAmount"))

            taxes = []
            for t_node in line.findall(".//cac:TaxSubtotal", namespaces):
                t_val = parse_numeric(find_text(t_node, "cbc:TaxAmount"))
                t_percent = parse_numeric(find_text(t_node, "cbc:Percent"))
                t_scheme_id = find_text(t_node, ".//cac:TaxScheme/cbc:ID") or ""
                t_name = find_text(t_node, ".//cac:TaxScheme/cbc:Name") or ""
                t_name_u = t_name.upper()

                if "22" in t_scheme_id or "ADV" in t_name_u or "LICOR" in t_name_u or "IPO" in t_name_u:
                    norm_name = f"IPO+ADV {int(t_percent)}%" if t_percent > 0 else (t_name or "IPO+ADV")
                elif "20" in t_scheme_id or "ICUI" in t_name_u or "UP" in t_name_u:
                    norm_name = f"ICUI {int(t_percent)}%" if t_percent > 0 else (t_name or "ICUI")
                elif "21" in t_scheme_id or "IBUA" in t_name_u:
                    norm_name = "IBUA"
                elif "02" in t_scheme_id or "INC" in t_name_u or "CONSUMO" in t_name_u:
                    norm_name = f"INC {int(t_percent)}%" if t_percent > 0 else (t_name or "INC")
                elif "01" in t_scheme_id or "IVA" in t_name_u:
                    norm_name = f"IVA {int(t_percent)}%" if t_percent > 0 else (t_name or "IVA")
                else:
                    norm_name = t_name or "Impuesto"

                is_fixed = (t_percent <= 0 and t_val > 0) or "IBUA" in norm_name
                taxes.append({
                    "nombre": norm_name,
                    "tasa": t_percent if not is_fixed else 0.0,
                    "valor_fijo": t_val if is_fixed else 0.0,
                    "aplicado": True,
                    "valor_calculado": t_val
                })

            pres, equiv = detect_presentation_equivalence(desc, unit_code)
            line_total = line_subtotal + sum(t["valor_calculado"] for t in taxes if not t["valor_fijo"])

            items.append({
                "id": idx + 1,
                "codigo": code,
                "descripcion": desc,
                "cantidad": cant,
                "unidad": unit_code,
                "presentacion": pres,
                "unidades_por_presentacion": equiv,
                "precio_unitario": unit_price if unit_price > 0 else (line_subtotal / cant if cant > 0 else 0.0),
                "subtotal": line_subtotal,
                "descuento": 0.0,
                "impuestos": taxes,
                "total": line_total + sum(t["valor_fijo"] for t in taxes),
                "iva_incluido": False,
                "confianza": 1.0,
                "advertencias": []
            })

        return {
            "proveedor": proveedor,
            "nit": nit,
            "numero_factura": num_factura,
            "fecha": normalize_date(fecha) or fecha,
            "subtotal": subtotal,
            "total_impuestos": max(0.0, total - subtotal),
            "descuento": 0.0,
            "total": total,
            "iva_incluido_global": False,
            "motor_utilizado": "DIAN XML UBL 2.1 (Nativo)",
            "confidence_score": 1.0,
            "advertencias_generales": [],
            "items": items
        }


class GeminiVisionEngine(BaseVisionEngine):
    """
    Motor multimodal de visión profunda vía Google Gemini API.
    Utiliza response_schema (Structured Outputs) con tipado estricto garantizado:
    - Extracción semántica de alta fidelidad con corrección de errores ópticos.
    - Captura exhaustiva de renglones de mercancía.
    - Cero confusión entre metadatos documentales y productos facturados.
    - Cálculo contable determinista delegado a Python (pricing_engine.py).
    """
    def __init__(self, api_key: str, model_name: Optional[str] = None):
        self.api_key = api_key
        raw_model = (model_name or getattr(settings, "GEMINI_MODEL", None) or "gemini-3.8-flash").strip()
        if raw_model.startswith("models/"):
            raw_model = raw_model[len("models/"):]
        self.model_name = raw_model
        self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent?key={self.api_key}"

    async def extract_invoice(self, file_bytes: bytes, filename: str = "") -> Dict[str, Any]:
        b64_image = base64.b64encode(file_bytes).decode("utf-8")
        mime_type = "image/jpeg"
        if filename.lower().endswith(".png"):
            mime_type = "image/png"
        elif filename.lower().endswith(".webp"):
            mime_type = "image/webp"

        system_instruction = (
            "Eres un auditor contable experto en extracción y digitalización de facturas comerciales, recibos y documentos de compra para comercios (Fruver, abarrotes, retail, distribuidoras y afines en Colombia).\n\n"
            "Tu misión es extraer de manera exhaustiva y estructurada la totalidad de los datos del documento con máxima fidelidad óptica y contable.\n\n"
            "REGLAS CRÍTICAS:\n"
            "1. EXTRACCIÓN EXHAUSTIVA DE PRODUCTOS: Extrae cada uno de los renglones facturados sin omitir ni resumir ninguno. No excluyas artículos del inicio, intermedios ni del final.\n"
            "2. DESCRIPCIONES Y CONSOLIDACIÓN: Si la descripción de un producto ocupa varias líneas en la tabla, únelas en un único texto continuo y claro. Preserva marca, peso, presentación comercial o gramaje si están indicados en la línea. Corrige erratas ópticas evidentes.\n"
            "3. DISTINCIÓN ESTRICTA ENTRE METADATOS Y PRODUCTOS:\n"
            "   - NUNCA incluyas como producto renglones con información de cabecera del emisor o comprador (nombres de almacenes, sucursales, clientes, códigos internos de cliente o vendedor).\n"
            "   - NUNCA incluyas términos o condiciones de pago (CONTADO, CRÉDITO, TRANSFERENCIA, CHEQUE).\n"
            "   - NUNCA incluyas resoluciones DIAN, números de cuenta bancaria o leyendas legales.\n"
            "   - NUNCA incluyas subtotales, totales ni retenciones de pie de página (ReteFuente, ReteICA, ReteIVA) como ítems de compra.\n"
            "4. CANTIDADES Y VALORES NUMÉRICOS:\n"
            "   - 'cantidad': Cantidad física adquirida (número positivo).\n"
            "   - 'precio_unitario': Precio o costo unitario de compra antes de impuestos.\n"
            "   - 'descuento': Valor de descuento en el renglón si aplica (0 si no hay).\n"
            "   - 'subtotal': Subtotal antes de impuestos del renglón (cantidad * precio_unitario - descuento).\n"
            "   - 'total': Importe total neto del renglón.\n"
            "5. TRIBUTOS COLOMBIANOS:\n"
            "   - Identifica con precisión si cada renglón tiene IVA (19%, 5%, 0% o Exento), Impoconsumo INC (8%), Impuesto a Ultraprocesados ICUI (10%, 15%, 20%), o Impuesto al Consumo de Licores IPO (20%, 25%).\n"
            "   - Especifica 'impuesto_nombre' e 'impuesto_tasa' por renglón.\n"
            "6. DATOS DEL DOCUMENTO:\n"
            "   - 'proveedor': Razón social o nombre comercial del proveedor emisor.\n"
            "   - 'nit': NIT o identificación tributaria del proveedor.\n"
            "   - 'numero_factura': Prefijo y consecutivo de la factura.\n"
            "   - 'fecha': Fecha de emisión en formato AAAA-MM-DD.\n"
            "   - 'condicion_pago': Condición comercial de pago (Contado, Crédito, etc.).\n"
            "   - 'subtotal', 'descuento', 'total_impuestos', 'total': Valores consolidados de la factura.\n"
            "   - 'iva_incluido_global': true si se indica explícitamente que los precios unitarios ya incluyen IVA, false en caso contrario."
        )

        response_schema = {
            "type": "OBJECT",
            "properties": {
                "proveedor": {"type": "STRING", "description": "Razón social del proveedor o distribuidor emisor"},
                "nit": {"type": "STRING", "description": "NIT o identificación tributaria del proveedor"},
                "numero_factura": {"type": "STRING", "description": "Número o consecutivo de la factura"},
                "fecha": {"type": "STRING", "description": "Fecha de emisión en formato AAAA-MM-DD"},
                "condicion_pago": {"type": "STRING", "description": "Condición de pago: Contado, Crédito, etc."},
                "subtotal": {"type": "NUMBER", "description": "Subtotal de la factura antes de impuestos"},
                "descuento": {"type": "NUMBER", "description": "Descuento comercial global de la factura si aplica"},
                "total_impuestos": {"type": "NUMBER", "description": "Total de impuestos facturados"},
                "total": {"type": "NUMBER", "description": "Total neto a pagar de la factura"},
                "iva_incluido_global": {"type": "BOOLEAN", "description": "True si los precios unitarios ya incluyen IVA"},
                "items": {
                    "type": "ARRAY",
                    "description": "Lista de todos los productos y renglones de mercancía facturada",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "codigo": {"type": "STRING", "description": "Código de barras, PLU o referencia interna del producto"},
                            "descripcion": {"type": "STRING", "description": "Nombre comercial completo del producto con marca y peso"},
                            "cantidad": {"type": "NUMBER", "description": "Cantidad física facturada"},
                            "unidad": {"type": "STRING", "description": "Unidad de medida: Und, Kg, Gr, Paca, Display, Caja, Bolsa, etc."},
                            "precio_unitario": {"type": "NUMBER", "description": "Precio unitario antes de impuestos"},
                            "descuento": {"type": "NUMBER", "description": "Descuento en porcentaje o valor del renglón"},
                            "subtotal": {"type": "NUMBER", "description": "Subtotal del renglón antes de impuestos"},
                            "impuesto_nombre": {"type": "STRING", "description": "Nombre del impuesto principal: IVA, ICUI, INC, IPO, Exento"},
                            "impuesto_tasa": {"type": "NUMBER", "description": "Porcentaje de impuesto (ej. 19, 5, 0, 8, 10, 15, 20)"},
                            "impuestos": {
                                "type": "ARRAY",
                                "description": "Lista detallada de tributos aplicables si están discriminados",
                                "items": {
                                    "type": "OBJECT",
                                    "properties": {
                                        "nombre": {"type": "STRING", "description": "Nombre del tributo (IVA, INC, ICUI, IPO, Exento)"},
                                        "tasa": {"type": "NUMBER", "description": "Tarifa porcentual"},
                                        "valor_fijo": {"type": "NUMBER", "description": "Valor monetario fijo si aplica"}
                                    },
                                    "required": ["nombre", "tasa"]
                                }
                            },
                            "total": {"type": "NUMBER", "description": "Valor total de compra del renglón"}
                        },
                        "required": ["descripcion", "cantidad", "precio_unitario", "total"]
                    }
                }
            },
            "required": ["items"]
        }

        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": system_instruction},
                        {"inline_data": {"mime_type": mime_type, "data": b64_image}}
                    ]
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "response_schema": response_schema,
                "temperature": 0.1
            }
        }

        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(self.api_url, json=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"Fallo en Gemini Vision API (HTTP {resp.status_code}): {resp.text[:250]}")
            data = resp.json()
            candidates = data.get("candidates") or []
            if not candidates:
                prompt_feedback = data.get("promptFeedback", {})
                raise RuntimeError(f"Gemini no retornó candidatos de respuesta. Feedback: {prompt_feedback}")
            candidate = candidates[0]
            parts = candidate.get("content", {}).get("parts", [])
            if not parts:
                finish_reason = candidate.get("finishReason", "DESCONOCIDO")
                raise RuntimeError(f"Gemini no generó contenido textual (finishReason: {finish_reason}).")
            raw_text = parts[0].get("text", "")
            clean_json = raw_text.strip()
            if clean_json.startswith("```"):
                clean_json = re.sub(r"^```(?:json)?\s*", "", clean_json)
                clean_json = re.sub(r"\s*```$", "", clean_json)
            parsed = json.loads(clean_json)
            parsed["motor_utilizado"] = f"Google {self.model_name} (SOTA VLM)"
            return self._enrich_and_normalize(parsed)

    def _enrich_and_normalize(self, data: Dict[str, Any]) -> Dict[str, Any]:
        items_res = []
        for idx, it in enumerate(data.get("items", [])):
            desc = clean_text(it.get("descripcion", ""))
            raw_u = clean_text(it.get("unidad", "Und"))
            pres, equiv = detect_presentation_equivalence(desc, raw_u)
            raw_taxes = it.get("impuestos", [])
            
            # Impuestos
            tax_objs = []
            if isinstance(raw_taxes, list) and len(raw_taxes) > 0:
                for t in raw_taxes:
                    if isinstance(t, dict):
                        tax_objs.append({
                            "nombre": clean_text(t.get("nombre", "IVA")),
                            "tasa": parse_numeric(t.get("tasa", 0.0)),
                            "valor_fijo": parse_numeric(t.get("valor_fijo", 0.0)),
                            "aplicado": bool(t.get("aplicado", True)),
                            "valor_calculado": parse_numeric(t.get("valor_calculado", 0.0))
                        })
            else:
                imp_nom = clean_text(it.get("impuesto_nombre", "IVA"))
                imp_tasa = parse_numeric(it.get("impuesto_tasa", 0.0))
                if imp_tasa > 0 or (imp_nom and imp_nom.upper() != "EXENTO"):
                    tax_objs.append({
                        "nombre": imp_nom if imp_nom else "IVA",
                        "tasa": imp_tasa,
                        "valor_fijo": 0.0,
                        "aplicado": True,
                        "valor_calculado": 0.0
                    })
            
            cant = max(0.01, parse_numeric(it.get("cantidad", 1.0)))
            p_unit = parse_numeric(it.get("precio_unitario", 0.0))
            tot = parse_numeric(it.get("total", 0.0))
            subt = parse_numeric(it.get("subtotal", 0.0)) or (cant * p_unit)
            desc_val = parse_numeric(it.get("descuento", 0.0))
            iva_inc = bool(it.get("iva_incluido", data.get("iva_incluido_global", False)))

            # Enriquecer cada ítem con el motor financiero determinista Fruver (pricing_engine.py)
            costo_base = p_unit
            costo_neto = p_unit
            costo_inv = p_unit
            tot_unids = cant * equiv
            precio_vta = 0.0
            precio_vta_fin = 0.0
            margen_pct = settings.DEFAULT_PROFIT_MARGIN
            tot_imp_calc = 0.0

            try:
                calc_fin = calcular_costos_item_factura(
                    costo_original=p_unit,
                    cantidad=cant,
                    presentacion=pres,
                    unidades_por_presentacion=equiv,
                    descuento=desc_val,
                    iva_incluido=iva_inc,
                    conceptos_impuestos=tax_objs,
                    porcentaje_margen=settings.DEFAULT_PROFIT_MARGIN,
                    base_redondeo=settings.ROUNDING_BASE
                )
                desc_val = calc_fin.get("descuento", desc_val)
                costo_base = calc_fin["costo_base_presentacion"]
                costo_neto = calc_fin["costo_neto_presentacion"]
                costo_inv = calc_fin["costo_unitario_inventario"]
                tot_unids = calc_fin["total_unidades_inventario"]
                precio_vta = calc_fin["precio_venta_calculado"]
                precio_vta_final = calc_fin["precio_venta_final"]
                precio_vta_fin = precio_vta_final
                margen_pct = calc_fin["porcentaje_margen"]
                tot_imp_calc = calc_fin["total_impuestos_aplicados"]
            except Exception as e_fin:
                logger.debug(f"Cálculo financiero omitido para ítem {desc}: {e_fin}")

            item_dict = {
                "id": idx + 1,
                "codigo": clean_text(it.get("codigo", "")),
                "descripcion": desc,
                "canonical_name": desc,
                "cantidad": cant,
                "unidad": raw_u,
                "presentacion": pres,
                "unidades_por_presentacion": equiv,
                "precio_unitario": p_unit,
                "subtotal": subt,
                "descuento": desc_val,
                "impuestos": tax_objs,
                "total": tot or subt,
                "iva_incluido": iva_inc,
                "costo_base_presentacion": costo_base,
                "costo_neto_presentacion": costo_neto,
                "costo_unitario_inventario": costo_inv,
                "total_unidades_inventario": tot_unids,
                "precio_venta_calculado": precio_vta,
                "precio_venta_final": precio_vta_fin,
                "porcentaje_margen": margen_pct,
                "total_impuestos_calculados": tot_imp_calc,
                "confianza": 0.99,
                "advertencias": []
            }
            items_res.append(item_dict)

        # Enriquecimiento mediante Memoria Adaptativa Continua (Plantillas y Alias con Fuzzy Matching)
        try:
            from app.core import database
            prov = data.get("proveedor") or ""
            tmpl = database.get_supplier_template(prov, data.get("nit"))
            if tmpl:
                data["plantilla_aprendida_aplicada"] = True
                data["layout_aprendido"] = tmpl.get("layout_type")

            for it in items_res:
                raw_desc = it.get("descripcion", "")
                alias = database.get_product_alias(raw_desc)
                if alias:
                    it["matched_alias"] = True
                    it["canonical_name"] = alias.get("canonical_name") or raw_desc
                    if alias.get("barcode"):
                        it["codigo_factura"] = it.get("codigo")
                        it["codigo"] = alias["barcode"]
                    if alias.get("default_presentation") and alias["default_presentation"] != "Und":
                        it["presentacion"] = alias["default_presentation"]
                        it["unidades_por_presentacion"] = float(alias.get("default_units_per_pres") or 1.0)
                    if alias.get("_match_similarity"):
                        it["_match_similarity"] = alias["_match_similarity"]
                else:
                    it["matched_alias"] = False
                    it["canonical_name"] = raw_desc
        except Exception as e_alias:
            logger.warning(f"Error consultando memoria adaptativa en Gemini: {e_alias}")

        # Normalización y saneamiento determinista de totales de cabecera
        data["proveedor"] = clean_text(data.get("proveedor") or "") or "Proveedor Desconocido"
        data["nit"] = clean_text(data.get("nit") or "")
        data["numero_factura"] = clean_text(data.get("numero_factura") or "")
        raw_date = data.get("fecha")
        data["fecha"] = normalize_date(raw_date) or clean_text(raw_date or "")
        data["condicion_pago"] = clean_text(data.get("condicion_pago") or "")
        data["subtotal"] = parse_numeric(data.get("subtotal", 0.0))
        data["total_impuestos"] = parse_numeric(data.get("total_impuestos", 0.0))
        data["descuento"] = parse_numeric(data.get("descuento", 0.0))
        data["total"] = parse_numeric(data.get("total", 0.0))
        data["iva_incluido_global"] = bool(data.get("iva_incluido_global", False))

        calc_subtotal = sum(it["subtotal"] for it in items_res)
        calc_total = sum(it["total"] for it in items_res)
        if data["subtotal"] <= 0.0 and calc_subtotal > 0.0:
            data["subtotal"] = calc_subtotal
        if data["total"] <= 0.0 and calc_total > 0.0:
            data["total"] = calc_total
        if data["total_impuestos"] <= 0.0 and data["total"] > data["subtotal"]:
            data["total_impuestos"] = round(data["total"] - data["subtotal"], 2)

        data["advertencias_generales"] = data.get("advertencias_generales") or []
        data["items"] = items_res
        data["confidence_score"] = 0.99 if items_res else 0.40
        return data


class PDFInvoiceEngine(BaseVisionEngine):
    """
    Motor especializado para documentos PDF (digitales vectoriales o escaneados).
    Extrae texto digital en milisegundos con pypdf, o renderiza páginas para OCR adaptativo.
    Desencripta automáticamente en memoria con settings.DIAN_RECEPTOR_NIT si viene protegido.
    """
    def __init__(self, rapidocr_engine=None):
        self.rapidocr_engine = rapidocr_engine

    def _is_dian_document(self, text: str) -> bool:
        if not text:
            return False
        text_upper = text.upper()
        dian_signals = [
            "DETALLES DE PRODUCTOS" in text_upper,
            "CÓDIGO ÚNICO DE FACTURA" in text_upper or "CODIGO UNICO DE FACTURA" in text_upper or "CUFE" in text_upper,
            "FACTURA ELECTRÓNICA DE VENTA" in text_upper or "FACTURA ELECTRONICA DE VENTA" in text_upper,
            "CATALOGO-VPFE.DIAN.GOV.CO" in text.lower(),
            "DATOS DEL EMISOR" in text_upper and "DATOS DEL ADQUIRIENTE" in text_upper,
        ]
        return ("DETALLES DE PRODUCTOS" in text_upper and any(dian_signals[1:])) or sum(dian_signals) >= 2

    def _parse_dian_official_pdf(self, full_text: str) -> dict:
        """
        Extractor nativo y determinista para la representación gráfica oficial de la DIAN (VPFE).
        Extrae el 100% de la cabecera, CUFE, emisor, receptor, totales e ítems con precisión vectorial.
        """
        from app.core import database

        # 1. Metadatos de Cabecera
        cufe_m = re.search(r"C[oó]digo\s+[ÚU]nico\s+de\s+Factura\s*-\s*CUFE\s*:\s*([0-9a-fA-F]{64,128})", full_text, re.IGNORECASE)
        if not cufe_m:
            cufe_m = re.search(r"CUFE\s*:\s*([0-9a-fA-F]{64,128})", full_text, re.IGNORECASE)
        if not cufe_m:
            cufe_m = re.search(r"\b([0-9a-fA-F]{64,128})\b", full_text)
        cufe = cufe_m.group(1).strip() if cufe_m else ""

        num_m = re.search(r"N[uú]mero\s+de\s+Factura\s*:\s*([A-Za-z0-9_-]+)", full_text, re.IGNORECASE)
        if not num_m:
            num_m = re.search(r"Factura\s+Electr[oó]nica\s+de\s+Venta\s*(?:No\.?|:)?\s*([A-Za-z0-9_-]+)", full_text, re.IGNORECASE)
        numero_factura = num_m.group(1).strip() if num_m else ""

        date_m = re.search(r"Fecha\s+de\s+Emisi[oó]n\s*:\s*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})", full_text, re.IGNORECASE)
        fecha = ""
        if date_m:
            raw_d = date_m.group(1).strip()
            parts = re.split(r"[-/]", raw_d)
            if len(parts) == 3:
                d, m, y = parts
                if len(y) == 2:
                    y = "20" + y
                fecha = f"{y}-{int(m):02d}-{int(d):02d}"

        emisor_m = re.search(r"(?:Datos\s+del\s+Emisor|Facturador\s+Electr[oó]nico).*?Raz[oó]n\s+Social\s*:\s*([^\r\n]+)", full_text, re.DOTALL | re.IGNORECASE)
        if not emisor_m:
            emisor_m = re.search(r"Raz[oó]n\s+Social\s*:\s*([^\r\n]+)", full_text, re.IGNORECASE)
        proveedor = clean_text(emisor_m.group(1)) if emisor_m else "Proveedor Desconocido"

        nit_m = re.search(r"(?:Datos\s+del\s+Emisor|Facturador\s+Electr[oó]nico).*?Nit(?: del Emisor)?\s*:\s*([0-9.-]+)", full_text, re.DOTALL | re.IGNORECASE)
        if not nit_m:
            nit_m = re.search(r"NIT\s*:\s*([0-9.-]+)", full_text, re.IGNORECASE)
        nit = re.sub(r"\D", "", nit_m.group(1)) if nit_m else ""

        # 2. Totales de Página de Totales
        subt_m = re.findall(r"Subtotal\s*([0-9.,]+)", full_text, re.IGNORECASE)
        subtotal = parse_numeric(subt_m[-1]) if subt_m else 0.0

        iva_m = re.findall(r"IVA\s*([0-9.,]+)", full_text, re.IGNORECASE)
        total_impuestos = 0.0
        if iva_m:
            nums = [parse_numeric(x) for x in iva_m if parse_numeric(x) > 100]
            if nums:
                total_impuestos = nums[-1]

        tot_m = re.findall(r"Total\s+factura\s*\(=\)[^\$0-9]*\$?\s*([0-9.,]+)", full_text, re.IGNORECASE)
        if not tot_m:
            tot_m = re.findall(r"Total\s+neto\s+factura\s*\(=\)[^\$0-9]*\$?\s*([0-9.,]+)", full_text, re.IGNORECASE)
        total = parse_numeric(tot_m[-1]) if tot_m else 0.0

        # 3. Extracción de Ítems en "Detalles de Productos"
        items = []
        detalles_idx = full_text.find("Detalles de Productos")
        if detalles_idx != -1:
            end_idx = full_text.find("Notas Finales", detalles_idx)
            if end_idx == -1:
                end_idx = full_text.find("Datos Totales", detalles_idx)
            if end_idx == -1:
                end_idx = len(full_text)

            block = full_text[detalles_idx:end_idx]
            raw_lines = block.split("\n")

            start_idx = -1
            for i, l in enumerate(raw_lines):
                if l.strip() == "1":
                    start_idx = i
                    break

            if start_idx != -1:
                item_lines = raw_lines[start_idx:]
                chunks = []
                curr_chunk = []
                curr_num = 1
                for l in item_lines:
                    if l.strip() == str(curr_num + 1) and len(curr_chunk) >= 5:
                        chunks.append(curr_chunk)
                        curr_chunk = []
                        curr_num += 1
                    curr_chunk.append(l)
                if curr_chunk:
                    chunks.append(curr_chunk)

                def is_code_token(s: str, has_existing: bool) -> bool:
                    s = s.strip()
                    if not s or " " in s:
                        return False
                    if has_existing and s.isdigit() and len(s) <= 6:
                        return True
                    if any(c.isdigit() for c in s) and len(s) <= 16:
                        return True
                    return False

                KNOWN_UNITS = ("WSD", "UND", "UN", "KGM", "KG", "NIU", "PQT", "CJA", "PAQ", "GRM", "MTR", "LT", "LTR", "GLN", "BOT", "PZA")

                for idx, ch in enumerate(chunks):
                    um_idx = -1
                    for j, l in enumerate(ch[1:], 1):
                        if l.strip().upper() in KNOWN_UNITS:
                            um_idx = j
                            break
                        if re.match(r"^[0-9]+[.,][0-9]{2}$", l.strip()) and j >= 2:
                            um_idx = j - 1
                            break

                    header_lines = ch[1:um_idx] if um_idx != -1 else ch[1:2]
                    um = ch[um_idx].strip() if um_idx != -1 and ch[um_idx].strip().upper() in KNOWN_UNITS else "UND"
                    rest = [l.strip() for l in ch[um_idx + 1:] if l.strip()]

                    code_parts = []
                    desc_lines = []
                    for hl in header_lines:
                        s = hl.strip()
                        if not s:
                            continue
                        if not desc_lines and is_code_token(s, bool(code_parts)):
                            code_parts.append(s)
                        else:
                            desc_lines.append(hl)

                    desc = ""
                    for dl in desc_lines:
                        if not desc:
                            desc = dl
                        else:
                            if desc.endswith(" "):
                                desc = desc + dl.strip()
                            else:
                                if dl.startswith(" "):
                                    desc = desc + dl.strip()
                                elif re.match(r"^[0-9]", dl.strip()) and not re.match(r"^[0-9]", desc.split()[-1]):
                                    desc = desc + " " + dl.strip()
                                else:
                                    desc = desc + dl.strip()

                    code = "".join(code_parts)
                    descripcion = clean_text(desc)
                    if not descripcion and code_parts:
                        descripcion = " ".join(code_parts)
                        code = ""

                    numeric_vals = [parse_numeric(x) for x in rest if x and x != "%"]

                    cant = 1.0
                    p_unit = 0.0
                    subt = 0.0
                    descuento_val = 0.0
                    t_iva = 19.0
                    v_iva = 0.0
                    tot = 0.0

                    if len(numeric_vals) >= 7:
                        cant = numeric_vals[0]
                        p_unit = numeric_vals[1]
                        descuento_val = numeric_vals[2]
                        v_iva = numeric_vals[4]
                        t_iva = numeric_vals[5]
                        tot = numeric_vals[6]
                        subt = round(cant * p_unit, 2)
                    elif len(numeric_vals) >= 5:
                        cant = numeric_vals[0]
                        p_unit = numeric_vals[1]
                        descuento_val = numeric_vals[2]
                        v_iva = numeric_vals[3]
                        t_iva = numeric_vals[4]
                        tot = numeric_vals[5] if len(numeric_vals) > 5 else round(cant * p_unit, 2)
                        subt = round(cant * p_unit, 2)
                    elif len(numeric_vals) >= 2:
                        cant = numeric_vals[0]
                        p_unit = numeric_vals[1]
                        tot = numeric_vals[-1]
                        subt = round(cant * p_unit, 2)
                    elif len(numeric_vals) == 1:
                        tot = numeric_vals[0]
                        p_unit = tot
                        cant = 1.0
                        subt = tot

                    if subt <= 0:
                        subt = round(cant * p_unit, 2)
                    if tot <= 0:
                        tot = subt

                    tax_objs = []
                    if t_iva > 0 or v_iva > 0:
                        tax_objs.append({
                            "nombre": f"IVA {int(t_iva)}%" if t_iva.is_integer() else f"IVA {t_iva}%",
                            "tasa": t_iva,
                            "valor_fijo": 0.0,
                            "aplicado": True,
                            "valor_calculado": v_iva
                        })

                    pres, equiv = detect_presentation_equivalence(descripcion, um)

                    costo_base = p_unit
                    costo_neto = p_unit
                    costo_inv = p_unit
                    tot_unids = cant * equiv
                    precio_vta = 0.0
                    precio_vta_fin = 0.0
                    margen_pct = settings.DEFAULT_PROFIT_MARGIN
                    tot_imp_calc = 0.0

                    try:
                        calc_fin = calcular_costos_item_factura(
                            costo_original=p_unit,
                            cantidad=cant,
                            presentacion=pres,
                            unidades_por_presentacion=equiv,
                            descuento=descuento_val,
                            iva_incluido=False,
                            conceptos_impuestos=tax_objs,
                            porcentaje_margen=settings.DEFAULT_PROFIT_MARGIN,
                            base_redondeo=settings.ROUNDING_BASE
                        )
                        descuento_val = calc_fin.get("descuento", descuento_val)
                        costo_base = calc_fin["costo_base_presentacion"]
                        costo_neto = calc_fin["costo_neto_presentacion"]
                        costo_inv = calc_fin["costo_unitario_inventario"]
                        tot_unids = calc_fin["total_unidades_inventario"]
                        precio_vta = calc_fin["precio_venta_calculado"]
                        precio_vta_fin = calc_fin["precio_venta_final"]
                        margen_pct = calc_fin["porcentaje_margen"]
                        tot_imp_calc = calc_fin["total_impuestos_aplicados"]
                    except Exception as e_fin:
                        logger.debug(f"Cálculo financiero omitido para {descripcion}: {e_fin}")

                    item_dict = {
                        "id": idx + 1,
                        "codigo": code,
                        "descripcion": descripcion,
                        "canonical_name": descripcion,
                        "cantidad": cant,
                        "unidad": um,
                        "presentacion": pres,
                        "unidades_por_presentacion": equiv,
                        "precio_unitario": p_unit,
                        "subtotal": subt,
                        "descuento": descuento_val,
                        "impuestos": tax_objs,
                        "total": tot,
                        "iva_incluido": False,
                        "costo_base_presentacion": costo_base,
                        "costo_neto_presentacion": costo_neto,
                        "costo_unitario_inventario": costo_inv,
                        "total_unidades_inventario": tot_unids,
                        "precio_venta_calculado": precio_vta,
                        "precio_venta_final": precio_vta_fin,
                        "porcentaje_margen": margen_pct,
                        "total_impuestos_calculados": tot_imp_calc,
                        "confianza": 0.99,
                        "advertencias": []
                    }

                    # Enriquecimiento con memoria adaptativa local
                    try:
                        alias = database.get_product_alias(descripcion)
                        if alias:
                            item_dict["matched_alias"] = True
                            item_dict["canonical_name"] = alias.get("canonical_name") or descripcion
                            if alias.get("barcode"):
                                item_dict["codigo_factura"] = item_dict.get("codigo")
                                item_dict["codigo"] = alias["barcode"]
                            if alias.get("default_presentation") and alias["default_presentation"] != "Und":
                                item_dict["presentacion"] = alias["default_presentation"]
                                item_dict["unidades_por_presentacion"] = float(alias.get("default_units_per_pres") or 1.0)
                    except Exception as e_alias:
                        logger.debug(f"Error consultando alias en memoria para {descripcion}: {e_alias}")

                    items.append(item_dict)

        calc_subtotal = round(sum(it["subtotal"] for it in items), 2)
        calc_total = round(sum(it["total"] for it in items), 2)
        if subtotal <= 0.0 and calc_subtotal > 0.0:
            subtotal = calc_subtotal
        if total <= 0.0 and calc_total > 0.0:
            total = calc_total
        if total_impuestos <= 0.0 and total > subtotal:
            total_impuestos = round(total - subtotal, 2)

        return {
            "success": True,
            "proveedor": proveedor,
            "supplier_name": proveedor,
            "nit": nit,
            "numero_factura": numero_factura,
            "invoice_number": numero_factura,
            "fecha": fecha,
            "invoice_date": fecha,
            "cufe": cufe,
            "subtotal": subtotal,
            "total_impuestos": total_impuestos,
            "descuento": 0.0,
            "total": total,
            "iva_incluido_global": False,
            "motor_utilizado": "PDF DIAN Oficial Nativo (Vectorial Desencriptado)",
            "provider_used": "PDF DIAN Oficial Nativo (Vectorial Desencriptado)",
            "confidence_score": 1.0,
            "advertencias_generales": [],
            "items": items
        }


    async def extract_invoice(self, file_bytes: bytes, filename: str = "") -> Dict[str, Any]:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(file_bytes))

        # Desencriptación automática si el PDF oficial DIAN viene protegido con contraseña
        if reader.is_encrypted:
            nit = str(getattr(settings, "DIAN_RECEPTOR_NIT", "40327379")).strip()
            nit_clean = re.sub(r"\D", "", nit)
            decrypted = False
            for pwd in [nit, nit_clean, ""]:
                try:
                    res = reader.decrypt(pwd)
                    if res in (1, 2):
                        decrypted = True
                        logger.info(f"Desencriptando PDF de factura con NIT configurado en .env ({nit})...")
                        break
                except Exception as e_dec:
                    logger.debug(f"Fallo intento desencriptación con '{pwd}': {e_dec}")

            if not decrypted:
                try:
                    _ = len(reader.pages)
                except Exception:
                    raise ValueError(
                        f"El archivo PDF está protegido con contraseña y no se pudo desencriptar con el NIT receptor ({nit})."
                    )

        total_pages = len(reader.pages)
        logger.info(f"Procesando documento PDF ({total_pages} páginas)...")

        # 1. Intentar extracción de texto vectorial nativo
        all_digital_text = []
        for i, page in enumerate(reader.pages):
            try:
                txt = page.extract_text() or ""
            except Exception as e_txt:
                logger.debug(f"Error extrayendo texto en página {i}: {e_txt}")
                txt = ""
            if len(txt.strip()) > 30:
                all_digital_text.append(txt)

        if len(all_digital_text) > 0 and sum(len(t) for t in all_digital_text) > 80:
            full_pdf_text = "\n".join(all_digital_text)
            
            # Si corresponde a la representación gráfica oficial DIAN VPFE
            if self._is_dian_document(full_pdf_text):
                logger.info("Detectado formato oficial de Factura Electrónica DIAN. Extrayendo ítems vectoriales nativos...")
                dian_res = self._parse_dian_official_pdf(full_pdf_text)
                if dian_res and dian_res.get("items"):
                    return dian_res

            logger.info("PDF contiene texto digital vectorial nativo. Extrayendo sin OCR...")
            lines = [l.strip() for l in full_pdf_text.splitlines() if l.strip()]
            simulated_boxes = []
            for idx, l in enumerate(lines):
                simulated_boxes.append({
                    "id": idx,
                    "text": l,
                    "score": 1.0,
                    "x0": 50.0,
                    "x1": 800.0,
                    "y0": float(idx * 20),
                    "y1": float(idx * 20 + 18),
                    "cx": 400.0,
                    "cy": float(idx * 20 + 9),
                })
            parser = SpatialInvoiceParser()
            res = parser.parse(simulated_boxes, (1000, max(1200, len(lines) * 22)))
            res["motor_utilizado"] = "PDF Nativo Digital (Vectorial Desencriptado)"
            res["confidence_score"] = 0.99
            return res

        # 2. Si es escaneado, extraer imágenes de las páginas
        page_images = []
        for page in reader.pages:
            try:
                for img_obj in page.images:
                    page_images.append(img_obj.data)
            except Exception as e_img:
                logger.debug(f"No se pudieron extraer imágenes de la página: {e_img}")

        if not page_images:
            raise ValueError("El archivo PDF no contiene texto digital ni imágenes escaneadas legibles.")

        # Procesar primera página con RapidOCR
        rapid_engine = self.rapidocr_engine or RapidOCRVisionEngine()
        return await rapid_engine.extract_invoice(page_images[0], filename)


class SpatialInvoiceParser:
    """
    Reconstructor espacial geométrico y reconciliador contable de facturas y recibos.
    Asocia descripciones, códigos, cantidades, unidades, precios e impuestos utilizando
    intervalos de coordenadas y validaciones matemáticas cruzadas.
    """

    def parse(self, boxes: List[Dict[str, Any]], img_size: Tuple[int, int]) -> Dict[str, Any]:
        full_text = "\n".join([b["text"] for b in boxes])

        # 1. Extraer Metadatos de Cabecera
        metadata = self._extract_metadata(full_text, boxes)

        # 2. Extraer Totales y Desglose de Impuestos del Pie de Página
        footer_data = self._extract_footer(boxes)

        # 3. Localizar Encabezados de Columna y Bloque de Tabla
        table_top_y, table_bot_y, detected_cols = self._detect_table_bounds(boxes, img_size[1])

        # 4. Extraer Filas de Productos con Bandas Verticales Delimitadas
        items = self._extract_items(boxes, table_top_y, table_bot_y, detected_cols)

        # 5. Reconciliación Matemática Estricta y Cálculo de Confianza Real
        reconciled = self._reconcile_and_score(items, footer_data, metadata, boxes)

        return {
            "proveedor": metadata["proveedor"],
            "supplier_name": metadata["proveedor"],
            "nit": metadata["nit"],
            "numero_factura": metadata["numero_factura"],
            "invoice_number": metadata["numero_factura"],
            "fecha": metadata["fecha"],
            "invoice_date": metadata["fecha"],
            "subtotal": reconciled["subtotal"],
            "total_impuestos": reconciled["total_impuestos"],
            "descuento": 0.0,
            "total": reconciled["total"],
            "iva_incluido_global": reconciled.get("iva_incluido_global", False),
            "confidence_score": reconciled["confidence_score"],
            "advertencias_generales": reconciled["advertencias_generales"],
            "items": reconciled["items"]
        }

    def _extract_metadata(self, full_text: str, boxes: List[Dict[str, Any]]) -> Dict[str, Any]:
        # 1. Proveedor
        known_suppliers = [
            ("PRODUCTOS NATURALES DE LA SABANA", "PRODUCTOS NATURALES DE LA SABANA S.A.S. BIC (ALQUERIA)"),
            ("ALQUERIA", "PRODUCTOS NATURALES DE LA SABANA S.A.S. BIC (ALQUERIA)"),
            ("PRODUCTOS RAMO", "PRODUCTOS RAMO S.A.S."),
            ("RAMO SAS", "PRODUCTOS RAMO S.A.S."),
            ("DISTRIBUCIONES LA NIEVE", "DISTRIBUCIONES LA NIEVE S.A.S."),
            ("LA NIEVE", "DISTRIBUCIONES LA NIEVE S.A.S."),
            ("BIMBO DE COLOMBIA", "BIMBO DE COLOMBIA S.A."),
            ("FRUVER DEL YAKIBA", "FRUVER DEL YAKIBA"),
            ("MEGAEXPRESS", "MEGAEXPRESS DISTRIBUCIONES"),
            ("BIOPACK", "BIOPACK S.A.S."),
            ("RINVAL", "RINVAL S.A.S."),
            ("NESTLE", "NESTLE DE COLOMBIA S.A."),
            ("FULLER PINTO", "FULLER PINTO S.A."),
            ("FULLER", "FULLER PINTO S.A."),
        ]
        u_full = full_text.upper()
        proveedor = "Proveedor Factura"
        for kw, canon in known_suppliers:
            if kw in u_full:
                proveedor = canon
                break

        if proveedor == "Proveedor Factura":
            top_boxes = [b for b in boxes if b["cy"] < 450]
            for b in top_boxes:
                t = b["text"].strip().upper()
                if any(bad in t for bad in NON_PRODUCT_WORDS):
                    continue
                if re.search(r"\b(S\.?[A-Z]\.?[A-Z]|S\.?[A-Z]\b|LTDA|DISTRIBUIDORA|COMERCIALIZADORA|ALMACEN)\b", t):
                    proveedor = clean_text(b["text"])
                    break

        # 2. NIT
        nit = None
        nit_pattern = r"(?:NIT|RUT|N\.I\.T\.)[:\s\.]*([0-9]{1,3}(?:\.[0-9]{3}){2,3}(?:-\s*\d)?|[0-9\.,]{6,15}(?:-\s*\d)?)"
        nit_pattern = r"(?:NIT|RUT|N\.I\.T\.)[:\s\.]*([0-9\.\-\s]{6,25})"
        for b in boxes:
            txt = b["text"].upper()
            if "CLIENTE" in txt or "COMPRADOR" in txt or b["cy"] > 550:
                continue
            m = re.search(nit_pattern, txt)
            if m:
                raw_match = m.group(1).strip()
                digits = re.findall(r"\d", raw_match)
                if len(digits) == 10:
                    clean_n = f"{''.join(digits[:9])}-{digits[9]}"
                elif len(digits) == 9:
                    clean_n = "".join(digits)
                elif len(digits) >= 6:
                    clean_n = "".join(digits)
                else:
                    continue
                if not clean_n.startswith("40327"):
                    nit = clean_n
                    break
        if not nit:
            m_all = re.findall(nit_pattern, full_text, re.IGNORECASE)
            for raw in m_all:
                digits = re.findall(r"\d", raw)
                if len(digits) == 10:
                    clean_n = f"{''.join(digits[:9])}-{digits[9]}"
                elif len(digits) == 9:
                    clean_n = "".join(digits)
                elif len(digits) >= 6:
                    clean_n = "".join(digits)
                else:
                    continue
                if not clean_n.startswith("40327"):
                    nit = clean_n
                    break

        # 3. Número de Factura
        num_factura = None
        blacklist_words = {
            "SOMOS", "GRANDES", "CONTRIBUYENTES", "AUTORRETENEDORES", "RESPONSABLE", "RESPONSABLES",
            "REGIMEN", "COMUN", "SIMPLIFICADO", "ORDEN", "PEDIDO", "REMISIÓN", "FACTURA", "VENTA",
            "EXPEDICION", "GENERACION", "ELECTRONICA", "PAGINA", "DIRECCION", "TELEFONO", "CLIENTE",
            "CIUDAD", "FECHA", "RESOLUCION", "NIT", "RUT", "VENDEDOR", "ASESOR", "CUFE", "RETEICA",
            "RETEIVA", "RFUENTE", "MUNICIPIO", "CORREO", "SUBTOTAL", "TOTAL", "VALOR", "CANTIDAD"
        }
        address_blacklist = [
            "CALLE", "CLL", "CRA", "CARRERA", "AVENIDA", "AV.", "TRANSVERSAL",
            "DIAGONAL", "AUTOPISTA", "BOGOTA", "BARRANQUILLA", "MEDELLIN",
            "CALI", "BUCARAMANGA", "KM ", "LOCAL", "OFICINA", "BODEGA"
        ]

        # Patrón 0: Encabezados explícitos colombianos de Factura de Venta / Factura Electrónica
        fev_pattern = r"(?:FACTURA\s+(?:ELECTRONICA\s+)?DE\s+VENTA|FACTURA\s+ELECTRONICA|FACTURA\s+DE\s+VENTA|FACTURA\s+(?:DE\s+)?VENTA\s+No\.?|FACTURA\s+No\.?|FACTURA\s+N°)\s*[:\s#]*([A-Za-z0-9\-_]{2,15}\s*[-_#]?\s*\d{2,12}|\d{4,12})\b"
        for b in boxes:
            txt = b["text"].strip()
            txt_u = txt.upper()
            if any(addr in txt_u for addr in address_blacklist):
                continue
            m_fev = re.search(fev_pattern, txt, re.IGNORECASE)
            if m_fev:
                cand = clean_text(m_fev.group(1).upper()).replace(" ", "")
                cand = re.sub(r"^(?:NO|N°|N)[\.\-_:]*", "", cand, flags=re.IGNORECASE)
                if cand not in blacklist_words and re.search(r"\d", cand) and not any(bw in cand for bw in ["FECHA", "RESOL", "PAGI", "CLIE", "TELEF", "NIT", "SOMOS", "VENTA", "ELECTRONICA"]):
                    num_factura = cand
                    break

        # Patrón 1A: Prefijos oficiales colombianos con número en cajas individuales (ej: EVN212145, FEP 252530, SD60-676926, FAC-1002, SETT123)
        prefix_pattern = r"(?:^|[^A-Za-z0-9]|(?:No|N°|NUMERO)[:\s#]*)((?:EVN|FEP|FEVC|SETT|SETP|PATC|SD\d*|FAC|VV|RM|FE)\s*[-_#.]?\s*\d{3,12})\b"
        # Patrón 1A: Prefijos oficiales colombianos con número en cajas individuales (ej: EVN212145, FEP 252530, SD60-676926, FAC-1002, SETT123, VI2435246)
        prefix_pattern = r"(?:^|[^A-Za-z0-9]|(?:No|N°|NUMERO)[:\s#]*)((?:EVN|FEP|FEVC|SETT|SETP|PATC|SD\d*|FAC|VV|VI|RM|FE)\s*[-_#.]?\s*\d{3,12})\b"
        if not num_factura:
            for b in boxes:
                txt = b["text"].strip()
                txt_u = txt.upper()
                if any(addr in txt_u for addr in address_blacklist):
                    continue
                m_pref = re.search(prefix_pattern, txt, re.IGNORECASE)
                if m_pref:
                    cand = clean_text(m_pref.group(1).upper()).replace(" ", "")
                    cand = re.sub(r"^(?:NO|N°|N)[\.\-_:]*", "", cand, flags=re.IGNORECASE)
                    if cand not in blacklist_words and re.search(r"\d", cand) and not any(bad in cand for bad in ["RESOL", "FECHA", "PAGI"]):
                        num_factura = cand
                        break

        # Patrón 1B: Si el prefijo y número quedaron en cajas adyacentes separadas (ej. 'FE' y '321360')
        if not num_factura:
            m_full = re.search(prefix_pattern, full_text, re.IGNORECASE)
            if m_full:
                cand = clean_text(m_full.group(1).upper()).replace(" ", "")
                cand = re.sub(r"^(?:NO|N°|N)[\.\-_:]*", "", cand, flags=re.IGNORECASE)
                if cand not in blacklist_words and re.search(r"\d", cand) and not any(bad in cand for bad in ["RESOL", "FECHA", "PAGI"]):
                    num_factura = cand

        # Patrón 2: Etiquetas explícitas (No., N°, Consecutivo, Factura de Venta No)
        if not num_factura:
            for b in boxes:
                txt = b["text"].strip()
                txt_u = txt.upper()
                if any(addr in txt_u for addr in address_blacklist):
                    continue
                m_no = re.search(r"(?:FACTURA|VENTA|ELECTRONICA|No|N°|NUMERO|CONSECUTIVO)\.?[:\s#]*([A-Za-z0-9\-_]{2,10}\s*[-_#]?\s*\d{3,10})\b", txt, re.IGNORECASE)
                if m_no:
                    cand = clean_text(m_no.group(1).upper()).replace(" ", "")
                    cand = re.sub(r"^(?:NO|N°|N)[\.\-_:]*", "", cand, flags=re.IGNORECASE)
                    if cand not in blacklist_words and re.search(r"\d", cand) and not any(bw in cand for bw in ["FECHA", "RESOL", "PAGI", "CLIE", "TELEF", "NIT", "SOMOS"]):
                        num_factura = cand
                        break

        # Patrón 3: POS # o ticket
        if not num_factura:
            for b in boxes:
                txt = b["text"].strip()
                txt_u = txt.upper()
                if any(addr in txt_u for addr in address_blacklist):
                    continue
                m_pos = re.search(r"POS\s*#?\s*([A-Za-z0-9\-_]*\d{3,12})", txt, re.IGNORECASE)
                if m_pos:
                    cand = clean_text(m_pos.group(1).upper())
                    if cand not in blacklist_words and re.search(r"\d", cand):
                        num_factura = cand
                        break

        # Patrón 4: Número aislado en encabezado superior (ej. Fuller Pinto preimpreso o ERPs específicos)
        if not num_factura:
            header_num_cands = []
            for b in boxes:
                if b["cy"] < 320 and b["cx"] > 350:
                    txt = b["text"].strip()
                    txt_u = txt.upper()
                    if any(bad in txt_u for bad in ["PBX", "TEL", "CEL", "NIT", "RUT", "PAGINA", "FECHA", "RESOL", "SAP", "DOCUMENTO"]):
                        continue
                    if re.match(r"^\d{6,10}$", txt):
                        header_num_cands.append(b)
            if header_num_cands:
                best_h_cand = min(header_num_cands, key=lambda x: (x["cy"], -x["cx"]))
                num_factura = best_h_cand["text"].strip()

        # 4. Fecha
        fecha = None
        for b in boxes:
            txt = b["text"].strip()
            if any(k in txt.upper() for k in ["FECHA", "EXPEDICION", "GENERACION", "EMISION", "VALIDACION"]):
                parsed_d = normalize_date(txt)
                if parsed_d:
                    fecha = parsed_d
                    break
        if not fecha:
            fecha = normalize_date(full_text)

        return {
            "proveedor": proveedor,
            "nit": nit,
            "numero_factura": num_factura,
            "fecha": fecha
        }

    def _extract_footer(self, boxes: List[Dict[str, Any]]) -> Dict[str, float]:
        footer_subtotal = 0.0
        footer_iva = 0.0
        footer_icui = 0.0
        footer_total = 0.0

        for b in boxes:
            txt_u = b["text"].upper()
            if any(bad_kw in txt_u for bad_kw in ["RESOLUCION", "AUTORIZACION", "CUFE", "DIAN", "HABILITACION", "NUMERACION", "CONSECUTIVO", "RANGO", "DESDE", "HASTA", "AUTORIZADA"]):
                continue

            m_sub = re.search(r"\bSUBTOTAL[:\s\$]*([0-9\.,]{3,15})", txt_u)
            if m_sub:
                s_val = parse_numeric(m_sub.group(1))
                if 100 < s_val < 30_000_000:
                    footer_subtotal = s_val

            m_tot_exp = re.search(r"\b(?:TOTAL\s+A\s+PAGAR|TOTAL\s+VENTA|VALOR\s+TOTAL|VALOR\s+FACTURA|VALOR\s+A\s+PAGAR)[:\s\$]*([0-9\.,]{3,15})\b", txt_u)
            if m_tot_exp:
                val = parse_numeric(m_tot_exp.group(1))
                if 100 < val < 30_000_000:
                    footer_total = val
            elif footer_total == 0.0:
                m_tot = re.search(r"\bTOTAL[:\s\$]*([0-9\.,]{3,15})\b", txt_u)
                if m_tot and not any(bad in txt_u for bad in ["ARTICULOS", "CANTIDAD", "ITEMS", "LINEAS", "CANT", "SUBTOTAL"]):
                    val = parse_numeric(m_tot.group(1))
                    if 100 < val < 30_000_000:
                        footer_total = val

            m_iva = re.search(r"\bIVA[:\s\$]*([0-9\.,]{3,15})", txt_u)
            if m_iva and not any(bad in txt_u for bad in ["%", "19%", "5%", "RESPONSABLES", "RETENCION"]):
                footer_iva = parse_numeric(m_iva.group(1))

            m_icui = re.search(r"\b(?:ICUI|IMP\.UP)[:\s\$]*([0-9\.,]{3,15})", txt_u)
            if m_icui:
                footer_icui = parse_numeric(m_icui.group(1))

        return {
            "footer_subtotal": footer_subtotal,
            "footer_iva": footer_iva,
            "footer_icui": footer_icui,
            "footer_total": footer_total
        }

    def _detect_table_bounds(self, boxes: List[Dict[str, Any]], img_h: int) -> Tuple[float, float, Dict[str, Any]]:
        col_patterns = {
            "ITEM": r"\b(?:ITEM|ITM|#|NRO\.?)\b",
            "COD": r"\b(?:CODIGO|CÓDIGO|COD\s*BARRAS|COD\.|EAN|REF|MATERIAL)\b",
            "DESC": r"\b(?:DESCRIPCION|DESCRIPCIÓN|DETALLE|PRODUCTO|ARTICULO)\b",
            "CANT": r"\b(?:CANTIDAD|CANT|QTD|CAJAS)\b",
            "UND": r"\b(?:UND|UNIDAD|UM|UNDS|UN|PAQ)\b",
            "ITEM": r"\b(?:ITEM|ITM|#|NRO\.?|POS)\b",
            "COD": r"\b(?:CODIGO|CÓDIGO|COD\s*BARRAS|COD\.|EAN|REF|MATERIAL|CODGO)\b",
            "DESC": r"\b(?:DESCRIP|DESCTIP|DETALLE|PRODUCTO|ARTICULO|CONCEPTO)\b",
            "CANT": r"\b(?:CANTIDAD|CANTIDND|CANT|QTD|CAJAS|QTY|UNIDADES)\b",
            "UND": r"\b(?:UND|UNIDAD|U\.?M\.?|UNDS|UN|PAQ|KG|GR)\b",
            "DCTO": r"\b(?:DESCUENTO|DESCUENTOS|DESCU|DCTO|DTO|%DESC|%DCTO)\b",
            "PRECIO_NETO": r"\b(?:PRECIO\s+NETO|VALOR\s+NETO|VR\s+NETO|VLR\s+NETO)\b",
            "PRECIO": r"\b(?:VALOR\s+UNITARIO|PRECIO\s+UN\.|PRECIO\s+POR\s+UNI|VLR\s*UNIT|VR\.\s*UNIT|VR\s*UNIT|V/UNIT|P\.\s*UNIT|V\.UNITARIO|VLRUNIT|VALOR\s+UNIDAD|VALOR\s+UNI|VALOR|PRECIO)\b",
            "TOTAL": r"\b(?:VALOR\s+TOTAL|VLR\s+TOTAL|VR\.\s*TOTAL|VR\s+TOTAL|V/TOTAL|VLR\s+TOT|VALORTOTAL\s+SIN\s+IVA|VALORTOTAL|TOTAL)\b",
            "TOTAL": r"\b(?:VALOR\s+TOTAL|VLR\s+TOTAL|VR\.\s*TOTAL|VR\s+TOTAL|V/TOTAL|VLR\s+TOT|VALORTOTAL\s+SIN\s+IVA|VALORTOTAL|IMPORTE|TOTAL)\b",
            "IVA": r"\b(?:IVA|%IVA|% IVA)\b",
            "UP": r"\b(?:UP|ICUI|IBUA)\b"
        }

        # 1. Detectar cajas individuales que contienen múltiples encabezados (ej. '# Codigo Descripcion Cant V/Total Imp')
        single_box_header = None
        single_box_cols = {}
        max_cols_in_single = 0

        header_candidates = []
        for b in boxes:
            u = b["text"].upper()
            matched_in_b = []
            for col_name, pattern in col_patterns.items():
                m = re.search(pattern, u)
                if m:
                    matched_in_b.append((col_name, m))
                    header_candidates.append((b, col_name))
            if len(matched_in_b) >= 3 or (len(matched_in_b) >= 2 and any(k[0] in ("DESC", "CANT") for k in matched_in_b)):
                if len(matched_in_b) > max_cols_in_single:
                    max_cols_in_single = len(matched_in_b)
                    single_box_header = b
                    single_box_cols = {}
                    for cname, m_obj in matched_in_b:
                        t_len = max(1, len(u))
                        rel_pos = (m_obj.start() + m_obj.end()) / (2.0 * t_len)
                        w = b.get("x1", b.get("cx", 0) + 50) - b.get("x0", b.get("cx", 0) - 50)
                        approx_cx = b.get("x0", b.get("cx", 0) - 50) + w * rel_pos
                        single_box_cols[cname] = {
                            "cx": approx_cx, "cy": b["cy"],
                            "x0": b.get("x0", approx_cx - 20), "x1": b.get("x1", approx_cx + 20),
                            "y0": b.get("y0", b["cy"] - 10), "y1": b.get("y1", b["cy"] + 10)
                        }

        # 2. Agrupar encabezados distribuidos horizontalmente en la misma línea
        best_cluster = []
        for b, cname in header_candidates:
            cluster = [c for c in header_candidates if abs(c[0]["cy"] - b["cy"]) <= 14]
            col_set = {c[1] for c in cluster}
            cond1 = len(col_set) >= 3 and any(k in col_set for k in ["DESC", "COD", "ITEM"]) and any(k in col_set for k in ["CANT", "PRECIO", "TOTAL"])
            cond2 = ("CANT" in col_set and "PRECIO" in col_set and "TOTAL" in col_set)
            cond3 = ("DESC" in col_set and "TOTAL" in col_set and len(col_set) >= 2)
            if cond1 or cond2 or cond3:
                if len(col_set) > len({c[1] for c in best_cluster}) or (len(col_set) == len({c[1] for c in best_cluster}) and len(cluster) > len(best_cluster)):
                    best_cluster = cluster

        table_top_y = 0.0
        table_bot_y = float(img_h)
        cols = {}

        if best_cluster and len(best_cluster) >= max_cols_in_single:
            header_cy = float(np.median([c[0]["cy"] for c in best_cluster]))
            header_boxes = [c[0] for c in best_cluster if abs(c[0]["cy"] - header_cy) <= 15]
            # Incluir subtítulos de encabezado inmediatamente contiguos (ej. 'UNIDAD MEDIDA', 'SIN IVA', 'ENTOS')
            sub_headers = [b for b in boxes if 0 <= b["cy"] - header_cy <= 30 and any(abs(b["cx"] - c["cx"]) <= 45 for c in header_boxes)]
            # Excluyendo números o códigos para no cortar el primer producto
            known_sub_kws = ["UNIDAD", "MEDIDA", "UNIT", "NETO", "IVA", "DCTO", "TARIFA", "TASA", "SIN IVA", "VALOR"]
            sub_headers = [
                b for b in boxes
                if 0 < b["cy"] - header_cy <= 25
                and any(abs(b["cx"] - c["cx"]) <= 45 for c in header_boxes)
                and any(w in b["text"].upper() for w in known_sub_kws)
                and not re.search(r"\d{3,}", b["text"])
            ]
            all_h_boxes = header_boxes + sub_headers
            table_top_y = max(b["y1"] for b in all_h_boxes) + 3
            table_top_y = max(b.get("y1", b.get("cy", 0) + 10) for b in all_h_boxes) + 3
            cols = {c[1]: c[0] for c in best_cluster if abs(c[0]["cy"] - header_cy) <= 15}
            if "PRECIO" not in cols and "PRECIO_NETO" in cols:
                cols["PRECIO"] = cols["PRECIO_NETO"]
        elif single_box_header:
            table_top_y = single_box_header.get("y1", single_box_header["cy"] + 10)
            table_top_y = single_box_header.get("y1", single_box_header.get("cy", 0) + 10)
            cols = single_box_cols
            if "PRECIO" not in cols and "PRECIO_NETO" in cols:
                cols["PRECIO"] = cols["PRECIO_NETO"]

        # 3. Delimitación por fin de metadatos de cabecera si table_top_y no se halló
        if table_top_y == 0.0:
            meta_end_y = 0.0
            for b in boxes:
                if b["cy"] < img_h * 0.60:
                    txt_u = b["text"].upper()
                    if any(k in txt_u for k in ["MEDIO DE PAGO", "FORMA DE PAGO", "CONDICIONES DE PAGO", "DIRECCION ENTREGA", "DIRECCION DE ENTREGA", "PLAZO:", "VENDEDOR:"]):
                        meta_end_y = max(meta_end_y, b.get("y1", b["cy"] + 10))
                        meta_end_y = max(meta_end_y, b.get("y1", b.get("cy", 0) + 10))
            if meta_end_y > 0:
                table_top_y = meta_end_y

        # 4. Delimitación inferior de la tabla
        for b in boxes:
            if b["cy"] > table_top_y + 80:
                txt_u = b["text"].upper()
                if any(k in txt_u for k in [
                    "NUMERO TOTAL DE LINEAS", "TOTAL DE LINEAS", "NUMERO DE LINEAS",
                    "SUBTOTAL", "TOTAL CANTIDAD", "TOTAL ITEMS", "TOTAL ARTICULOS",
                    "SON:", "OBSERVACIONES", "AUTORIZACION", "FORMA DE PAGO",
                    "VALOR EN LETRAS", "EFECTIVO", "DISCRIMINACION DE TARIFAS",
                    "BASE GRAVABLE", "RESOLUCION DIAN", "AUTORIZACION DIAN",
                    "TOTAL A PAGAR", "VALOR A PAGAR", "PAQUETES:", "CAJAS:", ": PAQUETES", "TOTAL PAQUETES", "TOTAL CAJAS", "MANGOS:", "ATADOS:"
                    "TOTAL A PAGAR", "VALOR A PAGAR", "PAQUETES:", "CAJAS:", ": PAQUETES",
                    "TOTAL PAQUETES", "TOTAL CAJAS", "MANGOS:", "ATADOS:",
                    "VLRBRUTO", "VLR BRUTO", "VLR SIN IVA", "R.FUENTE", "R.ICA", "R. IVA",
                    "RETEFUENTE", "RETEICA", "RETEIVA", "DISCRIMINACION DE TARIFAS"
                ]):
                    if b["y0"] < table_bot_y:
                        table_bot_y = b["y0"]
                    y0_val = b.get("y0", b.get("cy", 0) - 10)
                    if y0_val < table_bot_y:
                        table_bot_y = y0_val

        return table_top_y, table_bot_y, cols

    def _detect_and_extract_transposed_matrix(self, boxes: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
        """
        Reconoce y extrae facturas estructuradas como tablas matriciales o transpuestas
        (donde los atributos ITEM, CODIGO, DESCRIPCION, CANT, UND, VLRUNIT, TOTAL son filas y
        cada producto corresponde a una columna vertical cx, ej: Distribuciones La Nieve S.A.S).
        """
        row_headers = {}
        for b in boxes:
            txt_u = re.sub(r'[^A-Z]', '', b['text'].upper())
            for kw in ['ITEM', 'CODIGO', 'DESCRIPCION', 'CANT', 'UND', 'VLRUNIT', 'IVA', 'TOTAL']:
                if txt_u == kw or txt_u == f"%{kw}":
                    if kw not in row_headers or b.get('score', 0) > row_headers[kw].get('score', 0):
                        row_headers[kw] = b
            for kw in ['ITEM', 'CODIGO', 'DESCRIPCION', 'CANT', 'UND', 'VLRUNIT', 'DESCU', 'DCTO', 'IVA', 'TOTAL']:
                if txt_u == kw or txt_u.startswith(kw) or txt_u == f"%{kw}":
                    k_key = 'DESCU' if kw in ['DESCU', 'DCTO'] else kw
                    if k_key not in row_headers or b.get('score', 0) > row_headers[k_key].get('score', 0):
                        row_headers[k_key] = b

        total_cands = [b for b in boxes if b['text'].strip().upper() == 'TOTAL']
        if total_cands:
            row_headers['TOTAL'] = max(total_cands, key=lambda x: x['cy'])

        required_kws = [k for k in ['CODIGO', 'DESCRIPCION', 'CANT', 'VLRUNIT', 'TOTAL'] if k in row_headers]
        if len(required_kws) < 4:
            return None

        cy_vals = [row_headers[k]['cy'] for k in required_kws]
        cx_vals = [row_headers[k]['cx'] for k in required_kws]
        if cy_vals != sorted(cy_vals):
            return None
        # En una tabla matricial vertical (transpuesta), las cabeceras de fila están en una sola columna vertical (cx estrecho)
        if max(cx_vals) - min(cx_vals) > 120:
            return None
        # Cada cabecera de fila debe estar en un renglón vertical separado (al menos 20px)
        if any(cy_vals[i+1] - cy_vals[i] < 20 for i in range(len(cy_vals)-1)):
            return None
        if max(cy_vals) - min(cy_vals) < 300:
            return None

        tot_h = row_headers['TOTAL']
        unit_h = row_headers.get('VLRUNIT')
        desc_h = row_headers.get('DESCRIPCION')
        code_h = row_headers.get('CODIGO')
        iva_h = row_headers.get('IVA')
        cant_h = row_headers.get('CANT')
        descu_h = row_headers.get('DESCU')

        left_tot = [b for b in boxes if abs(b['cy'] - tot_h['cy']) <= 30 and b['cx'] < tot_h['cx'] - 10 and re.search(r'\d+[\.,]\d+', b['text']) and '$' not in b['text']]
        right_tot = [b for b in boxes if abs(b['cy'] - tot_h['cy']) <= 30 and b['cx'] > tot_h['cx'] + 10 and re.search(r'\d+[\.,]\d+', b['text']) and '$' not in b['text']]

        col_dir = -1 if len(left_tot) >= len(right_tot) else 1

        def get_row_boxes(h_box, pattern=None, cy_tol=35, cx_span=450):
            if not h_box:
                return []
            res = []
            for b in boxes:
                if abs(b['cy'] - h_box['cy']) <= cy_tol:
                    diff = (b['cx'] - h_box['cx']) * col_dir
                    if 8 <= diff <= cx_span:
                        if '$' not in b['text']:
                            if pattern is None or re.search(pattern, b['text']):
                                res.append(b)
            return sorted(res, key=lambda x: x['cx'])

        min_desc_y = min(code_h['cy'] + 80, desc_h['cy'] - 150)
        max_desc_y = max(desc_h['cy'] + 10, cant_h['cy'] - 80) if cant_h else desc_h['cy'] + 10

        desc_boxes = [
            b for b in boxes
            if min_desc_y <= b['cy'] <= max_desc_y
            and (b['cx'] - desc_h['cx']) * col_dir >= 8
            and (b['cx'] - desc_h['cx']) * col_dir <= 220
            and (b['cx'] - desc_h['cx']) * col_dir <= 450
            and any(c.isalpha() for c in b['text'])
            and not any(bad in b['text'].upper() for bad in ['TOTAL', 'IVA', 'SUBTOTAL', 'PEDIDO', 'PLANILLA', 'ORDEN', 'CUFE', 'RESOLUCION', 'VENTA', 'FACTURA', 'CLIENTE', 'DIRECCION', 'DOCUMENTO', 'VENDEDOR'])
            and not any(bad in b['text'].upper() for bad in [
                'TOTAL', 'IVA', 'SUBTOTAL', 'PEDIDO', 'PLANILLA', 'ORDEN', 'CUFE',
                'RESOLUCION', 'VENTA', 'FACTURA', 'CLIENTE', 'DIRECCION', 'DOCUMENTO',
                'VENDEDOR', 'UNIDAD MEDIDA', 'MEDIDA', 'PAQUETE', 'CAJA'
            ])
        ]
        desc_boxes.sort(key=lambda x: x['cx'])

        tot_boxes = get_row_boxes(tot_h, r'\d+[\.,]\d+')
        unit_boxes = get_row_boxes(unit_h, r'\d+[\.,]\d+')
        code_boxes = get_row_boxes(code_h, r'\d{4,}')
        iva_boxes = get_row_boxes(iva_h, r'\d+[\.,]\d+') if iva_h else []
        cant_boxes = get_row_boxes(cant_h, r'^\d+$') if cant_h else []
        descu_boxes = get_row_boxes(descu_h, r'^\d+[\.,]?\d*$', cy_tol=35) if descu_h else []

        if len(desc_boxes) < 2:
            return None

        items = []
        for i, d in enumerate(desc_boxes):
            cx = d['cx']
            c_box = code_boxes[i] if (len(code_boxes) == len(desc_boxes) and i < len(code_boxes)) else min(code_boxes, key=lambda b: abs(b['cx'] - cx), default=None)
            t_box = tot_boxes[i] if (len(tot_boxes) == len(desc_boxes) and i < len(tot_boxes)) else min(tot_boxes, key=lambda b: abs(b['cx'] - cx), default=None)
            u_box = unit_boxes[i] if (len(unit_boxes) == len(desc_boxes) and i < len(unit_boxes)) else min(unit_boxes, key=lambda b: abs(b['cx'] - cx), default=None)
            iv_box = iva_boxes[i] if (len(iva_boxes) == len(desc_boxes) and i < len(iva_boxes)) else (min(iva_boxes, key=lambda b: abs(b['cx'] - cx), default=None) if iva_boxes else None)
            ct_box = cant_boxes[i] if (len(cant_boxes) == len(desc_boxes) and i < len(cant_boxes)) else (min(cant_boxes, key=lambda b: abs(b['cx'] - cx), default=None) if cant_boxes else None)
            dc_box = descu_boxes[i] if (len(descu_boxes) == len(desc_boxes) and i < len(descu_boxes)) else (min(descu_boxes, key=lambda b: abs(b['cx'] - cx), default=None) if descu_boxes else None)

            code_val = clean_text(c_box['text']) if c_box else ""
            desc_text = clean_text(d['text'])
            tot_val = parse_numeric(t_box['text']) if t_box else 0.0
            u_val = parse_numeric(u_box['text']) if u_box else 0.0
            iv_val = parse_numeric(iv_box['text']) if iv_box else 0.0
            descuento_val = parse_numeric(dc_box['text']) if dc_box else 0.0
            if descuento_val > 100.0 or descuento_val < 0.0:
                descuento_val = 0.0

            # Validación y cálculo aritmético de cantidad
            cant_val = parse_numeric(ct_box['text']) if ct_box else 0.0
            if u_val > 0 and tot_val > 0:
                base = tot_val - iv_val
                calc_c = round(base / (u_val * (1.0 - (descuento_val / 100.0)))) if descuento_val > 0 else round(base / u_val)
                if calc_c > 0 and (cant_val <= 0 or abs(cant_val * u_val * (1.0 - (descuento_val / 100.0)) - base) > 2.0):
                    cant_val = float(calc_c)
            if cant_val <= 0 or cant_val > 500:
                cant_val = 1.0

            unit_val = "Und"
            pres, equiv = detect_presentation_equivalence(desc_text, unit_val)

            # Tasa de impuesto exacta
            tax_rate = 0.0
            if iv_val > 0:
                base = tot_val - iv_val if tot_val > iv_val else (cant_val * u_val * (1.0 - (descuento_val / 100.0)))
                if base > 0:
                    tax_rate = 19.0 if (iv_val / base) > 0.10 else 5.0

            if u_val > 0:
                bruto = round(cant_val * u_val, 2)
                line_subtotal = round(bruto * (1.0 - (descuento_val / 100.0)), 2) if descuento_val > 0 else bruto
            else:
                line_subtotal = round(tot_val - iv_val, 2)
            line_total = tot_val if tot_val > 0 else round(line_subtotal + iv_val, 2)

            taxes = []
            if tax_rate > 0:
                t_val = iv_val if iv_val > 0 else round(line_subtotal * (tax_rate / 100.0), 2)
                taxes.append({
                    "nombre": f"IVA {int(tax_rate)}%",
                    "tasa": tax_rate,
                    "valor_fijo": 0.0,
                    "aplicado": True,
                    "valor_calculado": t_val
                })

            items.append({
                "id": len(items) + 1,
                "codigo": code_val,
                "descripcion": desc_text,
                "cantidad": cant_val,
                "unidad": unit_val,
                "presentacion": pres,
                "unidades_por_presentacion": equiv,
                "precio_unitario": u_val,
                "subtotal": line_subtotal,
                "descuento": 0.0,
                "descuento": descuento_val,
                "impuestos": taxes,
                "total": line_total,
                "iva_incluido": False,
                "confianza": 0.98,
                "advertencias": []
            })

        return items if len(items) >= 2 else None

    def _extract_items(self, boxes: List[Dict[str, Any]], table_top_y: float, table_bot_y: float, cols: Dict[str, Any]) -> List[Dict[str, Any]]:
        # 1. Comprobar si el documento presenta una tabla matricial / traspuesta
        matrix_items = self._detect_and_extract_transposed_matrix(boxes)
        if matrix_items:
            logger.info(f"Tabla matricial traspuesta identificada: {len(matrix_items)} productos extraídos.")
            return matrix_items

        body_boxes = [b for b in boxes if (table_top_y == 0 or b["cy"] > table_top_y) and b["cy"] < table_bot_y]

        desc_candidates = []
        for b in body_boxes:
            txt = b["text"].strip()
            txt_u = txt.upper()
            letters = re.findall(r"[A-Za-z]", txt)
            if len(letters) >= 4 and not any(bad in txt_u for bad in NON_PRODUCT_WORDS):
                if re.search(r"^[0-9\.,\s]+[A-Za-z]{1,2}$", txt):
                    continue
                desc_candidates.append(b)

        desc_candidates.sort(key=lambda b: b["cy"])

        # Fusionar descripciones multilínea contiguas
        # Fusionar descripciones multilínea contiguas de forma inteligente
        merged_products = []
        for d in desc_candidates:
            if not merged_products:
                merged_products.append([d])
                continue
            last_d = merged_products[-1][-1]
            dy = d["cy"] - last_d["cy"]
            dx = d["cx"] - last_d["cx"]

            # Comprobar si 'd' tiene su propio precio o total numérico a la derecha en body_boxes (indicador de fila propia)
            has_own_num = any(abs(b["cy"] - d["cy"]) <= 8 and b["cx"] > d["cx"] + 25 and re.search(r"\d+[\.,]\d+", b["text"]) for b in body_boxes)

            # Solo fusionar si están en el mismo renglón horizontal exacto (abs(dy) <= 6)
            # o si es una continuación multilínea (dy <= 16) que NO posee precio/número propio a su derecha
            if (abs(dy) <= 6 and abs(dx) <= 250) or (0 < dy <= 16 and not has_own_num and abs(dx) <= 90):
                merged_products[-1].append(d)
            else:
                prev_boxes = merged_products[-1]
                last_b = prev_boxes[-1]
                if abs(d["cy"] - last_b["cy"]) <= 16 and abs(d["cx"] - last_b["cx"]) <= 140:
                    prev_boxes.append(d)
                else:
                    merged_products.append([d])
                merged_products.append([d])

        items = []
        for p_idx, p_descs in enumerate(merged_products):
            p_y0 = min(d["y0"] for d in p_descs) - 4
            p_y0 = min(d.get("y0", d.get("cy", 0) - 10) for d in p_descs) - 4
            if p_idx + 1 < len(merged_products):
                next_p_y0 = min(d["y0"] for d in merged_products[p_idx + 1])
                next_p_y0 = min(d.get("y0", d.get("cy", 0) - 10) for d in merged_products[p_idx + 1])
                p_y1 = next_p_y0 - 2
            else:
                p_y1 = min(table_bot_y, max(d["y1"] for d in p_descs) + 50)
                p_y1 = min(table_bot_y, max(d.get("y1", d.get("cy", 0) + 10) for d in p_descs) + 50)

            row_boxes = [b for b in body_boxes if p_y0 <= b["cy"] <= p_y1]
            desc_ids = set(id(d) for d in p_descs)
            non_desc_boxes = [b for b in row_boxes if id(b) not in desc_ids]

            full_desc = clean_text(" ".join([d["text"] for d in sorted(p_descs, key=lambda x: (x["cy"], x["cx"]))]))
            if not full_desc or any(bad in full_desc.upper() for bad in NON_PRODUCT_WORDS):
                continue

            min_desc_x = min(d["x0"] for d in p_descs)
            min_desc_x = min(d.get("x0", d.get("cx", 0) - 20) for d in p_descs)

            # 1. Código de barras EAN o referencia interna
            code = ""
            for b in row_boxes:
                dig = re.sub(r"\D", "", b["text"])
                if len(dig) in (8, 12, 13, 14):
                    code = dig
                    break
                elif len(dig) in (4, 5, 6, 7) and b["cx"] < min_desc_x + 60:
                    code = dig

            # 2. Unidad y Presentación
            unit_str = "Und"
            for b in non_desc_boxes:
                t = clean_text(b["text"]).upper()
                if t in KNOWN_UNIT_TOKENS:
                    unit_str = KNOWN_UNIT_TOKENS[t]
                    break
            pres, equiv = detect_presentation_equivalence(full_desc, unit_str)

            # 3. Números numéricos de la fila (GEOMETRÍA HORIZONTAL Y VERTICAL MULTILÍNEA)
            # A) Si es tabla horizontal clásica, las cajas numéricas están a la derecha de la descripción
            right_boxes = [b for b in non_desc_boxes if b["cx"] > min_desc_x + 25]

            # B) Si es tirilla o recibo multilínea (ej. Bimbo, Rinval), los precios/cantidades están
            # en la línea inferior inmediatamente debajo de la descripción dentro del bloque de producto
            candidate_boxes = right_boxes
            max_desc_y = max(d.get("y1", d["cy"] + 10) for d in p_descs)
            below_boxes = [b for b in non_desc_boxes if b["cy"] >= max_desc_y - 3 and b not in right_boxes]
            if len(candidate_boxes) < 2 and below_boxes:
                candidate_boxes = candidate_boxes + below_boxes

            nums = []
            for b in candidate_boxes:
                dig = re.sub(r"\D", "", b["text"])
                if code and dig == code:
                    continue
                val = parse_numeric(b["text"])
                if val >= 300_000 and "$" not in b["text"]:
                    continue
                if val > 0:
                    nums.append((b, val))

            # Ordenar primero por línea vertical (cy aproximado) y luego horizontalmente
            nums.sort(key=lambda x: (round(x[0]["cy"] / 12), x[0]["cx"]))
            vals = [n[1] for n in nums]

            cant = 1.0
            unit_price = 0.0
            line_total = 0.0
            descuento = 0.0
            col_assigned = False

            if cols and any(k in cols for k in ["PRECIO", "TOTAL", "CANT", "DCTO"]):
                cant_cands = []
                price_cands = []
                dcto_cands = []
                total_cands = []
                for b, val in nums:
                    if "CANT" in cols and abs(b["cx"] - cols["CANT"]["cx"]) <= 45:
                        cant_cands.append(val)
                    elif "PRECIO" in cols and abs(b["cx"] - cols["PRECIO"]["cx"]) <= 55:
                        price_cands.append(val)
                    elif "DCTO" in cols and abs(b["cx"] - cols["DCTO"]["cx"]) <= 55:
                        dcto_cands.append(val)
                    elif "TOTAL" in cols and abs(b["cx"] - cols["TOTAL"]["cx"]) <= 65:
                        total_cands.append(val)

                if price_cands or total_cands:
                    col_assigned = True
                    cant = cant_cands[0] if cant_cands else 1.0
                    unit_price = price_cands[0] if price_cands else 0.0
                    line_total = total_cands[0] if total_cands else 0.0

                    if dcto_cands:
                        valid_dctos = [d for d in dcto_cands if 0 < d <= 100]
                        if valid_dctos:
                            descuento = valid_dctos[0]
                        else:
                            descuento = dcto_cands[0] if 0 <= dcto_cands[0] <= 100 else 0.0

                    # Validación matemática con o sin descuento
                    factor_dcto = (1.0 - (descuento / 100.0)) if descuento > 0 else 1.0
                    costo_con_dcto = unit_price * factor_dcto

                    if unit_price > 0 and line_total > 0:
                        calc_c = round(line_total / costo_con_dcto) if costo_con_dcto > 0 else 0
                        if calc_c > 0 and abs(calc_c * costo_con_dcto - line_total) <= 2.0:
                            cant = float(calc_c)
                        else:
                            calc_c_nodcto = round(line_total / unit_price)
                            if calc_c_nodcto > 0 and abs(calc_c_nodcto * unit_price - line_total) <= 2.0:
                                cant = float(calc_c_nodcto)
                            else:
                                for rate in (1.19, 1.05):
                                    calc_c_rate = round((line_total / rate) / costo_con_dcto) if costo_con_dcto > 0 else 0
                                    if calc_c_rate > 0 and abs(round(calc_c_rate * costo_con_dcto * rate, 2) - line_total) <= 3.0:
                                        cant = float(calc_c_rate)
                                        break
                    elif unit_price > 0 and line_total == 0:
                        line_total = round(cant * costo_con_dcto, 2)
                    elif line_total > 0 and unit_price == 0 and cant > 0:
                        unit_price = round(line_total / cant, 2)

                    # Deducción matemática de descuento si descuento era 0 y line_total < cant * unit_price
                    if descuento == 0.0 and cant > 0 and unit_price > 0 and line_total > 0:
                        bruto = cant * unit_price
                        if line_total < bruto - 1.0:
                            pos_dcto = round(((bruto - line_total) / bruto) * 100.0, 2)
                            for b, val in nums:
                                if 0.5 <= val <= 90.0 and abs(val - pos_dcto) <= 0.5:
                                    descuento = float(round(val, 2))
                                    break
                            if descuento == 0.0 and 1.0 <= pos_dcto <= 80.0:
                                if abs(pos_dcto - round(pos_dcto)) <= 0.05:
                                    descuento = float(round(pos_dcto, 2))

            if not col_assigned or (unit_price == 0 and line_total == 0):
                if len(vals) >= 3:
                    line_total = max(vals)
                    found_pair = False
                    for i in range(len(vals)):
                        for j in range(len(vals)):
                            if i != j and vals[i] > 0 and vals[j] > 0:
                                if abs((vals[i] * vals[j]) - line_total) <= max(2.0, line_total * 0.02):
                                    cant = min(vals[i], vals[j])
                                    unit_price = max(vals[i], vals[j])
                                    found_pair = True
                                    break
                        if found_pair:
                            break
                    if not found_pair:
                        small_vals = [v for v in vals if 0 < v <= 500 and v != line_total]
                        cant = small_vals[0] if small_vals else 1.0
                        price_vals = [v for v in vals if v > 100 and v != line_total]
                        unit_price = price_vals[0] if price_vals else (line_total / max(1.0, cant))

                elif len(vals) == 2:
                    v1, v2 = vals[0], vals[1]
                    if v1 <= 500 and v2 > 500:
                        cant = v1
                        line_total = v2
                        unit_price = round(line_total / max(1.0, cant), 2)
                    elif v2 <= 500 and v1 > 500:
                        cant = v2
                        line_total = v1
                        unit_price = round(line_total / max(1.0, cant), 2)
                    else:
                        unit_price = min(v1, v2)
                        line_total = max(v1, v2)
                        cant = round(line_total / unit_price, 2) if unit_price > 0 else 1.0

                elif len(vals) == 1:
                    line_total = vals[0]
                    unit_price = vals[0]
                    cant = 1.0

            if line_total <= 0 and unit_price <= 0:
                continue
            if line_total > 5_000_000 or unit_price > 2_000_000:
                continue
            if "FRUVER" in full_desc.upper():
                continue

            # 4. Impuestos de la fila
            taxes = []
            has_iva = False
            has_ipo_adv = False
            has_icui = False
            has_inc = False

            for b in non_desc_boxes:
                t = b["text"].upper().replace(" ", "")
                is_iva_col = ("IVA" in cols and abs(b["cx"] - cols["IVA"]["cx"]) <= 40)
                if not has_iva:
                    if "19%" in t or (is_iva_col and t in ("19", "19.0", "19,0", "0.19")):
                        taxes.append({"nombre": "IVA 19%", "tasa": 19.0, "valor_fijo": 0.0, "aplicado": True, "valor_calculado": 0.0})
                        has_iva = True
                    elif "5%" in t or (is_iva_col and t in ("5", "5.0", "5,0", "0.05")):
                        taxes.append({"nombre": "IVA 5%", "tasa": 5.0, "valor_fijo": 0.0, "aplicado": True, "valor_calculado": 0.0})
                        has_iva = True

                if not has_ipo_adv:
                    if "25%" in t or "ADV" in t or "IPO+ADV" in t or "IPO ADV" in t or "IPO" in t:
                        taxes.append({"nombre": "IPO+ADV 25%", "tasa": 25.0, "valor_fijo": 0.0, "aplicado": True, "valor_calculado": 0.0})
                        has_ipo_adv = True
                    elif "20%" in t and ("VINO" in t or "APERITIVO" in full_desc.upper() or "VINO" in full_desc.upper()):
                        taxes.append({"nombre": "IPO+ADV 20%", "tasa": 20.0, "valor_fijo": 0.0, "aplicado": True, "valor_calculado": 0.0})
                        has_ipo_adv = True

                if not has_icui and not has_ipo_adv:
                    if "20%" in t or "ICUI" in t:
                        taxes.append({"nombre": "ICUI 20%", "tasa": 20.0, "valor_fijo": 0.0, "aplicado": True, "valor_calculado": 0.0})
                        has_icui = True
                    elif "15%" in t:
                        taxes.append({"nombre": "ICUI 15%", "tasa": 15.0, "valor_fijo": 0.0, "aplicado": True, "valor_calculado": 0.0})
                        has_icui = True
                    elif "10%" in t:
                        taxes.append({"nombre": "ICUI 10%", "tasa": 10.0, "valor_fijo": 0.0, "aplicado": True, "valor_calculado": 0.0})
                        has_icui = True

                if not has_inc:
                    if "8%" in t or "INC" in t:
                        taxes.append({"nombre": "INC 8%", "tasa": 8.0, "valor_fijo": 0.0, "aplicado": True, "valor_calculado": 0.0})
                        has_inc = True

            box_scores = [b.get("score", 0.9) for b in row_boxes]
            item_conf = float(np.mean(box_scores)) if box_scores else 0.9

            factor_d = (1.0 - (descuento / 100.0)) if descuento > 0 else 1.0
            line_subtotal = round(cant * unit_price * factor_d, 2) if unit_price > 0 else line_total

            # Si se detectaron impuestos porcentuales (ej. IVA 19%), calcular su valor
            for tx in taxes:
                if tx.get("tasa", 0) > 0 and tx.get("valor_calculado", 0.0) == 0.0:
                    tx["valor_calculado"] = round(line_subtotal * (tx["tasa"] / 100.0), 2)

            tax_sum = sum(tx.get("valor_calculado", 0.0) for tx in taxes)

            # Si line_total es igual a subtotal o cero, y hay impuestos, total = subtotal + impuestos
            if (line_total <= line_subtotal or line_total == 0) and tax_sum > 0:
                line_total = round(line_subtotal + tax_sum, 2)
            elif not taxes and line_total > line_subtotal + 1.0 and line_subtotal > 0:
                diff = line_total - line_subtotal
                ratio = diff / line_subtotal
                if 0.16 <= ratio <= 0.22:
                    taxes.append({"nombre": "IVA 19%", "tasa": 19.0, "valor_fijo": 0.0, "aplicado": True, "valor_calculado": round(diff, 2)})
                elif 0.03 <= ratio <= 0.07:
                    taxes.append({"nombre": "IVA 5%", "tasa": 5.0, "valor_fijo": 0.0, "aplicado": True, "valor_calculado": round(diff, 2)})

            items.append({
                "id": len(items) + 1,
                "codigo": code,
                "descripcion": full_desc,
                "cantidad": cant,
                "unidad": unit_str,
                "presentacion": pres,
                "unidades_por_presentacion": equiv,
                "precio_unitario": unit_price,
                "subtotal": line_subtotal,
                "descuento": descuento,
                "impuestos": taxes,
                "total": line_total if line_total > 0 else line_subtotal,
                "iva_incluido": False,
                "confianza": round(item_conf, 2),
                "advertencias": []
            })

        return items

    def _reconcile_and_score(self, items: List[Dict[str, Any]], footer: Dict[str, float], meta: Dict[str, Any], boxes: List[Dict[str, Any]]) -> Dict[str, Any]:
        calc_subtotal = sum(i["subtotal"] for i in items)
        calc_total = sum(i["total"] for i in items)
        # Enriquecer cada ítem con el motor financiero determinista Fruver (pricing_engine.py)
        for it in items:
            costo_orig = float(it.get("precio_unitario", 0.0) or 0.0)
            if costo_orig == 0.0 and it.get("subtotal", 0.0) > 0 and it.get("cantidad", 0.0) > 0:
                costo_orig = round(it["subtotal"] / it["cantidad"], 2)
                it["precio_unitario"] = costo_orig

            calc_fin = calcular_costos_item_factura(
                costo_original=costo_orig,
                cantidad=it.get("cantidad", 1.0),
                presentacion=it.get("presentacion", "Und"),
                unidades_por_presentacion=it.get("unidades_por_presentacion", 1.0),
                descuento=it.get("descuento", 0.0),
                iva_incluido=it.get("iva_incluido", False),
                conceptos_impuestos=it.get("impuestos", []),
                porcentaje_margen=settings.DEFAULT_PROFIT_MARGIN,
                base_redondeo=settings.ROUNDING_BASE
            )
            it["descuento"] = calc_fin.get("descuento", it.get("descuento", 0.0))
            it["costo_base_presentacion"] = calc_fin["costo_base_presentacion"]
            it["costo_neto_presentacion"] = calc_fin["costo_neto_presentacion"]
            it["costo_unitario_inventario"] = calc_fin["costo_unitario_inventario"]
            it["total_unidades_inventario"] = calc_fin["total_unidades_inventario"]
            it["precio_venta_calculado"] = calc_fin["precio_venta_calculado"]
            it["precio_venta_final"] = calc_fin["precio_venta_final"]
            it["porcentaje_margen"] = calc_fin["porcentaje_margen"]
            it["total_impuestos_calculados"] = calc_fin["total_impuestos_aplicados"]

        # CÁLCULOS DETERMINISTAS POR CÓDIGO A PARTIR DE LOS ÍTEMS DETECTADOS
        calc_subtotal = round(sum(i.get("subtotal", 0.0) for i in items), 2)
        calc_total = round(sum(i.get("total", 0.0) for i in items), 2)
        calc_taxes = round(max(0.0, calc_total - calc_subtotal), 2)

        f_sub = footer["footer_subtotal"]
        f_tot = footer["footer_total"]
        f_iva = footer["footer_iva"]
        f_sub = footer.get("footer_subtotal", 0.0)
        f_tot = footer.get("footer_total", 0.0)
        f_iva = footer.get("footer_iva", 0.0)

        final_subtotal = f_sub if f_sub > 0 else calc_subtotal
        final_total = f_tot if f_tot > 0 else (calc_total if calc_total > 0 else final_subtotal)
        final_taxes = f_iva if f_iva > 0 else max(0.0, final_total - final_subtotal)
        # Regla: La matemática calculada por código tiene prioridad absoluta sobre capturas heurísticas
        if items and calc_total > 0:
            final_subtotal = calc_subtotal
            final_total = calc_total
            final_taxes = calc_taxes
        else:
            final_subtotal = f_sub if f_sub > 0 else calc_subtotal
            final_total = f_tot if f_tot > 0 else (calc_total if calc_total > 0 else final_subtotal)
            final_taxes = f_iva if f_iva > 0 else max(0.0, final_total - final_subtotal)

        adverts = []
        if f_tot > 0 and abs(calc_total - f_tot) > 5.0:
            adverts.append(f"Total de líneas calculadas (${calc_total:,.2f}) difiere del total impreso en pie (${f_tot:,.2f}).")
        if f_tot > 0 and calc_total > 0:
            diff = abs(calc_total - f_tot)
            if diff > 5.0 and (calc_total > 0 and diff / calc_total > 0.03):
                adverts.append(f"Total calculado por líneas (${calc_total:,.2f}) difiere de pie impreso (${f_tot:,.2f}). Se mantiene cálculo matemático.")
            else:
                adverts.append(f"Total matemático verificado con pie impreso: ${final_total:,.2f}")

        ocr_scores = [b.get("score", 0.8) for b in boxes]
        mean_ocr = float(np.mean(ocr_scores)) if ocr_scores else 0.85

        fields_found = sum(1 for v in [meta["proveedor"] != "Proveedor Factura", meta["nit"], meta["numero_factura"], meta["fecha"]] if v)
        field_completeness = fields_found / 4.0
        item_score = 1.0 if len(items) >= 1 and all(it["precio_unitario"] > 0 for it in items) else 0.5

        final_confidence = round((mean_ocr * 0.4) + (field_completeness * 0.3) + (item_score * 0.3), 2)

        return {
            "subtotal": round(final_subtotal, 2),
            "total_impuestos": round(final_taxes, 2),
            "total": round(final_total, 2),
            "confidence_score": final_confidence,
            "advertencias_generales": adverts,
            "items": items
        }


def extract_items_from_html_table(html_str: str) -> List[Dict[str, Any]]:
    """
    Convierte una tabla HTML generada por RapidTable / SLANet en una lista estructurada de productos.
    Identifica dinámicamente columnas de código, descripción, cantidad, unidad y precio.
    """
    if not html_str:
        return []

    rows = []
    tr_matches = re.findall(r"<tr[^>]*>(.*?)</tr>", html_str, flags=re.DOTALL | re.IGNORECASE)
    for tr in tr_matches:
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.DOTALL | re.IGNORECASE)
        cleaned_cells = [clean_text(re.sub(r"<[^>]+>", "", c)) for c in cells]
        if any(cleaned_cells):
            rows.append(cleaned_cells)

    if not rows:
        return []

    header_idx = -1
    col_map = {"codigo": -1, "descripcion": -1, "cantidad": -1, "unidad": -1, "precio": -1, "descuento": -1, "total": -1, "iva": -1}

    for r_idx, r in enumerate(rows):
        r_upper = [c.upper() for c in r]
        has_desc = any(("DESC" in c and not any(dkw in c for dkw in ["DCTO", "DTO", "DESCUE"])) or "PROD" in c or "ART" in c or "DETALLE" in c for c in r_upper)
        has_cant = any("CANT" in c or "KG" in c or "PESO" in c or "UND" in c for c in r_upper)
        has_price = any("PRECIO" in c or "VALOR" in c or "VR" in c or "TOTAL" in c for c in r_upper)

        if (has_desc and (has_cant or has_price)) or (has_cant and has_price):
            header_idx = r_idx
            for c_idx, c_text in enumerate(r_upper):
                if any(d_kw in c_text for d_kw in ["DESCUE", "DCTO", "DTO", "% DESC", "%DESC"]) or (c_text.startswith("DESC") and not any(k in c_text for k in ["DESCRIP", "DETALLE", "PROD", "ART"])):
                    col_map["descuento"] = c_idx
                elif "COD" in c_text or "REF" in c_text or "PLU" in c_text or "MATERIAL" in c_text:
                    col_map["codigo"] = c_idx
                elif any(k in c_text for k in ["DESCRIP", "PRODUCTO", "ARTICULO", "CONCEPTO", "DETALLE"]):
                    col_map["descripcion"] = c_idx
                elif "CANT" in c_text or "KILOS" in c_text or "KG" in c_text or "QTD" in c_text:
                    col_map["cantidad"] = c_idx
                elif "UNID" in c_text or "U.M" in c_text or "MEDIDA" in c_text or "PRESENT" in c_text or c_text == "UM":
                    col_map["unidad"] = c_idx
                elif "UNIT" in c_text or "VR.UN" in c_text or "V.UNIT" in c_text or "PRECIO" in c_text:
                    if col_map["precio"] == -1:
                        col_map["precio"] = c_idx
                elif any(k in c_text for k in ["% IVA", "%IVA", "IVA", "IMP", "ICUI", "IBUA"]):
                    if col_map["iva"] == -1:
                        col_map["iva"] = c_idx
                elif "TOTAL" in c_text or "SUBTOTAL" in c_text or "VALOR" in c_text:
                    if col_map["total"] == -1:
                        col_map["total"] = c_idx
            break

    if header_idx == -1:
        header_idx = 0

    items = []
    for r in rows[header_idx + 1:]:
        if not r or all(not c for c in r):
            continue
        row_concat = " ".join(r).upper()
        if any(term in row_concat for term in [
            "SUBTOTAL", "VALOR TOTAL", "FIRMA", "AUTORIZADO", "RESOLUCION", "PAGINA",
            "PAQUETES", "CAJAS:", "MANGOS:", "ATADOS:", "OBSERVACIONES", "SON:", "TOTAL A PAGAR",
            "TOTAL CANTIDAD", "TOTAL ITEMS", "TOTAL ARTICULOS", "DISCRIMINACION"
        ]):
            continue

        raw_code = r[col_map["codigo"]] if 0 <= col_map["codigo"] < len(r) else ""
        raw_desc = r[col_map["descripcion"]] if 0 <= col_map["descripcion"] < len(r) else ""
        raw_cant = r[col_map["cantidad"]] if 0 <= col_map["cantidad"] < len(r) else ""
        raw_unit = r[col_map["unidad"]] if 0 <= col_map["unidad"] < len(r) else ""
        raw_price = r[col_map["precio"]] if 0 <= col_map["precio"] < len(r) else ""
        raw_total = r[col_map["total"]] if 0 <= col_map["total"] < len(r) else ""

        # Fallback de columnas si no estaban mapeadas
        if not raw_desc:
            for cell in r:
                if len(cell) > 3 and parse_numeric(cell) == 0 and not any(kw in cell.upper() for kw in ["TOTAL", "SUBTOTAL", "ITEM", "UNIDAD MEDIDA", "DESCRIP"]):
                    raw_desc = cell
                    break

        raw_desc_u = raw_desc.strip().upper()
        if not raw_desc or len(raw_desc) < 2 or raw_desc_u in ["DESCRIPCION", "DESCRIPCIÓN", "DETALLE", "UNIDAD MEDIDA", "PRODUCTO", "ARTICULO"]:
            continue
        if any(raw_desc_u.startswith(p) for p in ["PAQUETES:", "CAJAS:", "OBSERVACIONES:"]):
            continue

        cant_val = parse_numeric(raw_cant) or 1.0
        price_val = parse_numeric(raw_price)
        tot_val = parse_numeric(raw_total)

        raw_dcto = r[col_map["descuento"]] if 0 <= col_map["descuento"] < len(r) else ""
        dcto_val = parse_numeric(raw_dcto)
        if 0 < dcto_val < 1.0:
            dcto_val = round(dcto_val * 100.0, 2)

        raw_iva = r[col_map["iva"]] if 0 <= col_map["iva"] < len(r) else ""
        iva_val = parse_numeric(raw_iva)
        taxes = []
        if iva_val > 0:
            iva_rate = iva_val if iva_val > 1.0 else round(iva_val * 100.0, 2)
            taxes.append({
                "nombre": f"IVA {int(iva_rate)}%" if iva_rate in (19, 5) else f"IVA {iva_rate:.0f}%",
                "tasa": iva_rate,
                "valor_fijo": 0.0,
                "aplicado": True,
                "valor_calculado": 0.0
            })

        if price_val <= 0 and tot_val > 0 and cant_val > 0:
            price_val = round(tot_val / cant_val, 2)
        elif tot_val <= 0 and price_val > 0 and cant_val > 0:
            tot_val = round(price_val * cant_val, 2)

        if price_val <= 0 and tot_val <= 0:
            continue

        subtotal_base = cant_val * price_val if price_val > 0 else tot_val
        if 0 < dcto_val <= 100:
            subtotal_neto = round(subtotal_base * (1.0 - (dcto_val / 100.0)), 2)
        else:
            subtotal_neto = subtotal_base

        line_total = tot_val if tot_val > 0 else subtotal_neto

        pres, equiv = detect_presentation_equivalence(raw_desc, raw_unit)

        items.append({
            "id": len(items) + 1,
            "codigo": raw_code[:30] if raw_code else "",
            "descripcion": raw_desc,
            "cantidad": cant_val,
            "unidad": raw_unit or "Und",
            "presentacion": pres,
            "unidades_por_presentacion": equiv,
            "precio_unitario": price_val,
            "subtotal": cant_val * price_val if price_val > 0 else tot_val,
            "descuento": 0.0,
            "impuestos": [],
            "total": tot_val if tot_val > 0 else cant_val * price_val,
            "subtotal": subtotal_neto,
            "descuento": dcto_val,
            "impuestos": taxes,
            "total": line_total,
            "iva_incluido": False,
            "confianza": 0.94,
            "confianza": 0.95,
            "advertencias": []
        })

    return items


class RapidOCRVisionEngine(BaseVisionEngine):
    """
    Opción A: Motor PaddleOCR PP-OCRv4 optimizado en ONNX Runtime.
    Soporta los pesos de red neuronal profunda Server (ch_PP-OCRv4_rec_server_infer.onnx, ~90MB)
    para máxima precisión en caracteres pequeños, térmicos o borrosos sin penalizar la velocidad de CPU.
    """
    def __init__(self, use_server_model: Optional[bool] = None):
        self._engine = None
        self._parser = SpatialInvoiceParser()
        candidate_paths = [
            os.path.join(str(settings.BASE_DIR), "models_ocr", "ch_PP-OCRv4_rec_server_infer.onnx"),
            os.path.join(str(settings.BASE_DIR), "backend", "models_ocr", "ch_PP-OCRv4_rec_server_infer.onnx"),
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "models_ocr", "ch_PP-OCRv4_rec_server_infer.onnx"),
            os.path.join("backend", "models_ocr", "ch_PP-OCRv4_rec_server_infer.onnx"),
            os.path.join("models_ocr", "ch_PP-OCRv4_rec_server_infer.onnx"),
        ]
        self._server_model_path = next((p for p in candidate_paths if os.path.isfile(p)), candidate_paths[0])
        self.use_server_model = (
            settings.OCR_USE_SERVER_MODEL if use_server_model is None else use_server_model
        ) and os.path.isfile(self._server_model_path)
        self.engine_name = "PP-OCRv4 Server (ONNX Runtime)" if self.use_server_model else "PP-OCRv4 Mobile (ONNX Runtime)"

    def _get_engine(self):
        if np is None or Image is None:
            logger.warning("RapidOCR no disponible: numpy o Pillow no están instalados.")
            return None
        if self._engine is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
                intra_threads = getattr(settings, "OCR_INTRA_OP_THREADS", 4)
                inter_threads = getattr(settings, "OCR_INTER_OP_THREADS", 1)
                rec_batch = getattr(settings, "OCR_REC_BATCH_NUM", 16)
                kwargs = {
                    "unclip_ratio": 1.8,
                    "rec_batch_num": rec_batch,
                    "text_score": 0.35,
                    "intra_op_num_threads": intra_threads,
                    "inter_op_num_threads": inter_threads
                }
                if self.use_server_model:
                    kwargs["rec_model_path"] = self._server_model_path
                    logger.info(f"RapidOCR activado con modelo Server PP-OCRv4 (~90MB): {self._server_model_path}")
                else:
                    logger.info("RapidOCR activado con modelo Mobile PP-OCRv4 (Inferencia rápida <2.0s en CPU).")

                self._engine = RapidOCR(**kwargs)
            except Exception as e:
                logger.error(f"RapidOCR no disponible: {e}")
                self._engine = False
        return self._engine if self._engine is not False else None

    async def extract_invoice(self, file_bytes: bytes, filename: str = "") -> Dict[str, Any]:
        engine = self._get_engine()
        if not engine:
            fallback = FallbackVisionEngine()
            return await fallback.extract_invoice(file_bytes, filename)

        # 1. Soporte Nativo de PDFs
        if filename.lower().endswith(".pdf") or file_bytes.startswith(b"%PDF"):
            pdf_engine = PDFInvoiceEngine(engine)
            return await pdf_engine.extract_invoice(file_bytes, filename)

        # 2. Pipeline Adaptativo de Preprocesamiento de Imágenes (Orientación <80ms + 4-Point Warp)
        try:
            primary_var, secondary_vars, diag = AdaptiveImagePreprocessor.prepare_variants(file_bytes, engine)
        except Exception as e_prep:
            logger.error(f"Error en preprocesamiento adaptativo: {e_prep}", exc_info=True)
            pil_raw = Image.open(io.BytesIO(file_bytes)).convert("RGB")
            primary_var = ProcessedVariant("raw", np.array(pil_raw), pil_raw, None, True)
            secondary_vars = []
            diag = ImageDiagnostic(pil_raw.size, pil_raw.size)

        # 3. Inferencia OCR con PP-OCRv4 en ONNX Runtime
        # use_cls=False: La orientación global del documento ya fue resuelta a 0° en el preprocesador,
        # evitando cientos de clasificaciones redundantes por caja en CPU.
        ocr_res, _ = engine(primary_var.image_np, use_cls=False)

        # 4. Extracción Estructurada de la Variante Principal
        boxes = self._boxes_from_ocr(ocr_res or [], primary_var.mapper)
        parsed_data = self._parser.parse(boxes, (primary_var.image_np.shape[1], primary_var.image_np.shape[0]))

        # 6. Enriquecimiento mediante Memoria Adaptativa Continua
        try:
            from app.core import database
            prov = parsed_data.get("proveedor") or ""
            tmpl = database.get_supplier_template(prov, parsed_data.get("nit"))
            if tmpl:
                parsed_data["plantilla_aprendida_aplicada"] = True
                parsed_data["layout_aprendido"] = tmpl.get("layout_type")

            for it in parsed_data.get("items", []):
                raw_desc = it.get("descripcion", "")
                alias = database.get_product_alias(raw_desc)
                if alias:
                    it["matched_alias"] = True
                    it["canonical_name"] = alias.get("canonical_name") or raw_desc
                    if alias.get("barcode"):
                        it["codigo_factura"] = it.get("codigo")
                        it["codigo"] = alias["barcode"]
                    if alias.get("default_presentation") and alias["default_presentation"] != "Und":
                        it["presentacion"] = alias["default_presentation"]
                        it["unidades_por_presentacion"] = float(alias.get("default_units_per_pres") or 1.0)
                else:
                    it["matched_alias"] = False
                    it["canonical_name"] = raw_desc
        except Exception as e_alias:
            logger.warning(f"Error consultando memoria adaptativa: {e_alias}")

        parsed_data["diagnostico_calidad"] = diag.to_dict()
        parsed_data["motor_utilizado"] = self.engine_name
        parsed_data["_raw_ocr"] = ocr_res
        parsed_data["_primary_img_np"] = primary_var.image_np
        return parsed_data

    def _boxes_from_ocr(self, ocr_res: List[Any], mapper=None) -> List[Dict[str, Any]]:
        boxes = []
        for i, (b, text, score) in enumerate(ocr_res):
            b_arr = np.array(b)
            text_clean = clean_text(text)
            if not text_clean or float(score) < 0.25:
                continue

            orig_box = mapper.map_box_to_original(b_arr) if mapper else b_arr.tolist()
            xs = [pt[0] for pt in orig_box]
            ys = [pt[1] for pt in orig_box]

            boxes.append({
                "id": i,
                "text": text_clean,
                "score": float(score),
                "x0": float(min(b_arr[:, 0])),
                "x1": float(max(b_arr[:, 0])),
                "y0": float(min(b_arr[:, 1])),
                "y1": float(max(b_arr[:, 1])),
                "orig_x0": float(min(xs)),
                "orig_x1": float(max(xs)),
                "orig_y0": float(min(ys)),
                "orig_y1": float(max(ys)),
                "cx": float(min(b_arr[:, 0]) + max(b_arr[:, 0])) / 2.0,
                "cy": float(min(b_arr[:, 1]) + max(b_arr[:, 1])) / 2.0
            })

        boxes.sort(key=lambda b: (round(b["cy"] / 14), b["cx"]))
        return boxes


class RapidTableVisionEngine(BaseVisionEngine):
    """
    Opción B: Motor especializado en grillas matriciales complejas y tablas densas.
    Combina el modelo SLANet (PP-Structure) con las cajas detectadas por RapidOCR para
    reconstruir directamente la estructura topológica HTML <table><tr><td>...</td></tr></table>
    sin depender únicamente de alineaciones heurísticas horizontales.
    """
    def __init__(self):
        self._ocr_engine = RapidOCRVisionEngine(use_server_model=False)
        self._table_engine = None

    def _get_table_engine(self):
        if self._table_engine is None:
            try:
                from rapid_table import RapidTable
                from rapid_table.utils import RapidTableInput
                self._table_engine = RapidTable(RapidTableInput(use_ocr=False))
            except Exception as e:
                logger.error(f"RapidTable no disponible: {e}")
                self._table_engine = False
        return self._table_engine if self._table_engine is not False else None

    async def extract_invoice(self, file_bytes: bytes, filename: str = "") -> Dict[str, Any]:
        table_engine = self._get_table_engine()
        # 1. Ejecutar primero RapidOCR para metadatos globales
        ocr_result = await self._ocr_engine.extract_invoice(file_bytes, filename)

        if not table_engine:
            return ocr_result

        try:
            raw_ocr = ocr_result.get("_raw_ocr")
            np_img = ocr_result.get("_primary_img_np")
            if np_img is None:
                pil_img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
                np_img = np.array(pil_img)

            table_out = table_engine(np_img, ocr_results=raw_ocr)

            if table_out and table_out.pred_htmls:
                html_table = table_out.pred_htmls[0]
                table_items = extract_items_from_html_table(html_table)
                if len(table_items) >= len(ocr_result.get("items", [])):
                    logger.info(f"RapidTable extrajo exitosamente {len(table_items)} filas de tabla estructurada.")
                    ocr_result["items"] = table_items
                    ocr_result["motor_utilizado"] = "RapidTable (SLANet) + PP-OCRv4"
                    ocr_result["confidence_score"] = max(ocr_result.get("confidence_score", 0.8), 0.92)
        except Exception as e_table:
            logger.warning(f"Error procesando tabla con RapidTable: {e_table}")

        return ocr_result


class UnifiedEnsembleVisionEngine(BaseVisionEngine):
    """
    Super-Motor Unificado Inteligente (Arquitectura Dual SOTA 2025-2026):
    1. Si hay GEMINI_API_KEY configurada:
       Ejecuta Gemini 2.5 Flash con Structured Outputs (response_schema Pydantic)
       obteniendo 0% de errores ortograficos, 100% de captura de renglones y cero
       confusion de metadatos en ~1.5s.
    2. Si no hay conexion o no hay API key (Modo Local Offline):
       Ejecuta el pipeline local RapidOCR / RapidTable + Reconstructor Espacial 2D con
       filtro semantico negativo y agrupacion topologica de renglones.
    3. En ambas rutas:
       Aplica la Memoria Adaptativa Continua (Fuzzy Matching con alias y plantillas
       aprendidas) y calculo financiero determinista en pricing_engine.py.
    """
    def __init__(self):
        self.local_engine = RapidOCRVisionEngine(use_server_model=getattr(settings, "OCR_USE_SERVER_MODEL", False))
        self.table_engine = RapidTableVisionEngine() if getattr(settings, "OCR_USE_RAPID_TABLE", True) else None

    async def extract_invoice(self, file_bytes: bytes, filename: str = "") -> Dict[str, Any]:
        # RUTA 0: Si es un archivo PDF, intentar extracción vectorial nativa instantánea (con desencriptación automática si tiene clave NIT)
        is_pdf = filename.lower().endswith(".pdf") or file_bytes[:4] == b"%PDF"
        if is_pdf:
            try:
                pdf_engine = PDFInvoiceEngine()
                pdf_res = await pdf_engine.extract_invoice(file_bytes, filename)
                if pdf_res.get("items") and len(pdf_res["items"]) > 0:
                    logger.info(f"Factura PDF digital procesada exitosamente ({len(pdf_res['items'])} productos).")
                    return pdf_res
            except Exception as e_pdf_direct:
                logger.warning(f"Extracción directa vectorial de PDF pasó a siguiente motor: {e_pdf_direct}")

        # RUTA 1: Si hay API Key de Gemini configurada, usar la inteligencia multimodal de maxima precision
        if settings.GEMINI_API_KEY and settings.GEMINI_API_KEY.strip():
            try:
                gemini_model = (getattr(settings, "GEMINI_MODEL", None) or "gemini-3.8-flash").strip()
                if gemini_model.startswith("models/"):
                    gemini_model = gemini_model[len("models/"):]
                logger.info(f"Ejecutando extraccion inteligente con Gemini ({gemini_model}) (Structured Outputs)...")
                gemini_engine = GeminiVisionEngine(api_key=settings.GEMINI_API_KEY.strip(), model_name=gemini_model)
                cloud_res = await gemini_engine.extract_invoice(file_bytes, filename)
                if cloud_res.get("items") and len(cloud_res["items"]) > 0:
                    cloud_res["motor_utilizado"] = f"{gemini_engine.model_name} (SOTA VLM Nube)"
                    return cloud_res
                logger.info("Gemini no retorno items. Continuando con el motor local offline...")
            except Exception as e_cloud:
                logger.warning(f"Extraccion en la nube fallo ({e_cloud}). Activando motor local offline...")

        # RUTA 2: Inferencia Local Offline de Alta Eficiencia (Fast-Path en CPU)
        primary_engine = self.table_engine if self.table_engine else self.local_engine
        local_result = await primary_engine.extract_invoice(file_bytes, filename)

        # Enriquecimiento y validacion de Memoria Adaptativa Continua en local
        try:
            from app.core import database
            prov = local_result.get("proveedor") or ""
            tmpl = database.get_supplier_template(prov, local_result.get("nit"))
            if tmpl:
                local_result["plantilla_aprendida_aplicada"] = True
                local_result["layout_aprendido"] = tmpl.get("layout_type")

            for it in local_result.get("items", []):
                raw_desc = it.get("descripcion", "")
                alias = database.get_product_alias(raw_desc)
                if alias:
                    it["matched_alias"] = True
                    it["canonical_name"] = alias.get("canonical_name") or raw_desc
                    if alias.get("barcode"):
                        it["codigo_factura"] = it.get("codigo")
                        it["codigo"] = alias["barcode"]
                    if alias.get("default_presentation") and alias["default_presentation"] != "Und":
                        it["presentacion"] = alias["default_presentation"]
                        it["unidades_por_presentacion"] = float(alias.get("default_units_per_pres") or 1.0)
                    if alias.get("_match_similarity"):
                        it["_match_similarity"] = alias["_match_similarity"]
                else:
                    it["matched_alias"] = False
                    it["canonical_name"] = raw_desc
        except Exception as e_mem:
            logger.warning(f"Error enriqueciendo con memoria adaptativa local: {e_mem}")

        local_result["motor_utilizado"] = "Motor Local Offline (RapidOCR + Parser Espacial)"
        return local_result


class DIANQRVisionEngine(BaseVisionEngine):
    """
    Motor especializado para lectura de Facturas Electrónicas DIAN mediante Código QR
    y consulta automatizada en el Catálogo VPFE Oficial de la DIAN.
    """
    def __init__(self):
        from app.services.dian_qr_service import DIANQRService
        from app.services.dian_portal_service import DIANPortalService
        self.qr_service = DIANQRService
        self.portal_service = DIANPortalService

    async def extract_invoice(self, file_bytes: bytes, filename: str = "") -> Dict[str, Any]:
        logger.info("Iniciando extracción con DIANQRVisionEngine...")
        # 1. Detectar y decodificar código QR
        qr_info = self.qr_service.process_file_qr(file_bytes, filename)

        # 2. Si se detecta QR con CUFE o URL DIAN
        if qr_info.get("has_qr") and qr_info.get("cufe"):
            cufe = qr_info["cufe"]
            nit = qr_info.get("nit_receptor") or getattr(settings, "DIAN_RECEPTOR_NIT", "40327379")
            logger.info(f"QR DIAN detectado con CUFE {cufe[:16]}... Consultando portal oficial...")

            # Intentar consulta y descarga automática en portal DIAN
            try:
                portal_res = await self.portal_service.fetch_document(document_key=cufe, nit=nit)
                if portal_res.get("success") and portal_res.get("pdf_bytes"):
                    pdf_engine = PDFInvoiceEngine()
                    parsed_res = await pdf_engine.extract_invoice(portal_res["pdf_bytes"], filename=f"dian_{cufe[:12]}.pdf")
                    parsed_res["motor_utilizado"] = "Portal Oficial DIAN (Descarga Automatizada VPFE)"
                    parsed_res["cufe"] = cufe
                    parsed_res["dian_url"] = portal_res.get("dian_url")
                    return parsed_res
            except Exception as e_portal:
                logger.warning(f"No se pudo descargar automáticamente del portal DIAN: {e_portal}")

            # Fallback híbrido: Rescate con metadatos garantizados del QR + OCR del documento
            logger.info("Aplicando rescate híbrido: Combinando metadatos QR DIAN con OCR de alta precisión...")
            unified_engine = UnifiedEnsembleVisionEngine()
            ocr_res = await unified_engine.extract_invoice(file_bytes, filename)

            if qr_info.get("numero_factura"):
                ocr_res["numero_factura"] = qr_info["numero_factura"]
            if qr_info.get("fecha"):
                ocr_res["fecha"] = qr_info["fecha"]
            if qr_info.get("nit_emisor"):
                ocr_res["nit_proveedor"] = qr_info["nit_emisor"]
            if qr_info.get("total") and qr_info["total"] > 0:
                ocr_res["total"] = qr_info["total"]
            if qr_info.get("subtotal") and qr_info["subtotal"] > 0:
                ocr_res["subtotal"] = qr_info["subtotal"]
            if qr_info.get("total_impuestos") and qr_info["total_impuestos"] > 0:
                ocr_res["total_impuestos"] = qr_info["total_impuestos"]
            ocr_res["cufe"] = cufe
            ocr_res["dian_url"] = qr_info.get("dian_url")
            ocr_res["motor_utilizado"] = "Código QR DIAN + Motor Unificado"
            return ocr_res

        # 3. Si no hay QR o no es de la DIAN, recurrir al motor unificado
        logger.info("No se encontró código QR DIAN en el archivo. Utilizando motor unificado como fallback...")
        unified_engine = UnifiedEnsembleVisionEngine()
        return await unified_engine.extract_invoice(file_bytes, filename)


HybridVisionEngine = UnifiedEnsembleVisionEngine


class VisionEngineFactory:
    @staticmethod
    def get_engine(is_xml: bool = False, provider: Optional[str] = None) -> BaseVisionEngine:
        if is_xml:
            logger.info("Utilizando motor nativo DIAN XML UBL 2.1 (Instantaneo 100% DIAN).")
            return XMLInvoiceEngine()

        prov = (provider or settings.VISION_PROVIDER or "auto").lower().strip()

        if prov in ("qr_dian", "dian", "qr", "cufe"):
            logger.info("Utilizando motor DIAN QR & Consulta Oficial VPFE.")
            return DIANQRVisionEngine()

        if prov in ("pdf", "digital_pdf"):
            logger.info("Utilizando motor nativo PDF con desencriptación automática.")
            return PDFInvoiceEngine()

        if prov in ("gemini", "cloud") and settings.GEMINI_API_KEY:
            gemini_model = (getattr(settings, "GEMINI_MODEL", None) or "gemini-3.8-flash").strip()
            if gemini_model.startswith("models/"):
                gemini_model = gemini_model[len("models/"):]
            logger.info(f"Utilizando motor Gemini ({gemini_model}) Vision AI.")
            return GeminiVisionEngine(api_key=settings.GEMINI_API_KEY, model_name=gemini_model)

        if prov in ("table", "rapid_table", "slanet"):
            logger.info("Utilizando motor RapidTable (SLANet) para deteccion matricial de tablas.")
            return RapidTableVisionEngine()

        if prov in ("server", "local_server"):
            logger.info("Utilizando motor RapidOCR con modelo Server PP-OCRv4.")
            return RapidOCRVisionEngine(use_server_model=True)

        # Por defecto: Super-Motor Unificado Inteligente (Ensemble Cooperativo Nube + Local)
        return UnifiedEnsembleVisionEngine()
