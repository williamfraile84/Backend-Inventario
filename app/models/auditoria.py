from typing import Optional
from sqlalchemy import Column, Integer, String, Float, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.models.base import Base, utc_now

class PriceAuditLog(Base):
    """Auditoría histórica de cambios de precios (retrocompatible con el esquema existente)."""
    __tablename__ = "price_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=True)
    username = Column(String(50), nullable=False)
    action_type = Column(String(50), nullable=False)
    barcode = Column(String(100), nullable=False, index=True)
    item_name = Column(String(200), nullable=True)
    old_price = Column(Float, nullable=True)
    new_price = Column(Float, nullable=False)
    formatted_new_price = Column(String(50), nullable=False)
    updated_count = Column(Integer, default=1, nullable=False)
    timestamp = Column(String(50), nullable=False)
    details = Column(Text, nullable=True)


class Auditoria(Base):
    """Bitácora empresarial inmutable de auditoría para todas las acciones del sistema."""
    __tablename__ = "auditorias"

    id = Column(Integer, primary_key=True, index=True)
    usuario_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    username = Column(String(100), nullable=False)
    modulo = Column(String(50), nullable=False, index=True)
    accion = Column(String(50), nullable=False, index=True)
    entidad = Column(String(50), nullable=True, index=True)
    entidad_id = Column(Integer, nullable=True, index=True)
    ip_origen = Column(String(45), nullable=True)
    user_agent = Column(String(255), nullable=True)
    detalles = Column(Text, nullable=True)
    creado_en = Column(DateTime, default=utc_now, nullable=False, index=True)

    cambios = relationship("AuditoriaCambio", back_populates="auditoria", cascade="all, delete-orphan")

    @property
    def usuario_nombre(self) -> str:
        return self.username

    @property
    def ip_address(self) -> Optional[str]:
        return self.ip_origen

    @property
    def usuario_rol(self) -> str:
        return "admin" if self.username == "admin" else "operador"


class AuditoriaCambio(Base):
    """Detalle de diferencias JSON campo por campo (antes / después)."""
    __tablename__ = "auditoria_cambios"

    id = Column(Integer, primary_key=True, index=True)
    auditoria_id = Column(Integer, ForeignKey("auditorias.id", ondelete="CASCADE"), nullable=False, index=True)
    campo = Column(String(100), nullable=False)
    valor_anterior = Column(Text, nullable=True)
    valor_nuevo = Column(Text, nullable=True)

    auditoria = relationship("Auditoria", back_populates="cambios")

