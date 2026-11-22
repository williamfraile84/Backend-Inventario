import asyncio
import io
import logging
import os
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, status
from fastapi.responses import Response, JSONResponse

from sqlalchemy import or_

from app.core.config import settings
from app.core.security import get_current_user, require_permission
from app.core import database
from app.core.database import SessionLocal
from app.models.producto import Producto
from app.services.invoice_parser_service import VisionEngineFactory
from app.services.pricing_engine import calcular_costos_item_factura
from app.services.product_service import ProductService
from app.services.pos_service import POSService
from app.services.storage.storage_factory import get_storage_provider
from app.services.export_service import ExportService
from app.services.dian_qr_service import DIANQRService
from app.services.dian_portal_service import DIANPortalService
from app.services.dian_unit_service import DIANUnitService
from app.models.schemas import (
    InvoiceExtractionResponse,
    InvoiceCostCalculationRequest,
    InvoiceCostCalculationResponse,
    InvoiceApplyRequest,
    InvoiceLearnRequest,
    InvoiceLearnResponse,
    DianConsultRequest,
    DianQRScanResponse,
    DianConsultResponse
)

logger = logging.getLogger("InvoicesRouter")
router = APIRouter(prefix="/invoices", tags=["Lectura y Procesamiento de Facturas"])

# Almacén en memoria de tareas asíncronas para procesamiento de facturas
# Permite desacoplar peticiones de larga duración y evitar timeouts de 30s en túneles (tunnl.gg)
INVOICE_TASKS: Dict[str, Dict[str, Any]] = {}

# Mapeo de abreviaturas frecuentes en facturas colombianas para normalización fonética y semántica
INVOICE_ABBREVIATIONS = {
    "cza": "cereza",
    "fre": "fresa",
    "mor": "mora",
    "meloc": "melocoton",
    "mel": "melocoton",
    "nat": "natural",
    "q": "queso",
    "yog": "yogurt",
    "fts": "frutos",
    "orig": "original",
    "rgn": "regeneris",
    "zuc": "zucaritas",
    "alm": "almendras",
    "manz": "manzana",
    "nar": "naranja",
    "vain": "vainilla",
    "choc": "chocolate",
    "prem": "premio",
    "descr": "descremado",
    "vas": "vaso",
    "bot": "botella",
    "caj": "caja"
}

INVOICE_STOPWORDS = {"de", "la", "el", "los", "las", "con", "en", "por", "para", "un", "una", "del", "al"}

INVOICE_FLAVORS = {
    "fresa", "mora", "cereza", "naranja", "limon", "vainilla",
    "chocolate", "melocoton", "maracuya", "arequipe", "natural",
    "manzana", "uva", "pera", "coco", "frutos"
}

def _clean_product_tokens(text: str) -> List[str]:
    if not text:
        return []
    s = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii").lower()
    raw = re.findall(r'[a-z0-9]+', s)
    tokens = []
    for r in raw:
        if r in INVOICE_STOPWORDS:
            continue
        if len(r) == 1 and r != "q":
            continue
        tokens.append(INVOICE_ABBREVIATIONS.get(r, r))
    return tokens

def _token_match_metrics(tokens1: List[str], tokens2: List[str]):
    if not tokens1 or not tokens2:
        return 0, 0.0
    s1 = set(tokens1)
    s2 = set(tokens2)
    matches = 0.0
    matched_words = 0
    for t1 in s1:
        if t1 in s2:
            matches += 1.0
            matched_words += 1
        elif len(t1) >= 3 and any(t2.startswith(t1) or t1.startswith(t2) for t2 in s2 if len(t2) >= 3):
            matches += 0.9
            matched_words += 1
    union_len = len(s1.union(s2))
    jaccard = matches / union_len if union_len > 0 else 0.0
    return matched_words, jaccard

def _compute_product_similarity(desc1: str, desc2: str) -> float:
    t1 = _clean_product_tokens(desc1)
    t2 = _clean_product_tokens(desc2)
    matched_words, jaccard = _token_match_metrics(t1, t2)
    if matched_words == 0:
        return 0.0
    s1 = " ".join(t1)
    s2 = " ".join(t2)
    seq = SequenceMatcher(None, s1, s2).ratio()
    base_sim = max(jaccard, seq, jaccard * 0.7 + seq * 0.3)

    # Penalizar sabores en conflicto (ej: factura fresa vs producto mora)
    f1 = set(t1).intersection(INVOICE_FLAVORS)
    f2 = set(t2).intersection(INVOICE_FLAVORS)
    if f1 and f2 and not f1.intersection(f2):
        base_sim = max(0.0, base_sim - 0.5)

    return base_sim

