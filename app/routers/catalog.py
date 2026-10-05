from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from app.core.security import get_current_user, require_permission
from app.core import database
from app.services.product_service import ProductService
from app.services.pos_service import POSService
from app.models.schemas import (
    ProductCreateRequest,
    ProductUpdateRequest,
    ProductDetailResponse,
    UnitOfMeasureResponse,
    UnitOfMeasureCreateRequest,
    UnitOfMeasureUpdateRequest,
    BarcodeCheckResponse,
    PackagingTypeResponse,
    PackagingTypeCreateRequest,
    PackagingTypeUpdateRequest,
    ExpenseConceptResponse,
    ExpenseConceptCreateRequest,
    ExpenseConceptUpdateRequest,
    DepartmentResponse,
    CategoryItemResponse,
    CategorySearchResponse
)

router = APIRouter(prefix="/catalog", tags=["Catálogo y Creación de Productos"])

def get_product_service() -> ProductService:
    return ProductService.get_instance()

def get_pos_service() -> POSService:
    return POSService.get_instance()

@router.get("/departments", response_model=List[DepartmentResponse])
async def list_departments(
    current_user: Dict[str, Any] = Depends(require_permission("can_lookup")),
    pos_svc: POSService = Depends(get_pos_service)
):
    """Retorna los 50 departamentos maestros del POS (csopos.co / softwarepos.online)."""
    return pos_svc.get_departments()

@router.get("/categories-by-department", response_model=List[CategoryItemResponse])
async def list_categories_by_department(
    department_code: str = Query(..., description="Código de departamento POS (ej. D56)"),
    current_user: Dict[str, Any] = Depends(require_permission("can_lookup")),
    pos_svc: POSService = Depends(get_pos_service)
):
    """Retorna las categorías pertenecientes a un departamento maestro."""
    return await pos_svc.get_categories_by_department(department_code)

@router.get("/search-categories", response_model=List[CategorySearchResponse])
async def search_categories(
    term: str = Query(..., min_length=1, description="Texto de búsqueda de categoría"),
    current_user: Dict[str, Any] = Depends(require_permission("can_lookup")),
    pos_svc: POSService = Depends(get_pos_service)
):
    """Búsqueda omnidireccional de categorías en el POS."""
    return await pos_svc.search_categories_pos(term)

@router.get("/products", response_model=List[ProductDetailResponse])
async def list_products(
    query: Optional[str] = Query(None, description="Búsqueda por nombre o código"),
    category: Optional[str] = Query(None, description="Filtrar por categoría"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: Dict[str, Any] = Depends(require_permission("can_lookup")),
    svc: ProductService = Depends(get_product_service)
):
    """Lista productos del catálogo maestro con sus códigos de barra adicionales."""
    return svc.list_products(query=query, category=category, limit=limit, offset=offset)

@router.get("/products/{product_id}", response_model=ProductDetailResponse)
async def get_product(
    product_id: int,
    current_user: Dict[str, Any] = Depends(require_permission("can_lookup")),
    svc: ProductService = Depends(get_product_service)
):
    """Obtiene el detalle completo de un producto."""
    prod = svc.get_product(product_id)
    if not prod:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Producto no encontrado.")
    return prod

@router.post("/products", response_model=ProductDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_product(
    payload: ProductCreateRequest,
    current_user: Dict[str, Any] = Depends(require_permission("can_edit_single")),
    svc: ProductService = Depends(get_product_service)
):
    """
    Crea un nuevo producto en el catálogo e intenta sincronizarlo con el POS externo.
    Validaciones estrictas: Nombre, Categoría, Costo y Precio de venta obligatorios.
    UPC/EAN/ISBN opcional. Soporte de múltiples números adicionales.
    """
    try:
        created = await svc.create_product(payload.model_dump())
        return created
    except ValueError as val_err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(val_err))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error creando producto: {str(e)}")

@router.put("/products/{product_id}", response_model=ProductDetailResponse)
async def update_product(
    product_id: int,
    payload: ProductUpdateRequest,
    current_user: Dict[str, Any] = Depends(require_permission("can_edit_single")),
    svc: ProductService = Depends(get_product_service)
):
    """Actualiza la información de un producto existente y sus códigos adicionales."""
    try:
        updated = await svc.update_product(product_id, payload.model_dump())
        return updated
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Producto no encontrado.")
    except ValueError as val_err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(val_err))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error actualizando producto: {str(e)}")

