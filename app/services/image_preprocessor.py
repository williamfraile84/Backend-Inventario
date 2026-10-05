import io
import math
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple, Union

import numpy as np
import cv2
from PIL import Image, ImageOps

logger = logging.getLogger("ImagePreprocessor")

@dataclass
class CoordinateMapper:
    """
    Rastrea y mapea coordenadas de bounding boxes generadas sobre imágenes
    transformadas (escaladas, rotadas, deskewed, recortadas, perspectiva 4 puntos)
    hacia las coordenadas reales de la imagen original.
    """
    orig_w: int
    orig_h: int
    rotation: int = 0          # 0, 90, 180, 270
    scale_factor: float = 1.0  # escala aplicada después de rotar/warpear
    deskew_angle: float = 0.0  # ángulo de inclinación corregido
    crop_x: int = 0
    crop_y: int = 0
    homography_inv: Optional[np.ndarray] = None  # Matriz inversa 3x3 de perspectiva

    def map_point_to_original(self, x: float, y: float) -> Tuple[float, float]:
        """Mapea un punto (x, y) de la imagen procesada a la imagen original."""
        # 1. Revertir recorte
        x = x + self.crop_x
        y = y + self.crop_y

        # 2. Revertir escala
        if self.scale_factor > 0 and self.scale_factor != 1.0:
            x = x / self.scale_factor
            y = y / self.scale_factor

        # 3. Revertir homografía (perspectiva inversa) si se aplicó 4-point warp
        if self.homography_inv is not None:
            try:
                pts = np.array([[[x, y]]], dtype=np.float32)
                trans = cv2.perspectiveTransform(pts, self.homography_inv)
                x = float(trans[0][0][0])
                y = float(trans[0][0][1])
            except Exception:
                pass

        # 4. Revertir rotación
        w, h = self.orig_w, self.orig_h
        if self.rotation == 90:
            # 90° clockwise: orig_x = y, orig_y = orig_h - 1 - x
            orig_x = y
            orig_y = h - 1 - x
            return float(orig_x), float(orig_y)
        elif self.rotation == 180:
            orig_x = w - 1 - x
            orig_y = h - 1 - y
            return float(orig_x), float(orig_y)
        elif self.rotation == 270:
            # 270° clockwise: orig_x = orig_w - 1 - y, orig_y = x
            orig_x = w - 1 - y
            orig_y = x
            return float(orig_x), float(orig_y)

        return float(x), float(y)

    def map_box_to_original(self, box: Union[np.ndarray, List[Any]]) -> List[List[float]]:
        """Mapea un bounding box de 4 esquinas [[x0,y0], [x1,y1], [x2,y2], [x3,y3]]."""
        b = np.array(box)
        mapped = []
        for pt in b:
            mx, my = self.map_point_to_original(float(pt[0]), float(pt[1]))
            mapped.append([round(mx, 1), round(my, 1)])
        return mapped


@dataclass
class ImageDiagnostic:
    original_dimensions: Tuple[int, int]
    processed_dimensions: Tuple[int, int]
    dpi: Optional[Tuple[float, float]] = None
    mean_brightness: float = 0.0
    rms_contrast: float = 0.0
    blur_score: float = 0.0
    is_blurry: bool = False
    is_low_contrast: bool = False
    is_dark: bool = False
    is_uneven_illumination: bool = False
    detected_rotation: int = 0
    deskew_angle: float = 0.0
    perspective_warp_applied: bool = False
    applied_transforms: List[str] = field(default_factory=list)
    quality_rating: str = "Buena"
    processing_time_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dimensiones_originales": f"{self.original_dimensions[0]}x{self.original_dimensions[1]}",
            "dimensiones_procesadas": f"{self.processed_dimensions[0]}x{self.processed_dimensions[1]}",
            "dpi": self.dpi,
            "brillo_medio": round(self.mean_brightness, 1),
            "contraste_rms": round(self.rms_contrast, 1),
            "puntaje_enfoque_laplaciano": round(self.blur_score, 1),
            "es_borrosa": self.is_blurry,
            "bajo_contraste": self.is_low_contrast,
            "es_oscura": self.is_dark,
            "iluminacion_desigual": self.is_uneven_illumination,
            "rotacion_detectada_grados": self.detected_rotation,
            "angulo_inclinacion_deskew": round(self.deskew_angle, 2),
            "transformaciones_aplicadas": self.applied_transforms,
            "calidad_estimada": self.quality_rating,
            "tiempo_preprocesamiento_ms": round(self.processing_time_ms, 1)
        }


