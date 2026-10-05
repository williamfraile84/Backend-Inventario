from sqlalchemy import Column, Integer, String, Float, Boolean, ForeignKey, Index
from sqlalchemy.orm import relationship
from app.models.base import Base

class ProductAdditionalNumber(Base):
    """Códigos de barra adicionales normalizados para un producto."""
    __tablename__ = "product_additional_numbers"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    item_number = Column(String(100), unique=True, nullable=False, index=True)
    created_at = Column(String(50), nullable=False)

    product = relationship("Producto", back_populates="additional_numbers_rel")


class Producto(Base):
    """Catálogo maestro de productos e inventario Fruver integrado con POS."""
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    pos_item_id = Column(String(100), nullable=True)
    item_number = Column(String(100), nullable=True, index=True)
    name = Column(String(200), nullable=False, index=True)
    category = Column(String(100), nullable=False, index=True)
    category_code = Column(String(50), nullable=True, index=True)
    department_code = Column(String(50), nullable=True, index=True)
    cost_price = Column(Float, default=0.0, nullable=False)
    unit_price = Column(Float, default=0.0, nullable=False)
    unit_code = Column(String(20), default="UN", nullable=False)
    stock_quantity = Column(Float, default=0.0, nullable=False)
    description = Column(String(500), nullable=True)
    profit_percentage = Column(Float, default=30.0, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    created_at = Column(String(50), nullable=False)
    updated_at = Column(String(50), nullable=False)

    additional_numbers_rel = relationship(
        "ProductAdditionalNumber",
        back_populates="product",
        cascade="all, delete-orphan"
    )

    @property
    def additional_numbers(self):
        return [an.item_number for an in (self.additional_numbers_rel or [])]

    @property
    def formatted_sale_price(self) -> str:
        return f"${self.unit_price:,.0f}".replace(",", ".")

    @property
    def formatted_cost_price(self) -> str:
        return f"${self.cost_price:,.0f}".replace(",", ".")