async def enrich_items_with_pos_and_dian_conversion(items: List[Dict[str, Any]], pos_service: POSService) -> None:
    """
    Enriquece los ítems con coincidencias del catálogo/POS y calcula conversiones automáticas
    entre la unidad de medida de la factura DIAN y la unidad de venta del producto en POS.
    Optimizado con búsqueda local primero y resolución concurrente inteligente en POS por código y descripción.
    """
    if not items:
        return

    # 1. Búsqueda local ultrarrápida (SQLite WAL) por código, alias o nombre
    unmatched_items: List[Dict[str, Any]] = []

    for it in items:
        code = str(it.get("codigo") or "").strip()
        desc = str(it.get("descripcion") or "").strip()
        matched = None

        # A. Búsqueda local por código de barras (código principal o número adicional)
        if code:
            local_p = database.get_product_by_barcode(code)
            if local_p:
                matched = {
                    "item_id": str(local_p["id"]),
                    "barcode": local_p.get("item_number") or code,
                    "item_number": local_p.get("item_number") or code,
                    "name": local_p["name"],
                    "category": local_p.get("category", ""),
                    "cost_price": float(local_p.get("cost_price", 0.0) or 0.0),
                    "sale_price": float(local_p.get("unit_price", 0.0) or 0.0),
                    "unit_price": float(local_p.get("unit_price", 0.0) or 0.0),
                    "formatted_sale_price": local_p.get("formatted_sale_price", ""),
                    "stock": str(local_p.get("stock_quantity", 0)),
                    "unit_code": local_p.get("unit_code", "UN"),
                    "match_type": "local_code"
                }

        # B. Búsqueda local por alias aprendido previamente
        if not matched and desc:
            alias = database.get_product_alias(desc)
            if alias and alias.get("barcode"):
                p_alias = database.get_product_by_barcode(alias["barcode"])
                if p_alias:
                    matched = {
                        "item_id": str(p_alias["id"]),
                        "barcode": p_alias.get("item_number") or alias["barcode"],
                        "item_number": p_alias.get("item_number") or alias["barcode"],
                        "name": p_alias["name"],
                        "category": p_alias.get("category", ""),
                        "cost_price": float(p_alias.get("cost_price", 0.0) or 0.0),
                        "sale_price": float(p_alias.get("unit_price", 0.0) or 0.0),
                        "unit_price": float(p_alias.get("unit_price", 0.0) or 0.0),
                        "formatted_sale_price": p_alias.get("formatted_sale_price", ""),
                        "stock": str(p_alias.get("stock_quantity", 0)),
                        "unit_code": p_alias.get("unit_code", "UN"),
                        "match_type": "alias"
                    }

        # C. Búsqueda local por nombre en catálogo
        if not matched and desc:
            local_by_name = database.get_product_by_name(desc)
            if local_by_name:
                matched = {
                    "item_id": str(local_by_name["id"]),
                    "barcode": local_by_name.get("item_number") or "",
                    "item_number": local_by_name.get("item_number") or "",
                    "name": local_by_name["name"],
                    "category": local_by_name.get("category", ""),
                    "cost_price": float(local_by_name.get("cost_price", 0.0) or 0.0),
                    "sale_price": float(local_by_name.get("unit_price", 0.0) or 0.0),
                    "unit_price": float(local_by_name.get("unit_price", 0.0) or 0.0),
                    "formatted_sale_price": local_by_name.get("formatted_sale_price", ""),
                    "stock": str(local_by_name.get("stock_quantity", 0)),
                    "unit_code": local_by_name.get("unit_code", "UN"),
                    "match_type": "local_name"
                }

        it["matched_pos_item"] = matched
        if not matched:
            unmatched_items.append(it)

    # 2. Resolución concurrente inteligente en el sistema POS externo (csopos / softwarepos)
    if unmatched_items:
        pos_cache: Dict[str, List[Any]] = {}
        sem = asyncio.Semaphore(10)

        async def _query_pos(q: str):
            clean_q = q.strip()
            if not clean_q or clean_q in pos_cache:
                return
            async with sem:
                try:
                    res = await asyncio.wait_for(pos_service.search_products(clean_q), timeout=4.0)
                    pos_cache[clean_q] = res.items if res and res.items else []
                except Exception:
                    pos_cache[clean_q] = []

        # Fase A: Consultas concurrentes prioritarias por CÓDIGO en POS
        unique_codes = list({
            str(it.get("codigo") or "").strip()
            for it in unmatched_items
            if str(it.get("codigo") or "").strip() and len(str(it.get("codigo") or "").strip()) >= 2
        })
        if unique_codes:
            await asyncio.gather(*[_query_pos(c) for c in unique_codes], return_exceptions=True)

        # Evaluar matches por código usando similitud para evitar colisiones de PLU numérico
        still_unmatched = []
        for it in unmatched_items:
            code = str(it.get("codigo") or "").strip()
            desc = str(it.get("descripcion") or "").strip()
            candidates = pos_cache.get(code, []) if code else []

            best_cand = None
            best_score = -1.0

            for cand in candidates:
                cand_barcode = str(cand.barcode or "").strip()
                sim = _compute_product_similarity(desc, cand.name)
                t_cand = _clean_product_tokens(cand.name)
                t_desc = _clean_product_tokens(desc)
                matched_words, _ = _token_match_metrics(t_desc, t_cand)

                cand_score = sim
                if code and cand_barcode == code:
                    if matched_words >= 1 and sim >= 0.15:
                        cand_score += 0.6
                    else:
                        cand_score = 0.0  # Posible colisión de PLU numérico interno no relacionado
                elif code:
                    if matched_words >= 1:
                        cand_score += 0.4
                    else:
                        cand_score = sim + 0.1

                if cand_score > best_score:
                    best_score = cand_score
                    best_cand = cand

            if best_cand and best_score >= 0.30:
                sale_val = float(best_cand.sale_price or 0.0)
                it["matched_pos_item"] = {
                    "item_id": str(best_cand.item_id),
                    "barcode": best_cand.barcode or best_cand.item_id,
                    "item_number": best_cand.barcode or best_cand.item_id,
                    "name": best_cand.name,
                    "category": best_cand.category or "",
                    "cost_price": float(best_cand.cost_price or 0.0),
                    "sale_price": sale_val,
                    "unit_price": sale_val,
                    "formatted_sale_price": best_cand.formatted_sale_price or f"${int(sale_val):,}",
                    "stock": str(best_cand.stock or "0"),
                    "unit_code": best_cand.unit_code or "UN",
                    "match_type": "pos_code"
                }
            else:
                still_unmatched.append(it)

        # Fase B: Para ítems sin match por código, buscar por términos significativos de la descripción
        if still_unmatched:
            desc_queries = set()
            for it in still_unmatched:
                desc = str(it.get("descripcion") or "").strip()
                words = [w for w in _clean_product_tokens(desc) if len(w) >= 3 and not w.isdigit()]
                if words:
                    desc_queries.add(words[0])
                    if len(words) >= 2:
                        desc_queries.add(f"{words[0]} {words[1]}")

            if desc_queries:
                await asyncio.gather(*[_query_pos(q) for q in desc_queries if q not in pos_cache], return_exceptions=True)

            for it in still_unmatched:
                desc = str(it.get("descripcion") or "").strip()
                words = [w for w in _clean_product_tokens(desc) if len(w) >= 3 and not w.isdigit()]
                cand_pool: List[Any] = []
                for q_cand in [words[0] if words else "", f"{words[0]} {words[1]}" if len(words) >= 2 else ""]:
                    if q_cand and q_cand in pos_cache:
                        cand_pool.extend(pos_cache[q_cand])

                seen_ids = set()
                uniq_candidates = []
                for c in cand_pool:
                    if c.item_id not in seen_ids:
                        seen_ids.add(c.item_id)
                        uniq_candidates.append(c)

                best_cand = None
                best_score = -1.0
                for cand in uniq_candidates:
                    sim = _compute_product_similarity(desc, cand.name)
                    t_cand = _clean_product_tokens(cand.name)
                    t_desc = _clean_product_tokens(desc)
                    matched_words, _ = _token_match_metrics(t_desc, t_cand)
                    if matched_words >= 1:
                        score = sim + 0.1
                        if score > best_score:
                            best_score = score
                            best_cand = cand

                if best_cand and best_score >= 0.35:
                    sale_val = float(best_cand.sale_price or 0.0)
                    it["matched_pos_item"] = {
                        "item_id": str(best_cand.item_id),
                        "barcode": best_cand.barcode or best_cand.item_id,
                        "item_number": best_cand.barcode or best_cand.item_id,
                        "name": best_cand.name,
                        "category": best_cand.category or "",
                        "cost_price": float(best_cand.cost_price or 0.0),
                        "sale_price": sale_val,
                        "unit_price": sale_val,
                        "formatted_sale_price": best_cand.formatted_sale_price or f"${int(sale_val):,}",
                        "stock": str(best_cand.stock or "0"),
                        "unit_code": best_cand.unit_code or "UN",
                        "match_type": "pos_keyword"
                    }

    # 3. Metadatos DIAN y conversiones de unidades normalizadas
    for it in items:
        matched = it.get("matched_pos_item") or {}
        desc = it.get("descripcion", "")
        raw_um = it.get("codigo_dian_unidad") or it.get("unidad") or "UND"
        dian_info = DIANUnitService.resolve_unit(raw_um, desc)
        if not it.get("codigo_dian_unidad"):
            it["codigo_dian_unidad"] = dian_info["codigo_dian"]
            it["nombre_dian_unidad"] = dian_info["nombre_dian"]
            it["etiqueta_dian"] = dian_info["etiqueta_dian"]

        # Determinar unidad de destino en POS
        target_unit = matched.get("unit_code") if (matched and matched.get("unit_code")) else (dian_info.get("unidad_base") or "UN")
        source_unit = it.get("codigo_dian_unidad") or it.get("unidad") or "UND"
        qty = float(it.get("cantidad", 1.0) or 1.0)
        p_unit = float(it.get("precio_unitario", 0.0) or 0.0)
        pack_equiv = float(it.get("unidades_por_presentacion", 1.0) or 1.0)

        cross_conv = DIANUnitService.calculate_cross_unit_conversion(
            from_unit=source_unit,
            to_unit=target_unit,
            quantity=qty,
            unit_cost=p_unit,
            units_per_pack=pack_equiv
        )

        it["unidad_pos"] = target_unit
        if cross_conv.get("conversion_requerida"):
            it["conversion_requerida"] = True
            it["factor_conversion"] = cross_conv["factor"]
            it["conversion_texto"] = cross_conv["mensaje"]
            it["cantidad_inventario_convertida"] = cross_conv["cantidad_convertida"]
            it["costo_inventario_convertido"] = cross_conv["costo_convertido"]
        else:
            it["conversion_requerida"] = False
            it["factor_conversion"] = 1.0
            it["cantidad_inventario_convertida"] = round(qty * pack_equiv, 4)
            it["costo_inventario_convertido"] = round(p_unit / pack_equiv, 4) if pack_equiv > 0 else p_unit