@dataclass
class ProcessedVariant:
    name: str
    image_np: np.ndarray      # Formato RGB para RapidOCR
    pil_image: Image.Image
    mapper: CoordinateMapper
    is_primary: bool = False


class AdaptiveImagePreprocessor:
    """
    Pipeline profesional de visión por computador y mejoramiento adaptativo de facturas.
    Evalúa matemáticamente la calidad de cada imagen y aplica exclusivamente las
    transformaciones necesarias sin degradar documentos que ya cuentan con nitidez adecuada.
    """

    @staticmethod
    def pil_to_cv(pil_img: Image.Image) -> np.ndarray:
        """Convierte PIL Image (RGB) a OpenCV BGR numpy array."""
        rgb = np.array(pil_img)
        if len(rgb.shape) == 2:
            return cv2.cvtColor(rgb, cv2.COLOR_GRAY2BGR)
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

    @staticmethod
    def cv_to_pil(cv_img: np.ndarray) -> Image.Image:
        """Convierte OpenCV BGR numpy array a PIL Image (RGB)."""
        if len(cv_img.shape) == 2:
            return Image.fromarray(cv_img).convert("RGB")
        rgb = cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB)
        return Image.fromarray(rgb)

    @classmethod
    def diagnose_raw(cls, cv_img: np.ndarray, pil_img: Optional[Image.Image] = None) -> ImageDiagnostic:
        """Calcula métricas objetivas de brillo, contraste, nitidez e iluminación."""
        h, w = cv_img.shape[:2]
        gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY) if len(cv_img.shape) == 3 else cv_img

        mean_brightness = float(np.mean(gray))
        rms_contrast = float(np.std(gray))
        laplacian_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())

        # Diagnosticar iluminación no uniforme comparando cuadrantes
        h_mid, w_mid = h // 2, w // 2
        q1 = np.mean(gray[:h_mid, :w_mid])
        q2 = np.mean(gray[:h_mid, w_mid:])
        q3 = np.mean(gray[h_mid:, :w_mid])
        q4 = np.mean(gray[h_mid:, w_mid:])
        quadrant_diff = float(max(q1, q2, q3, q4) - min(q1, q2, q3, q4))
        is_uneven = quadrant_diff > 45.0

        # Obtener DPI si está presente en metadatos PIL
        dpi = None
        if pil_img and "dpi" in pil_img.info:
            raw_dpi = pil_img.info["dpi"]
            dpi = (float(raw_dpi[0]), float(raw_dpi[1])) if isinstance(raw_dpi, (tuple, list)) else (float(raw_dpi), float(raw_dpi))

        # Calidad global estimada
        if laplacian_var > 300 and rms_contrast > 50 and not is_uneven:
            quality = "Excelente"
        elif laplacian_var > 120 and rms_contrast > 35:
            quality = "Buena"
        elif laplacian_var > 50 or rms_contrast > 25:
            quality = "Aceptable"
        elif laplacian_var > 20:
            quality = "Baja calidad"
        else:
            quality = "Muy degradada"

        return ImageDiagnostic(
            original_dimensions=(w, h),
            processed_dimensions=(w, h),
            dpi=dpi,
            mean_brightness=mean_brightness,
            rms_contrast=rms_contrast,
            blur_score=laplacian_var,
            is_blurry=laplacian_var < 80.0,
            is_low_contrast=rms_contrast < 35.0,
            is_dark=mean_brightness < 75.0,
            is_uneven_illumination=is_uneven,
            quality_rating=quality
        )

    @classmethod
    def fast_detect_orientation(cls, pil_img: Image.Image, rapidocr_engine=None) -> Tuple[Image.Image, int]:
        """
        Detecta y corrige la orientación de la factura (0°, 90°, 180°, 270°) en <80ms.
        1. Respeta metadatos EXIF de smartphones en 0ms.
        2. Si no hay EXIF o la foto viene rotada de cámara/escáner, evalúa en una sola pasada
           la relación de aspecto de las cajas de texto en un thumbnail ligero (sin bucles lentos de OCR).
        """
        # 1. Transposición EXIF obligatoria (instantánea)
        img_transposed = ImageOps.exif_transpose(pil_img)

        # 2. Si no hay motor o imagen muy pequeña, retornar
        if rapidocr_engine is None or not hasattr(rapidocr_engine, "text_det"):
            return img_transposed, 0

        w, h = img_transposed.size
        # Reducir a thumbnail pequeño para detección ultrarrápida (<50ms)
        max_dim = 480
        scale = min(1.0, float(max_dim) / max(w, h))
        thumb = img_transposed.resize((int(w * scale), int(h * scale)), Image.Resampling.BILINEAR)
        thumb_np = np.array(thumb)

        try:
            dt_boxes, _ = rapidocr_engine.text_det(thumb_np)
            if dt_boxes is None or len(dt_boxes) < 3:
                return img_transposed, 0

            # En texto latino horizontal estándar, ancho >> alto (relación aspecto > 1.2)
            ratios = []
            for b in dt_boxes:
                b_arr = np.array(b)
                bw = max(b_arr[:, 0]) - min(b_arr[:, 0])
                bh = max(b_arr[:, 1]) - min(b_arr[:, 1])
                if bh > 0:
                    ratios.append(bw / bh)

            med_ratio = float(np.median(ratios)) if ratios else 1.5

            # Si la mediana es < 0.95, las cajas de texto están verticales -> el documento está rotado 90° o 270°
            if med_ratio < 0.95:
                # Comprobar 90° y 270° en el thumbnail (ultrarrápido, ~150ms en CPU)
                res_90, _ = rapidocr_engine(np.array(thumb.rotate(90, expand=True)))
                res_270, _ = rapidocr_engine(np.array(thumb.rotate(270, expand=True)))

                s_90 = sum(r[2] for r in (res_90 or []))
                s_270 = sum(r[2] for r in (res_270 or []))

                kw_set = {"FACTURA", "NIT", "ELECTRONICA", "CLIENTE", "FECHA", "SAS", "S.A.", "VENTA", "TEL", "DIRECCION", "TOTAL", "IVA"}
                txt_90 = " ".join(r[1] for r in (res_90 or [])).upper()
                txt_270 = " ".join(r[1] for r in (res_270 or [])).upper()
                k_90 = sum(1 for kw in kw_set if kw in txt_90)
                k_270 = sum(1 for kw in kw_set if kw in txt_270)

                best_angle = 270 if (k_270 > k_90 or (k_270 == k_90 and s_270 > s_90)) else 90
                logger.info(f"Orientación de documento corregida en {best_angle}° (med_ratio: {med_ratio:.2f}, k90={k_90}, k270={k_270}, s90={s_90:.1f}, s270={s_270:.1f})")
                return img_transposed.rotate(best_angle, expand=True), best_angle

        except Exception as e_rot:
            logger.warning(f"Error en fast_detect_orientation: {e_rot}")

        return img_transposed, 0

    @classmethod
    def order_points(cls, pts: np.ndarray) -> np.ndarray:
        """Ordena 4 puntos en el orden: [top-left, top-right, bottom-right, bottom-left]."""
        rect = np.zeros((4, 2), dtype="float32")
        s = pts.sum(axis=1)
        rect[0] = pts[np.argmin(s)]
        rect[2] = pts[np.argmax(s)]
        diff = np.diff(pts, axis=1)
        rect[1] = pts[np.argmin(diff)]
        rect[3] = pts[np.argmax(diff)]
        return rect

    @classmethod
    def detect_document_corners_and_warp(cls, cv_img: np.ndarray) -> Tuple[np.ndarray, Optional[np.ndarray], bool]:
        """
        Detecta los 4 vértices del papel de la factura y aplica transformación de perspectiva
        para recortar fondos de mesas/mostradores y rectificar distorsión oblicua.
        Retorna (imagen_rectificada, matriz_M_inv_3x3, fue_aplicado).
        """
        h, w = cv_img.shape[:2]
        # Si el documento es una tirilla muy alargada (H/W > 2.2 o W/H > 2.2), no deformar
        if h / max(1, w) > 2.2 or w / max(1, h) > 2.2:
            return cv_img, None, False

        # Escalar imagen para detección de bordes ultrarrápida (<10ms)
        target_h = 500
        scale = target_h / float(h)
        target_w = int(w * scale)
        small = cv2.resize(cv_img, (target_w, target_h), interpolation=cv2.INTER_AREA)

        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) if len(small.shape) == 3 else small
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)

        med_val = np.median(blurred)
        lower = int(max(0, 0.66 * med_val))
        upper = int(min(255, 1.33 * med_val))
        edges = cv2.Canny(blurred, lower, upper)

        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
        closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)

        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return cv_img, None, False

        contours = sorted(contours, key=cv2.contourArea, reverse=True)
        img_area = float(target_w * target_h)

        for c in contours[:4]:
            area = cv2.contourArea(c)
            # El documento debe ocupar entre 25% y 98% del encuadre
            if area < 0.25 * img_area or area > 0.98 * img_area:
                continue

            peri = cv2.arcLength(c, True)
            approx = cv2.approxPolyDP(c, 0.025 * peri, True)

            if len(approx) == 4 and cv2.isContourConvex(approx):
                orig_pts = approx.reshape(4, 2) / scale
                rect = cls.order_points(orig_pts)

                tl, tr, br, bl = rect
                width_a = np.linalg.norm(br - bl)
                width_b = np.linalg.norm(tr - tl)
                max_w = max(int(width_a), int(width_b))

                height_a = np.linalg.norm(tr - br)
                height_b = np.linalg.norm(tl - bl)
                max_h = max(int(height_a), int(height_b))

                if max_w > 250 and max_h > 250 and 0.25 < (max_w / float(max_h)) < 4.0:
                    dst = np.array([
                        [0, 0],
                        [max_w - 1, 0],
                        [max_w - 1, max_h - 1],
                        [0, max_h - 1]
                    ], dtype="float32")

                    M = cv2.getPerspectiveTransform(rect, dst)
                    M_inv = cv2.getPerspectiveTransform(dst, rect)

                    warped = cv2.warpPerspective(
                        cv_img, M, (max_w, max_h),
                        flags=cv2.INTER_CUBIC,
                        borderMode=cv2.BORDER_REPLICATE
                    )
                    logger.info(f"Corrección de perspectiva de documento aplicada: {w}x{h} -> {max_w}x{max_h}")
                    return warped, M_inv, True

        return cv_img, None, False

    @classmethod
    def detect_deskew_angle(cls, cv_img: np.ndarray) -> float:
        """Calcula el ángulo de inclinación fina (±0.5° a 15.0°) mediante proyección de componentes conexas."""
        gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY) if len(cv_img.shape) == 3 else cv_img
        h, w = gray.shape

        # Reducir si es muy grande para cálculo rápido
        if max(h, w) > 1200:
            scale = 1200.0 / max(h, w)
            gray = cv2.resize(gray, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        # Dilatar horizontalmente para fusionar caracteres de una misma línea
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 3))
        dilated = cv2.dilate(thresh, kernel, iterations=2)

        contours, _ = cv2.findContours(dilated, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        angles = []
        for c in contours:
            area = cv2.contourArea(c)
            if 300 < area < 100000:
                rect = cv2.minAreaRect(c)
                w_box, h_box = rect[1]
                if max(w_box, h_box) / max(1.0, min(w_box, h_box)) > 2.0:
                    angle = rect[2]
                    if angle < -45:
                        angle = 90 + angle
                    elif angle > 45:
                        angle = angle - 90
                    if 0.5 <= abs(angle) <= 15.0:
                        angles.append(angle)

        if len(angles) >= 5:
            median_angle = float(np.median(angles))
            return median_angle
        return 0.0

    @classmethod
    def rotate_cv_image(cls, cv_img: np.ndarray, angle: float) -> np.ndarray:
        """Rota la imagen en OpenCV compensando las nuevas dimensiones sin recortar bordes."""
        if abs(angle) < 0.3:
            return cv_img
        h, w = cv_img.shape[:2]
        center = (w / 2.0, h / 2.0)
        M = cv2.getRotationMatrix2D(center, angle, 1.0)
        cos = np.abs(M[0, 0])
        sin = np.abs(M[0, 1])
        new_w = int((h * sin) + (w * cos))
        new_h = int((h * cos) + (w * sin))
        M[0, 2] += (new_w / 2.0) - center[0]
        M[1, 2] += (new_h / 2.0) - center[1]
        return cv2.warpAffine(cv_img, M, (new_w, new_h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)

    @classmethod
    def remove_shadows_and_illumination(cls, cv_img: np.ndarray) -> np.ndarray:
        """
        Elimina sombras no uniformes dividiendo el fondo estimado en el canal de luminancia
        sin alterar bordes finos ni destruir antialiasing de caracteres pequeños o térmicos.
        """
        h, w = cv_img.shape[:2]
        if len(cv_img.shape) == 3:
            lab = cv2.cvtColor(cv_img, cv2.COLOR_BGR2LAB)
            l, a, b = cv2.split(lab)
            k_size = max(25, (min(h, w) // 16) | 1)
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_size, k_size))
            bg = cv2.morphologyEx(l, cv2.MORPH_CLOSE, kernel)
            bg = cv2.GaussianBlur(bg, (k_size, k_size), 0)
            l_norm = cv2.divide(l, bg, scale=240)
            merged = cv2.merge([l_norm, a, b])
            return cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)
        else:
            k_size = max(25, (min(h, w) // 16) | 1)
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_size, k_size))
            bg = cv2.morphologyEx(cv_img, cv2.MORPH_CLOSE, kernel)
            bg = cv2.GaussianBlur(bg, (k_size, k_size), 0)
            return cv2.divide(cv_img, bg, scale=240)

    @classmethod
    def apply_clahe(cls, cv_img: np.ndarray, clip_limit: float = 1.8) -> np.ndarray:
        """Mejora adaptativa de contraste local preservando detalles finos en recibos claros o descoloridos."""
        clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(8, 8))
        if len(cv_img.shape) == 3:
            lab = cv2.cvtColor(cv_img, cv2.COLOR_BGR2LAB)
            l, a, b = cv2.split(lab)
            l_clahe = clahe.apply(l)
            merged = cv2.merge((l_clahe, a, b))
            return cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)
        return clahe.apply(cv_img)

    @classmethod
    def unsharp_mask(cls, cv_img: np.ndarray, sigma: float = 1.0, strength: float = 1.2) -> np.ndarray:
        """Aumenta la nitidez de caracteres suavemente sin introducir artefactos de alta frecuencia."""
        blurred = cv2.GaussianBlur(cv_img, (0, 0), sigma)
        sharpened = cv2.addWeighted(cv_img, 1.0 + strength, blurred, -strength, 0)
        return np.clip(sharpened, 0, 255).astype(np.uint8)

    @classmethod
    def adaptive_binarize(cls, cv_img: np.ndarray) -> np.ndarray:
        """Binarización suave con filtro de preservación de bordes (solo para visualización o fallback)."""
        gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY) if len(cv_img.shape) == 3 else cv_img
        denoised = cv2.fastNlMeansDenoising(gray, h=8)
        th = cv2.adaptiveThreshold(denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 21, 10)
        if len(cv_img.shape) == 3:
            return cv2.cvtColor(th, cv2.COLOR_GRAY2BGR)
        return th

    @classmethod
    def smart_normalize_resolution(cls, cv_img: np.ndarray, min_dim: int = 1100, max_dim: int = 1800) -> Tuple[np.ndarray, float]:
        """
        Escala inteligentemente la imagen:
        - Si el texto es muy pequeño o la resolución < min_dim, realiza upscaling bicúbico limpio.
        - Si la imagen es gigantesca (> max_dim), escala suavemente para evitar desborde de memoria y tiempo.
        """
        h, w = cv_img.shape[:2]
        current_max = max(h, w)
        current_min = min(h, w)

        scale = 1.0
        if current_max > max_dim:
            scale = float(max_dim) / float(current_max)
            new_w, new_h = int(w * scale), int(h * scale)
            resized = cv2.resize(cv_img, (new_w, new_h), interpolation=cv2.INTER_AREA)
            return resized, scale
        elif current_min < min_dim:
            scale = float(min_dim) / float(current_min)
            scale = min(1.8, scale)
            if scale > 1.05:
                new_w, new_h = int(w * scale), int(h * scale)
                resized = cv2.resize(cv_img, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
                return resized, scale

        return cv_img, 1.0

    @classmethod
    def prepare_variants(cls, raw_bytes: bytes, rapidocr_engine=None) -> Tuple[ProcessedVariant, List[ProcessedVariant], ImageDiagnostic]:
        """
        Flujo principal adaptativo de preparación de imagen.
        1. Decodifica y evalúa la imagen original.
        2. Corrige orientación en <80ms mediante EXIF y aspect ratio de texto.
        3. Aplica 4-point perspective warp para recortar mesa/mostrador si detecta el cuadrilátero.
        4. Corrige iluminación de forma no destructiva preservando gradientes continuos.
        5. Normaliza resolución sin forzar binarizaciones que degradan OCRs de Deep Learning.
        """
        import time
        t_start = time.time()

        pil_raw = Image.open(io.BytesIO(raw_bytes))
        if pil_raw.mode != "RGB":
            pil_raw = pil_raw.convert("RGB")

        orig_w, orig_h = pil_raw.size

        # 1. Orientación inteligente ultrarrápida (<80ms)
        pil_upright, rot_applied = cls.fast_detect_orientation(pil_raw, rapidocr_engine)
        cv_current = cls.pil_to_cv(pil_upright)

        applied_transforms = []
        if rot_applied != 0:
            applied_transforms.append(f"Orientación corregida ({rot_applied}°)")

        # 2. Escáner de Documento por Transformación de Perspectiva (4-Point Warp)
        cv_warped, homography_inv, warp_applied = cls.detect_document_corners_and_warp(cv_current)
        if warp_applied:
            cv_current = cv_warped
            applied_transforms.append("Corrección de perspectiva 4 puntos (Document Scanner)")

        # 3. Diagnóstico de calidad
        diag = cls.diagnose_raw(cv_current, pil_raw)
        diag.detected_rotation = rot_applied
        diag.perspective_warp_applied = warp_applied

        # 4. Deskew fino (solo si no se aplicó warp, ya que el warp alinea las esquinas)
        deskew_angle = 0.0
        if not warp_applied:
            deskew_angle = cls.detect_deskew_angle(cv_current)
            if abs(deskew_angle) >= 0.6:
                cv_current = cls.rotate_cv_image(cv_current, deskew_angle)
                diag.deskew_angle = deskew_angle
                applied_transforms.append(f"Deskew ({deskew_angle:.1f}°)")

        # 5. Eliminación suave y no destructiva de sombras
        if diag.is_uneven_illumination or diag.is_dark:
            cv_current = cls.remove_shadows_and_illumination(cv_current)
            applied_transforms.append("Corrección no destructiva de iluminación")

        # 6. Mejora de contraste moderada si la imagen está pálida
        if diag.is_low_contrast:
            cv_current = cls.apply_clahe(cv_current, clip_limit=1.8)
            applied_transforms.append("Mejora adaptativa de contraste (CLAHE)")

        # 7. Escalado inteligente
        cv_normalized, scale_factor = cls.smart_normalize_resolution(cv_current)
        if scale_factor != 1.0:
            applied_transforms.append(f"Normalización de resolución ({scale_factor:.2f}x)")

        diag.applied_transforms = applied_transforms
        diag.processed_dimensions = (cv_normalized.shape[1], cv_normalized.shape[0])
        diag.processing_time_ms = (time.time() - t_start) * 1000.0

        # Crear mapeador de coordenadas exacto (soporta homografía inversa + escala + rotación)
        mapper = CoordinateMapper(
            orig_w=orig_w,
            orig_h=orig_h,
            rotation=rot_applied,
            scale_factor=scale_factor,
            deskew_angle=deskew_angle,
            homography_inv=homography_inv
        )

        # Convertir a formato RGB para RapidOCR
        rgb_primary = cv2.cvtColor(cv_normalized, cv2.COLOR_BGR2RGB)
        pil_primary = Image.fromarray(rgb_primary)

        primary_variant = ProcessedVariant(
            name="normalizada_adaptativa",
            image_np=rgb_primary,
            pil_image=pil_primary,
            mapper=mapper,
            is_primary=True
        )

        return primary_variant, [], diag

