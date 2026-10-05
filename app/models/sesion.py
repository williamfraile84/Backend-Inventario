from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship
from app.models.base import Base, utc_now

class Sesion(Base):
    """Registro y seguimiento de sesiones activas en BD con identificador JTI."""
    __tablename__ = "sesiones"

    id = Column(Integer, primary_key=True, index=True)
    usuario_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_jti = Column(String(64), unique=True, nullable=False, index=True)
    ip_origen = Column(String(45), nullable=True)
    user_agent = Column(String(255), nullable=True)
    creada_en = Column(DateTime, default=utc_now, nullable=False)
    ultimo_acceso = Column(DateTime, default=utc_now, nullable=False)
    activa = Column(Boolean, default=True, nullable=False, index=True)

    usuario = relationship("Usuario", back_populates="sesiones")