@router.post("/process-image")
async def process_invoice_image(
    file: UploadFile = File(..., description="Imagen de la factura o captura de cámara"),
    provider: str = Query("gemini", description="Motor de visión e IA: 'gemini' (Gemini Vision AI Ultrarrápido Nube), 'xml' (XML UBL 2.1 DIAN Oficial), 'qr_dian' (Asistente QR DIAN VPFE), 'pdf' (PDF DIAN Digital)"),
    nit: Optional[str] = Query(None, description="NIT receptor opcional para desencriptar PDFs protegidos de la DIAN"),
    rotation: int = Query(0, description="Grados de rotación horaria aplicados por el usuario (0, 90, 180, 270)"),
    async_mode: Optional[bool] = Query(None, description="Ejecutar en modo asíncrono para evitar timeouts en túneles lentos (tunnl.gg)"),
    current_user: Dict[str, Any] = Depends(require_permission("can_process_invoices"))
):
    """
    Procesa una imagen de factura mediante visión por computadora / OCR desacoplado.
    Identifica proveedor, número, fecha, productos, cantidades, unidades, costos e impuestos.
    Soporta rotación manual y ejecución asíncrona desacoplada con polling para evitar timeouts en túneles (tunnl.gg).
    """
    # 1. Validación de tipo y tamaño de archivo
    content_type = file.content_type or ""
    is_xml_ext = file.filename.lower().endswith(".xml")
    is_img_ext = file.filename.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".bmp", ".pdf"))
    is_valid_type = content_type.startswith("image/") or content_type in ("text/xml", "application/xml") or is_xml_ext or is_img_ext

    if not is_valid_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Formato no admitido. Por favor suba una imagen válida (JPEG, PNG, WEBP) o un archivo XML de Factura Electrónica DIAN."
        )

    image_bytes = await file.read()
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(image_bytes) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"El archivo supera el tamaño máximo permitido de {settings.MAX_UPLOAD_SIZE_MB}MB."
        )

    # 1.1 Aplicar rotación si el usuario rotó la foto en la interfaz web (evita OCR horizontal de lado)
    if rotation in (90, 180, 270) and not is_xml_ext and not file.filename.lower().endswith(".pdf"):
        try:
            from PIL import Image
            pil_img = Image.open(io.BytesIO(image_bytes))
            pil_img = pil_img.rotate((360 - rotation) % 360, expand=True)
            buf = io.BytesIO()
            fmt = "PNG" if "png" in content_type.lower() else "JPEG"
            pil_img.save(buf, format=fmt, quality=90)
            image_bytes = buf.getvalue()
            logger.info(f"Imagen rotada {rotation}° exitosamente antes de OCR.")
        except Exception as e_rot:
            logger.warning(f"No se pudo aplicar rotación {rotation}° a la imagen: {e_rot}")

    # Guardar archivo en el subsistema de almacenamiento
    saved_storage_path = None
    file_url = None
    try:
        storage = get_storage_provider()
        ext = os.path.splitext(file.filename or "")[1].lower() or ".jpg"
        date_folder = datetime.now().strftime("%Y%m")
        unique_key = f"invoices/{date_folder}/{uuid.uuid4().hex[:12]}{ext}"
        saved_storage_path = await storage.save_file(unique_key, image_bytes, content_type=content_type)
        file_url = storage.get_public_url(saved_storage_path)
    except Exception as st_err:
        logger.warning(f"No se pudo guardar la factura en almacenamiento: {st_err}")

    # Determinar si se ejecuta en segundo plano (True por defecto para evitar timeouts de 30s en tunnl.gg y redes móviles)
    should_run_async = True if (async_mode is None or async_mode is True) else False

    if should_run_async:
        task_id = uuid.uuid4().hex
        now_ts = datetime.now(timezone.utc).timestamp()
        INVOICE_TASKS[task_id] = {
            "task_id": task_id,
            "status": "processing",
            "message": "Inferencia neuronal local iniciada en segundo plano...",
            "timestamp": now_ts,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "result": None,
            "error": None
        }

        async def _background_invoice_task(t_id: str, img_data: bytes, fname: str, n_val: Optional[str], prov: str, s_path: Optional[str], f_url: Optional[str]):
            try:
                is_xml = fname.lower().endswith(".xml") or img_data.strip().startswith(b"<")
                engine = VisionEngineFactory.get_engine(is_xml=is_xml, provider=prov)
                raw_res = await engine.extract_invoice(img_data, filename=fname, nit=n_val)
                raw_res["storage_path"] = s_path
                raw_res["file_url"] = f_url

                pos_service = POSService.get_instance()
                await enrich_items_with_pos_and_dian_conversion(raw_res.get("items", []), pos_service)

                raw_res["success"] = True
                raw_res["supplier_name"] = raw_res.get("proveedor")
                raw_res["invoice_number"] = raw_res.get("numero_factura")
                raw_res["invoice_date"] = raw_res.get("fecha")
                raw_res["provider_used"] = raw_res.get("motor_utilizado")
                raw_res["confidence_score"] = raw_res.get("confidence_score", 0.95)

                validated_data = InvoiceExtractionResponse(**raw_res).model_dump(mode="json")
                INVOICE_TASKS[t_id]["status"] = "completed"
                INVOICE_TASKS[t_id]["result"] = validated_data
                INVOICE_TASKS[t_id]["message"] = "Extracción completada exitosamente."
                logger.info(f"Tarea en segundo plano {t_id} finalizada exitosamente.")
            except Exception as e_bg:
                logger.error(f"Error procesando tarea en segundo plano {t_id}: {e_bg}")
                INVOICE_TASKS[t_id]["status"] = "error"
                INVOICE_TASKS[t_id]["error"] = str(e_bg)
                INVOICE_TASKS[t_id]["message"] = f"Error en inferencia: {str(e_bg)}"

        asyncio.create_task(_background_invoice_task(task_id, image_bytes, file.filename, nit, provider, saved_storage_path, file_url))

        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "success": True,
                "async_task": True,
                "task_id": task_id,
                "status": "processing",
                "message": "Inferencia iniciada en segundo plano para evitar timeouts de red."
            }
        )

    # 2. Ejecución síncrona directa (Gemini, XML, PDF)
    try:
        is_xml = is_xml_ext or content_type in ("text/xml", "application/xml") or image_bytes.strip().startswith(b"<")
        engine = VisionEngineFactory.get_engine(is_xml=is_xml, provider=provider)
        result = await engine.extract_invoice(image_bytes, filename=file.filename, nit=nit)
        result["storage_path"] = saved_storage_path
        result["file_url"] = file_url
        
        # 3. Vincular ítems con catálogo/POS y aplicar conversión de unidades DIAN
        pos_service = POSService.get_instance()
        await enrich_items_with_pos_and_dian_conversion(result.get("items", []), pos_service)

        result["success"] = True
        result["supplier_name"] = result.get("proveedor")
        result["invoice_number"] = result.get("numero_factura")
        result["invoice_date"] = result.get("fecha")
        result["provider_used"] = result.get("motor_utilizado")
        result["confidence_score"] = result.get("confidence_score", 0.95)

        return InvoiceExtractionResponse(**result)
    except Exception as e:
        logger.error(f"Error procesando factura: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error durante el procesamiento de la factura: {str(e)}"
        )

