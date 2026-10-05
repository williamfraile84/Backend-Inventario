import sqlite3
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone
import difflib
import unicodedata
import re

from sqlalchemy import create_engine, event, text, or_, func
from sqlalchemy.orm import declarative_base, sessionmaker, Session, joinedload
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.security import get_password_hash, verify_password
from app.models.base import Base
import app.models  # Registra todos los modelos relacionales en Base.metadata
from app.models.usuario import Usuario, User
from app.models.catalogos import UnidadMedida, TipoEmpaque, ConceptoGasto
from app.models.producto import Producto, ProductAdditionalNumber
from app.models.invoices import Invoice, InvoiceItem, InvoiceSupplierTemplate, InvoiceProductAlias
from app.models.auditoria import PriceAuditLog
from app.models.empresa import Empresa, EmpresaAtributo

logger = logging.getLogger("Database")

DB_PATH = Path(__file__).resolve().parent.parent.parent / "fruver_pos.db"

# ==========================================
# Motor SQLAlchemy Multi-Base de Datos
# ==========================================
db_url = settings.effective_database_url
is_sqlite = db_url.lower().startswith("sqlite")

engine_kwargs = {
    "pool_pre_ping": True,
}

if is_sqlite:
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    engine_kwargs["connect_args"] = {
        "connect_timeout": 30,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
    }
    if settings.DB_USE_NULLPOOL:
        engine_kwargs["poolclass"] = NullPool
    else:
        engine_kwargs["pool_size"] = 10
        engine_kwargs["max_overflow"] = 20
        engine_kwargs["pool_recycle"] = 300

engine = create_engine(db_url, **engine_kwargs)

