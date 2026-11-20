import json
from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.orm import relationship
from app.models.base import Base, utc_now

class Usuario(Base):
    """Modelo de Usuario del sistema con soporte para roles y permisos granulares."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    full_name = Column(String(100), nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(30), default="cajero", nullable=False)
    permissions = Column(String(1000), default="{}", nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(String(50), nullable=False)
    last_login = Column(String(50), nullable=True)

    sesiones = relationship("Sesion", back_populates="usuario", cascade="all, delete-orphan")

    @property
    def activo(self) -> bool:
        return bool(self.is_active)

    @property
    def rol(self) -> str:
        return self.role

    @property
    def permisos_dict(self) -> dict:
        if isinstance(self.permissions, str):
            try:
                return json.loads(self.permissions)
            except Exception:
                return {}
        return self.permissions or {}

    def tiene_permiso(self, perm_key: str) -> bool:
        if self.role == "admin":
            return True
        return bool(self.permisos_dict.get(perm_key, False))


User = Usuario

