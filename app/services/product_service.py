import logging
from typing import Dict, Any, List, Optional, Tuple
from app.core import database
from app.services.pos_service import POSService, format_cop_currency

logger = logging.getLogger("ProductService")

class ProductService:
    _instance: Optional['ProductService'] = None

    @classmethod
    def get_instance(cls) -> 'ProductService':
        if cls._instance is None:
            cls._instance = ProductService()
        return cls._instance

    def __init__(self):
        self.pos_service = POSService.get_instance()

    def check_barcode(self, barcode: str, exclude_product_id: Optional[int] = None) -> Tuple[bool, Optional[str]]:
        return database.is_barcode_available(barcode, exclude_product_id)

    async def create_product(self, payload_dict: Dict[str, Any]) -> Dict[str, Any]:
        """
        Crea un producto en inventario_fruver con validaciones estrictas de backend.
        1. Campos obligatorios: name, category, cost_price, unit_price.
        2. Campo opcional: item_number (UPC/EAN/ISBN).
        3. Validación de unicidad de códigos de barras.
        4. Inserción local normalizada (productos + códigos adicionales).
        5. Sincronización transparente con el sistema POS externo (csopos.co).
        """
        name = str(payload_dict.get("name", "")).strip()
        category = str(payload_dict.get("category", "")).strip()
        cost_price = float(payload_dict.get("cost_price", 0.0) or 0.0)
        unit_price = float(payload_dict.get("unit_price", 0.0) or 0.0)
        item_number = str(payload_dict.get("item_number", "")).strip() if payload_dict.get("item_number") else None
        unit_code = str(payload_dict.get("unit_code", "UN")).strip().upper()
        stock_quantity = float(payload_dict.get("stock_quantity", 0.0) or 0.0)
        description = payload_dict.get("description")
        profit_percentage = float(payload_dict.get("profit_percentage", 30.0) or 30.0)
        additional_numbers = payload_dict.get("additional_numbers") or []

        # 1. Validaciones en Backend
        if not name:
            raise ValueError("El campo 'Nombre' es estrictamente obligatorio.")
        if not category:
            raise ValueError("El campo 'Categoría' es estrictamente obligatorio.")
        if cost_price < 0:
            raise ValueError("El 'Costo (Sin Impuesto)' no puede ser negativo.")
        if unit_price < 0:
            raise ValueError("El 'Precio de venta (Sin Impuesto)' no puede ser negativo.")

        # 2. Validar colisiones de código de barras principal
        if item_number:
            is_avail, conflict_msg = self.check_barcode(item_number)
            if not is_avail:
                raise ValueError(conflict_msg)

        # 3. Validar colisiones en códigos adicionales
        clean_additionals = []
        seen_additionals = set()
        if item_number:
            seen_additionals.add(item_number)

        for add_code in additional_numbers:
            c_code = str(add_code).strip()
            if not c_code:
                continue
            if c_code in seen_additionals:
                raise ValueError(f"El código adicional '{c_code}' está duplicado en la misma solicitud.")
            
            is_avail, conflict_msg = self.check_barcode(c_code)
            if not is_avail:
                raise ValueError(conflict_msg)
            
            seen_additionals.add(c_code)
            clean_additionals.append(c_code)

        # 4. Intentar sincronización con POS externo (csopos.co)
        category_code = payload_dict.get("category_code")
        department_code = payload_dict.get("department_code")
        pos_sync_res = {}
        pos_item_id = None
        try:
            pos_sync_res = await self.pos_service.create_or_update_pos_item({
                "name": name,
                "category": category,
                "category_code": category_code,
                "department_code": department_code,
                "cost_price": cost_price,
                "unit_price": unit_price,
                "item_number": item_number,
                "unit_code": unit_code,
                "stock_quantity": stock_quantity,
                "profit_percentage": profit_percentage,
                "additional_numbers": clean_additionals
            })
            if not pos_sync_res.get("success") or str(pos_sync_res.get("item_id", "")) in ["", "-1"]:
                err_msg = pos_sync_res.get("message") or "El servidor POS externo (csopos.co) rechazó la creación del producto."
                raise ValueError(f"Fallo en servidor POS: {err_msg}")
            pos_item_id = str(pos_sync_res["item_id"])
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error conectando con POS al crear producto: {e}")
            raise ValueError(f"No se pudo sincronizar el producto con el sistema POS: {e}")

        # 5. Persistir en base de datos local
        created_prod = database.create_product(
            name=name,
            category=category,
            cost_price=cost_price,
            unit_price=unit_price,
            item_number=item_number,
            unit_code=unit_code,
            stock_quantity=stock_quantity,
            description=description,
            profit_percentage=profit_percentage,
            additional_numbers=clean_additionals,
            pos_item_id=pos_item_id
        )

        created_prod["formatted_sale_price"] = format_cop_currency(created_prod["unit_price"])
        created_prod["pos_sync"] = pos_sync_res
        return created_prod

    async def update_product(self, product_id: int, payload_dict: Dict[str, Any]) -> Dict[str, Any]:
        existing = database.get_product_by_id(product_id)
        if not existing:
            raise KeyError(f"Producto con ID {product_id} no encontrado.")

        name = str(payload_dict.get("name", existing["name"])).strip()
        category = str(payload_dict.get("category", existing["category"])).strip()
        cost_price = float(payload_dict.get("cost_price", existing["cost_price"]))
        unit_price = float(payload_dict.get("unit_price", existing["unit_price"]))
        item_number = str(payload_dict.get("item_number", "")).strip() if payload_dict.get("item_number") else None
        unit_code = str(payload_dict.get("unit_code", existing["unit_code"])).strip().upper()
        stock_quantity = float(payload_dict.get("stock_quantity", existing["stock_quantity"]))
        description = payload_dict.get("description", existing.get("description", ""))
        profit_percentage = float(payload_dict.get("profit_percentage", existing["profit_percentage"]))
        additional_numbers = payload_dict.get("additional_numbers")
        is_active = bool(payload_dict.get("is_active", existing.get("is_active", True)))

        if not name:
            raise ValueError("El campo 'Nombre' es estrictamente obligatorio.")
        if not category:
            raise ValueError("El campo 'Categoría' es estrictamente obligatorio.")
        if cost_price < 0 or unit_price < 0:
            raise ValueError("Los valores monetarios no pueden ser negativos.")

        # Validar unicidad de código principal
        if item_number and item_number != existing.get("item_number"):
            is_avail, conflict_msg = self.check_barcode(item_number, exclude_product_id=product_id)
            if not is_avail:
                raise ValueError(conflict_msg)

        # Validar códigos adicionales
        clean_additionals = []
        if additional_numbers is not None:
            seen = set()
            if item_number:
                seen.add(item_number)
            for add_code in additional_numbers:
                c_code = str(add_code).strip()
                if not c_code:
                    continue
                if c_code in seen:
                    raise ValueError(f"El código adicional '{c_code}' está duplicado.")
                is_avail, conflict_msg = self.check_barcode(c_code, exclude_product_id=product_id)
                if not is_avail:
                    raise ValueError(conflict_msg)
                seen.add(c_code)
                clean_additionals.append(c_code)
        else:
            clean_additionals = existing.get("additional_numbers", [])

        # Sincronización POS
        category_code = payload_dict.get("category_code")
        department_code = payload_dict.get("department_code")
        pos_item_id = existing.get("pos_item_id")
        pos_sync_res = {}
        try:
            pos_sync_res = await self.pos_service.create_or_update_pos_item({
                "name": name,
                "category": category,
                "category_code": category_code,
                "department_code": department_code,
                "cost_price": cost_price,
                "unit_price": unit_price,
                "item_number": item_number,
                "unit_code": unit_code,
                "stock_quantity": stock_quantity,
                "profit_percentage": profit_percentage,
                "additional_numbers": clean_additionals
            }, item_id=pos_item_id)
            if pos_sync_res.get("success") and pos_sync_res.get("item_id") and str(pos_sync_res["item_id"]) not in ["-1", ""]:
                pos_item_id = str(pos_sync_res["item_id"])
        except Exception as e:
            logger.warning(f"Error actualizando POS: {e}")

        updated = database.update_product(
            product_id=product_id,
            name=name,
            category=category,
            cost_price=cost_price,
            unit_price=unit_price,
            item_number=item_number,
            unit_code=unit_code,
            stock_quantity=stock_quantity,
            description=description,
            profit_percentage=profit_percentage,
            additional_numbers=clean_additionals,
            pos_item_id=pos_item_id,
            is_active=is_active
        )
        updated["formatted_sale_price"] = format_cop_currency(updated["unit_price"])
        updated["pos_sync"] = pos_sync_res
        return updated

    def get_product(self, product_id: int) -> Optional[Dict[str, Any]]:
        prod = database.get_product_by_id(product_id)
        if prod:
            prod["formatted_sale_price"] = format_cop_currency(prod["unit_price"])
        return prod

    def list_products(self, query: Optional[str] = None, category: Optional[str] = None, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        prods = database.list_catalog_products(query=query, category=category, limit=limit, offset=offset)
        for p in prods:
            p["formatted_sale_price"] = format_cop_currency(p["unit_price"])
        return prods

    def get_units(self) -> List[Dict[str, Any]]:
        return database.get_all_units(only_active=True)

    def create_unit(self, code: str, name: str, magnitude_type: str = "PESO", factor: float = 1.0) -> Dict[str, Any]:
        return database.create_unit_of_measure(code, name, magnitude_type, factor)

    def update_unit(self, unit_id: int, name: Optional[str] = None, magnitude_type: Optional[str] = None, factor: Optional[float] = None, is_active: Optional[bool] = None) -> Optional[Dict[str, Any]]:
        return database.update_unit_of_measure(unit_id, name=name, magnitude_type=magnitude_type, conversion_factor_kg=factor, is_active=is_active)

    def delete_unit(self, unit_id: int, soft: bool = True) -> bool:
        return database.delete_unit_of_measure(unit_id, soft=soft)

    def get_categories(self) -> List[str]:
        return database.get_distinct_categories()

    def get_departments(self) -> List[Dict[str, str]]:
        return self.pos_service.get_departments()

    async def get_categories_by_department(self, department_code: str) -> List[Dict[str, str]]:
        return await self.pos_service.get_categories_by_department(department_code)

    async def search_categories(self, term: str) -> List[Dict[str, Any]]:
        return await self.pos_service.search_categories_pos(term)

    async def resync_product_with_pos(
        self,
        product_id: int,
        category_code: Optional[str] = None,
        department_code: Optional[str] = None
    ) -> Dict[str, Any]:
        """Sincroniza un producto existente en la BD local que tenga pos_item_id inválido (-1)."""
        existing = database.get_product_by_id(product_id)
        if not existing:
            raise KeyError(f"Producto con ID {product_id} no encontrado.")

        res = await self.pos_service.create_or_update_pos_item({
            "name": existing["name"],
            "category": existing["category"],
            "category_code": category_code,
            "department_code": department_code,
            "cost_price": existing["cost_price"],
            "unit_price": existing["unit_price"],
            "item_number": existing.get("item_number"),
            "unit_code": existing.get("unit_code", "UN"),
            "stock_quantity": existing.get("stock_quantity", 0.0),
            "profit_percentage": existing.get("profit_percentage", 30.0),
            "additional_numbers": existing.get("additional_numbers", [])
        }, item_id=existing.get("pos_item_id"))

        if res.get("success") and res.get("item_id") and str(res["item_id"]) not in ["", "-1"]:
            pos_id = str(res["item_id"])
            updated = database.update_product(
                product_id=product_id,
                name=existing["name"],
                category=existing["category"],
                cost_price=existing["cost_price"],
                unit_price=existing["unit_price"],
                item_number=existing.get("item_number"),
                unit_code=existing.get("unit_code", "UN"),
                stock_quantity=existing.get("stock_quantity", 0.0),
                description=existing.get("description", ""),
                profit_percentage=existing.get("profit_percentage", 30.0),
                additional_numbers=existing.get("additional_numbers", []),
                pos_item_id=pos_id,
                is_active=existing.get("is_active", True)
            )
            updated["pos_sync"] = res
            return updated
        else:
            raise ValueError(f"Fallo al sincronizar con el POS: {res.get('message', 'Error desconocido')}")

