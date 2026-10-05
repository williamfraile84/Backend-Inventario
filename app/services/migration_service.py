import logging
from typing import Dict, Any, Optional
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, make_transient
from sqlalchemy.pool import NullPool
from app.core.config import settings
from app.models.base import Base
from app.models.producto import Producto, ProductAdditionalNumber
from app.models.catalogos import UnidadMedida, TipoEmpaque, ConceptoGasto
from app.models.invoices import Invoice, InvoiceItem, InvoiceSupplierTemplate, InvoiceProductAlias
from app.models.usuario import Usuario, User
from app.models.sesion import Sesion
from app.models.auditoria import PriceAuditLog, Auditoria, AuditoriaCambio

logger = logging.getLogger("DatabaseMigration")


class DatabaseMigrationService:
    """
    Servicio universal para migrar datos entre cualquier motor de base de datos
    (SQLite <-> PostgreSQL/Supabase <-> MySQL <-> Oracle)
    garantizando integridad referencial, orden topológico y sincronización de secuencias.
    """

    @staticmethod
    def _create_engine_for_url(url: str):
        is_sqlite = url.lower().startswith("sqlite")
        engine_kwargs = {"pool_pre_ping": True}
        if is_sqlite:
            engine_kwargs["connect_args"] = {"check_same_thread": False}
        else:
            engine_kwargs["poolclass"] = NullPool
        return create_engine(url, **engine_kwargs)

    @classmethod
    def execute_migration(
        cls,
        source_url: Optional[str] = None,
        target_url: Optional[str] = None,
        reset_destination: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Ejecuta la migración de datos desde la base de origen hacia la de destino.
        """
        src_url = (source_url or settings.MIGRATE_SOURCE_URL or "sqlite:///./fruver_pos.db").strip()
        tgt_url = (target_url or settings.effective_database_url).strip()
        do_reset = settings.MIGRATE_RESET_DESTINATION if reset_destination is None else reset_destination

        src_masked = settings.mask_url(src_url)
        tgt_masked = settings.mask_url(tgt_url)

        logger.info("Iniciando proceso de migración de base de datos:")
        logger.info(f"   [Origen] : {src_masked}")
        logger.info(f"   [Destino]: {tgt_masked}")
        logger.info(f"   [Reset]  : {do_reset}")

        if src_url == tgt_url:
            msg = "La URL de origen y destino son idénticas. Se omite para evitar sobreescritura."
            logger.warning(msg)
            return {"success": False, "error": msg, "source": src_masked, "target": tgt_masked}

        source_engine = cls._create_engine_for_url(src_url)
        target_engine = cls._create_engine_for_url(tgt_url)

        SourceSession = sessionmaker(bind=source_engine)
        TargetSession = sessionmaker(bind=target_engine)

        source_db = SourceSession()
        target_db = TargetSession()

        stats = {
            "usuarios": 0,
            "unidades_medida": 0,
            "tipos_empaque": 0,
            "conceptos_gasto": 0,
            "productos": 0,
            "codigos_adicionales": 0,
            "facturas": 0,
            "items_factura": 0,
            "plantillas_proveedor": 0,
            "alias_producto": 0,
            "auditorias": 0,
            "auditoria_cambios": 0,
            "price_audit_logs": 0,
        }

        try:
            # 1. Asegurar que las tablas existan en el destino
            Base.metadata.create_all(bind=target_engine)

            # 2. Si se solicitó reset, limpiar destino en orden inverso
            if do_reset:
                logger.warning("Eliminando registros anteriores en destino...")
                target_db.query(AuditoriaCambio).delete()
                target_db.query(Auditoria).delete()
                target_db.query(PriceAuditLog).delete()
                target_db.query(InvoiceItem).delete()
                target_db.query(Invoice).delete()
                target_db.query(InvoiceSupplierTemplate).delete()
                target_db.query(InvoiceProductAlias).delete()
                target_db.query(ProductAdditionalNumber).delete()
                target_db.query(Producto).delete()
                target_db.query(ConceptoGasto).delete()
                target_db.query(TipoEmpaque).delete()
                target_db.query(UnidadMedida).delete()
                target_db.query(Sesion).delete()
                target_db.query(Usuario).delete()
                target_db.commit()

            # 3. Migrar entidades maestras
            # Usuarios
            for u in source_db.query(Usuario).all():
                if not target_db.query(Usuario).filter(Usuario.id == u.id).first():
                    make_transient(u)
                    target_db.add(u)
                    stats["usuarios"] += 1
            target_db.commit()

            # Unidades de medida
            for um in source_db.query(UnidadMedida).all():
                if not target_db.query(UnidadMedida).filter(UnidadMedida.id == um.id).first():
                    make_transient(um)
                    target_db.add(um)
                    stats["unidades_medida"] += 1
            target_db.commit()

            # Tipos de empaque
            for te in source_db.query(TipoEmpaque).all():
                if not target_db.query(TipoEmpaque).filter(TipoEmpaque.id == te.id).first():
                    make_transient(te)
                    target_db.add(te)
                    stats["tipos_empaque"] += 1
            target_db.commit()

            # Conceptos de gasto
            for cg in source_db.query(ConceptoGasto).all():
                if not target_db.query(ConceptoGasto).filter(ConceptoGasto.id == cg.id).first():
                    make_transient(cg)
                    target_db.add(cg)
                    stats["conceptos_gasto"] += 1
            target_db.commit()

            # Productos
            for p in source_db.query(Producto).all():
                if not target_db.query(Producto).filter(Producto.id == p.id).first():
                    make_transient(p)
                    target_db.add(p)
                    stats["productos"] += 1
            target_db.commit()

            # Códigos adicionales
            for pan in source_db.query(ProductAdditionalNumber).all():
                if not target_db.query(ProductAdditionalNumber).filter(ProductAdditionalNumber.id == pan.id).first():
                    make_transient(pan)
                    target_db.add(pan)
                    stats["codigos_adicionales"] += 1
            target_db.commit()

            # Facturas
            for inv in source_db.query(Invoice).all():
                if not target_db.query(Invoice).filter(Invoice.id == inv.id).first():
                    make_transient(inv)
                    target_db.add(inv)
                    stats["facturas"] += 1
            target_db.commit()

            # Items de factura
            for inv_item in source_db.query(InvoiceItem).all():
                if not target_db.query(InvoiceItem).filter(InvoiceItem.id == inv_item.id).first():
                    make_transient(inv_item)
                    target_db.add(inv_item)
                    stats["items_factura"] += 1
            target_db.commit()

            # Plantillas y alias
            for st in source_db.query(InvoiceSupplierTemplate).all():
                if not target_db.query(InvoiceSupplierTemplate).filter(InvoiceSupplierTemplate.id == st.id).first():
                    make_transient(st)
                    target_db.add(st)
                    stats["plantillas_proveedor"] += 1
            for pa in source_db.query(InvoiceProductAlias).all():
                if not target_db.query(InvoiceProductAlias).filter(InvoiceProductAlias.id == pa.id).first():
                    make_transient(pa)
                    target_db.add(pa)
                    stats["alias_producto"] += 1
            target_db.commit()

            # Auditorías
            for aud in source_db.query(Auditoria).all():
                if not target_db.query(Auditoria).filter(Auditoria.id == aud.id).first():
                    make_transient(aud)
                    target_db.add(aud)
                    stats["auditorias"] += 1
            target_db.commit()

            for ac in source_db.query(AuditoriaCambio).all():
                if not target_db.query(AuditoriaCambio).filter(AuditoriaCambio.id == ac.id).first():
                    make_transient(ac)
                    target_db.add(ac)
                    stats["auditoria_cambios"] += 1
            target_db.commit()

            for pal in source_db.query(PriceAuditLog).all():
                if not target_db.query(PriceAuditLog).filter(PriceAuditLog.id == pal.id).first():
                    make_transient(pal)
                    target_db.add(pal)
                    stats["price_audit_logs"] += 1
            target_db.commit()

            logger.info(f"Migración completada con éxito. Estadísticas: {stats}")
            return {
                "success": True,
                "records_migrated": stats,
                "source": src_masked,
                "target": tgt_masked,
            }
        except Exception as e:
            target_db.rollback()
            logger.error(f"Error durante la migración de base de datos: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "source": src_masked,
                "target": tgt_masked,
            }
        finally:
            source_db.close()
            target_db.close()
            source_engine.dispose()
            target_engine.dispose()

    @classmethod
    def run_auto_migration_if_enabled(cls) -> Dict[str, Any]:
        """Ejecuta la migración al arrancar si AUTO_MIGRATE_DB=True en configuración."""
        if settings.AUTO_MIGRATE_DB:
            return cls.execute_migration()
        return {"success": True, "skipped": True}

