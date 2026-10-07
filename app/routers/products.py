import asyncio
from typing import Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from app.core.security import get_current_user, require_permission
from app.core.database import add_audit_log
from app.services.pos_service import POSService
from app.models.schemas import (
    ProductLookupResponse,
    ProductSearchResponse,
    SinglePriceUpdateRequest,
    BulkPriceUpdateRequest,
    PriceUpdateResponse,
    PosStatusResponse,
    PosItemDetailResponse
)

router = APIRouter(prefix="/products", tags=["Productos y Precios"])

def get_pos_service() -> POSService:
    return POSService.get_instance()

@router.get("/status", response_model=PosStatusResponse)
async def get_pos_status(
    current_user: Dict[str, Any] = Depends(get_current_user),
    pos_service: POSService = Depends(get_pos_service)
):
    """Consulta el estado del servicio POS externo."""
    if not pos_service.is_initialized and not pos_service.last_error:
        try:
            await asyncio.wait_for(pos_service.initialize(), timeout=12.0)
        except asyncio.TimeoutError:
            pos_service.last_error = "Tiempo de espera agotado al conectar con el servidor POS externo."
        except Exception as e:
            pos_service.last_error = f"Error conectando con POS: {str(e) or type(e).__name__}"
    status_info = await pos_service.get_status()
    return PosStatusResponse(**status_info)

@router.get("/lookup", response_model=ProductLookupResponse)
async def lookup_product(
    barcode: str = Query(..., min_length=1, description="Código de barras escaneado o ingresado"),
    current_user: Dict[str, Any] = Depends(require_permission("can_lookup")),
    pos_service: POSService = Depends(get_pos_service)
):
    """
    Consulta un producto en el POS por código de barras.
    Obtiene los datos con validación estricta y respuesta acelerada.
    """
    try:
        result = await pos_service.lookup_product_by_barcode(barcode)
        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error durante la consulta del producto: {str(e)}"
        )

@router.get("/search", response_model=ProductSearchResponse)
async def search_products(
    query: str = Query("", description="Término o nombre de producto a buscar (vacío para catálogo inicial)"),
    current_user: Dict[str, Any] = Depends(require_permission("can_lookup")),
    pos_service: POSService = Depends(get_pos_service)
):
    """
    Busca productos en el catálogo de CSOPOS.
    """
    try:
        results = await pos_service.search_products(query)
        return results
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error durante la búsqueda de productos: {str(e)}"
        )

@router.get("/csopos/{item_id}", response_model=PosItemDetailResponse)
async def get_csopos_item_detail(
    item_id: str,
    current_user: Dict[str, Any] = Depends(require_permission("can_lookup")),
    pos_service: POSService = Depends(get_pos_service)
):
    """
    Obtiene los detalles completos de un producto directamente desde CSOPOS para su edición.
    """
    try:
        data = await pos_service.get_pos_item_details(item_id)
        if not data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No se encontró el producto con ID '{item_id}' en CSOPOS."
            )
        return PosItemDetailResponse(**data)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al obtener el producto de CSOPOS: {str(e)}"
        )

@router.post("/update-price", response_model=PriceUpdateResponse)
async def update_single_price(
    payload: SinglePriceUpdateRequest,
    current_user: Dict[str, Any] = Depends(require_permission("can_edit_single")),
    pos_service: POSService = Depends(get_pos_service)
):
    """
    Modifica el precio de venta de un producto individual y registra la auditoría.
    """
    try:
        response = await pos_service.update_single_price(
            barcode=payload.barcode,
            new_price=payload.new_price,
            item_id=payload.item_id,
            modal_barcode=payload.modal_barcode
        )
        if not response.success:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=response.message)
            
        # Registrar en la tabla de auditoría
        add_audit_log(
            user_id=current_user.get("id"),
            username=current_user.get("username", "admin"),
            action_type="single_update",
            barcode=payload.modal_barcode or payload.barcode,
            item_name=payload.item_name,
            old_price=payload.old_price,
            new_price=payload.new_price,
            formatted_new_price=response.formatted_new_price,
            updated_count=1,
            details=f"Precio actualizado individualmente por {current_user.get('username')}"
        )
        
        return response
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error actualizando el precio del producto: {str(e)}"
        )

@router.post("/bulk-update-prices", response_model=PriceUpdateResponse)
async def bulk_update_prices(
    payload: BulkPriceUpdateRequest,
    current_user: Dict[str, Any] = Depends(require_permission("can_edit_bulk")),
    pos_service: POSService = Depends(get_pos_service)
):
    """
    Modificación masiva de precios con registro de auditoría.
    """
    try:
        response = await pos_service.bulk_update_prices(
            item_ids=payload.item_ids,
            new_price=payload.new_price
        )
        if not response.success:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=response.message)
            
        # Registrar en auditoría
        add_audit_log(
            user_id=current_user.get("id"),
            username=current_user.get("username", "admin"),
            action_type="bulk_update",
            barcode=f"{len(payload.item_ids)} artículos",
            item_name=f"Edición masiva de {len(payload.item_ids)} productos",
            new_price=payload.new_price,
            formatted_new_price=response.formatted_new_price,
            updated_count=len(payload.item_ids),
            details=payload.details or f"Actualización masiva de {len(payload.item_ids)} productos por {current_user.get('username')}"
        )
        
        return response
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error en la actualización masiva de precios: {str(e)}"
        )
