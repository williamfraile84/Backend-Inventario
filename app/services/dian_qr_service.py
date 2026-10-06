import io
import re
import urllib.parse
from typing import Dict, Any, Optional, Tuple, List
import cv2
import numpy as np
from PIL import Image

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger("DIANQRService")


class DIANQRService:
    """
    Servicio de detección, decodificación y extracción de metadatos de códigos QR
    de Factura Electrónica de Venta de Colombia (DIAN / Catálogo VPFE).
    """

    @classmethod
    def detect_and_decode_qr(cls, file_bytes: bytes, filename: str = "") -> Optional[str]:
        """
        Detecta y decodifica el contenido textual de un código QR en una imagen o PDF.
        Aplica técnicas de preprocesamiento (CLAHE, escala de grises, umbralización)
        para asegurar alta tasa de detección en imágenes de cámara y tickets.
        """
        # Si es PDF, intentar extraer imagen de la primera página
        if filename.lower().endswith(".pdf") or file_bytes[:4] == b"%PDF":
            img_bytes = cls._extract_first_page_image_from_pdf(file_bytes)
            if img_bytes:
                file_bytes = img_bytes

        try:
            np_arr = np.frombuffer(file_bytes, np.uint8)
            img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
            if img is None:
                # Intentar con PIL como fallback
                pil_img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
                img = np.array(pil_img)
        except Exception as e:
            logger.warning(f"Error decodificando imagen para QR: {e}")
            return None

        detector = cv2.QRCodeDetector()

        # 1. Intento directo sobre la imagen original
        try:
            data, bbox, _ = detector.detectAndDecode(img)
            if data and data.strip():
                return data.strip()
        except Exception:
            pass

        # 2. Preprocesamiento: Escala de grises + CLAHE (Contraste adaptativo)
        try:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            enhanced = clahe.apply(gray)
            data, bbox, _ = detector.detectAndDecode(enhanced)
            if data and data.strip():
                return data.strip()
        except Exception:
            pass

        # 3. Preprocesamiento: Umbralización adaptativa (Otsu)
        try:
            _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            data, bbox, _ = detector.detectAndDecode(thresh)
            if data and data.strip():
                return data.strip()
        except Exception:
            pass

        # 4. Escaneo multi-escala si la imagen es de alta resolución
        h, w = img.shape[:2]
        if max(h, w) > 1800:
            scale = 1600.0 / max(h, w)
            resized = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
            data, bbox, _ = detector.detectAndDecode(resized)
            if data and data.strip():
                return data.strip()

        return None

    @classmethod
    def _extract_first_page_image_from_pdf(cls, pdf_bytes: bytes) -> Optional[bytes]:
        """Extrae la primera imagen utilizable de un PDF para buscar código QR."""
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
            if reader.is_encrypted:
                try:
                    nit = getattr(settings, "DIAN_RECEPTOR_NIT", "40327379")
                    reader.decrypt(nit)
                except Exception:
                    pass
            if len(reader.pages) > 0:
                p0 = reader.pages[0]
                if p0.images:
                    return p0.images[0].data
        except Exception as e:
            logger.debug(f"No se pudo extraer imagen de PDF: {e}")
        return None

    @classmethod
    def parse_dian_qr(cls, qr_text: str) -> Dict[str, Any]:
        """
        Parsea el texto decodificado de un código QR y extrae CUFE, URL DIAN,
        emisor, receptor y valores contables.
        """
        result: Dict[str, Any] = {
            "has_qr": True,
            "raw_qr_text": qr_text,
            "is_dian_qr": False,
            "cufe": None,
            "document_key": None,
            "dian_url": None,
            "numero_factura": None,
            "fecha": None,
            "nit_emisor": None,
            "nit_receptor": getattr(settings, "DIAN_RECEPTOR_NIT", "40327379"),
            "subtotal": 0.0,
            "total_impuestos": 0.0,
            "total": 0.0,
            "confidence_score": 0.99
        }

        clean = qr_text.strip()

        # 1. Caso: URL directa de consulta DIAN
        # Ej: https://catalogo-vpfe.dian.gov.co/User/SearchDocument?DocumentKey=4762e53ca28b77ae...
        if "catalogo-vpfe.dian.gov.co" in clean or "dian.gov.co" in clean:
            result["is_dian_qr"] = True
            result["dian_url"] = clean
            try:
                parsed_url = urllib.parse.urlparse(clean)
                qs = urllib.parse.parse_qs(parsed_url.query)
                doc_key = qs.get("DocumentKey", [None])[0] or qs.get("documentKey", [None])[0]
                if doc_key:
                    result["document_key"] = doc_key.strip()
                    result["cufe"] = doc_key.strip()
            except Exception:
                pass

        # 2. Caso: CUFE directo de 64 a 100 caracteres hexadecimales
        cufe_match = re.search(r"([0-9a-fA-F]{64,100})", clean)
        if cufe_match and not result.get("cufe"):
            result["cufe"] = cufe_match.group(1).lower()
            result["document_key"] = result["cufe"]
            result["is_dian_qr"] = True

        # 3. Caso: Formato DIAN estándar de parámetros (URL-encoded o clave=valor)
        # NumFac=FEP269491 FecFac=2026-10-02 NitFac=901741291 DocAdq=40327379 ValFac=61269 ValIva=11641 ValTotal=72910 CUFE=...
        pairs = re.findall(r"([A-Za-z0-9_]+)\s*[:=]\s*([^&\s\r\n\|]+)", clean)
        kv = {k.lower(): v.strip() for k, v in pairs}

        if kv:
            if "numfac" in kv:
                result["numero_factura"] = kv["numfac"]
                result["is_dian_qr"] = True
            if "fecfac" in kv:
                result["fecha"] = kv["fecfac"]
            if "nitfac" in kv:
                result["nit_emisor"] = re.sub(r"\D", "", kv["nitfac"])
            if "docadq" in kv:
                result["nit_receptor"] = re.sub(r"\D", "", kv["docadq"])
            if "valfac" in kv:
                result["subtotal"] = cls._parse_num(kv["valfac"])
            if "valiva" in kv:
                result["total_impuestos"] = cls._parse_num(kv["valiva"])
            if "valtotal" in kv:
                result["total"] = cls._parse_num(kv["valtotal"])
            if "cufe" in kv and not result.get("cufe"):
                result["cufe"] = kv["cufe"].lower()
                result["document_key"] = result["cufe"]
                result["is_dian_qr"] = True
            if "qrcode" in kv and not result.get("dian_url"):
                result["dian_url"] = kv["qrcode"]

        # 4. Caso: Formato DIAN delimitado por pipe (|)
        # NumFac|FecFac|HorFac|ValFac|CodImp1|ValImp1|...|CUFE|QRCode
        if "|" in clean and not kv:
            tokens = [t.strip() for t in clean.split("|")]
            if len(tokens) >= 5:
                result["is_dian_qr"] = True
                result["numero_factura"] = tokens[0]
                result["fecha"] = tokens[1]
                # Buscar números y CUFE entre los tokens
                for tok in tokens:
                    if re.match(r"^[0-9a-fA-F]{64,100}$", tok):
                        result["cufe"] = tok.lower()
                        result["document_key"] = result["cufe"]
                    elif "catalogo-vpfe.dian.gov.co" in tok:
                        result["dian_url"] = tok

        # Asegurar construcción de URL si se tiene el CUFE
        if result.get("cufe") and not result.get("dian_url"):
            result["dian_url"] = f"https://catalogo-vpfe.dian.gov.co/User/SearchDocument?DocumentKey={result['cufe']}"

        return result

    @classmethod
    def process_file_qr(cls, file_bytes: bytes, filename: str = "") -> Dict[str, Any]:
        """Detecta y parsea el QR en un solo paso."""
        qr_text = cls.detect_and_decode_qr(file_bytes, filename)
        if not qr_text:
            return {"has_qr": False, "is_dian_qr": False, "message": "No se detectó código QR en el documento."}
        return cls.parse_dian_qr(qr_text)

    @staticmethod
    def _parse_num(val: Any) -> float:
        try:
            s = str(val).replace(",", ".").strip()
            return float(s)
        except Exception:
            return 0.0