@router.get("/task-status/{task_id}")
async def get_invoice_task_status(
    task_id: str,
    current_user: Dict[str, Any] = Depends(require_permission("can_process_invoices"))
):
    """
    Consulta el estado de una tarea de extracción asíncrona.
    Permite polling ultrarrápido (sub-10ms) para túneles o conexiones móviles.
    """
    now_ts = datetime.now(timezone.utc).timestamp()
    expired = [
        tid for tid, tdata in INVOICE_TASKS.items()
        if (now_ts - tdata.get("timestamp", now_ts)) > 1800
    ]
    for tid in expired:
        INVOICE_TASKS.pop(tid, None)

    task = INVOICE_TASKS.get(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tarea de procesamiento no encontrada o expirada."
        )
    return task

@router.post("/scan-qr", response_model=DianQRScanResponse)
async def scan_invoice_qr(
    file: UploadFile = File(..., description="Imagen o PDF que contiene el código QR de la factura DIAN"),
    allow_ocr: bool = Query(True, description="Permitir fallback de OCR para CUFE"),
    current_user: Dict[str, Any] = Depends(require_permission("can_lookup"))
):
    """
    Escanea y decodifica un código QR de Factura Electrónica DIAN desde una imagen o PDF.
    Extrae el CUFE/UUID, URL del catálogo VPFE, emisor, receptor, fecha y totales.
    Por defecto allow_ocr=False para no saturar la CPU en escaneos automáticos de fondo.
    """
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Archivo vacío.")

    # Ejecutar en hilo de fondo para no bloquear el bucle asíncrono de FastAPI
    qr_data = await asyncio.to_thread(DIANQRService.process_file_qr, file_bytes, file.filename or "", allow_ocr_fallback=allow_ocr)
    if not qr_data.get("has_qr"):
        return DianQRScanResponse(
            success=False,
            message="No se detectó ningún código QR legible en el documento o imagen."
        )

    cufe = qr_data.get("cufe") or qr_data.get("document_key")
    dian_url = qr_data.get("dian_url") or (DIANPortalService.get_search_url(cufe) if cufe else None)
    nit_receptor = qr_data.get("nit_receptor") or getattr(settings, "DIAN_RECEPTOR_NIT", "") or ""

    return DianQRScanResponse(
        success=True,
        cufe=cufe,
        dian_url=dian_url,
        document_key=qr_data.get("document_key") or cufe,
        raw_qr_data=qr_data.get("raw_text"),
        nit_emisor=qr_data.get("nit_emisor"),
        nit_receptor=nit_receptor,
        numero_factura=qr_data.get("numero_factura"),
        fecha=qr_data.get("fecha"),
        total=qr_data.get("total"),
        parsed_fields=qr_data.get("fields"),
        message="Código QR DIAN detectado y decodificado exitosamente."
    )

