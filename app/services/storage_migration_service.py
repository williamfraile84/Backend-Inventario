import logging
import asyncio
from typing import Optional, List, Dict, Any
from app.core.config import settings
from app.core.security import get_file_media_type
from app.services.storage.storage_factory import create_storage_provider, get_storage_provider

logger = logging.getLogger(__name__)


class StorageMigrationService:
    """
    Servicio universal para migrar y sincronizar archivos físicos
    entre proveedores de almacenamiento (Local <-> Supabase Storage / S3).
    """

    DEFAULT_FOLDERS = ["invoices", "exports", "temp"]

    @classmethod
    async def migrate_storage(
        cls,
        source_name: Optional[str] = None,
        target_name: Optional[str] = None,
        overwrite: Optional[bool] = None,
        folders: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        src_name = (source_name or getattr(settings, "MIGRATE_STORAGE_SOURCE", "local")).lower().strip()
        tgt_name = (target_name or getattr(settings, "MIGRATE_STORAGE_TARGET", settings.STORAGE_PROVIDER)).lower().strip()
        do_overwrite = getattr(settings, "MIGRATE_STORAGE_OVERWRITE", False) if overwrite is None else overwrite
        target_folders = folders or cls.DEFAULT_FOLDERS

        logger.info("Iniciando sincronización de almacenamiento de archivos:")
        logger.info(f"   [Origen]     : {src_name.upper()}")
        logger.info(f"   [Destino]    : {tgt_name.upper()}")
        logger.info(f"   [Sobrescribir]: {do_overwrite}")

        if src_name == tgt_name:
            msg = f"El proveedor de origen y destino son el mismo ({src_name}). Se omite la sincronización."
            logger.warning(msg)
            return {"success": False, "error": msg, "source": src_name, "target": tgt_name}

        try:
            source_provider = create_storage_provider(src_name)
            target_provider = create_storage_provider(tgt_name)
        except Exception as e:
            logger.error(f"Error inicializando proveedores de almacenamiento: {e}")
            return {"success": False, "error": str(e), "source": src_name, "target": tgt_name}

        if hasattr(target_provider, "ensure_bucket_exists"):
            target_provider.ensure_bucket_exists()

        stats = {
            "transferred": 0,
            "skipped": 0,
            "failed": 0,
            "details": [],
        }

        files_to_migrate: List[str] = []
        for folder in target_folders:
            if hasattr(source_provider, "list_all_files"):
                folder_files = source_provider.list_all_files(folder)
                files_to_migrate.extend(folder_files)

        logger.info(f"Archivos identificados para sincronización: {len(files_to_migrate)}")

        for rel_path in files_to_migrate:
            clean_path = rel_path.replace("\\", "/").lstrip("/")
            try:
                if not do_overwrite and await target_provider.file_exists(clean_path):
                    stats["skipped"] += 1
                    continue

                file_bytes = await source_provider.get_file_bytes(clean_path)
                if file_bytes is None:
                    continue
                media_type = get_file_media_type(clean_path)

                await target_provider.save_file(clean_path, file_bytes, content_type=media_type)
                stats["transferred"] += 1
                logger.info(f"   -> Sincronizado: {clean_path} ({len(file_bytes)} bytes)")
            except Exception as item_err:
                stats["failed"] += 1
                err_msg = f"Fallo al transferir '{clean_path}': {item_err}"
                logger.error(err_msg)
                stats["details"].append(err_msg)

        logger.info(f"Sincronización finalizada: {stats['transferred']} copiados, {stats['skipped']} omitidos, {stats['failed']} fallidos.")

        return {
            "success": stats["failed"] == 0,
            "source": src_name,
            "target": tgt_name,
            "stats": stats,
        }

    @classmethod
    def run_auto_storage_migration_if_enabled(cls) -> Dict[str, Any]:
        """Ejecuta la migración de storage si AUTO_MIGRATE_STORAGE está activo."""
        auto_migrate = getattr(settings, "AUTO_MIGRATE_STORAGE", False)
        if not auto_migrate:
            return {"executed": False, "reason": "AUTO_MIGRATE_STORAGE está desactivado"}

        logger.info("AUTO_MIGRATE_STORAGE=True detectado. Ejecutando migración de almacenamiento...")
        return asyncio.run(cls.migrate_storage())