@router.post("/products/{product_id}/resync-pos", response_model=ProductDetailResponse)
async def resync_product_pos(
    product_id: int,
    current_user: Dict[str, Any] = Depends(require_permission("can_edit_single")),
    svc: ProductService = Depends(get_product_service)
):
    """Reintenta la sincronización de un producto local con el POS externo."""
    prod = svc.get_product(product_id)
    if not prod:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Producto no encontrado.")
    try:
        updated = await svc.update_product(product_id, prod)
        return updated
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error al re-sincronizar con POS: {str(e)}")

@router.get("/check-barcode", response_model=BarcodeCheckResponse)
async def check_barcode_availability(
    barcode: str = Query(..., min_length=1, description="Código a verificar"),
    exclude_id: Optional[int] = Query(None, description="ID de producto a excluir en edición"),
    current_user: Dict[str, Any] = Depends(require_permission("can_lookup")),
    svc: ProductService = Depends(get_product_service)
):
    """Verifica en tiempo real si un código principal o adicional ya se encuentra en uso."""
    is_avail, msg = svc.check_barcode(barcode, exclude_product_id=exclude_id)
    return BarcodeCheckResponse(barcode=barcode, available=is_avail, message=msg)

@router.get("/units", response_model=List[UnitOfMeasureResponse])
async def list_units(
    current_user: Dict[str, Any] = Depends(require_permission("can_lookup")),
    svc: ProductService = Depends(get_product_service)
):
    """Retorna las unidades de medida disponibles para Tienda Principal."""
    return svc.get_units()

@router.post("/units", response_model=UnitOfMeasureResponse, status_code=status.HTTP_201_CREATED)
async def create_unit(
    payload: UnitOfMeasureCreateRequest,
    current_user: Dict[str, Any] = Depends(require_permission("can_edit_single")),
    svc: ProductService = Depends(get_product_service)
):
    """Agrega una nueva unidad o medida de forma desacoplada y extensible."""
    try:
        created = svc.create_unit(
            code=payload.code,
            name=payload.name,
            magnitude_type=payload.magnitude_type,
            factor=payload.conversion_factor_kg
        )
        return created
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Error creando unidad: {str(e)}")

@router.put("/units/{unit_id}", response_model=UnitOfMeasureResponse)
async def update_unit(
    unit_id: int,
    payload: UnitOfMeasureUpdateRequest,
    current_user: Dict[str, Any] = Depends(require_permission("can_edit_single")),
    svc: ProductService = Depends(get_product_service)
):
    """Actualiza una unidad de medida existente."""
    updated = svc.update_unit(
        unit_id,
        name=payload.name,
        magnitude_type=payload.magnitude_type,
        factor=payload.conversion_factor_kg,
        is_active=payload.is_active
    )
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unidad de medida no encontrada.")
    return updated

@router.delete("/units/{unit_id}")
async def delete_unit(
    unit_id: int,
    soft: bool = Query(True, description="Baja lógica (soft delete)"),
    current_user: Dict[str, Any] = Depends(require_permission("can_edit_single")),
    svc: ProductService = Depends(get_product_service)
):
    """Elimina o da de baja lógica una unidad de medida."""
    ok = svc.delete_unit(unit_id, soft=soft)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unidad de medida no encontrada.")
    return {"status": "ok", "message": f"Unidad {unit_id} eliminada/desactivada exitosamente."}

@router.get("/categories", response_model=List[str])
async def list_categories(
    current_user: Dict[str, Any] = Depends(require_permission("can_lookup")),
    svc: ProductService = Depends(get_product_service)
):
    """Retorna la lista de categorías del catálogo."""
    return svc.get_categories()

