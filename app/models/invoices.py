import json
from sqlalchemy import Column, Integer, String, Float, Text, ForeignKey, Index
from sqlalchemy.orm import relationship
from app.models.base import Base

class Invoice(Base):
    """Factura procesada con desglose contable y custodia documental."""
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)
    provider_name = Column(String(200), nullable=True)
    provider_nit = Column(String(50), nullable=True, index=True)
    invoice_number = Column(String(100), nullable=True, index=True)
    invoice_date = Column(String(50), nullable=True)
    subtotal = Column(Float, default=0.0, nullable=False)
    tax_total = Column(Float, default=0.0, nullable=False)
    discount_total = Column(Float, default=0.0, nullable=False)
    total = Column(Float, default=0.0, nullable=False)
    status = Column(String(50), default="procesada", nullable=False)
    raw_data_json = Column(Text, nullable=True)
    created_at = Column(String(50), nullable=False)

    items = relationship("InvoiceItem", back_populates="invoice", cascade="all, delete-orphan")


class InvoiceItem(Base):
    """Línea de ítem extraída de la factura."""
    __tablename__ = "invoice_items"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    product_code = Column(String(100), nullable=True)
    description = Column(String(255), nullable=False)
    quantity = Column(Float, default=1.0, nullable=False)
    presentation = Column(String(50), default="Und", nullable=False)
    units_per_presentation = Column(Float, default=1.0, nullable=False)
    unit_cost_base = Column(Float, default=0.0, nullable=False)
    tax_concepts_json = Column(Text, default="[]", nullable=False)
    unit_cost_net = Column(Float, default=0.0, nullable=False)
    margin_percent = Column(Float, default=30.0, nullable=False)
    sale_price_calculated = Column(Float, default=0.0, nullable=False)
    sale_price_final = Column(Integer, default=0, nullable=False)
    matched_product_id = Column(Integer, nullable=True)

    invoice = relationship("Invoice", back_populates="items")


class InvoiceSupplierTemplate(Base):
    """Memoria de plantillas por proveedor para aprendizaje continuo."""
    __tablename__ = "invoice_supplier_templates"

    id = Column(Integer, primary_key=True, index=True)
    provider_nit = Column(String(50), nullable=True, index=True)
    provider_name_pattern = Column(String(200), unique=True, nullable=False, index=True)
    layout_type = Column(String(50), default="table", nullable=False)
    default_orientation = Column(Integer, default=0, nullable=False)
    column_mapping_json = Column(Text, nullable=True)
    default_taxes_json = Column(Text, nullable=True)
    times_used = Column(Integer, default=1, nullable=False)
    confidence_score = Column(Float, default=0.95, nullable=False)
    created_at = Column(String(50), nullable=False)
    updated_at = Column(String(50), nullable=False)


class InvoiceProductAlias(Base):
    """Diccionario adaptativo de alias y equivalencias de productos."""
    __tablename__ = "invoice_product_aliases"

    id = Column(Integer, primary_key=True, index=True)
    raw_ocr_pattern = Column(String(255), unique=True, nullable=False, index=True)
    canonical_name = Column(String(200), nullable=False)
    barcode = Column(String(100), nullable=True, index=True)
    product_id = Column(Integer, nullable=True)
    default_presentation = Column(String(50), default="Und", nullable=False)
    default_units_per_pres = Column(Float, default=1.0, nullable=False)
    default_margin = Column(Float, default=30.0, nullable=False)
    times_seen = Column(Integer, default=1, nullable=False)
    created_at = Column(String(50), nullable=False)
    updated_at = Column(String(50), nullable=False)