if is_sqlite:
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA cache_size=-64000")  # 64MB de cache
            cursor.execute("PRAGMA busy_timeout=5000")   # 5s ante concurrencia
            cursor.execute("PRAGMA temp_store=MEMORY")
        except Exception:
            pass
        finally:
            cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """Generador de sesión SQLAlchemy para inyección de dependencias en FastAPI."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_connection() -> sqlite3.Connection:
    """Conexión SQLite local de compatibilidad retroactiva."""
    target_path = DB_PATH
    if not target_path.exists():
        backend_db = Path(__file__).resolve().parent.parent / "fruver_pos.db"
        if backend_db.exists():
            target_path = backend_db
    conn = sqlite3.connect(str(target_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.row_factory = sqlite3.Row
    return conn


def hash_password(password: str) -> str:
    return get_password_hash(password)


def check_password(plain_password: str, hashed_password: str) -> bool:
    return verify_password(plain_password, hashed_password)


DEFAULT_PERMISSIONS = {
    "admin": {
        "can_lookup": True,
        "can_edit_single": True,
        "can_edit_bulk": True,
        "can_manage_users": True,
        "can_view_audit": True,
        "can_process_invoices": True,
        "can_sync_pos": True,
    },
    "supervisor": {
        "can_lookup": True,
        "can_edit_single": True,
        "can_edit_bulk": True,
        "can_manage_users": False,
        "can_view_audit": True,
        "can_process_invoices": True,
        "can_sync_pos": True,
    },
    "cajero": {
        "can_lookup": True,
        "can_edit_single": False,
        "can_edit_bulk": False,
        "can_manage_users": False,
        "can_view_audit": False,
        "can_process_invoices": False,
        "can_sync_pos": False,
    }
}


def init_db():
    """Inicializa todas las tablas del modelo y siembra datos maestros si la BD está vacía."""
    logger.info(f"Verificando tablas en base de datos: [{engine.dialect.name.upper()}] {settings.mask_database_url()}")
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as session:
        now_ts = datetime.now(timezone.utc).isoformat()

        # 1. Sembrar Administrador Inicial si no existe
        admin_user = session.query(Usuario).filter(Usuario.username == (settings.APP_ADMIN_USER or "admin")).first()
        if not admin_user:
            admin_pass = settings.APP_ADMIN_PASS or "fruver2026"
            hashed = hash_password(admin_pass)
            new_admin = Usuario(
                username=settings.APP_ADMIN_USER or "admin",
                full_name="Administrador Principal",
                password_hash=hashed,
                role="admin",
                permissions=json.dumps(DEFAULT_PERMISSIONS["admin"]),
                is_active=True,
                created_at=now_ts
            )
            session.add(new_admin)
            session.commit()
            logger.info("✅ Usuario administrador inicial verificado/creado con éxito.")

        # 2. Sembrar Unidades de Medida si está vacía
        units_count = session.query(UnidadMedida).count()
        if units_count == 0:
            standard_units = [
                ("UN", "unidad (UN)", "UNIDAD", 1.0),
                ("KG", "kilogramo neto (KG)", "PESO", 1.0),
                ("GRM", "gramo (GRM)", "PESO", 0.001),
                ("LB", "libra neta (LB)", "PESO", 0.5),
                ("L", "litro (L)", "VOLUMEN", 1.0),
                ("ML", "mililitro (ML)", "VOLUMEN", 0.001),
                ("CMT", "centímetro (CMT)", "LONGITUD", 0.01),
                ("MTR", "metro (MTR)", "LONGITUD", 1.0),
                ("MTQ", "Metro cúbico (MTQ)", "VOLUMEN", 1000.0),
                ("OZ", "onza (OZ)", "PESO", 0.02835),
            ]
            for code, name, mag, factor in standard_units:
                session.add(UnidadMedida(
                    code=code,
                    name=name,
                    magnitude_type=mag,
                    conversion_factor_kg=factor,
                    is_active=True,
                    created_at=now_ts
                ))
            session.commit()
            logger.info("✅ Catálogo de unidades de medida sembrado.")

        # 3. Sembrar Tipos de Empaque si está vacía
        empaques_count = session.query(TipoEmpaque).count()
        if empaques_count == 0:
            standard_empaques = [
                ("CAJA", "Caja Plástica / Cartón", 1.5, 20.0, 0.0),
                ("BOLSA", "Bolsa Plástica / Biodegradable", 0.02, 5.0, 0.0),
                ("MALLA", "Malla / Red Extensible", 0.05, 10.0, 0.0),
                ("GUACAL", "Guacal de Madera Tradicional", 2.5, 30.0, 0.0),
                ("BULTO", "Bulto / Costal de Fique o Fibra", 0.5, 50.0, 0.0),
            ]
            for codigo, nombre, tara, capacidad, costo in standard_empaques:
                session.add(TipoEmpaque(
                    codigo=codigo,
                    nombre=nombre,
                    tara_kg=tara,
                    capacidad_kg=capacidad,
                    costo_empaque=costo,
                    activo=True
                ))
            session.commit()
            logger.info("✅ Catálogo de tipos de empaque sembrado.")

        # 4. Sembrar Conceptos de Gasto si está vacía
        gastos_count = session.query(ConceptoGasto).count()
        if gastos_count == 0:
            standard_gastos = [
                ("FLETE", "Transporte / Flete de Mercancía", "variable", True),
                ("DESCARGUE", "Mano de Obra y Descargue", "variable", True),
                ("SELECCION", "Selección y Clasificación Fruver", "variable", True),
                ("EMPAQUE", "Materiales y Empaque", "variable", True),
                ("COMISION", "Comisión / Intermediación", "variable", False),
            ]
            for codigo, nombre, tipo, distr in standard_gastos:
                session.add(ConceptoGasto(
                    codigo=codigo,
                    nombre=nombre,
                    tipo=tipo,
                    distribuir_en_costo=distr,
                    activo=True
                ))
            session.commit()
            logger.info("✅ Catálogo de conceptos de gasto sembrado.")

        # 5. Sembrar Empresa por Defecto si está vacía
        empresa_count = session.query(Empresa).count()
        if empresa_count == 0:
            emp = Empresa(
                nombre="FRUVER POS",
                nit="900.000.000-1",
                direccion="Calle Principal # 10 - 20",
                telefono="300 000 0000",
                email="pos@fruver.com",
                activo=True
            )
            session.add(emp)
            session.commit()
            session.refresh(emp)
            attrs = [
                EmpresaAtributo(empresa_id=emp.id, clave="margen_defecto", valor="30"),
                EmpresaAtributo(empresa_id=emp.id, clave="base_redondeo", valor="100"),
                EmpresaAtributo(empresa_id=emp.id, clave="moneda", valor="COP"),
            ]
            session.add_all(attrs)
            session.commit()
            logger.info("✅ Empresa inicial verificada/creada con éxito.")


# =========================================================================
# REPOSITORIO DE USUARIOS (SQLAlchemy Universal)
# =========================================================================

def _user_to_dict(u: Usuario) -> Dict[str, Any]:
    return {
        "id": u.id,
        "username": u.username,
        "full_name": u.full_name,
        "password_hash": u.password_hash,
        "role": u.role,
        "permissions": u.permisos_dict,
        "is_active": bool(u.is_active),
        "created_at": str(u.created_at),
        "last_login": str(u.last_login) if u.last_login else None
    }


def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    with SessionLocal() as db:
        user = db.query(Usuario).filter(Usuario.username == username.strip()).first()
        return _user_to_dict(user) if user else None


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    with SessionLocal() as db:
        user = db.query(Usuario).filter(Usuario.id == user_id).first()
        return _user_to_dict(user) if user else None


def get_all_users() -> List[Dict[str, Any]]:
    with SessionLocal() as db:
        users = db.query(Usuario).order_by(Usuario.id.asc()).all()
        return [_user_to_dict(u) for u in users]


def create_user(username: str, full_name: str, password: str, role: str, permissions: Optional[Dict[str, bool]] = None) -> Dict[str, Any]:
    with SessionLocal() as db:
        if permissions is None:
            permissions = DEFAULT_PERMISSIONS.get(role, DEFAULT_PERMISSIONS["cajero"])
        hashed = hash_password(password)
        now = datetime.now(timezone.utc).isoformat()
        u = Usuario(
            username=username.strip(),
            full_name=full_name.strip(),
            password_hash=hashed,
            role=role,
            permissions=json.dumps(permissions),
            is_active=True,
            created_at=now
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return _user_to_dict(u)


def update_user(user_id: int, full_name: Optional[str] = None, role: Optional[str] = None, permissions: Optional[Dict[str, bool]] = None, is_active: Optional[bool] = None, password: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with SessionLocal() as db:
        u = db.query(Usuario).filter(Usuario.id == user_id).first()
        if not u:
            return None
        if full_name is not None:
            u.full_name = full_name.strip()
        if role is not None:
            u.role = role
        if permissions is not None:
            u.permissions = json.dumps(permissions)
        if is_active is not None:
            u.is_active = bool(is_active)
        if password:
            u.password_hash = hash_password(password)
        db.commit()
        db.refresh(u)
        return _user_to_dict(u)


def delete_user(user_id: int) -> bool:
    with SessionLocal() as db:
        u = db.query(Usuario).filter(Usuario.id == user_id).first()
        if not u:
            return False
        db.delete(u)
        db.commit()
        return True


def update_last_login(user_id: int):
    with SessionLocal() as db:
        u = db.query(Usuario).filter(Usuario.id == user_id).first()
        if u:
            u.last_login = datetime.now(timezone.utc).isoformat()
            db.commit()


# =========================================================================
# REPOSITORIO DE AUDITORÍA (SQLAlchemy Universal)
# =========================================================================

def add_audit_log(username: str, action_type: str, barcode: str, new_price: float, formatted_new_price: str, user_id: Optional[int] = None, item_name: Optional[str] = None, old_price: Optional[float] = None, updated_count: int = 1, details: Optional[str] = None):
    with SessionLocal() as db:
        now_ts = datetime.now(timezone.utc).isoformat()
        log = PriceAuditLog(
            user_id=user_id,
            username=username,
            action_type=action_type,
            barcode=barcode,
            item_name=item_name,
            old_price=old_price,
            new_price=float(new_price),
            formatted_new_price=formatted_new_price,
            updated_count=updated_count,
            timestamp=now_ts,
            details=details
        )
        db.add(log)
        db.commit()


def get_audit_logs(limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
    with SessionLocal() as db:
        logs = db.query(PriceAuditLog).order_by(PriceAuditLog.id.desc()).offset(offset).limit(limit).all()
        return [
            {
                "id": l.id,
                "user_id": l.user_id,
                "username": l.username,
                "action_type": l.action_type,
                "barcode": l.barcode,
                "item_name": l.item_name,
                "old_price": l.old_price,
                "new_price": l.new_price,
                "formatted_new_price": l.formatted_new_price,
                "updated_count": l.updated_count,
                "timestamp": l.timestamp,
                "details": l.details
            }
            for l in logs
        ]


# =========================================================================
# REPOSITORIO DE UNIDADES DE MEDIDA (SQLAlchemy Universal)
# =========================================================================

def _unit_to_dict(u: UnidadMedida) -> Dict[str, Any]:
    return {
        "id": u.id,
        "code": u.code,
        "name": u.name,
        "magnitude_type": u.magnitude_type,
        "conversion_factor_kg": u.conversion_factor_kg,
        "is_active": bool(u.is_active),
        "created_at": str(u.created_at)
    }


def get_all_units(only_active: bool = True) -> List[Dict[str, Any]]:
    with SessionLocal() as db:
        q = db.query(UnidadMedida)
        if only_active:
            q = q.filter(UnidadMedida.is_active == True)
        units = q.order_by(UnidadMedida.id.asc()).all()
        return [_unit_to_dict(u) for u in units]


def get_unit_by_code(code: str) -> Optional[Dict[str, Any]]:
    if not code:
        return None
    with SessionLocal() as db:
        u = db.query(UnidadMedida).filter(func.upper(UnidadMedida.code) == code.strip().upper()).first()
        return _unit_to_dict(u) if u else None


def create_unit_of_measure(code: str, name: str, magnitude_type: str = "PESO", conversion_factor_kg: float = 1.0) -> Dict[str, Any]:
    with SessionLocal() as db:
        clean_code = code.strip().upper()
        now_ts = datetime.now(timezone.utc).isoformat()
        u = UnidadMedida(
            code=clean_code,
            name=name.strip(),
            magnitude_type=magnitude_type.strip(),
            conversion_factor_kg=float(conversion_factor_kg),
            is_active=True,
            created_at=now_ts
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return _unit_to_dict(u)


def update_unit_of_measure(unit_id: int, name: Optional[str] = None, magnitude_type: Optional[str] = None, conversion_factor_kg: Optional[float] = None, is_active: Optional[bool] = None) -> Optional[Dict[str, Any]]:
    with SessionLocal() as db:
        u = db.query(UnidadMedida).filter(UnidadMedida.id == unit_id).first()
        if not u:
            return None
        if name is not None:
            u.name = name.strip()
        if magnitude_type is not None:
            u.magnitude_type = magnitude_type.strip()
        if conversion_factor_kg is not None:
            u.conversion_factor_kg = float(conversion_factor_kg)
        if is_active is not None:
            u.is_active = bool(is_active)
        db.commit()
        db.refresh(u)
        return _unit_to_dict(u)


def delete_unit_of_measure(unit_id: int, soft: bool = True) -> bool:
    with SessionLocal() as db:
        u = db.query(UnidadMedida).filter(UnidadMedida.id == unit_id).first()
        if not u:
            return False
        if soft:
            u.is_active = False
        else:
            db.delete(u)
        db.commit()
        return True


# =========================================================================
# REPOSITORIO DE EMPAQUES Y CONCEPTOS DE GASTO
# =========================================================================

def get_all_packaging_types(only_active: bool = True) -> List[Dict[str, Any]]:
    with SessionLocal() as db:
        q = db.query(TipoEmpaque)
        if only_active:
            q = q.filter(TipoEmpaque.activo == True)
        empaques = q.order_by(TipoEmpaque.id.asc()).all()
        return [
            {
                "id": e.id,
                "codigo": e.codigo,
                "nombre": e.nombre,
                "tara_kg": e.tara_kg,
                "capacidad_kg": e.capacidad_kg,
                "costo_empaque": e.costo_empaque,
                "activo": e.activo
            }
            for e in empaques
        ]


def create_packaging_type(codigo: str, nombre: str, tara_kg: float = 0.0, capacidad_kg: float = 0.0, costo_empaque: float = 0.0) -> Dict[str, Any]:
    with SessionLocal() as db:
        e = TipoEmpaque(
            codigo=codigo.strip().upper(),
            nombre=nombre.strip(),
            tara_kg=float(tara_kg),
            capacidad_kg=float(capacidad_kg),
            costo_empaque=float(costo_empaque),
            activo=True
        )
        db.add(e)
        db.commit()
        db.refresh(e)
        return {
            "id": e.id,
            "codigo": e.codigo,
            "nombre": e.nombre,
            "tara_kg": e.tara_kg,
            "capacidad_kg": e.capacidad_kg,
            "costo_empaque": e.costo_empaque,
            "activo": e.activo
        }


def update_packaging_type(packaging_id: int, nombre: Optional[str] = None, tara_kg: Optional[float] = None, capacidad_kg: Optional[float] = None, costo_empaque: Optional[float] = None, activo: Optional[bool] = None) -> Optional[Dict[str, Any]]:
    with SessionLocal() as db:
        e = db.query(TipoEmpaque).filter(TipoEmpaque.id == packaging_id).first()
        if not e:
            return None
        if nombre is not None:
            e.nombre = nombre.strip()
        if tara_kg is not None:
            e.tara_kg = float(tara_kg)
        if capacidad_kg is not None:
            e.capacidad_kg = float(capacidad_kg)
        if costo_empaque is not None:
            e.costo_empaque = float(costo_empaque)
        if activo is not None:
            e.activo = bool(activo)
        db.commit()
        db.refresh(e)
        return {
            "id": e.id,
            "codigo": e.codigo,
            "nombre": e.nombre,
            "tara_kg": e.tara_kg,
            "capacidad_kg": e.capacidad_kg,
            "costo_empaque": e.costo_empaque,
            "activo": e.activo
        }


def delete_packaging_type(packaging_id: int, soft: bool = True) -> bool:
    with SessionLocal() as db:
        e = db.query(TipoEmpaque).filter(TipoEmpaque.id == packaging_id).first()
        if not e:
            return False
        if soft:
            e.activo = False
        else:
            db.delete(e)
        db.commit()
        return True


def get_all_expense_concepts(only_active: bool = True) -> List[Dict[str, Any]]:
    with SessionLocal() as db:
        q = db.query(ConceptoGasto)
        if only_active:
            q = q.filter(ConceptoGasto.activo == True)
        gastos = q.order_by(ConceptoGasto.id.asc()).all()
        return [
            {
                "id": g.id,
                "codigo": g.codigo,
                "nombre": g.nombre,
                "tipo": g.tipo,
                "distribuir_en_costo": g.distribuir_en_costo,
                "activo": g.activo
            }
            for g in gastos
        ]


def create_expense_concept(codigo: str, nombre: str, tipo: str = "variable", distribuir_en_costo: bool = True) -> Dict[str, Any]:
    with SessionLocal() as db:
        g = ConceptoGasto(
            codigo=codigo.strip().upper(),
            nombre=nombre.strip(),
            tipo=tipo.strip().lower(),
            distribuir_en_costo=distribuir_en_costo,
            activo=True
        )
        db.add(g)
        db.commit()
        db.refresh(g)
        return {
            "id": g.id,
            "codigo": g.codigo,
            "nombre": g.nombre,
            "tipo": g.tipo,
            "distribuir_en_costo": g.distribuir_en_costo,
            "activo": g.activo
        }


def update_expense_concept(concept_id: int, nombre: Optional[str] = None, tipo: Optional[str] = None, distribuir_en_costo: Optional[bool] = None, activo: Optional[bool] = None) -> Optional[Dict[str, Any]]:
    with SessionLocal() as db:
        g = db.query(ConceptoGasto).filter(ConceptoGasto.id == concept_id).first()
        if not g:
            return None
        if nombre is not None:
            g.nombre = nombre.strip()
        if tipo is not None:
            g.tipo = tipo.strip().lower()
        if distribuir_en_costo is not None:
            g.distribuir_en_costo = bool(distribuir_en_costo)
        if activo is not None:
            g.activo = bool(activo)
        db.commit()
        db.refresh(g)
        return {
            "id": g.id,
            "codigo": g.codigo,
            "nombre": g.nombre,
            "tipo": g.tipo,
            "distribuir_en_costo": g.distribuir_en_costo,
            "activo": g.activo
        }


def delete_expense_concept(concept_id: int, soft: bool = True) -> bool:
    with SessionLocal() as db:
        g = db.query(ConceptoGasto).filter(ConceptoGasto.id == concept_id).first()
        if not g:
            return False
        if soft:
            g.activo = False
        else:
            db.delete(g)
        db.commit()
        return True


# =========================================================================
# REPOSITORIO DE PRODUCTOS Y CÓDIGOS ADICIONALES (SQLAlchemy Universal)
# =========================================================================

def _product_to_dict(p: Producto) -> Dict[str, Any]:
    additionals = [an.item_number for an in (p.additional_numbers_rel or [])]
    return {
        "id": p.id,
        "pos_item_id": p.pos_item_id,
        "item_number": p.item_number,
        "name": p.name,
        "category": p.category,
        "category_code": p.category_code,
        "department_code": p.department_code,
        "cost_price": float(p.cost_price or 0.0),
        "unit_price": float(p.unit_price or 0.0),
        "unit_code": p.unit_code or "UN",
        "stock_quantity": float(p.stock_quantity or 0.0),
        "description": p.description or "",
        "profit_percentage": float(p.profit_percentage or 30.0),
        "is_active": bool(p.is_active),
        "created_at": str(p.created_at),
        "updated_at": str(p.updated_at),
        "additional_numbers": additionals,
        "unit_name": p.unit_code or "UN"
    }


def is_barcode_available(barcode: str, exclude_product_id: Optional[int] = None) -> Tuple[bool, Optional[str]]:
    """Verifica si un código de barras ya existe en códigos principales o adicionales."""
    if not barcode or not str(barcode).strip():
        return True, None
    clean_code = str(barcode).strip()

    with SessionLocal() as db:
        # 1. Verificar en productos principales
        q_main = db.query(Producto).filter(Producto.item_number == clean_code, Producto.is_active == True)
        if exclude_product_id:
            q_main = q_main.filter(Producto.id != exclude_product_id)
        prod_main = q_main.first()
        if prod_main:
            return False, f"El código '{clean_code}' ya está asignado al producto '{prod_main.name}' (ID: {prod_main.id})."

        # 2. Verificar en códigos adicionales
        q_add = db.query(ProductAdditionalNumber).join(Producto, ProductAdditionalNumber.product_id == Producto.id).filter(
            ProductAdditionalNumber.item_number == clean_code,
            Producto.is_active == True
        )
        if exclude_product_id:
            q_add = q_add.filter(ProductAdditionalNumber.product_id != exclude_product_id)
        add_match = q_add.first()
        if add_match:
            prod_name = add_match.product.name if add_match.product else "Desconocido"
            return False, f"El código '{clean_code}' ya está registrado como código adicional en el producto '{prod_name}' (ID: {add_match.product_id})."

        return True, None


def get_product_by_id(product_id: int) -> Optional[Dict[str, Any]]:
    with SessionLocal() as db:
        p = db.query(Producto).options(joinedload(Producto.additional_numbers_rel)).filter(Producto.id == product_id).first()
        return _product_to_dict(p) if p else None


def get_product_by_barcode(barcode: str) -> Optional[Dict[str, Any]]:
    if not barcode:
        return None
    clean = str(barcode).strip()
    with SessionLocal() as db:
        # 1. Código principal
        p = db.query(Producto).options(joinedload(Producto.additional_numbers_rel)).filter(
            Producto.item_number == clean,
            Producto.is_active == True
        ).first()
        if p:
            return _product_to_dict(p)

        # 2. Código adicional
        add = db.query(ProductAdditionalNumber).filter(ProductAdditionalNumber.item_number == clean).first()
        if add and add.product_id:
            return get_product_by_id(add.product_id)

    return None


def create_product(
    name: str,
    category: str,
    cost_price: float,
    unit_price: float,
    item_number: Optional[str] = None,
    unit_code: str = "UN",
    stock_quantity: float = 0.0,
    description: Optional[str] = None,
    profit_percentage: float = 30.0,
    additional_numbers: Optional[List[str]] = None,
    pos_item_id: Optional[str] = None,
    category_code: Optional[str] = None,
    department_code: Optional[str] = None
) -> Dict[str, Any]:
    now_ts = datetime.now(timezone.utc).isoformat()
    clean_item_number = str(item_number).strip() if item_number and str(item_number).strip() else None
    clean_unit_code = str(unit_code).strip().upper() if unit_code else "UN"
    clean_cat_code = str(category_code).strip() if category_code and str(category_code).strip() else None
    clean_dep_code = str(department_code).strip() if department_code and str(department_code).strip() else None

    with SessionLocal() as db:
        prod = Producto(
            pos_item_id=pos_item_id,
            item_number=clean_item_number,
            name=name.strip(),
            category=category.strip(),
            category_code=clean_cat_code,
            department_code=clean_dep_code,
            cost_price=float(cost_price),
            unit_price=float(unit_price),
            unit_code=clean_unit_code,
            stock_quantity=float(stock_quantity),
            description=description.strip() if description else "",
            profit_percentage=float(profit_percentage),
            is_active=True,
            created_at=now_ts,
            updated_at=now_ts
        )
        db.add(prod)
        db.flush()

        if additional_numbers:
            added_set = set()
            for add_num in additional_numbers:
                c_num = str(add_num).strip()
                if c_num and c_num != clean_item_number and c_num not in added_set:
                    db.add(ProductAdditionalNumber(
                        product_id=prod.id,
                        item_number=c_num,
                        created_at=now_ts
                    ))
                    added_set.add(c_num)

        db.commit()
        db.refresh(prod)
        return _product_to_dict(prod)


def update_product(
    product_id: int,
    name: str,
    category: str,
    cost_price: float,
    unit_price: float,
    item_number: Optional[str] = None,
    unit_code: str = "UN",
    stock_quantity: float = 0.0,
    description: Optional[str] = None,
    profit_percentage: float = 30.0,
    additional_numbers: Optional[List[str]] = None,
    pos_item_id: Optional[str] = None,
    category_code: Optional[str] = None,
    department_code: Optional[str] = None
) -> Dict[str, Any]:
    now_ts = datetime.now(timezone.utc).isoformat()
    clean_item_number = str(item_number).strip() if item_number and str(item_number).strip() else None
    clean_unit_code = str(unit_code).strip().upper() if unit_code else "UN"
    clean_cat_code = str(category_code).strip() if category_code and str(category_code).strip() else None
    clean_dep_code = str(department_code).strip() if department_code and str(department_code).strip() else None

    with SessionLocal() as db:
        prod = db.query(Producto).filter(Producto.id == product_id).first()
        if not prod:
            raise KeyError(f"Producto con ID {product_id} no encontrado.")

        prod.name = name.strip()
        prod.category = category.strip()
        prod.cost_price = float(cost_price)
        prod.unit_price = float(unit_price)
        prod.item_number = clean_item_number
        prod.unit_code = clean_unit_code
        prod.stock_quantity = float(stock_quantity)
        prod.description = description.strip() if description else ""
        prod.profit_percentage = float(profit_percentage)
        prod.updated_at = now_ts

        if pos_item_id is not None:
            prod.pos_item_id = pos_item_id
        if clean_cat_code is not None:
            prod.category_code = clean_cat_code
        if clean_dep_code is not None:
            prod.department_code = clean_dep_code

        # Reemplazar números adicionales
        db.query(ProductAdditionalNumber).filter(ProductAdditionalNumber.product_id == product_id).delete()
        if additional_numbers:
            added_set = set()
            for add_num in additional_numbers:
                c_num = str(add_num).strip()
                if c_num and c_num != clean_item_number and c_num not in added_set:
                    db.add(ProductAdditionalNumber(
                        product_id=product_id,
                        item_number=c_num,
                        created_at=now_ts
                    ))
                    added_set.add(c_num)

        db.commit()
        db.refresh(prod)
        return _product_to_dict(prod)


def list_catalog_products(
    query: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 100,
    offset: int = 0
) -> List[Dict[str, Any]]:
    with SessionLocal() as db:
        q = db.query(Producto).options(joinedload(Producto.additional_numbers_rel)).filter(Producto.is_active == True)
        if category and category.strip():
            q = q.filter(Producto.category == category.strip())
        if query and query.strip():
            term = f"%{query.strip()}%"
            # Buscar por nombre, código de barras principal o coincidencia en códigos adicionales
            add_match_ids = db.query(ProductAdditionalNumber.product_id).filter(
                ProductAdditionalNumber.item_number.ilike(term)
            ).subquery()

            q = q.filter(
                or_(
                    Producto.name.ilike(term),
                    Producto.item_number.ilike(term),
                    Producto.id.in_(add_match_ids)
                )
            )

        products = q.order_by(Producto.name.asc()).offset(offset).limit(limit).all()
        return [_product_to_dict(p) for p in products]


def get_distinct_categories() -> List[str]:
    with SessionLocal() as db:
        cats = db.query(Producto.category).filter(
            Producto.is_active == True,
            Producto.category.isnot(None),
            Producto.category != ""
        ).distinct().order_by(Producto.category.asc()).all()
        return [c[0] for c in cats if c[0]]


# =========================================================================
# REPOSITORIO DE FACTURAS E HISTORIAL (SQLAlchemy Universal)
# =========================================================================

def save_invoice_record(invoice_data: Dict[str, Any], items: List[Dict[str, Any]]) -> int:
    now_ts = datetime.now(timezone.utc).isoformat()
    with SessionLocal() as db:
        inv = Invoice(
            provider_name=invoice_data.get("provider_name"),
            provider_nit=invoice_data.get("provider_nit"),
            invoice_number=invoice_data.get("invoice_number"),
            invoice_date=invoice_data.get("invoice_date"),
            subtotal=float(invoice_data.get("subtotal", 0.0) or 0.0),
            tax_total=float(invoice_data.get("tax_total", 0.0) or 0.0),
            discount_total=float(invoice_data.get("discount_total", 0.0) or 0.0),
            total=float(invoice_data.get("total", 0.0) or 0.0),
            status=invoice_data.get("status", "procesada"),
            raw_data_json=json.dumps(invoice_data),
            created_at=now_ts
        )
        db.add(inv)
        db.flush()

        for item in items:
            inv_item = InvoiceItem(
                invoice_id=inv.id,
                product_code=item.get("product_code") or item.get("codigo"),
                description=item.get("description") or item.get("descripcion", "Sin descripción"),
                quantity=float(item.get("quantity", 1.0) or 1.0),
                presentation=item.get("presentation") or item.get("unidad", "Und"),
                units_per_presentation=float(item.get("units_per_presentation", 1.0) or 1.0),
                unit_cost_base=float(item.get("unit_cost_base", 0.0) or 0.0),
                tax_concepts_json=json.dumps(item.get("tax_concepts", [])),
                unit_cost_net=float(item.get("unit_cost_net", 0.0) or 0.0),
                margin_percent=float(item.get("margin_percent", 30.0) or 30.0),
                sale_price_calculated=float(item.get("sale_price_calculated", 0.0) or 0.0),
                sale_price_final=int(item.get("sale_price_final", 0) or 0),
                matched_product_id=item.get("matched_product_id")
            )
            db.add(inv_item)

        db.commit()
        db.refresh(inv)
        return inv.id


def get_invoices_history(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    with SessionLocal() as db:
        results = (
            db.query(
                Invoice,
                func.count(InvoiceItem.id).label("items_count")
            )
            .outerjoin(InvoiceItem, Invoice.id == InvoiceItem.invoice_id)
            .group_by(Invoice.id)
            .order_by(Invoice.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        history = []
        for inv, count in results:
            d = {
                "id": inv.id,
                "provider_name": inv.provider_name,
                "provider_nit": inv.provider_nit,
                "invoice_number": inv.invoice_number,
                "invoice_date": inv.invoice_date,
                "subtotal": inv.subtotal,
                "tax_total": inv.tax_total,
                "discount_total": inv.discount_total,
                "total": inv.total,
                "status": inv.status,
                "created_at": inv.created_at,
                "items_count": count
            }
            history.append(d)
        return history


def get_invoice_detail(invoice_id: int) -> Optional[Dict[str, Any]]:
    with SessionLocal() as db:
        inv = db.query(Invoice).options(joinedload(Invoice.items)).filter(Invoice.id == invoice_id).first()
        if not inv:
            return None
        return {
            "id": inv.id,
            "provider_name": inv.provider_name,
            "provider_nit": inv.provider_nit,
            "invoice_number": inv.invoice_number,
            "invoice_date": inv.invoice_date,
            "subtotal": inv.subtotal,
            "tax_total": inv.tax_total,
            "discount_total": inv.discount_total,
            "total": inv.total,
            "status": inv.status,
            "created_at": inv.created_at,
            "raw_data": json.loads(inv.raw_data_json) if inv.raw_data_json else {},
            "items": [
                {
                    "id": it.id,
                    "product_code": it.product_code,
                    "description": it.description,
                    "quantity": it.quantity,
                    "presentation": it.presentation,
                    "units_per_presentation": it.units_per_presentation,
                    "unit_cost_base": it.unit_cost_base,
                    "unit_cost_net": it.unit_cost_net,
                    "margin_percent": it.margin_percent,
                    "sale_price_calculated": it.sale_price_calculated,
                    "sale_price_final": it.sale_price_final,
                    "matched_product_id": it.matched_product_id,
                    "tax_concepts": json.loads(it.tax_concepts_json) if it.tax_concepts_json else []
                }
                for it in (inv.items or [])
            ]
        }


# =========================================================================
# APRENDIZAJE CONTINUO: PLANTILLAS Y ALIAS (SQLAlchemy Universal)
# =========================================================================

def normalize_ocr_key(text: Any) -> str:
    """Normaliza texto OCR eliminando puntuación, espacios y acentos para coincidencia invariante."""
    if not text:
        return ""
    s = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def get_supplier_template(provider_name: str, nit: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with SessionLocal() as db:
        if nit and str(nit).strip():
            tmpl = db.query(InvoiceSupplierTemplate).filter(InvoiceSupplierTemplate.provider_nit == str(nit).strip()).first()
            if tmpl:
                return {
                    "id": tmpl.id,
                    "provider_nit": tmpl.provider_nit,
                    "provider_name_pattern": tmpl.provider_name_pattern,
                    "layout_type": tmpl.layout_type,
                    "default_orientation": tmpl.default_orientation,
                    "column_mapping": json.loads(tmpl.column_mapping_json) if tmpl.column_mapping_json else {},
                    "default_taxes": json.loads(tmpl.default_taxes_json) if tmpl.default_taxes_json else [],
                    "times_used": tmpl.times_used
                }

        if provider_name:
            clean_name = normalize_ocr_key(provider_name)
            all_tmpls = db.query(InvoiceSupplierTemplate).all()
            for t in all_tmpls:
                p_pat = normalize_ocr_key(t.provider_name_pattern)
                if p_pat and (p_pat in clean_name or clean_name in p_pat):
                    return {
                        "id": t.id,
                        "provider_nit": t.provider_nit,
                        "provider_name_pattern": t.provider_name_pattern,
                        "layout_type": t.layout_type,
                        "default_orientation": t.default_orientation,
                        "column_mapping": json.loads(t.column_mapping_json) if t.column_mapping_json else {},
                        "default_taxes": json.loads(t.default_taxes_json) if t.default_taxes_json else [],
                        "times_used": t.times_used
                    }
    return None


def save_or_update_supplier_template(template_data: Dict[str, Any]) -> Dict[str, Any]:
    now_ts = datetime.now(timezone.utc).isoformat()
    p_name = template_data.get("provider_name_pattern") or template_data.get("provider_name") or "PROVEEDOR GENERAL"
    p_nit = template_data.get("provider_nit")
    layout = template_data.get("layout_type", "table")
    orientation = int(template_data.get("default_orientation") or template_data.get("orientation") or 0)
    col_map = json.dumps(template_data.get("column_mapping", {}))
    taxes = json.dumps(template_data.get("default_taxes", []))

    with SessionLocal() as db:
        tmpl = db.query(InvoiceSupplierTemplate).filter(InvoiceSupplierTemplate.provider_name_pattern == p_name).first()
        if tmpl:
            if p_nit:
                tmpl.provider_nit = p_nit
            tmpl.layout_type = layout
            tmpl.default_orientation = orientation
            tmpl.column_mapping_json = col_map
            tmpl.default_taxes_json = taxes
            tmpl.times_used = (tmpl.times_used or 0) + 1
            tmpl.updated_at = now_ts
        else:
            tmpl = InvoiceSupplierTemplate(
                provider_nit=p_nit,
                provider_name_pattern=p_name,
                layout_type=layout,
                default_orientation=orientation,
                column_mapping_json=col_map,
                default_taxes_json=taxes,
                times_used=1,
                confidence_score=0.95,
                created_at=now_ts,
                updated_at=now_ts
            )
            db.add(tmpl)
        db.commit()
        db.refresh(tmpl)
        return {
            "id": tmpl.id,
            "provider_nit": tmpl.provider_nit,
            "provider_name_pattern": tmpl.provider_name_pattern,
            "layout_type": tmpl.layout_type,
            "default_orientation": tmpl.default_orientation,
            "column_mapping": json.loads(tmpl.column_mapping_json) if tmpl.column_mapping_json else {},
            "default_taxes": json.loads(tmpl.default_taxes_json) if tmpl.default_taxes_json else [],
            "times_used": tmpl.times_used
        }


def get_product_alias(raw_text: str, min_similarity: float = 0.80) -> Optional[Dict[str, Any]]:
    key = normalize_ocr_key(raw_text)
    if not key:
        return None

    with SessionLocal() as db:
        # 1. Búsqueda exacta
        match = db.query(InvoiceProductAlias).filter(InvoiceProductAlias.raw_ocr_pattern == key).first()
        if match:
            return {
                "id": match.id,
                "raw_ocr_pattern": match.raw_ocr_pattern,
                "canonical_name": match.canonical_name,
                "barcode": match.barcode,
                "product_id": match.product_id,
                "default_presentation": match.default_presentation,
                "default_units_per_pres": match.default_units_per_pres,
                "default_margin": match.default_margin,
                "times_seen": match.times_seen
            }

        # 2. Coincidencia difusa
        all_aliases = db.query(InvoiceProductAlias).order_by(InvoiceProductAlias.times_seen.desc()).all()
        best_match = None
        best_score = 0.0

        for alias in all_aliases:
            pat = alias.raw_ocr_pattern
            if not pat:
                continue

            ratio = difflib.SequenceMatcher(None, key, pat).ratio()
            if (len(pat) >= 6 and pat in key) or (len(key) >= 6 and key in pat):
                ratio = max(ratio, 0.88)

            if ratio > best_score and ratio >= min_similarity:
                best_score = ratio
                best_match = alias

        if best_match:
            return {
                "id": best_match.id,
                "raw_ocr_pattern": best_match.raw_ocr_pattern,
                "canonical_name": best_match.canonical_name,
                "barcode": best_match.barcode,
                "product_id": best_match.product_id,
                "default_presentation": best_match.default_presentation,
                "default_units_per_pres": best_match.default_units_per_pres,
                "default_margin": best_match.default_margin,
                "times_seen": best_match.times_seen,
                "_match_similarity": round(best_score, 3)
            }

    return None


def save_or_update_product_alias(
    raw_text: str,
    canonical_name: str,
    barcode: Optional[str] = None,
    default_presentation: str = "Und",
    default_units_per_pres: float = 1.0,
    default_margin: float = 30.0,
    product_id: Optional[int] = None
) -> Dict[str, Any]:
    key = normalize_ocr_key(raw_text)
    if not key:
        return {}

    now_ts = datetime.now(timezone.utc).isoformat()
    clean_barcode = str(barcode).strip() if barcode and str(barcode).strip() else None

    with SessionLocal() as db:
        if not product_id and clean_barcode:
            p_match = get_product_by_barcode(clean_barcode)
            if p_match:
                product_id = p_match["id"]

        alias = db.query(InvoiceProductAlias).filter(InvoiceProductAlias.raw_ocr_pattern == key).first()
        if alias:
            alias.canonical_name = canonical_name.strip()
            if clean_barcode:
                alias.barcode = clean_barcode
            if product_id:
                alias.product_id = product_id
            alias.default_presentation = default_presentation
            alias.default_units_per_pres = float(default_units_per_pres)
            alias.default_margin = float(default_margin)
            alias.times_seen = (alias.times_seen or 0) + 1
            alias.updated_at = now_ts
        else:
            alias = InvoiceProductAlias(
                raw_ocr_pattern=key,
                canonical_name=canonical_name.strip(),
                barcode=clean_barcode,
                product_id=product_id,
                default_presentation=default_presentation,
                default_units_per_pres=float(default_units_per_pres),
                default_margin=float(default_margin),
                times_seen=1,
                created_at=now_ts,
                updated_at=now_ts
            )
            db.add(alias)

        db.commit()
        db.refresh(alias)
        return {
            "id": alias.id,
            "raw_ocr_pattern": alias.raw_ocr_pattern,
            "canonical_name": alias.canonical_name,
            "barcode": alias.barcode,
            "product_id": alias.product_id,
            "default_presentation": alias.default_presentation,
            "default_units_per_pres": alias.default_units_per_pres,
            "default_margin": alias.default_margin,
            "times_seen": alias.times_seen
        }


def batch_learn_invoice_feedback(provider_data: Dict[str, Any], items_data: List[Dict[str, Any]]) -> Dict[str, int]:
    templates_updated = 0
    aliases_updated = 0

    p_name = provider_data.get("provider_name") or provider_data.get("proveedor")
    if p_name and p_name not in ("Proveedor Factura", "Proveedor Desconocido", "NO DETECTADO"):
        save_or_update_supplier_template(provider_data)
        templates_updated += 1

    for item in items_data:
        raw_desc = item.get("raw_description") or item.get("descripcion") or ""
        canon_name = item.get("canonical_name") or item.get("descripcion") or raw_desc
        code = item.get("barcode") or item.get("codigo")
        pres = item.get("presentation") or item.get("presentacion") or "Und"
        equiv = float(item.get("units_per_presentation") or item.get("unidades_por_presentacion") or 1.0)
        margin = float(item.get("margin") or item.get("margen_ganancia") or 30.0)

        if raw_desc.strip():
            save_or_update_product_alias(
                raw_text=raw_desc,
                canonical_name=canon_name,
                barcode=code,
                default_presentation=pres,
                default_units_per_pres=equiv,
                default_margin=margin
            )
            aliases_updated += 1

    return {"templates_updated": templates_updated, "aliases_updated": aliases_updated}


def get_learning_stats() -> Dict[str, Any]:
    with SessionLocal() as db:
        tmpl_count = db.query(func.count(InvoiceSupplierTemplate.id)).scalar() or 0
        alias_count = db.query(func.count(InvoiceProductAlias.id)).scalar() or 0
        top_tmpls = (
            db.query(InvoiceSupplierTemplate)
            .order_by(InvoiceSupplierTemplate.times_used.desc())
            .limit(10)
            .all()
        )
        top_providers = [
            {
                "provider_name_pattern": t.provider_name_pattern,
                "times_used": t.times_used,
                "default_orientation": t.default_orientation
            }
            for t in top_tmpls
        ]
        return {
            "templates_count": tmpl_count,
            "aliases_count": alias_count,
            "top_providers": top_providers
        }