@router.get("/packaging-types", response_model=List[PackagingTypeResponse])
async def list_packaging_types(
    current_user: Dict[str, Any] = Depends(require_permission("can_lookup"))
):
    """Retorna los tipos de empaque / canastillas configurados con tara y costo."""
    return database.get_all_packaging_types(only_active=True)

@router.post("/packaging-types", response_model=PackagingTypeResponse, status_code=status.HTTP_201_CREATED)
async def create_packaging_type(
    payload: PackagingTypeCreateRequest,
    current_user: Dict[str, Any] = Depends(require_permission("can_edit_single"))
):
    """Crea un nuevo tipo de empaque o canastilla."""
    try:
        return database.create_packaging_type(
            codigo=payload.codigo,
            nombre=payload.nombre,
            tara_kg=payload.tara_kg,
            capacidad_kg=payload.capacidad_kg,
            costo_empaque=payload.costo_empaque
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Error creando empaque: {str(e)}")

@router.put("/packaging-types/{packaging_id}", response_model=PackagingTypeResponse)
async def update_packaging_type(
    packaging_id: int,
    payload: PackagingTypeUpdateRequest,
    current_user: Dict[str, Any] = Depends(require_permission("can_edit_single"))
):
    """Actualiza un tipo de empaque existente."""
    updated = database.update_packaging_type(
        packaging_id=packaging_id,
        nombre=payload.nombre,
        tara_kg=payload.tara_kg,
        capacidad_kg=payload.capacidad_kg,
        costo_empaque=payload.costo_empaque,
        activo=payload.activo
    )
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tipo de empaque no encontrado.")
    return updated

@router.delete("/packaging-types/{packaging_id}")
async def delete_packaging_type(
    packaging_id: int,
    soft: bool = Query(True, description="Baja lógica"),
    current_user: Dict[str, Any] = Depends(require_permission("can_edit_single"))
):
    """Elimina o da de baja lógica un tipo de empaque."""
    ok = database.delete_packaging_type(packaging_id, soft=soft)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tipo de empaque no encontrado.")
    return {"status": "ok", "message": f"Empaque {packaging_id} eliminado/desactivado con éxito."}

@router.get("/expense-concepts", response_model=List[ExpenseConceptResponse])
async def list_expense_concepts(
    current_user: Dict[str, Any] = Depends(require_permission("can_lookup"))
):
    """Retorna conceptos de gastos operativos (fletes, empaques, cuadrilla)."""
    return database.get_all_expense_concepts(only_active=True)

@router.post("/expense-concepts", response_model=ExpenseConceptResponse, status_code=status.HTTP_201_CREATED)
async def create_expense_concept(
    payload: ExpenseConceptCreateRequest,
    current_user: Dict[str, Any] = Depends(require_permission("can_edit_single"))
):
    """Crea un nuevo concepto de gasto operativo."""
    try:
        return database.create_expense_concept(
            codigo=payload.codigo,
            nombre=payload.nombre,
            tipo=payload.tipo,
            distribuir_en_costo=payload.distribuir_en_costo
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Error creando concepto de gasto: {str(e)}")

@router.put("/expense-concepts/{concept_id}", response_model=ExpenseConceptResponse)
async def update_expense_concept(
    concept_id: int,
    payload: ExpenseConceptUpdateRequest,
    current_user: Dict[str, Any] = Depends(require_permission("can_edit_single"))
):
    """Actualiza un concepto de gasto operativo."""
    updated = database.update_expense_concept(
        concept_id=concept_id,
        nombre=payload.nombre,
        tipo=payload.tipo,
        distribuir_en_costo=payload.distribuir_en_costo,
        activo=payload.activo
    )
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Concepto de gasto no encontrado.")
    return updated

@router.delete("/expense-concepts/{concept_id}")
async def delete_expense_concept(
    concept_id: int,
    soft: bool = Query(True, description="Baja lógica"),
    current_user: Dict[str, Any] = Depends(require_permission("can_edit_single"))
):
    """Elimina o da de baja lógica un concepto de gasto operativo."""
    ok = database.delete_expense_concept(concept_id, soft=soft)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Concepto de gasto no encontrado.")
    return {"status": "ok", "message": f"Concepto de gasto {concept_id} eliminado/desactivado con éxito."}