_ACTIVE_DIAN_TASKS: Dict[str, str] = {}

@router.post("/consult-dian")
async def consult_dian_document(
    payload: DianConsultRequest,
    async_mode: Optional[bool] = Query(default=None),
    current_user: Dict[str, Any] = Depends(require_permission("can_process_invoices"))
):
    """
    Consulta una factura electrónica en el Catálogo VPFE de la DIAN mediante su CUFE.
    - Diligencia el NIT de receptor configurado en .env (DIAN_RECEPTOR_NIT).
    - Descarga el PDF oficial, lo desencripta y extrae todos los productos y valores.
    - Soporta modo asíncrono (async_mode) mediante polling para prevenir timeouts de 30s en túneles (tunnl.gg).
    - Si el portal exige captcha interactivo de Cloudflare Turnstile, retorna el enlace
      oficial y el estado para asistir al usuario en un solo clic.
    """
    cufe = (payload.document_key or "").strip()
    if not cufe:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Debe suministrar el CUFE o DocumentKey.")

    nit = (payload.nit or getattr(settings, "DIAN_RECEPTOR_NIT", "") or "").strip()
    dian_search_url = DIANPortalService.get_search_url(cufe)

    async def _execute_consult() -> Dict[str, Any]:
        portal_res = await DIANPortalService.fetch_document(document_key=cufe, nit=nit)

        pdf_bytes = portal_res.get("pdf_bytes")
        metadata = portal_res.get("metadata") or {}
        requires_captcha = portal_res.get("requires_user_captcha", False)

        if portal_res.get("success") and pdf_bytes:
            # Procesar el PDF con PDFInvoiceEngine usando el NIT dinámico
            engine = VisionEngineFactory.get_engine(provider="pdf")
            effective_nit = (payload.nit or metadata.get("nit_receptor") or nit or "").strip()
            extracted = await engine.extract_invoice(
                pdf_bytes,
                filename=f"dian_{cufe[:12]}.pdf",
                nit=effective_nit,
                metadata=metadata
            )

            # Vincular ítems con catálogo/POS y aplicar conversión de unidades DIAN
            pos_service = POSService.get_instance()
            await enrich_items_with_pos_and_dian_conversion(extracted.get("items", []), pos_service)

            extracted["success"] = True
            extracted["supplier_name"] = extracted.get("proveedor") or metadata.get("emisor_nombre")
            extracted["nit"] = extracted.get("nit") or metadata.get("emisor_nit")
            extracted["invoice_number"] = extracted.get("numero_factura") or metadata.get("serie_folio")
            extracted["invoice_date"] = extracted.get("fecha") or metadata.get("fecha_emision")
            extracted["provider_used"] = "Portal Oficial DIAN (PDF Desencriptado)"
            extracted["confidence_score"] = 1.0

            # Guardar PDF en almacenamiento si está configurado
            try:
                storage = get_storage_provider()
                date_folder = datetime.now().strftime("%Y%m")
                unique_key = f"invoices/{date_folder}/dian_{cufe[:12]}.pdf"
                saved_path = await storage.save_file(unique_key, pdf_bytes, content_type="application/pdf")
                extracted["storage_path"] = saved_path
                extracted["file_url"] = storage.get_public_url(saved_path)
            except Exception as st_err:
                logger.warning(f"No se pudo guardar PDF descargado de DIAN en storage: {st_err}")

            extraction_obj = InvoiceExtractionResponse(**extracted)
            return DianConsultResponse(
                success=True,
                requires_user_captcha=False,
                dian_url=dian_search_url,
                cufe=cufe,
                nit_receptor=nit,
                metadata=metadata,
                extraction=extraction_obj,
                invoice=extraction_obj,
                message="Factura DIAN descargada y procesada exitosamente."
            ).model_dump(mode="json")

        # Si no se pudo obtener el PDF automáticamente (ej. Turnstile)
        return DianConsultResponse(
            success=False,
            requires_user_captcha=requires_captcha or True,
            dian_url=dian_search_url,
            cufe=cufe,
            nit_receptor=nit,
            metadata=metadata,
            message=portal_res.get("message") or portal_res.get("error") or "Se requiere validación de captcha en el portal DIAN. Puede ingresar con 1 clic y descargar el PDF con contraseña NIT."
        ).model_dump(mode="json")

    # Si se solicita modo asíncrono, ejecutar en segundo plano y responder de inmediato
    if async_mode is True:
        # Prevenir consultas duplicadas concurrentes para el mismo CUFE (evita abrir dos navegadores Playwright)
        existing_task_id = _ACTIVE_DIAN_TASKS.get(cufe)
        if existing_task_id and existing_task_id in INVOICE_TASKS:
            existing_task = INVOICE_TASKS[existing_task_id]
            if existing_task.get("status") == "processing":
                logger.info(f"Reutilizando consulta DIAN en curso (Task: {existing_task_id}) para CUFE {cufe[:16]}...")
                return JSONResponse(
                    status_code=status.HTTP_202_ACCEPTED,
                    content={
                        "success": True,
                        "async_task": True,
                        "task_id": existing_task_id,
                        "status": "processing",
                        "message": "Consulta en catálogo DIAN ya en curso; asociando a la sesión activa."
                    }
                )

        task_id = uuid.uuid4().hex
        _ACTIVE_DIAN_TASKS[cufe] = task_id
        now_ts = datetime.now(timezone.utc).timestamp()
        INVOICE_TASKS[task_id] = {
            "task_id": task_id,
            "status": "processing",
            "message": "Consultando catálogo oficial de la DIAN en segundo plano...",
            "timestamp": now_ts,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "result": None,
            "error": None
        }

        async def _background_consult(t_id: str):
            try:
                res_dict = await _execute_consult()
                INVOICE_TASKS[t_id]["status"] = "completed"
                INVOICE_TASKS[t_id]["result"] = res_dict
                INVOICE_TASKS[t_id]["message"] = "Consulta en portal DIAN finalizada exitosamente."
            except Exception as e_bg:
                logger.error(f"Error en consulta DIAN en segundo plano: {e_bg}", exc_info=True)
                INVOICE_TASKS[t_id]["status"] = "completed"
                INVOICE_TASKS[t_id]["result"] = DianConsultResponse(
                    success=False,
                    requires_user_captcha=True,
                    dian_url=dian_search_url,
                    cufe=cufe,
                    nit_receptor=nit,
                    message=f"No fue posible consultar directamente: {str(e_bg)}. Use el enlace directo al portal."
                ).model_dump(mode="json")
            finally:
                if _ACTIVE_DIAN_TASKS.get(cufe) == t_id:
                    _ACTIVE_DIAN_TASKS.pop(cufe, None)

        asyncio.create_task(_background_consult(task_id))
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "success": True,
                "async_task": True,
                "task_id": task_id,
                "status": "processing",
                "message": "Consulta en catálogo DIAN iniciada en segundo plano para evitar timeouts de red."
            }
        )

    # Modo síncrono estándar (ej. llamadas directas locales)
    try:
        res_dict = await _execute_consult()
        return DianConsultResponse(**res_dict)
    except Exception as e:
        logger.error(f"Error consultando documento en DIAN: {e}", exc_info=True)
        return DianConsultResponse(
            success=False,
            requires_user_captcha=True,
            dian_url=dian_search_url,
            cufe=cufe,
            nit_receptor=nit,
            message=f"No fue posible consultar directamente: {str(e)}. Use el enlace directo al portal."
        )

