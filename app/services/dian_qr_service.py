import io
import re
import urllib.parse
from typing import Dict, Any, Optional, Tuple, List
import cv2
import numpy as np
from PIL import Image, ImageOps

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger("DIANQRService")


class DIANQRService:
    """
    Servicio de detección, decodificación y extracción de metadatos de códigos QR
    de Factura Electrónica de Venta de Colombia (DIAN / Catálogo VPFE).
    Incorpora motor de nivel Google Lens (ZXing C++, PyZBar, OpenCV) con corrección
    EXIF, multi-escala, eliminación de sombras, filtros CLAHE y zoom en ojos del QR.
    """

    @classmethod
    def _decode_image_with_engines(cls, img: np.ndarray) -> Optional[str]:
        """Intenta decodificar el código QR con ZXing-C++ (motor Google), PyZBar y OpenCV."""
        if img is None or img.size == 0:
            return None

        # 1. Motor Primario: ZXing C++ (mismo algoritmo central de Google Lens)
        try:
            import zxingcpp
            # A. Con binarizador adaptativo local (ideal para fotos con sombras o brillo)
            results = zxingcpp.read_barcodes(
                img,
                try_rotate=True,
                try_downscale=True,
                try_invert=True,
                binarizer=zxingcpp.Binarizer.LocalAverage,
            )
            for r in results:
                if r.text and r.text.strip():
                    return r.text.strip()

            # B. Con binarizador de histograma global (ideal para alto contraste)
            results_hist = zxingcpp.read_barcodes(
                img,
                try_rotate=True,
                try_downscale=True,
                try_invert=True,
                binarizer=zxingcpp.Binarizer.GlobalHistogram,
            )
            for r in results_hist:
                if r.text and r.text.strip():
                    return r.text.strip()
        except Exception as e_zxing:
            logger.debug(f"ZXing decode error: {e_zxing}")

        # 2. Motor Secundario: PyZBar (ZBar C)
        try:
            import pyzbar.pyzbar as pyzbar
            from pyzbar.pyzbar import ZBarSymbol
            barcodes = pyzbar.decode(img, symbols=[ZBarSymbol.QRCODE])
            for b in barcodes:
                txt = b.data.decode("utf-8", errors="ignore").strip()
                if txt:
                    return txt
        except Exception as e_pyzbar:
            logger.debug(f"PyZBar decode error: {e_pyzbar}")

        # 3. Motor Terciario: OpenCV QRCodeDetector
        try:
            detector = cv2.QRCodeDetector()
            data, _, _ = detector.detectAndDecode(img)
            if data and data.strip():
                return data.strip()
        except Exception:
            pass

        return None

    @classmethod
    def _find_qr_candidate_regions(cls, gray: np.ndarray) -> List[np.ndarray]:
        """Localiza regiones candidatas de códigos QR mediante contornos y patrones concéntricos de esquina."""
        rois = []
        h, w = gray.shape[:2]
        try:
            _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
            contours, hierarchy = cv2.findContours(thresh, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
            if hierarchy is None or len(contours) == 0:
                return rois

            candidates = []
            for i, c in enumerate(contours):
                x, y, cw, ch = cv2.boundingRect(c)
                aspect = float(cw) / max(1, ch)
                if 0.75 <= aspect <= 1.3 and cw > 12 and ch > 12:
                    child_idx = hierarchy[0][i][2]
                    if child_idx != -1:
                        candidates.append((x, y, cw, ch))

            if len(candidates) >= 2:
                min_x = min(c[0] for c in candidates)
                min_y = min(c[1] for c in candidates)
                max_x = max(c[0] + c[2] for c in candidates)
                max_y = max(c[1] + c[3] for c in candidates)

                box_w = max_x - min_x
                box_h = max_y - min_y

                if 40 < max(box_w, box_h) < max(w, h) * 0.90:
                    pad_x = int(box_w * 0.35)
                    pad_y = int(box_h * 0.35)
                    x0 = max(0, min_x - pad_x)
                    y0 = max(0, min_y - pad_y)
                    x1 = min(w, max_x + pad_x)
                    y1 = min(h, max_y + pad_y)
                    cropped = gray[y0:y1, x0:x1]
                    if cropped.size > 0:
                        rois.append(cropped)
                        if max(cropped.shape[:2]) < 500:
                            rois.append(cv2.resize(cropped, (0, 0), fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC))
        except Exception as e:
            logger.debug(f"Error detectando regiones QR: {e}")
        return rois

    @classmethod
    def detect_and_decode_qr(cls, file_bytes: bytes, filename: str = "") -> Optional[str]:
        """
        Detecta y decodifica el contenido textual de un código QR en una imagen o PDF.
        Aplica un pipeline industrial multi-motor (ZXing-C++, PyZBar, OpenCV) con
        orientación EXIF corregida, pirámide multi-escala, ecualización CLAHE,
        eliminación de sombras y recorte de patrones de esquina (estilo Google Lens).
        """
        # 1. Si es PDF, delegar al extractor nativo multi-página (imágenes y texto vectorial)
        if filename.lower().endswith(".pdf") or file_bytes[:4] == b"%PDF":
            pdf_result = cls._extract_qr_from_pdf(file_bytes, filename)
            if pdf_result:
                return pdf_result
            return None

        # Cargar imagen corrigiendo automáticamente orientación EXIF de celulares
        try:
            pil_img = Image.open(io.BytesIO(file_bytes))
            pil_img = ImageOps.exif_transpose(pil_img)
            if pil_img.mode != "RGB":
                pil_img = pil_img.convert("RGB")
            img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
        except Exception as e:
            logger.warning(f"Error decodificando imagen con PIL EXIF: {e}")
            try:
                np_arr = np.frombuffer(file_bytes, np.uint8)
                img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
            except Exception:
                img = None

        if img is None:
            return None

        h, w = img.shape[:2]

        # 1. Intento directo sobre la imagen en resolución original
        data = cls._decode_image_with_engines(img)
        if data:
            logger.info("QR decodificado exitosamente en resolución original.")
            return data

        # 2. Si la imagen es muy grande (> 2200px), redimensionar a 2000px para equilibrar detalle y velocidad
        if max(h, w) > 2200:
            scale = 2000.0 / max(h, w)
            img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
            h, w = img.shape[:2]
            data = cls._decode_image_with_engines(img)
            if data:
                logger.info("QR decodificado exitosamente en resolución de 2000px.")
                return data

        # 3. Pirámide multi-escala rápida (1600px, 1200px, 800px)
        for target_max in [1600, 1200, 800]:
            if max(h, w) > target_max:
                scale = float(target_max) / max(h, w)
                scaled = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
                data = cls._decode_image_with_engines(scaled)
                if data:
                    logger.info(f"QR decodificado en escala {target_max}px.")
                    return data

        # Si la imagen es muy pequeña (ej. foto lejana o recorte), ampliar con interpolación cúbica
        if max(h, w) < 700:
            upscaled = cv2.resize(img, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)
            data = cls._decode_image_with_engines(upscaled)
            if data:
                return data

        # 3. Preprocesamiento: Escala de grises, contraste CLAHE y eliminación de sombras
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img

        # A. CLAHE (Contraste adaptativo local)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)
        data = cls._decode_image_with_engines(enhanced)
        if data:
            logger.info("QR decodificado con filtro CLAHE.")
            return data

        # B. Eliminación de sombras causadas por el celular o curvatura de papel térmico
        k = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25))
        tophat = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, k)
        blackhat = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, k)
        shadow_free = cv2.subtract(cv2.add(gray, tophat), blackhat)
        data = cls._decode_image_with_engines(shadow_free)
        if data:
            logger.info("QR decodificado tras eliminación de sombras morfológicas.")
            return data

        # C. Filtro de enfoque (Unsharp Mask para fotos desenfocadas)
        gaussian = cv2.GaussianBlur(gray, (0, 0), 2.0)
        sharpened = cv2.addWeighted(gray, 1.8, gaussian, -0.8, 0)
        data = cls._decode_image_with_engines(sharpened)
        if data:
            logger.info("QR decodificado con Unsharp Mask.")
            return data

        # D. Umbralización Otsu y Adaptativa Gaussian
        _, otsu = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        data = cls._decode_image_with_engines(otsu)
        if data:
            return data

        adaptive = cv2.adaptiveThreshold(enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 21, 5)
        data = cls._decode_image_with_engines(adaptive)
        if data:
            return data

        # 4. Zoom en región candidata del QR (Finder Pattern Clustering)
        candidate_rois = cls._find_qr_candidate_regions(gray)
        for roi in candidate_rois:
            data = cls._decode_image_with_engines(roi)
            if data:
                logger.info("QR decodificado mediante zoom en región candidata.")
                return data

        # 5. Escaneo por zonas y cuadrantes de facturas (Arriba derecha, abajo derecha, abajo izquierda, franja inferior)
        quadrants = [
            img[0:int(h * 0.45), int(w * 0.45):w],       # Arriba derecha
            img[int(h * 0.50):h, int(w * 0.45):w],       # Abajo derecha
            img[int(h * 0.50):h, 0:int(w * 0.55)],       # Abajo izquierda
            img[int(h * 0.65):h, 0:w],                   # Franja inferior completa
            img[int(h * 0.25):int(h * 0.75), int(w * 0.25):int(w * 0.75)], # Centro
        ]
        for q in quadrants:
            if q is None or q.size == 0:
                continue
            data = cls._decode_image_with_engines(q)
            if data:
                logger.info("QR decodificado en cuadrante específico de factura.")
                return data
            q_gray = cv2.cvtColor(q, cv2.COLOR_BGR2GRAY) if len(q.shape) == 3 else q
            data = cls._decode_image_with_engines(clahe.apply(q_gray))
            if data:
                return data

        # 6. Rotaciones de 90, 180, 270 grados en caso de fotos en apaisado
        for rot in [cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_180, cv2.ROTATE_90_COUNTERCLOCKWISE]:
            try:
                rotated = cv2.rotate(img, rot)
                data = cls._decode_image_with_engines(rotated)
                if data:
                    logger.info("QR decodificado tras rotación manual.")
                    return data
            except Exception:
                pass

        return None

    @classmethod
    def _extract_qr_from_pdf(cls, pdf_bytes: bytes, filename: str = "") -> Optional[str]:
        """
        Extrae y decodifica el código QR o CUFE directamente de un archivo PDF oficial de la DIAN.
        1. Desencripta automáticamente en memoria con NIT adquirente si el PDF viene protegido.
        2. Itera todas las imágenes de todas las páginas buscando el código QR oficial.
        3. Si las imágenes no contienen QR, busca el CUFE y metadatos en el texto vectorial del PDF.
        """
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
            if reader.is_encrypted:
                default_nit = getattr(settings, "DIAN_RECEPTOR_NIT", "") or ""
                candidates = [str(default_nit).strip()] if default_nit else []
                candidates.extend(["900000000", ""])
                decrypted = False
                for pwd in candidates:
                    if not pwd and pwd != "":
                        continue
                    try:
                        res = reader.decrypt(pwd)
                        if res:
                            decrypted = True
                            break
                    except Exception:
                        pass
                if not decrypted and reader.is_encrypted:
                    logger.warning("No se pudo desencriptar el PDF con los NITs candidatos conocidos.")

            # A. Buscar código QR visual en todas las imágenes de todas las páginas del PDF
            try:
                num_pages = len(reader.pages)
            except Exception:
                num_pages = 0

            for p_idx in range(min(num_pages, 8)):
                try:
                    page = reader.pages[p_idx]
                    for im in page.images:
                        try:
                            im_data = im.data
                            arr = np.frombuffer(im_data, np.uint8)
                            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                            if img is not None and min(img.shape[:2]) >= 50:
                                decoded = cls._decode_image_with_engines(img)
                                if decoded:
                                    logger.info(f"QR DIAN decodificado exitosamente de imagen '{im.name}' en pág. {p_idx+1}.")
                                    return decoded
                        except Exception:
                            continue
                except Exception:
                    continue

            # B. Si no se halló QR visual en imágenes, extraer CUFE y metadatos del texto digital del PDF
            for p_idx in range(min(num_pages, 8)):
                try:
                    page = reader.pages[p_idx]
                    txt = page.extract_text() or ""
                    if not txt:
                        continue

                    # Buscar CUFE
                    cufe_m = re.search(r"C[oó]digo\s+[ÚU]nico\s+de\s+Factura\s*-\s*CUFE\s*:\s*([0-9a-fA-F]{64,128})", txt, re.IGNORECASE)
                    if not cufe_m:
                        cufe_m = re.search(r"CUFE\s*:\s*([0-9a-fA-F]{64,128})", txt, re.IGNORECASE)
                    if not cufe_m:
                        cufe_m = re.search(r"\b([0-9a-fA-F]{64,100})\b", txt)

                    if cufe_m:
                        cufe = cufe_m.group(1).strip().lower()
                        logger.info(f"CUFE DIAN extraído del texto vectorial en pág. {p_idx+1}: {cufe[:16]}...")

                        num_m = re.search(r"N[uú]mero\s+de\s+Factura\s*:\s*([A-Za-z0-9_-]+)", txt, re.IGNORECASE)
                        num_fac = num_m.group(1).strip() if num_m else ""

                        fec_m = re.search(r"([0-9]{4}-[0-9]{2}-[0-9]{2})", txt)
                        fec_fac = fec_m.group(1).strip() if fec_m else ""

                        tot_m = re.findall(r"Total\s+factura\s*\(=\)[^\$0-9]*\$?\s*([0-9.,]+)", txt, re.IGNORECASE)
                        tot_fac = tot_m[-1].replace(",", "").strip() if tot_m else ""

                        synthesized = f"NumFac:{num_fac}\nFecFac:{fec_fac}\nValTolFac:{tot_fac}\nCUFE:{cufe}\nQRCode:https://catalogo-vpfe.dian.gov.co/document/searchqr?documentkey={cufe}"
                        return synthesized
                except Exception:
                    continue

        except Exception as e_pdf:
            logger.warning(f"Error procesando PDF para QR/CUFE: {e_pdf}")

        # C. Fallback: Si el nombre del archivo contiene un CUFE válido (ej. dian_<cufe>.pdf)
        fn_cufe = re.search(r"([0-9a-fA-F]{64,100})", filename or "")
        if fn_cufe:
            cufe = fn_cufe.group(1).lower()
            return f"CUFE:{cufe}\nQRCode:https://catalogo-vpfe.dian.gov.co/document/searchqr?documentkey={cufe}"

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
            "nit_receptor": getattr(settings, "DIAN_RECEPTOR_NIT", "") or "",
            "subtotal": 0.0,
            "total_impuestos": 0.0,
            "total": 0.0,
            "confidence_score": 0.99
        }

        clean = qr_text.strip()

        # 1. Buscar CUFE directo de 64 a 100 caracteres hexadecimales
        cufe_match = re.search(r"\b([0-9a-fA-F]{64,100})\b", clean)
        if not cufe_match:
            cufe_match = re.search(r"([0-9a-fA-F]{64,100})", clean)
        if cufe_match:
            result["cufe"] = cufe_match.group(1).lower()
            result["document_key"] = result["cufe"]
            result["is_dian_qr"] = True

        # 2. Buscar URL oficial del Catálogo VPFE dentro del texto
        url_match = re.search(r"(https?://catalogo-vpfe\.dian\.gov\.co/[^\s\r\n\|]+)", clean, re.IGNORECASE)
        if url_match:
            result["is_dian_qr"] = True
            found_url = url_match.group(1).strip()
            try:
                parsed_url = urllib.parse.urlparse(found_url)
                qs = urllib.parse.parse_qs(parsed_url.query)
                doc_key = qs.get("DocumentKey", [None])[0] or qs.get("documentKey", [None])[0] or qs.get("documentkey", [None])[0]
                if doc_key and not result.get("cufe"):
                    result["document_key"] = doc_key.strip().lower()
                    result["cufe"] = doc_key.strip().lower()
            except Exception:
                pass

        # 3. Caso: Formato DIAN estándar de parámetros (URL-encoded o clave=valor)
        # NumFac=FEP269491 FecFac=2026-10-02 NitFac=900000000 DocAdq=900000000 ValFac=61269 ValIva=11641 ValTolFac=72910 CUFE=...
        pairs = re.findall(r"([A-Za-z0-9_]+)\s*[:=]\s*([^&\s\r\n\|]+)", clean)
        kv = {k.lower(): v.strip() for k, v in pairs}

        if kv:
            if "numfac" in kv:
                result["numero_factura"] = kv["numfac"]
                result["is_dian_qr"] = True
            elif "numerofactura" in kv:
                result["numero_factura"] = kv["numerofactura"]
                result["is_dian_qr"] = True

            if "fecfac" in kv:
                result["fecha"] = kv["fecfac"]
            elif "fecha" in kv:
                result["fecha"] = kv["fecha"]

            if "nitfac" in kv:
                result["nit_emisor"] = re.sub(r"\D", "", kv["nitfac"])
            elif "nitemisor" in kv:
                result["nit_emisor"] = re.sub(r"\D", "", kv["nitemisor"])

            if "docadq" in kv:
                result["nit_receptor"] = re.sub(r"\D", "", kv["docadq"])
            elif "nitreceptor" in kv:
                result["nit_receptor"] = re.sub(r"\D", "", kv["nitreceptor"])

            if "valfac" in kv:
                result["subtotal"] = cls._parse_num(kv["valfac"])

            if "valiva" in kv:
                result["total_impuestos"] = cls._parse_num(kv["valiva"])

            # Total: Soporta ValTotal, ValTolFac, ValTol, Total
            total_found = None
            for tk in ["valtotal", "valtolfac", "valtol", "total"]:
                if tk in kv:
                    val = cls._parse_num(kv[tk])
                    if val > 0:
                        total_found = val
                        break
            if total_found is not None:
                result["total"] = total_found
            elif result["subtotal"] > 0 and result["total_impuestos"] > 0:
                result["total"] = round(result["subtotal"] + result["total_impuestos"], 2)

            if "cufe" in kv and not result.get("cufe"):
                result["cufe"] = kv["cufe"].lower()
                result["document_key"] = result["cufe"]
                result["is_dian_qr"] = True

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
                        url_m = re.search(r"(https?://catalogo-vpfe\.dian\.gov\.co/[^\s\|]+)", tok, re.IGNORECASE)
                        if url_m:
                            result["dian_url"] = url_m.group(1).strip()

        # 5. Asegurar URL canónica válida oficial de la DIAN si se tiene el CUFE
        if result.get("cufe"):
            result["dian_url"] = f"https://catalogo-vpfe.dian.gov.co/document/searchqr?documentkey={result['cufe']}"
        elif not result.get("dian_url") and url_match:
            result["dian_url"] = url_match.group(1).strip()

        return result

    _rapid_ocr_instance = None

    @classmethod
    def _get_ocr_instance(cls):
        if cls._rapid_ocr_instance is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
                cls._rapid_ocr_instance = RapidOCR()
            except Exception as e:
                logger.warning(f"No se pudo cargar RapidOCR singleton: {e}")
        return cls._rapid_ocr_instance

    @classmethod
    def extract_cufe_via_ocr(cls, file_bytes: bytes, filename: str = "") -> Optional[Dict[str, Any]]:
        """
        Extracción de alta precisión mediante OCR para facturas impresas o fotografiadas
        donde el código QR sufrió distorsión o baja resolución, pero el código CUFE
        o la URL oficial es visible en el cuerpo del texto.
        """
        try:
            ocr = cls._get_ocr_instance()
            if not ocr:
                return None

            # Si es PDF, intentar extracción nativa del documento
            if filename.lower().endswith(".pdf") or file_bytes[:4] == b"%PDF":
                pdf_res = cls._extract_qr_from_pdf(file_bytes, filename)
                if pdf_res:
                    return cls.parse_dian_qr(pdf_res)
                return None

            # Reescalar imagen si es muy grande para que RapidOCR ejecute en subsegundos
            try:
                np_arr = np.frombuffer(file_bytes, np.uint8)
                img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
                if img is not None:
                    h, w = img.shape[:2]
                    max_dim = 1600
                    if max(h, w) > max_dim:
                        scale = max_dim / float(max(h, w))
                        img_small = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
                        _, buf = cv2.imencode(".jpg", img_small, [cv2.IMWRITE_JPEG_QUALITY, 85])
                        file_bytes = buf.tobytes()
            except Exception:
                pass

            ocr_results, _ = ocr(file_bytes)
            if not ocr_results:
                return None

            lines = [box[1] for box in ocr_results if box and len(box) >= 2]
            full_text = " ".join(lines)

            cufe_str = None

            # 1. Buscar URL oficial del Catálogo VPFE con DocumentKey en el texto
            url_match = re.search(r"catalogo-vpfe\.dian\.gov\.co[^\s]*DocumentKey=([0-9a-fA-F]{64,128})", full_text, re.IGNORECASE)
            if url_match:
                cufe_str = url_match.group(1).lower()
                logger.info(f"CUFE recuperado por URL en texto OCR: {cufe_str[:20]}...")

            # 2. Buscar token hexadecimal continuo de 64 a 96 caracteres
            if not cufe_str:
                cufe_direct = re.search(r"\b([0-9a-fA-F]{64,96})\b", full_text)
                if cufe_direct:
                    cufe_str = cufe_direct.group(1).lower()
                    logger.info(f"CUFE continuo recuperado mediante OCR: {cufe_str[:20]}...")

            # 3. Buscar CUFE multi-línea (tickets donde el código de 96 caracteres se divide en 2 o 3 renglones)
            if not cufe_str:
                cufe_label_m = re.search(r"(?:CUFE|C[oó]digo\s+[ÚU]nico)[^\w\s]*\s*([0-9a-fA-F\s\-]{64,140})", full_text, re.IGNORECASE)
                if cufe_label_m:
                    cleaned = re.sub(r"[^0-9a-fA-F]", "", cufe_label_m.group(1))
                    if 64 <= len(cleaned) <= 100:
                        cufe_str = cleaned.lower()
                        logger.info(f"CUFE multi-línea recuperado mediante OCR: {cufe_str[:20]}...")

            # 4. Escanear cajas OCR consecutivas tras la etiqueta "CUFE"
            if not cufe_str:
                for i, line in enumerate(lines):
                    if re.search(r"\b(?:CUFE|C[oó]digo\s+[ÚU]nico)\b", line, re.IGNORECASE):
                        chunks = lines[i:min(len(lines), i + 4)]
                        merged_hex = re.sub(r"[^0-9a-fA-F]", "", "".join(chunks))
                        hex_match = re.search(r"([0-9a-fA-F]{64,96})", merged_hex)
                        if hex_match:
                            cufe_str = hex_match.group(1).lower()
                            logger.info(f"CUFE por bloques consecutivos recuperado: {cufe_str[:20]}...")
                            break

            if cufe_str:
                parsed = cls.parse_dian_qr(cufe_str)
                parsed["is_dian_qr"] = True
                parsed["has_qr"] = True
                parsed["raw_qr_text"] = f"CUFE:{cufe_str}"

                # Extraer número de factura si está visible
                num_fac_match = re.search(
                    r"(?:Factura|Factura Electr[oó]nica|FE|Factura de Venta)[\s\w]*N[o°\.\s]*([A-Za-z0-9\-]+)",
                    full_text,
                    re.IGNORECASE
                )
                if num_fac_match and not parsed.get("numero_factura"):
                    parsed["numero_factura"] = num_fac_match.group(1).strip()

                # Extraer NIT emisor si está visible
                nit_match = re.search(r"NIT[\s.:]*([0-9]{7,10})", full_text, re.IGNORECASE)
                if nit_match and not parsed.get("nit_emisor"):
                    parsed["nit_emisor"] = nit_match.group(1).strip()

                return parsed
        except Exception as e:
            logger.warning(f"Error extrayendo CUFE mediante OCR: {e}")
        return None

    @classmethod
    def process_file_qr(cls, file_bytes: bytes, filename: str = "", allow_ocr_fallback: bool = True) -> Dict[str, Any]:
        """Detecta y parsea el QR en un solo paso. Si el QR visual no se detecta y allow_ocr_fallback es True, intenta extracción por OCR del CUFE."""
        qr_text = cls.detect_and_decode_qr(file_bytes, filename)
        if qr_text:
            return cls.parse_dian_qr(qr_text)

        # Fallback de alta precisión con OCR para facturas con CUFE impreso
        if allow_ocr_fallback:
            ocr_cufe = cls.extract_cufe_via_ocr(file_bytes, filename)
            if ocr_cufe:
                return ocr_cufe

        return {
            "has_qr": False,
            "is_dian_qr": False,
            "message": "No se detectó ningún código QR o CUFE DIAN legible en el documento o imagen."
        }

    @staticmethod
    def _parse_num(val: Any) -> float:
        try:
            s = str(val).replace(",", ".").strip()
            return float(s)
        except Exception:
            return 0.0

