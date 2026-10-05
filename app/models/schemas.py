from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict

# --- PERMISSIONS SCHEMA ---
class UserPermissions(BaseModel):
    can_lookup: bool = True
    can_edit_single: bool = False
    can_edit_bulk: bool = False
    can_manage_users: bool = False
    can_view_audit: bool = False

# --- AUTH & USER SCHEMAS ---
class LoginRequest(BaseModel):
    username: str = Field(..., description="Nombre de usuario")
    password: str = Field(..., description="Contraseña")

class UserResponse(BaseModel):
    id: Optional[int] = None
    username: str
    full_name: str
    role: str = "cajero"
    permissions: UserPermissions
    is_active: bool = True
    created_at: Optional[str] = None
    last_login: Optional[str] = None

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    user: UserResponse

class UserCreateRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    full_name: str = Field(..., min_length=2, max_length=100)
    password: str = Field(..., min_length=4)
    role: str = Field("cajero", description="admin, supervisor, cajero")
    permissions: Optional[UserPermissions] = None

class UserUpdateRequest(BaseModel):
    full_name: Optional[str] = None
    role: Optional[str] = None
    permissions: Optional[UserPermissions] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None

class PasswordResetRequest(BaseModel):
    new_password: str = Field(..., min_length=4)

# --- AUDIT LOG SCHEMA ---
class AuditLogResponse(BaseModel):
    id: int
    user_id: Optional[int] = None
    username: str
    action_type: str
    barcode: str
    item_name: Optional[str] = None
    old_price: Optional[float] = None
    new_price: float
    formatted_new_price: str
    updated_count: int = 1
    timestamp: str
    details: Optional[str] = None

# --- PRODUCT SCHEMAS ---
class ProductLookupResponse(BaseModel):
    found: bool
    item_id: Optional[str] = None
    barcode: str
    modal_barcode: Optional[str] = None
    name: Optional[str] = None
    category: Optional[str] = None
    unit_price: float = 0.0
    formatted_price: str = "$0"
    stock: Optional[str] = None
    message: Optional[str] = None

class ProductItem(BaseModel):
    item_id: str
    barcode: str
    name: str
    category: str = ""
    cost_price: float = 0.0
    sale_price: float = 0.0
    formatted_sale_price: str = "$0"
    stock: str = "0"

class ProductSearchResponse(BaseModel):
    query: str
    total: int
    items: List[ProductItem]

class SinglePriceUpdateRequest(BaseModel):
    barcode: str = Field(..., description="Código de barras escaneado o del modal")
    modal_barcode: Optional[str] = Field(None, description="Código de barras principal extraído del modal de ventas")
    new_price: float = Field(..., gt=0, description="Nuevo precio de venta unitario")
    item_id: Optional[str] = Field(None, description="ID interno del artículo si se conoce")
    item_name: Optional[str] = Field(None, description="Nombre descriptivo del producto para auditoría")
    old_price: Optional[float] = Field(None, description="Precio anterior para auditoría")

class BulkPriceUpdateRequest(BaseModel):
    item_ids: List[str] = Field(..., min_length=1, description="Lista de IDs de artículos a modificar")
    new_price: float = Field(..., gt=0, description="Nuevo precio de venta para todos los artículos seleccionados")
    details: Optional[str] = Field(None, description="Detalles o nombres de los artículos para auditoría")

class PriceUpdateResponse(BaseModel):
    success: bool
    message: str
    updated_count: int = 1
    new_price: float
    formatted_new_price: str

class PosStatusResponse(BaseModel):
    is_ready: bool
    is_logged_in: bool
    base_url: str
    active_domain: str = "csopos.co"
    active_page: str
    timestamp: str
    last_error: Optional[str] = None

# --- UNIDADES DE MEDIDA SCHEMAS ---
class UnitOfMeasureResponse(BaseModel):
    id: int
    code: str
    name: str
    magnitude_type: str = "PESO"
    conversion_factor_kg: float = 1.0
    is_active: bool = True
    created_at: Optional[str] = None

class UnitOfMeasureCreateRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=20, description="Código corto (ej. UN, KG, CJ)")
    name: str = Field(..., min_length=2, max_length=100, description="Nombre legible")
    magnitude_type: str = Field("PESO", description="PESO, VOLUMEN, UNIDAD, LONGITUD, etc.")
    conversion_factor_kg: float = Field(1.0, ge=0.0)

class UnitOfMeasureUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    magnitude_type: Optional[str] = None
    conversion_factor_kg: Optional[float] = Field(None, ge=0.0)
    is_active: Optional[bool] = None

