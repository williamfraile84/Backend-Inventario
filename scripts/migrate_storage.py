import sys
import os
import asyncio
import argparse
from pathlib import Path

# Agregar backend al path si se ejecuta directamente
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.storage_migration_service import StorageMigrationService
from app.core.config import settings


def main():
    parser = argparse.ArgumentParser(
        description="Sincronizador CLI de Archivos de Almacenamiento (Local <-> Supabase Storage / S3)"
    )
    parser.add_argument(
        "--source",
        type=str,
        default=None,
        help="Proveedor de origen ('local', 's3')"
    )
    parser.add_argument(
        "--target",
        type=str,
        default=None,
        help="Proveedor de destino ('local', 's3')"
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        default=False,
        help="Sobrescribir archivos si ya existen en el destino"
    )

    args = parser.parse_args()

    print("=" * 60)
    print("  SINCRONIZACIÓN DE ALMACENAMIENTO - INVENTARIO FRUVER")
    print("=" * 60)
    print(f"[*] Origen       : {args.source or getattr(settings, 'MIGRATE_STORAGE_SOURCE', 'local')}")
    print(f"[*] Destino      : {args.target or getattr(settings, 'MIGRATE_STORAGE_TARGET', settings.STORAGE_PROVIDER)}")
    print(f"[*] Sobrescribir : {args.overwrite}")
    print("-" * 60)

    res = asyncio.run(StorageMigrationService.migrate_storage(
        source_name=args.source,
        target_name=args.target,
        overwrite=args.overwrite
    ))

    if res.get("success"):
        st = res.get("stats", {})
        print("\n✅ SINCRONIZACIÓN COMPLETADA CON ÉXITO:")
        print(f"   -> Transferidos: {st.get('transferred', 0)}")
        print(f"   -> Omitidos    : {st.get('skipped', 0)}")
        print(f"   -> Fallidos    : {st.get('failed', 0)}")
        sys.exit(0)
    else:
        print("\n❌ FALLO EN LA SINCRONIZACIÓN:")
        print(f"Detalle: {res.get('error')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