@router.post("/calculate-costs", response_model=InvoiceCostCalculationResponse)
async def calculate_invoice_costs(
    payload: InvoiceCostCalculationRequest,
    current_user: Dict[str, Any] = Depends(require_permission("can_lookup"))
):
    """
    Recalcula dinámicamente costos, impuestos y redondeo a centenas para todos los ítems de la factura.
    Permite activar/desactivar impuestos, definir equivalencias de empaque y márgenes individuales.
    """
    items_calculados = []
    total_costo_compra = 0.0
    total_impuestos_general = 0.0
    total_venta_estimada = 0.0

    for it in payload.items:
        eff_cost = it.costo_original if (it.costo_original is not None and it.costo_original > 0) else (getattr(it, "costo_presentacion", 0.0) or 0.0)
        calc = calcular_costos_item_factura(
            costo_original=eff_cost,
            cantidad=it.cantidad,
            presentacion=it.presentacion,
            unidades_por_presentacion=it.unidades_por_presentacion,
            descuento=getattr(it, "descuento", 0.0),
            descuento_valor=getattr(it, "descuento_valor", None),
            modo_descuento=getattr(it, "modo_descuento", None),
            operacion_descuento=getattr(it, "operacion_descuento", "restar"),
            iva_incluido=it.iva_incluido,
            conceptos_impuestos=[c.model_dump() for c in it.conceptos_impuestos],
            porcentaje_margen=it.porcentaje_margen,
            precio_venta_manual=it.precio_venta_manual,
            base_redondeo=payload.base_redondeo
        )
        calc["item_id"] = it.item_id
        items_calculados.append(calc)

        total_costo_compra += (calc["costo_neto_presentacion"] * it.cantidad)
        total_impuestos_general += (calc["total_impuestos_aplicados"] * it.cantidad)
        total_venta_estimada += (calc["precio_venta_final"] * calc["total_unidades_inventario"])

    ganancia_estimada = max(0.0, total_venta_estimada - total_costo_compra)
    margen_global = (ganancia_estimada / total_venta_estimada * 100.0) if total_venta_estimada > 0 else 0.0

    return InvoiceCostCalculationResponse(
        items_calculados=items_calculados,
        resumen={
            "total_costo_compra": round(total_costo_compra, 2),
            "total_impuestos": round(total_impuestos_general, 2),
            "total_venta_estimada": round(total_venta_estimada, 2),
            "ganancia_estimada": round(ganancia_estimada, 2),
            "margen_global_porcentaje": round(margen_global, 2),
            "total_items": len(items_calculados)
        }
    )