# --- DEPARTAMENTOS Y CATEGORÍAS (REFERENCIA CSOPOS) ---
class DepartmentResponse(BaseModel):
    code: str = Field(..., description="Código de departamento (ej. D56)")
    name: str = Field(..., description="Nombre del departamento")

class CategoryItemResponse(BaseModel):
    code: str = Field(..., description="Código de categoría (ej. C3677)")
    name: str = Field(..., description="Nombre de la categoría")
    department_code: str = Field(..., description="Código del departamento padre")
    department_name: str = Field(..., description="Nombre del departamento padre")

class CategorySearchResponse(BaseModel):
    code: str = Field(..., description="Código de categoría (ej. C3677)")
    name: str = Field(..., description="Nombre de la categoría")
    department_code: Optional[str] = Field(None, description="Código de departamento")
    department_name: Optional[str] = Field(None, description="Nombre de departamento")

# --- CREACIÓN Y EDICIÓN DE PRODUCTOS (REFERENCIA CSOPOS) ---
class ProductCreateRequest(BaseModel):
    # UPC/EAN/ISBN es OPCIONAL según directiva
    item_number: Optional[str] = Field(None, max_length=50, description="UPC/EAN/ISBN opcional")
    # Campos estrictamente obligatorios validados en backend
    name: str = Field(..., min_length=2, max_length=200, description="Nombre del artículo (Obligatorio)")
    category: str = Field(..., min_length=1, max_length=100, description="Categoría del artículo (Obligatorio)")
    category_code: Optional[str] = Field(None, description="Código de categoría interna POS (ej. C3677)")
    department_code: Optional[str] = Field(None, description="Código de departamento POS (ej. D56)")
    cost_price: float = Field(..., ge=0.0, description="Costo Sin Impuesto (Obligatorio)")
    unit_price: float = Field(..., ge=0.0, description="Precio de venta Sin Impuesto (Obligatorio)")
    
    # Tienda Principal y opciones
    unit_code: str = Field("UN", description="Unidad de medida (UN, KG, etc.)")
    stock_quantity: float = Field(0.0, ge=0.0, description="Cantidad stock en tienda principal")
    description: Optional[str] = Field(None, description="Descripción opcional")
    profit_percentage: float = Field(30.0, description="Porcentaje de ganancia calculado")
    
    # Números adicionales de artículos (múltiples códigos alternativos)
    additional_numbers: Optional[List[str]] = Field(default_factory=list, description="Códigos de barra adicionales")

class ProductUpdateRequest(BaseModel):
    item_number: Optional[str] = Field(None, max_length=50)
    name: str = Field(..., min_length=2, max_length=200)
    category: str = Field(..., min_length=1, max_length=100)
    category_code: Optional[str] = Field(None, description="Código de categoría interna POS")
    department_code: Optional[str] = Field(None, description="Código de departamento POS")
    cost_price: float = Field(..., ge=0.0)
    unit_price: float = Field(..., ge=0.0)
    unit_code: str = Field("UN")
    stock_quantity: float = Field(0.0, ge=0.0)
    description: Optional[str] = None
    profit_percentage: float = Field(30.0)
    additional_numbers: Optional[List[str]] = Field(default_factory=list)
    is_active: bool = True

class ProductDetailResponse(BaseModel):
    id: int
    pos_item_id: Optional[str] = None
    item_number: Optional[str] = None
    name: str
    category: str
    category_code: Optional[str] = None
    department_code: Optional[str] = None
    cost_price: float
    unit_price: float
    formatted_sale_price: Optional[str] = None
    unit_code: str = "UN"
    unit_name: Optional[str] = "unidad (UN)"
    stock_quantity: float = 0.0
    description: Optional[str] = ""
    profit_percentage: float = 30.0
    additional_numbers: List[str] = Field(default_factory=list)
    is_active: bool = True
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

class BarcodeCheckResponse(BaseModel):
    barcode: str
    available: bool
    message: Optional[str] = None

# --- CONTRATO ESTRICTO DE EXTRACCIÓN PERCEPTUAL (0% CÁLCULOS / 100% EXTRACCIÓN) ---
class RawExtractedSupplier(BaseModel):
    name: Optional[str] = Field(None, description="Razón social o nombre comercial del proveedor")
    nit: Optional[str] = Field(None, description="NIT o RUT con o sin dígito de verificación")

class RawExtractedInvoiceMeta(BaseModel):
    number: Optional[str] = Field(None, description="Número o consecutivo de factura")
    date: Optional[str] = Field(None, description="Fecha de expedición o emisión")

