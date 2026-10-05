from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import relationship
from app.models.base import Base, utc_now


class Empresa(Base):
    """
    Entidad empresarial multi-sucursal / multi-empresa (3NF).
    Configuraciones de negocio, NIT, datos fiscales y márgenes por defecto.
    """
    __tablename__ = "empresas"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(255), nullable=False)
    nit = Column(String(100), nullable=False, index=True)
    direccion = Column(Text, nullable=True)
    telefono = Column(String(100), nullable=True)
    email = Column(String(150), nullable=True)
    logo_path = Column(String(500), nullable=True)
    activo = Column(Boolean, default=True, nullable=False, index=True)
    creado_en = Column(DateTime, default=utc_now, nullable=False)
    actualizado_en = Column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)

    atributos = relationship("EmpresaAtributo", back_populates="empresa", cascade="all, delete-orphan", lazy="selectin")

    __table_args__ = (
        UniqueConstraint("nit", name="uq_empresas_nit"),
    )

    @property
    def campos_extra(self) -> List[Dict[str, Any]]:
        return [{"id": str(a.id), "clave": a.clave, "valor": a.valor} for a in (self.atributos or [])]

    @campos_extra.setter
    def campos_extra(self, val):
        pass


class EmpresaAtributo(Base):
    """
    Atributos dinámicos relacionales para la empresa (3NF estricta).
    Reemplaza campos_extra JSON asegurando atomicidad y consultas estructuradas.
    """
    __tablename__ = "empresa_atributos"

    id = Column(Integer, primary_key=True, index=True)
    empresa_id = Column(Integer, ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False, index=True)
    clave = Column(String(100), nullable=False, index=True)
    valor = Column(Text, nullable=False)
    creado_en = Column(DateTime, default=utc_now, nullable=False)

    empresa = relationship("Empresa", back_populates="atributos")

    __table_args__ = (
        UniqueConstraint("empresa_id", "clave", name="uq_empresa_atributos_empresa_clave"),
        Index("ix_empresa_atributos_empresa_clave", "empresa_id", "clave"),
    )

