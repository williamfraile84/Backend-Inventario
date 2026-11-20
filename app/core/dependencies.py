from typing import Optional, Callable, Dict, Any
from datetime import datetime, timezone, timedelta
from fastapi import Depends, HTTPException, status, Header, Request, Query
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.security import decode_access_token

class CurrentUser(dict):
    """Diccionario que representa al usuario autenticado y permite acceso transparente por atributo y clave."""
    def __init__(self, data: dict, user_obj: Optional[Any] = None):
        super().__init__(data)
        self._user_obj = user_obj

    @property
    def id(self) -> int:
        return self.get("id")

    @property
    def username(self) -> str:
        return self.get("username", "")

    @property
    def full_name(self) -> str:
        return self.get("full_name", "")

    @property
    def role(self) -> str:
        return self.get("role", "cajero")

    @property
    def rol(self) -> str:
        return self.get("role", "cajero")

    @property
    def activo(self) -> bool:
        return bool(self.get("is_active", True))

    @property
    def is_active(self) -> bool:
        return bool(self.get("is_active", True))

    @property
    def permissions(self) -> dict:
        return self.get("permissions", {})

    @property
    def permisos_dict(self) -> dict:
        return self.get("permissions", {})

    def tiene_permiso(self, perm_key: str) -> bool:
        if self.role == "admin":
            return True
        return bool(self.permissions.get(perm_key, False))

    def __getattr__(self, name):
        if name in self:
            return self[name]
        if name == "rol":
            return self.get("role", "cajero")
        if name == "activo":
            return bool(self.get("is_active", True))
        if name == "permisos_dict":
            return self.get("permissions", {})
        raise AttributeError(f"'CurrentUser' object has no attribute '{name}'")


def get_db():
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(None),
    token_query: Optional[str] = Query(None, alias="token"),
) -> CurrentUser:
    raw_token = None
    if authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            raw_token = parts[1]
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Formato de token de autorización inválido. Use 'Bearer <token>'.",
                headers={"WWW-Authenticate": "Bearer"},
            )
    elif token_query:
        raw_token = token_query.strip()

    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticación requerida. Inicie sesión para continuar.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(raw_token)
    if not payload or "sub" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de acceso inválido o expirado. Inicie sesión nuevamente.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_sub = payload["sub"]
    token_jti = payload.get("jti")
    request.state.token_jti = token_jti
    request.state.raw_token = raw_token

    # Validar sesión activa en la base de datos si la opción está activa
    from app.models.sesion import Sesion
    from app.models.usuario import Usuario

    if settings.ENFORCE_DB_SESSION_VALIDATION and token_jti:
        sesion = db.query(Sesion).filter(Sesion.token_jti == token_jti, Sesion.activa == True).first()
        if not sesion:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Sesión cerrada, revocada o no encontrada. Inicie sesión nuevamente.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if settings.SESSION_INACTIVITY_TIMEOUT_MINUTES and sesion.ultimo_acceso:
            ult = sesion.ultimo_acceso
            if isinstance(ult, str):
                try:
                    ult = datetime.fromisoformat(ult)
                except Exception:
                    ult = None
            if ult:
                if ult.tzinfo is None:
                    ult = ult.replace(tzinfo=timezone.utc)
                limit = ult + timedelta(minutes=settings.SESSION_INACTIVITY_TIMEOUT_MINUTES)
                if datetime.now(timezone.utc) > limit:
                    sesion.activa = False
                    db.commit()
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Sesión expirada por inactividad. Inicie sesión nuevamente.",
                        headers={"WWW-Authenticate": "Bearer"},
                    )

        # Actualizar último acceso con throttling (> 60s)
        now_utc = datetime.now(timezone.utc)
        sesion.ultimo_acceso = now_utc
        db.commit()

    # Buscar usuario por ID numérico o username
    user = None
    if str(user_sub).isdigit():
        user = db.query(Usuario).filter(Usuario.id == int(user_sub)).first()
    if not user:
        user = db.query(Usuario).filter(Usuario.username == str(user_sub)).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="El usuario asociado a la sesión ya no existe.",
        )

    if not user.activo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Su cuenta ha sido desactivada. Contacte al administrador.",
        )

    user_dict = {
        "id": user.id,
        "username": user.username,
        "full_name": user.full_name,
        "password_hash": user.password_hash,
        "role": user.role,
        "permissions": user.permisos_dict,
        "is_active": user.activo,
        "created_at": user.created_at,
        "last_login": user.last_login
    }
    return CurrentUser(user_dict, user_obj=user)


def get_current_admin(current_user: Dict[str, Any] = Depends(get_current_user)):
    role = current_user.get("role") or getattr(current_user, "role", "") or getattr(current_user, "rol", "")
    if role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso restringido: Se requieren privilegios de Administrador para esta acción.",
        )
    return current_user

require_admin = get_current_admin


def require_permission(permission_key: str) -> Callable:
    def checker(current_user: Dict[str, Any] = Depends(get_current_user)):
        role = current_user.get("role") or getattr(current_user, "role", "") or getattr(current_user, "rol", "")
        if role == "admin":
            return current_user
        perms = current_user.get("permissions") or getattr(current_user, "permissions", {})
        if isinstance(perms, str):
            import json
            try:
                perms = json.loads(perms)
            except Exception:
                perms = {}
        if not perms.get(permission_key, False):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permiso denegado: No cuenta con el permiso requerido ({permission_key}).",
            )
        return current_user
    return checker