class RawExtractedItem(BaseModel):
    code: Optional[str] = Field(None, description="Código de barras, PLU o referencia")
    description: str = Field(..., description="Descripción literal del producto")
    quantity: float = Field(default=1.0, description="Cantidad impresa")
    unit: str = Field(default="Und", description="Unidad impresa (KG, UND, CAJA, BOLSA, etc.)")
    presentation: Optional[str] = Field(None, description="Presentación del empaque si está especificada")
    unit_price: float = Field(default=0.0, description="Precio o valor unitario impreso")
    tax: Optional[str] = Field(None, description="Tasa o código de impuesto impreso (ej: 19%, 5%, EX)")

class RawExtractedInvoice(BaseModel):
    supplier: RawExtractedSupplier = Field(default_factory=RawExtractedSupplier)
    invoice: RawExtractedInvoiceMeta = Field(default_factory=RawExtractedInvoiceMeta)
    items: List[RawExtractedItem] = Field(default_factory=list)

# --- FACTURAS OCR Y MOTOR DE COSTOS SCHEMAS ---
class InvoiceTaxConcept(BaseModel):
    nombre: str = "IVA"
    tasa: float = 0.0
    valor_fijo: float = 0.0
    aplicado: bool = True
    valor_calculado: Optional[float] = 0.0

class InvoiceItemRaw(BaseModel):
    id: int = 1
    codigo: Optional[str] = None
    descripcion: str
    cantidad: float = 1.0
    unidad: str = "Und"
    presentacion: str = "Und"
    unidades_por_presentacion: float = 1.0
    precio_unitario: float = 0.0
    subtotal: float = 0.0
    descuento: float = 0.0
    impuestos: List[InvoiceTaxConcept] = Field(default_factory=list)
    total: float = 0.0
    iva_incluido: bool = False
    confianza: float = 0.95
    advertencias: List[str] = Field(default_factory=list)
    matched_pos_item: Optional[ProductItem] = None
    matched_alias: Optional[bool] = False
    canonical_name: Optional[str] = None

class InvoiceFeedbackItem(BaseModel):
    raw_description: str
    canonical_name: Optional[str] = None
    barcode: Optional[str] = None
    presentation: Optional[str] = "Und"
    units_per_presentation: Optional[float] = 1.0
    margin: Optional[float] = 30.0

class InvoiceLearnRequest(BaseModel):
    provider_name: str
    provider_nit: Optional[str] = None
    orientation: Optional[int] = 0
    layout_type: Optional[str] = "table"
    items: List[InvoiceFeedbackItem] = Field(default_factory=list)

class InvoiceLearnResponse(BaseModel):
    success: bool = True
    message: str
    templates_updated: int = 0
    aliases_updated: int = 0
    stats: Optional[Dict[str, Any]] = None

class InvoiceExtractionResponse(BaseModel):
    success: bool = True
    proveedor: Optional[str] = "Proveedor Desconocido"
    supplier_name: Optional[str] = None
    nit: Optional[str] = None
    numero_factura: Optional[str] = None
    invoice_number: Optional[str] = None
    fecha: Optional[str] = None
    invoice_date: Optional[str] = None
    subtotal: float = 0.0
    total_impuestos: float = 0.0
    descuento: float = 0.0
    total: float = 0.0
    iva_incluido_global: bool = False
    motor_utilizado: str = "RapidOCR"
    provider_used: Optional[str] = None
    confidence_score: Optional[float] = 0.95
    storage_path: Optional[str] = None
    file_url: Optional[str] = None
    advertencias_generales: List[str] = Field(default_factory=list)
    diagnostico_calidad: Optional[Dict[str, Any]] = None
    items: List[InvoiceItemRaw]

class InvoiceCostCalculationItem(BaseModel):
    item_id: int
    costo_original: float
    cantidad: float = 1.0
    presentacion: str = "Und"
    unidades_por_presentacion: float = 1.0
    descuento: float = 0.0
    iva_incluido: bool = False
    conceptos_impuestos: List[InvoiceTaxConcept] = Field(default_factory=list)
    porcentaje_margen: float = 30.0
    precio_venta_manual: Optional[float] = None

class InvoiceCostCalculationRequest(BaseModel):
    items: List[InvoiceCostCalculationItem]
    base_redondeo: Optional[int] = 100

class InvoiceCostCalculationResponse(BaseModel):
    items_calculados: List[Dict[str, Any]]
    resumen: Dict[str, Any]

class InvoiceApplyItem(BaseModel):
    codigo: Optional[str] = None
    nombre: str
    categoria: str = "General"
    unidad_code: str = "UN"
    costo_unitario: float
    precio_venta_final: int
    cantidad_unidades: float = 1.0
    additional_numbers: Optional[List[str]] = Field(default_factory=list)
    pos_item_id: Optional[str] = None
    crear_como_nuevo: bool = False

