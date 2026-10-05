from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from app.core.security import require_permission, get_current_user
from app.core.database import DEFAULT_PERMISSIONS
from app.models.schemas import (
    RoleResponse,
    RoleCreate,
    RoleUpdate,
    PermisoResponse
)

router = APIRouter(prefix="/roles", tags=["Roles y Permisos RBAC"])

SYSTEM_PERMISSIONS = [
    {"id": 1, "codigo": "can_lookup", "nombre": "Consultar Catálogo", "modulo": "CATALOGO", "descripcion": "Búsqueda y consulta de productos y precios"},
    {"id": 2, "codigo": "can_edit_single", "nombre": "Editar Precio Individual", "modulo": "PRECIOS", "descripcion": "Modificación rápida de precio y alta de unidades"},
    {"id": 3, "codigo": "can_edit_bulk", "nombre": "Edición Masiva de Precios", "modulo": "PRECIOS", "descripcion": "Ajuste masivo por porcentaje o valor fijo"},
    {"id": 4, "codigo": "can_manage_users", "nombre": "Gestión de Usuarios", "modulo": "SEGURIDAD", "descripcion": "Creación, edición y revocación de usuarios"},
    {"id": 5, "codigo": "can_view_audit", "nombre": "Visualizar Auditoría", "modulo": "AUDITORIA", "descripcion": "Consulta y exportación de bitácoras inmutables"},
    {"id": 6, "codigo": "can_process_invoices", "nombre": "Procesamiento de Facturas", "modulo": "FACTURAS", "descripcion": "OCR de facturas y costeo dinámico"},
    {"id": 7, "codigo": "can_sync_pos", "nombre": "Sincronizar POS", "modulo": "POS", "descripcion": "Conexión directa y sincronización con POS externo"},
]

ROLE_METADATA = {
    "admin": {"id": 1, "nombre": "Administrador del Sistema", "descripcion": "Control total y configuración global de la plataforma."},
    "supervisor": {"id": 2, "nombre": "Supervisor Operativo", "descripcion": "Gestión operativa, precios, auditoría y facturas de compra."},
    "cajero": {"id": 3, "nombre": "Cajero / Operador", "descripcion": "Consulta ágil de catálogo y lectura de códigos de barra."},
}


@router.get("", response_model=List[RoleResponse])
def listar_roles(
    activo_only: bool = Query(True, description="Filtrar solo roles activos"),
    current_user: Dict[str, Any] = Depends(require_permission("can_lookup")),
):
    """Lista todos los roles configurados en el sistema con su matriz de permisos."""
    roles = []
    for code, meta in ROLE_METADATA.items():
        perms_map = DEFAULT_PERMISSIONS.get(code, {})
        assigned_perms = [
            PermisoResponse(**p, activo=True)
            for p in SYSTEM_PERMISSIONS
            if perms_map.get(p["codigo"], False)
        ]
        roles.append(
            RoleResponse(
                id=meta["id"],
                codigo=code,
                nombre=meta["nombre"],
                descripcion=meta["descripcion"],
                activo=True,
                permisos=assigned_perms
            )
        )
    return roles


@router.get("/permisos", response_model=List[PermisoResponse])
def listar_permisos(
    modulo: Optional[str] = Query(None, description="Filtrar permisos por módulo"),
    current_user: Dict[str, Any] = Depends(require_permission("can_lookup")),
):
    """Retorna el catálogo maestro de permisos granulares del sistema."""
    perms = SYSTEM_PERMISSIONS
    if modulo:
        perms = [p for p in perms if p["modulo"].upper() == modulo.strip().upper()]
    return [PermisoResponse(**p, activo=True) for p in perms]


@router.get("/{role_id}", response_model=RoleResponse)
def obtener_rol(
    role_id: int,
    current_user: Dict[str, Any] = Depends(require_permission("can_lookup")),
):
    """Obtiene el detalle y matriz de permisos de un rol específico."""
    target = next((code for code, meta in ROLE_METADATA.items() if meta["id"] == role_id), None)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rol no encontrado.")

    meta = ROLE_METADATA[target]
    perms_map = DEFAULT_PERMISSIONS.get(target, {})
    assigned_perms = [
        PermisoResponse(**p, activo=True)
        for p in SYSTEM_PERMISSIONS
        if perms_map.get(p["codigo"], False)
    ]
    return RoleResponse(
        id=meta["id"],
        codigo=target,
        nombre=meta["nombre"],
        descripcion=meta["descripcion"],
        activo=True,
        permisos=assigned_perms
    )


@router.post("", response_model=RoleResponse, status_code=status.HTTP_201_CREATED)
def crear_rol(
    payload: RoleCreate,
    current_user: Dict[str, Any] = Depends(require_permission("can_manage_users")),
):
    """Permite registrar un rol personalizado en la plataforma."""
    new_id = max((m["id"] for m in ROLE_METADATA.values()), default=0) + 1
    code = payload.codigo.strip().lower()
    ROLE_METADATA[code] = {
        "id": new_id,
        "nombre": payload.nombre.strip(),
        "descripcion": payload.descripcion or ""
    }
    assigned_codes = {p["codigo"] for p in SYSTEM_PERMISSIONS if p["id"] in (payload.permiso_ids or [])}
    DEFAULT_PERMISSIONS[code] = {p["codigo"]: (p["codigo"] in assigned_codes) for p in SYSTEM_PERMISSIONS}

    assigned_perms = [
        PermisoResponse(**p, activo=True)
        for p in SYSTEM_PERMISSIONS
        if p["codigo"] in assigned_codes
    ]
    return RoleResponse(
        id=new_id,
        codigo=code,
        nombre=payload.nombre.strip(),
        descripcion=payload.descripcion,
        activo=True,
        permisos=assigned_perms
    )