@router.post("/apply-to-inventory")
async def apply_invoice_to_inventory(
    payload: InvoiceApplyRequest,
    current_user: Dict[str, Any] = Depends(require_permission("can_process_invoices"))
):
    """
    Aplica los ítems de factura revisados al catálogo/POS:
    - Actualiza precios de productos existentes.
    - Crea nuevos productos si no existían previamente.
    - Guarda registro de la factura en el historial con trazabilidad completa.
    """
    prod_service = ProductService.get_instance()
    pos_service = POSService.get_instance()
    
    applied_count = 0
    created_count = 0
    errors = []
    items_to_save = []

    for item in payload.items:
        try:
            matched_id = None
            # Cantidad y costo efectivos considerando conversión de unidades DIAN
            effective_stock = item.cantidad_inventario_convertida if (getattr(item, "cantidad_inventario_convertida", None) is not None and item.cantidad_inventario_convertida > 0) else item.cantidad_unidades
            effective_cost = item.costo_inventario_convertido if (getattr(item, "costo_inventario_convertido", None) is not None and item.costo_inventario_convertido > 0) else item.costo_unitario

            if item.crear_como_nuevo or not item.pos_item_id:
                # Crear producto nuevo en catálogo
                created = await prod_service.create_product({
                    "name": item.nombre,
                    "category": item.categoria or "General",
                    "cost_price": effective_cost,
                    "unit_price": float(item.precio_venta_final),
                    "item_number": item.codigo if item.codigo and len(item.codigo) >= 3 else None,
                    "unit_code": item.unidad_code or "UN",
                    "stock_quantity": effective_stock,
                    "additional_numbers": item.additional_numbers or []
                })
                matched_id = created["id"]
                created_count += 1
            else:
                # Actualizar precio de producto existente en POS
                pos_id_str = str(item.pos_item_id).strip() if item.pos_item_id else None
                await pos_service.update_single_price(
                    barcode=item.codigo or pos_id_str,
                    new_price=float(item.precio_venta_final),
                    item_id=pos_id_str
                )

                # Sincronizar números adicionales en CSOPOS si cambiaron o se añadieron
                if item.additional_numbers and pos_id_str:
                    try:
                        clean_adds = [str(n).strip() for n in item.additional_numbers if str(n).strip() and str(n).strip() != "S/C"]
                        if clean_adds:
                            details = await pos_service.get_item_details_for_editing(pos_id_str)
                            if details:
                                existing_adds = details.get("additional_numbers") or []
                                combined_adds = list(set(existing_adds + clean_adds))
                                if len(combined_adds) > len(existing_adds):
                                    details["additional_numbers"] = combined_adds
                                    details["unit_price"] = float(item.precio_venta_final)
                                    await pos_service.create_or_update_pos_item(details, item_id=pos_id_str)
                    except Exception as add_err:
                        logger.warning(f"Aviso al sincronizar código adicional en CSOPOS para #{pos_id_str}: {add_err}")

                # Sincronización en base local SQLite:
                # Buscar producto local por pos_item_id, luego por código de barras / item_number, o por ID
                existing_p = None
                if pos_id_str:
                    existing_p = database.get_product_by_pos_id(pos_id_str)
                if not existing_p and item.codigo:
                    existing_p = database.get_product_by_barcode(item.codigo)
                if not existing_p and pos_id_str and pos_id_str.isdigit():
                    existing_p = database.get_product_by_id(int(pos_id_str))

                if existing_p:
                    local_id = existing_p["id"]
                    matched_id = local_id
                    canonical_name = existing_p.get("name") or item.nombre
                    primary_code = existing_p.get("item_number") or item.codigo

                    # Si el código de la factura es nuevo para este producto, vincularlo en números adicionales
                    if item.codigo and existing_p.get("item_number") != item.codigo:
                        database.add_additional_number_if_missing(local_id, item.codigo)

                    database.update_product(
                        product_id=local_id,
                        name=canonical_name,
                        category=item.categoria or (existing_p.get("category") if existing_p else "General"),
                        cost_price=effective_cost,
                        unit_price=float(item.precio_venta_final),
                        item_number=primary_code,
                        unit_code=item.unidad_code or existing_p.get("unit_code") or "UN",
                        stock_quantity=effective_stock
                    )
                else:
                    # El producto existe en el POS pero no estaba en la base SQLite local: registrarlo para mantener paridad
                    created_local = database.create_product(
                        name=item.nombre,
                        category=item.categoria or "General",
                        cost_price=effective_cost,
                        unit_price=float(item.precio_venta_final),
                        item_number=item.codigo,
                        unit_code=item.unidad_code or "UN",
                        stock_quantity=effective_stock,
                        pos_item_id=pos_id_str
                    )
                    matched_id = created_local.get("id") if isinstance(created_local, dict) else getattr(created_local, "id", None)
                    if matched_id and item.codigo:
                        database.add_additional_number_if_missing(matched_id, item.codigo)

                applied_count += 1

            items_to_save.append({
                "product_code": item.codigo,
                "description": item.nombre,
                "quantity": effective_stock,
                "presentation": item.unidad_code,
                "units_per_presentation": 1.0,
                "unit_cost_base": effective_cost,
                "tax_concepts": [],
                "unit_cost_net": effective_cost,
                "margin_percent": 30.0,
                "sale_price_calculated": float(item.precio_venta_final),
                "sale_price_final": int(item.precio_venta_final),
                "matched_product_id": matched_id
            })
        except Exception as it_err:
            logger.error(f"Error aplicando ítem {item.nombre}: {it_err}")
            errors.append(f"{item.nombre}: {str(it_err)}")

    # Guardar en base de datos local
    invoice_id = None
    if items_to_save:
        invoice_id = database.save_invoice_record({
            "provider_name": payload.provider_name or "Proveedor Factura",
            "provider_nit": payload.provider_nit,
            "invoice_number": payload.invoice_number,
            "invoice_date": payload.invoice_date,
            "total": sum(i["unit_cost_net"] * i["quantity"] for i in items_to_save),
            "status": "completada"
        }, items_to_save)

        # Auto-aprendizaje en la Memoria Adaptativa Continua (Plantilla y Alias)
        try:
            provider_data = {
                "provider_name": payload.provider_name or "Proveedor Factura",
                "provider_nit": payload.provider_nit,
                "layout_type": "table"
            }
            feedback_items = []
            for it in payload.items:
                feedback_items.append({
                    "raw_description": it.nombre,
                    "canonical_name": it.nombre,
                    "barcode": it.codigo,
                    "presentation": it.unidad_code or "Und",
                    "units_per_presentation": 1.0,
                    "margin": 30.0
                })
            learn_res = database.batch_learn_invoice_feedback(provider_data, feedback_items)
            logger.info(f"Memoria adaptativa auto-actualizada: {learn_res.get('aliases_updated', 0)} productos memorizados para '{payload.provider_name}'.")
        except Exception as e_learn:
            logger.warning(f"No se pudo auto-actualizar la memoria adaptativa: {e_learn}")

    return {
        "success": True,
        "invoice_id": invoice_id,
        "applied_count": applied_count,
        "created_count": created_count,
        "errors": errors,
        "message": f"Se procesaron {applied_count + created_count} productos exitosamente."
    }

