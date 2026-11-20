import uuid
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from app.core.security import create_access_token, verify_password, get_current_user
from app.core.database import get_user_by_username, update_last_login, SessionLocal
from app.models.schemas import LoginRequest, TokenResponse, UserResponse, UserPermissions

logger = logging.getLogger("AuthRouter")
router = APIRouter(prefix="/auth", tags=["Autenticación"])

def build_user_response(user: Any) -> UserResponse:
    if isinstance(user, dict):
        perms = user.get("permissions", {})
        if isinstance(perms, str):
            try:
                perms = json.loads(perms)
            except Exception:
                perms = {}
        perms_obj = UserPermissions(**perms) if isinstance(perms, dict) else UserPermissions()
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
    else:
        perms = getattr(user, "permisos_dict", {}) or getattr(user, "permissions", {})
        if isinstance(perms, str):
            try:
                perms = json.loads(perms)
            except Exception:
                perms = {}
        perms_obj = UserPermissions(**perms) if isinstance(perms, dict) else UserPermissions()
        return UserResponse(
            id=getattr(user, "id", None),
            username=getattr(user, "username", ""),
            full_name=getattr(user, "full_name", ""),
            role=getattr(user, "role", "cajero"),
            permissions=perms_obj,
            is_active=bool(getattr(user, "is_active", True)),
            created_at=getattr(user, "created_at", None),
            last_login=getattr(user, "last_login", None)
        )

def _register_session(user_id: int, jti: str, request: Optional[Request] = None):
    try:
        with SessionLocal() as db:
            from app.models.sesion import Sesion
            client_ip = request.client.host if request and request.client else None
            user_agent = request.headers.get("user-agent") if request else None
            now_dt = datetime.now(timezone.utc)
            db_sesion = Sesion(
                usuario_id=user_id,
                token_jti=jti,
                ip_origen=client_ip,
                user_agent=user_agent,
                creada_en=now_dt,
                ultimo_acceso=now_dt,
                activa=True
            )
            db.add(db_sesion)
            db.commit()
    except Exception as ses_err:
        logger.warning(f"No se pudo registrar la sesión en BD: {ses_err}")

@router.post("/login", response_model=TokenResponse)
async def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends()):
    """Autentica un usuario con Form Data y genera un token JWT."""
    user = get_user_by_username(form_data.username)
    if not user or not verify_password(form_data.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nombre de usuario o contraseña incorrectos.",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    if not user.get("is_active", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La cuenta de usuario se encuentra suspendida.",
        )
    
    update_last_login(user["id"])
    jti = uuid.uuid4().hex
    access_token = create_access_token(data={"sub": user["username"], "jti": jti})
    _register_session(user["id"], jti, request)
    
    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        username=user["username"],
        user=build_user_response(user)
    )

@router.post("/login-json", response_model=TokenResponse)
async def login_json(payload: LoginRequest, request: Request):
    """Autenticación con cuerpo JSON para aplicaciones SPA modernas."""
    user = get_user_by_username(payload.username)
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nombre de usuario o contraseña incorrectos.",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    if not user.get("is_active", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La cuenta de usuario se encuentra suspendida.",
        )
        
    update_last_login(user["id"])
    jti = uuid.uuid4().hex
    access_token = create_access_token(data={"sub": user["username"], "jti": jti})
    _register_session(user["id"], jti, request)
    
    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        username=user["username"],
        user=build_user_response(user)
    )

@router.get("/me", response_model=UserResponse)
async def get_me(current_user: Dict[str, Any] = Depends(get_current_user)):
    """Retorna la información completa y permisos del usuario autenticado."""
    return build_user_response(current_user)

@router.post("/logout")
async def logout(request: Request, current_user: Dict[str, Any] = Depends(get_current_user)):
    """Cierra la sesión actual invalidando el token JTI en la base de datos."""
    token_jti = getattr(request.state, "token_jti", None) if hasattr(request, "state") else None
    if token_jti:
        try:
            with SessionLocal() as db:
                from app.models.sesion import Sesion
                sesion = db.query(Sesion).filter(Sesion.token_jti == token_jti).first()
                if sesion:
                    sesion.activa = False
                    db.commit()
        except Exception as e:
            logger.warning(f"Error al revocar sesión: {e}")
    return {"message": "Sesión finalizada exitosamente."}
