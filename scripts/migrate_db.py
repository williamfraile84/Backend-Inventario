import sys
import os
import argparse
from pathlib import Path

# Agregar backend al path si se ejecuta directamente
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.migration_service import DatabaseMigrationService
from app.core.config import settings


def main():
    parser = argparse.ArgumentParser(
        description="Herramienta CLI para migrar datos de Inventario Fruver entre bases de datos (SQLite <-> PostgreSQL Supabase)"
    )
    parser.add_argument(
        "--source",
        type=str,
        default=None,
        help="URL de la base de datos de origen (ej: sqlite:///./fruver_pos.db)"
    )
    parser.add_argument(
        "--target",
        type=str,
        default=None,
        help="URL de destino (por defecto toma la configuración activa en .env)"
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        default=False,
        help="Limpiar las tablas de destino antes de insertar los registros migrados"
    )

    args = parser.parse_args()

    print("=" * 60)
    print("  MIGRACIÓN UNIVERSAL DE BASE DE DATOS - INVENTARIO FRUVER")
    print("=" * 60)
    
    src = args.source or settings.MIGRATE_SOURCE_URL or "sqlite:///./fruver_pos.db"
    tgt = args.target or settings.effective_database_url

    print(f"[*] Origen : {settings.mask_url(src)}")
    print(f"[*] Destino: {settings.mask_url(tgt)}")
    print(f"[*] Reset  : {args.reset}")
    print("-" * 60)

    res = DatabaseMigrationService.execute_migration(
        source_url=src,
        target_url=tgt,
        reset_destination=args.reset
    )

    if res.get("success"):
        print("\n✅ MIGRACIÓN COMPLETADA CON ÉXITO:")
        for tbl, count in res.get("records_migrated", {}).items():
            print(f"   -> {tbl}: {count} registros")
        sys.exit(0)
    else:
        print("\n❌ FALLO EN LA MIGRACIÓN:")
        print(f"Detalle: {res.get('error')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
