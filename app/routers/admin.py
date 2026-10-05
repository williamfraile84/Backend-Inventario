from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from app.core.security import require_admin, require_permission, get_current_user
from app.core.database import (
    get_all_users,
    get_user_by_id,
    get_user_by_username,
    create_user,
    update_user,
    delete_user,
    get_audit_logs,
    DEFAULT_PERMISSIONS
)
from app.models.schemas import (
    UserResponse,
    UserCreateRequest,
    UserUpdateRequest,
    PasswordResetRequest,
    AuditLogResponse,
    UserPermissions
)

router = APIRouter(prefix="/admin", tags=["Administración del Sistema"])

def format_user_response(user: Dict[str, Any]) -> UserResponse:
    perms = user.get("permissions", {})
    if isinstance(perms, dict):
        perms_obj = UserPermissions(**perms)
    else:
        perms_obj = UserPermissions()
        
    return UserResponse(
        id=user.get("id"),
        username=user.get("username"),
        full_name=user.get("full_name", ""),
        role=user.get("role", "cajero"),
        permissions=perms_obj,
        is_active=bool(user.get("is_active", True)),
        created_at=user.get("created_at"),
        last_login=user.get("last_login")
    )

# --- USUARIOS ---

@router.get("/users", response_model=List[UserResponse])
async def list_users(current_user: Dict[str, Any] = Depends(require_permission("can_manage_users"))):
    """Lista todos los usuarios registrados en el sistema."""
    users = get_all_users()
    return [format_user_response(u) for u in users]

@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_new_user(
    payload: UserCreateRequest,
    current_user: Dict[str, Any] = Depends(require_permission("can_manage_users"))
):
    """Crea un nuevo usuario con rol y permisos configurados."""
    existing = get_user_by_username(payload.username)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"El nombre de usuario '{payload.username}' ya se encuentra registrado."
        )
        
    perms_dict = payload.permissions.model_dump() if payload.permissions else DEFAULT_PERMISSIONS.get(payload.role, DEFAULT_PERMISSIONS["cajero"])
    
    user = create_user(
        username=payload.username.strip(),
        full_name=payload.full_name.strip(),
        password=payload.password,
        role=payload.role,
        permissions=perms_dict
    )
    return format_user_response(user)

@router.put("/users/{user_id}", response_model=UserResponse)
async def update_existing_user(
    user_id: int,
    payload: UserUpdateRequest,
    current_user: Dict[str, Any] = Depends(require_permission("can_manage_users"))
):
    """Actualiza datos, rol, permisos o estado de activación de un usuario."""
    target_user = get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario no encontrado."
        )
        
    # Impedir que un admin se desactive a sí mismo o cambie su propio rol a no-admin
    if target_user["username"] == "admin" and payload.is_active is False:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No se puede desactivar la cuenta del administrador principal."
        )
        
    perms_dict = payload.permissions.model_dump() if payload.permissions else None
    
    updated = update_user(
        user_id=user_id,
        full_name=payload.full_name,
        role=payload.role,
        permissions=perms_dict,
        is_active=payload.is_active,
        password=payload.password
    )
    return format_user_response(updated)

@router.delete("/users/{user_id}")
async def delete_existing_user(
    user_id: int,
    current_user: Dict[str, Any] = Depends(require_permission("can_manage_users"))
):
    """Elimina un usuario del sistema."""
    target_user = get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario no encontrado."
        )
        
    if target_user["username"] == "admin" or target_user["id"] == current_user.get("id"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No es posible eliminar el administrador principal ni tu propia cuenta activa."
        )
        
    deleted = delete_user(user_id)
    return {"success": deleted, "message": f"Usuario '{target_user['username']}' eliminado exitosamente."}

@router.post("/users/{user_id}/reset-password")
async def reset_user_password(
    user_id: int,
    payload: PasswordResetRequest,
    current_user: Dict[str, Any] = Depends(require_permission("can_manage_users"))
):
    """Restablece la contraseña de acceso de un usuario."""
    target_user = get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario no encontrado."
        )
        
    update_user(user_id=user_id, password=payload.new_password)
    return {"success": True, "message": f"Contraseña actualizada con éxito para '{target_user['username']}'."}

# --- AUDITORÍA DE PRECIOS ---

@router.get("/audit-logs", response_model=List[AuditLogResponse])
async def list_audit_logs(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: Dict[str, Any] = Depends(require_permission("can_view_audit"))
):
    """Obtiene el historial de modificaciones de precios realizadas en el sistema."""
    logs = get_audit_logs(limit=limit, offset=offset)
    return [AuditLogResponse(**l) for l in logs]