@router.get("/history")
async def get_invoice_history(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: Dict[str, Any] = Depends(require_permission("can_lookup"))
):
    """Consulta el historial de facturas cargadas y procesadas."""
    return database.get_invoices_history(limit=limit, offset=offset)

@router.post("/learn", response_model=InvoiceLearnResponse)
async def learn_invoice_feedback(
    payload: InvoiceLearnRequest,
    current_user: Dict[str, Any] = Depends(require_permission("can_process_invoices"))
):
    """
    Guarda retroalimentación activa de la factura en la Memoria Adaptativa Continua:
    - Actualiza o crea la plantilla y orientación del proveedor.
    - Memoriza o actualiza alias de productos con códigos de barras y presentaciones.
    """
    try:
        provider_data = {
            "provider_name": payload.provider_name,
            "provider_nit": payload.provider_nit,
            "default_orientation": payload.orientation,
            "layout_type": payload.layout_type
        }
        items_data = [it.model_dump() for it in payload.items]
        res = database.batch_learn_invoice_feedback(provider_data, items_data)
        stats = database.get_learning_stats()
        
        return InvoiceLearnResponse(
            success=True,
            message=f"Aprendizaje guardado con éxito: plantilla de '{payload.provider_name}' y {res['aliases_updated']} alias memorizados.",
            templates_updated=res["templates_updated"],
            aliases_updated=res["aliases_updated"],
            stats=stats
        )
    except Exception as e:
        logger.error(f"Error guardando aprendizaje de factura: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"No se pudo guardar el aprendizaje: {str(e)}"
        )

@router.get("/learning-stats")
async def get_learning_memory_stats(
    current_user: Dict[str, Any] = Depends(require_permission("can_lookup"))
):
    """Consulta estadísticas de la memoria adaptativa continua (plantillas y alias aprendidos)."""
    return database.get_learning_stats()

@router.get("/{invoice_id}")
async def get_invoice_detail(
    invoice_id: int,
    current_user: Dict[str, Any] = Depends(require_permission("can_lookup"))
):
    """Obtiene el detalle completo de una factura registrada con sus ítems."""
    detail = database.get_invoice_detail(invoice_id)
    if not detail:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Factura no encontrada.")
    return detail

@router.get("/{invoice_id}/export-pdf")
async def export_invoice_pdf(
    invoice_id: int,
    current_user: Dict[str, Any] = Depends(require_permission("can_lookup"))
):
    """Exporta el comprobante de liquidación de factura a formato PDF profesional."""
    detail = database.get_invoice_detail(invoice_id)
    if not detail:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Factura no encontrada.")
    
    try:
        pdf_bytes = ExportService.generate_invoice_pdf(detail, detail.get("items", []))
        num = detail.get("invoice_number") or str(invoice_id)
        safe_num = "".join(c for c in str(num) if c.isalnum() or c in ("-", "_"))
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=\"factura_{safe_num}.pdf\""}
        )
    except Exception as e:
        logger.error(f"Error generando PDF para factura {invoice_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error generando PDF: {str(e)}")

@router.get("/{invoice_id}/export-excel")
async def export_invoice_excel(
    invoice_id: int,
    current_user: Dict[str, Any] = Depends(require_permission("can_lookup"))
):
    """Exporta el detalle de la factura a formato Excel (.xlsx)."""
    detail = database.get_invoice_detail(invoice_id)
    if not detail:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Factura no encontrada.")
    
    try:
        excel_bytes = ExportService.generate_invoice_excel(detail, detail.get("items", []))
        num = detail.get("invoice_number") or str(invoice_id)
        safe_num = "".join(c for c in str(num) if c.isalnum() or c in ("-", "_"))
        return Response(
            content=excel_bytes,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename=\"factura_{safe_num}.xlsx\""}
        )
    except Exception as e:
        logger.error(f"Error generando Excel para factura {invoice_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error generando Excel: {str(e)}")