class InvoiceApplyRequest(BaseModel):
    provider_name: Optional[str] = None
    provider_nit: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    items: List[InvoiceApplyItem]

# --- CATALOGOS 3NF: EMPAQUES Y GASTOS ---
class PackagingTypeResponse(BaseModel):
    id: int
    codigo: str
    nombre: str
    tara_kg: float = 0.0
    capacidad_kg: float = 0.0
    costo_empaque: float = 0.0
    activo: bool = True

class PackagingTypeCreateRequest(BaseModel):
    codigo: str = Field(..., min_length=2, max_length=50)
    nombre: str = Field(..., min_length=2, max_length=100)
    tara_kg: float = Field(0.0, ge=0)
    capacidad_kg: float = Field(0.0, ge=0)
    costo_empaque: float = Field(0.0, ge=0)

class PackagingTypeUpdateRequest(BaseModel):
    nombre: Optional[str] = Field(None, min_length=2, max_length=100)
    tara_kg: Optional[float] = Field(None, ge=0)
    capacidad_kg: Optional[float] = Field(None, ge=0)
    costo_empaque: Optional[float] = Field(None, ge=0)
    activo: Optional[bool] = None

class ExpenseConceptResponse(BaseModel):
    id: int
    codigo: str
    nombre: str
    tipo: str = "variable"
    distribuir_en_costo: bool = True
    activo: bool = True

class ExpenseConceptCreateRequest(BaseModel):
    codigo: str = Field(..., min_length=2, max_length=50)
    nombre: str = Field(..., min_length=2, max_length=100)
    tipo: str = Field("variable", description="fijo o variable")
    distribuir_en_costo: bool = True

class ExpenseConceptUpdateRequest(BaseModel):
    nombre: Optional[str] = Field(None, min_length=2, max_length=100)
    tipo: Optional[str] = None
    distribuir_en_costo: Optional[bool] = None
    activo: Optional[bool] = None

# --- EMPRESA & MULTI-SUCURSAL (3NF) ---
class EmpresaBase(BaseModel):
    nombre: str
    nit: str
    direccion: Optional[str] = None
    telefono: Optional[str] = None
    email: Optional[str] = None
    logo_path: Optional[str] = None
    campos_extra: Optional[List[Any]] = []

class EmpresaCreate(EmpresaBase):
    pass

class EmpresaUpdate(BaseModel):
    nombre: Optional[str] = None
    nit: Optional[str] = None
    direccion: Optional[str] = None
    telefono: Optional[str] = None
    email: Optional[str] = None
    logo_path: Optional[str] = None
    campos_extra: Optional[List[Any]] = None

class EmpresaResponse(EmpresaBase):
    id: int
    activo: bool = True
    creado_en: Optional[datetime] = None
    actualizado_en: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)

class EmpresaStatusResponse(BaseModel):
    configurada: bool
    empresa: Optional[EmpresaResponse] = None
    total_empresas: int = 0
    empresas: List[EmpresaResponse] = []

# --- AUDITORÍA GENERAL DEL SISTEMA (3NF) ---
class AuditoriaCambioItem(BaseModel):
    campo: str
    valor_anterior: Optional[str] = None
    valor_nuevo: Optional[str] = None

class AuditoriaResponse(BaseModel):
    id: int
    usuario_id: Optional[int] = None
    usuario_nombre: Optional[str] = None
    usuario_rol: Optional[str] = None
    accion: str
    modulo: str
    entidad: Optional[str] = None
    entidad_id: Optional[str] = None
    detalles: Optional[Dict[str, Any]] = None
    ip_address: Optional[str] = None
    creado_en: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)

class AuditoriaListResponse(BaseModel):
    total: int
    items: List[AuditoriaResponse]

# --- ROLES & PERMISOS RBAC ---
class PermisoResponse(BaseModel):
    id: int
    codigo: str
    nombre: str
    modulo: Optional[str] = None
    descripcion: Optional[str] = None
    activo: bool = True
    model_config = ConfigDict(from_attributes=True)

class RoleBase(BaseModel):
    codigo: str
    nombre: str
    descripcion: Optional[str] = None
    activo: bool = True

class RoleCreate(RoleBase):
    permiso_ids: Optional[List[int]] = []

class RoleUpdate(BaseModel):
    nombre: Optional[str] = None
    descripcion: Optional[str] = None
    activo: Optional[bool] = None
    permiso_ids: Optional[List[int]] = None

class RoleResponse(RoleBase):
    id: int
    permisos: List[PermisoResponse] = []
    creado_en: Optional[datetime] = None
    actualizado_en: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


