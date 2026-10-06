import io
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, status
from fastapi.responses import Response

from app.core.config import settings
from app.core.security import get_current_user, require_permission
from app.core import database
from app.services.invoice_parser_service import VisionEngineFactory
from app.services.pricing_engine import calcular_costos_item_factura
from app.services.product_service import ProductService
from app.services.pos_service import POSService
from app.services.storage.storage_factory import get_storage_provider
from app.services.export_service import ExportService
from app.services.dian_qr_service import DIANQRService
from app.services.dian_portal_service import DIANPortalService
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

@router.post("/process-image", response_model=InvoiceExtractionResponse)
async def process_invoice_image(
    file: UploadFile = File(..., description="Imagen de la factura o captura de cámara"),
    provider: str = Query("auto", description="Motor OCR: auto / unified (Super-Motor Unificado Ensemble), paddle_vl (PaddleOCR-VL 0.9B ONNX Local), server (PP-OCRv4 Server), rapid_table (SLANet Tablas), gemini (Gemini 2.5 Flash Nube)"),
    current_user: Dict[str, Any] = Depends(require_permission("can_edit_single"))
):
    """
    Procesa una imagen de factura mediante visión por computadora / OCR desacoplado.
    Identifica proveedor, número, fecha, productos, cantidades, unidades, costos e impuestos.
    Soporta selección de motor:
      - 'auto' / 'unified': Super-Motor Unificado Inteligente (Ensemble: RapidOCR + RapidTable + PaddleOCR-VL 0.9B ONNX + Nube).
      - 'paddle_vl': Modelo VLM especializado PaddleOCR-VL 0.9B ONNX local en proceso.
      - 'server': Modelo PP-OCRv4 Server en ONNX Runtime (rápido, alta precisión en números/térmicas).
      - 'rapid_table': Modelo SLANet de RapidTable para reconstrucción matricial de tablas complejas.
      - 'gemini': Google Gemini 2.5 Flash Multimodal.
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

    # 2. Ejecutar motor de visión o lector XML
    try:
        is_xml = is_xml_ext or content_type in ("text/xml", "application/xml") or image_bytes.strip().startswith(b"<")
        engine = VisionEngineFactory.get_engine(is_xml=is_xml, provider=provider)
        result = await engine.extract_invoice(image_bytes, filename=file.filename)
        result["storage_path"] = saved_storage_path
        result["file_url"] = file_url
        
        # 3. Intentar vincular ítems con productos existentes en POS o catálogo local
        pos_service = POSService.get_instance()
        for it in result.get("items", []):
            code = it.get("codigo")
            desc = it.get("descripcion", "")
            matched = None

            if code:
                local_p = database.get_product_by_barcode(code)
                if local_p:
                    matched = {
                        "item_id": str(local_p["id"]),
                        "barcode": local_p.get("item_number") or code,
                        "name": local_p["name"],
                        "category": local_p.get("category", ""),
                        "cost_price": local_p.get("cost_price", 0.0),
                        "sale_price": local_p.get("unit_price", 0.0),
                        "formatted_sale_price": local_p.get("formatted_sale_price", ""),
                        "stock": str(local_p.get("stock_quantity", 0))
                    }

            if not matched and desc:
                # Buscar por primeras palabras en POS
                search_term = " ".join(desc.split()[:2])
                try:
                    pos_search = await pos_service.search_products(search_term)
                    if pos_search.items:
                        matched = pos_search.items[0].model_dump()
                except Exception:
                    pass

            it["matched_pos_item"] = matched

        result["success"] = True
        result["supplier_name"] = result.get("proveedor")
        result["invoice_number"] = result.get("numero_factura")
        result["invoice_date"] = result.get("fecha")
        result["provider_used"] = result.get("motor_utilizado")
        result["confidence_score"] = 0.95
        result["confidence_score"] = result.get("confidence_score", 0.95)

        return InvoiceExtractionResponse(**result)
    except Exception as e:
        logger.error(f"Error procesando factura: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error durante el procesamiento de la factura: {str(e)}"
        )

@router.post("/scan-qr", response_model=DianQRScanResponse)
async def scan_invoice_qr(
    file: UploadFile = File(..., description="Imagen o PDF que contiene el código QR de la factura DIAN"),
    current_user: Dict[str, Any] = Depends(require_permission("can_lookup"))
):
    """
    Escanea y decodifica un código QR de Factura Electrónica DIAN desde una imagen o PDF.
    Extrae el CUFE/UUID, URL del catálogo VPFE, emisor, receptor, fecha y totales.
    """
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Archivo vacío.")

    qr_data = DIANQRService.process_file_qr(file_bytes, filename=file.filename or "")
    if not qr_data.get("has_qr"):
        return DianQRScanResponse(
            success=False,
            message="No se detectó ningún código QR legible en el documento o imagen."
        )

    cufe = qr_data.get("cufe") or qr_data.get("document_key")
    dian_url = qr_data.get("dian_url") or (DIANPortalService.get_search_url(cufe) if cufe else None)
    nit_receptor = qr_data.get("nit_receptor") or getattr(settings, "DIAN_RECEPTOR_NIT", "40327379")

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

@router.post("/consult-dian", response_model=DianConsultResponse)
async def consult_dian_document(
    payload: DianConsultRequest,
    current_user: Dict[str, Any] = Depends(require_permission("can_edit_single"))
):
    """
    Consulta una factura electrónica en el Catálogo VPFE de la DIAN mediante su CUFE.
    - Diligencia el NIT de receptor configurado en .env (DIAN_RECEPTOR_NIT).
    - Descarga el PDF oficial, lo desencripta y extrae todos los productos y valores.
    - Si el portal exige captcha interactivo de Cloudflare Turnstile, retorna el enlace
      oficial y el estado para asistir al usuario en un solo clic.
    """
    cufe = (payload.document_key or "").strip()
    if not cufe:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Debe suministrar el CUFE o DocumentKey.")

    nit = (payload.nit or getattr(settings, "DIAN_RECEPTOR_NIT", "40327379")).strip()
    dian_search_url = DIANPortalService.get_search_url(cufe)

    try:
        portal_res = await DIANPortalService.fetch_document(document_key=cufe, nit=nit)

        pdf_bytes = portal_res.get("pdf_bytes")
        metadata = portal_res.get("metadata") or {}
        requires_captcha = portal_res.get("requires_user_captcha", False)

        if portal_res.get("success") and pdf_bytes:
            # Procesar el PDF con PDFInvoiceEngine
            engine = VisionEngineFactory.get_engine(provider="pdf")
            extracted = await engine.extract_invoice(pdf_bytes, filename=f"dian_{cufe[:12]}.pdf")

            # Vincular ítems con catálogo/POS (idéntico a /process-image)
            pos_service = POSService.get_instance()
            for it in extracted.get("items", []):
                code = it.get("codigo")
                desc = it.get("descripcion", "")
                matched = None
                if code:
                    local_p = database.get_product_by_barcode(code)
                    if local_p:
                        matched = {
                            "item_id": str(local_p["id"]),
                            "barcode": local_p.get("item_number") or code,
                            "name": local_p["name"],
                            "category": local_p.get("category", ""),
                            "cost_price": local_p.get("cost_price", 0.0),
                            "sale_price": local_p.get("unit_price", 0.0),
                            "formatted_sale_price": local_p.get("formatted_sale_price", ""),
                            "stock": str(local_p.get("stock_quantity", 0))
                        }
                if not matched and desc:
                    search_term = " ".join(desc.split()[:2])
                    try:
                        pos_search = await pos_service.search_products(search_term)
                        if pos_search.items:
                            matched = pos_search.items[0].model_dump()
                    except Exception:
                        pass
                it["matched_pos_item"] = matched

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

            return DianConsultResponse(
                success=True,
                requires_user_captcha=False,
                dian_url=dian_search_url,
                cufe=cufe,
                nit_receptor=nit,
                metadata=metadata,
                extraction=InvoiceExtractionResponse(**extracted),
                message="Factura DIAN descargada y procesada exitosamente."
            )

        # Si no se pudo obtener el PDF automáticamente (ej. Turnstile)
        return DianConsultResponse(
            success=False,
            requires_user_captcha=requires_captcha or True,
            dian_url=dian_search_url,
            cufe=cufe,
            nit_receptor=nit,
            metadata=metadata,
            message=portal_res.get("error") or "Se requiere validación de captcha en el portal DIAN. Puede ingresar con 1 clic y descargar el PDF con contraseña NIT."
        )
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
        calc = calcular_costos_item_factura(
            costo_original=it.costo_original,
            cantidad=it.cantidad,
            presentacion=it.presentacion,
            unidades_por_presentacion=it.unidades_por_presentacion,
            descuento=getattr(it, "descuento", 0.0),
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
    current_user: Dict[str, Any] = Depends(require_permission("can_edit_single"))
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
            if item.crear_como_nuevo or not item.pos_item_id:
                # Crear producto nuevo en catálogo
                created = await prod_service.create_product({
                    "name": item.nombre,
                    "category": item.categoria or "General",
                    "cost_price": item.costo_unitario,
                    "unit_price": float(item.precio_venta_final),
                    "item_number": item.codigo if item.codigo and len(item.codigo) >= 3 else None,
                    "unit_code": item.unidad_code or "UN",
                    "stock_quantity": item.cantidad_unidades,
                    "additional_numbers": item.additional_numbers or []
                })
                matched_id = created["id"]
                created_count += 1
            else:
                # Actualizar precio de producto existente
                matched_id = int(item.pos_item_id) if str(item.pos_item_id).isdigit() else None
                await pos_service.update_single_price(
                    barcode=item.codigo or str(item.pos_item_id),
                    new_price=float(item.precio_venta_final),
                    item_id=str(item.pos_item_id)
                )
                if matched_id:
                    database.update_product(
                        product_id=matched_id,
                        name=item.nombre,
                        category=item.categoria or "General",
                        cost_price=item.costo_unitario,
                        unit_price=float(item.precio_venta_final),
                        item_number=item.codigo,
                        unit_code=item.unidad_code or "UN",
                        stock_quantity=item.cantidad_unidades
                    )
                applied_count += 1

            items_to_save.append({
                "product_code": item.codigo,
                "description": item.nombre,
                "quantity": item.cantidad_unidades,
                "presentation": item.unidad_code,
                "units_per_presentation": 1.0,
                "unit_cost_base": item.costo_unitario,
                "tax_concepts": [],
                "unit_cost_net": item.costo_unitario,
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
    current_user: Dict[str, Any] = Depends(require_permission("can_edit_single"))
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


