from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime
from app.models.base import Base, utc_now

class UnidadMedida(Base):
    """Catálogo maestro de unidades de medida estandarizadas."""
    __tablename__ = "units_of_measure"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(20), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    magnitude_type = Column(String(50), default="PESO", nullable=False)
    conversion_factor_kg = Column(Float, default=1.0, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(String(50), nullable=False)

    @property
    def activo(self) -> bool:
        return bool(self.is_active)


class TipoEmpaque(Base):
    """Catálogo maestro de tipos de presentación / empaques con tara y costos."""
    __tablename__ = "tipos_empaque"

    id = Column(Integer, primary_key=True, index=True)
    codigo = Column(String(50), unique=True, nullable=False, index=True)
    nombre = Column(String(100), nullable=False)
    tara_kg = Column(Float, default=0.0, nullable=False)
    capacidad_kg = Column(Float, default=0.0, nullable=False)
    costo_empaque = Column(Float, default=0.0, nullable=False)
    activo = Column(Boolean, default=True, nullable=False)
    creado_en = Column(DateTime, default=utc_now, nullable=False)


class ConceptoGasto(Base):
    """Catálogo maestro de conceptos de gasto operativo (fletes, empaques, cuadrilla)."""
    __tablename__ = "conceptos_gasto"

    id = Column(Integer, primary_key=True, index=True)
    codigo = Column(String(50), unique=True, nullable=False, index=True)
    nombre = Column(String(100), nullable=False)
    tipo = Column(String(50), default="fijo", nullable=False)
    distribuir_en_costo = Column(Boolean, default=True, nullable=False)
    activo = Column(Boolean, default=True, nullable=False)
    creado_en = Column(DateTime, default=utc_now, nullable=False)

