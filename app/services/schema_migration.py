"""
Servicio de Migración Automática y Evolución Segura de Esquema SQLite.
Permite actualizar bases de datos existentes sin pérdida de información,
realizando backups automáticos y agregando columnas/tablas faltantes con ALTER TABLE.
"""

import os
import shutil
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

from sqlalchemy import inspect, text
from app.core.config import settings
from app.core.database import engine, Base, DB_PATH
import app.models  # Asegura el registro de todos los modelos relacionales

logger = logging.getLogger("SchemaMigration")


def create_database_backup() -> str:
    """
    Crea una copia de seguridad timestamped de la base de datos SQLite antes de cualquier cambio.
    Retorna la ruta absoluta del archivo de respaldo creado.
    """
    db_file = DB_PATH
    if not db_file.exists():
        logger.info(f"Base de datos {db_file} no existe aún. Omitiendo backup preliminar.")
        return ""

    backup_dir = db_file.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"fruver_pos_backup_{timestamp}.db"

    try:
        # Copia atómica usando la API de backup de SQLite para garantizar consistencia incluso si hay locks WAL
        source_conn = sqlite3.connect(str(db_file))
        dest_conn = sqlite3.connect(str(backup_path))
        with dest_conn:
            source_conn.backup(dest_conn)
        dest_conn.close()
        source_conn.close()
        logger.info(f"✅ Copia de seguridad creada con éxito en: {backup_path}")
        return str(backup_path)
    except Exception as e:
        logger.warning(f"Aviso durante backup SQLite nativo: {e}. Intentando copia directa de archivo...")
        try:
            shutil.copy2(db_file, backup_path)
            logger.info(f"✅ Copia directa de seguridad creada en: {backup_path}")
            return str(backup_path)
        except Exception as copy_err:
            logger.error(f"Error crítico al generar backup: {copy_err}")
            return ""


def get_sqlite_column_type(sa_column) -> str:
    """Mapea el tipo de columna SQLAlchemy a su equivalente seguro en SQLite."""
    type_name = str(sa_column.type).upper()
    if "INT" in type_name:
        return "INTEGER"
    elif "FLOAT" in type_name or "NUMERIC" in type_name or "DECIMAL" in type_name or "REAL" in type_name:
        return "REAL"
    elif "BOOL" in type_name:
        return "BOOLEAN"
    else:
        return "TEXT"


def auto_upgrade_schema() -> Dict[str, Any]:
    """
    Inspecciona todas las tablas del modelo contra la base de datos física actual.
    Crea tablas faltantes y agrega columnas nuevas a tablas existentes sin borrar datos.
    """
    is_sqlite = engine.dialect.name.lower() == "sqlite"
    logger.info(f"Iniciando verificación y actualización de esquema [{engine.dialect.name.upper()}]...")

    # 1. Crear backup si es SQLite
    backup_file = ""
    if is_sqlite:
        backup_file = create_database_backup()

    # 2. Crear tablas que no existan aún
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()
    added_columns_count = 0
    migrations_applied: List[str] = []

    if is_sqlite:
        with engine.connect() as conn:
            for table_name, table_obj in Base.metadata.tables.items():
                if table_name not in existing_tables:
                    continue

                # Obtener columnas físicas existentes en la tabla
                cursor = conn.execute(text(f"PRAGMA table_info({table_name})"))
                physical_columns = {row[1] for row in cursor.fetchall()}

                # Comparar con el modelo SQLAlchemy
                for col in table_obj.columns:
                    if col.name not in physical_columns:
                        col_type = get_sqlite_column_type(col)
                        default_clause = ""

                        if col.default is not None:
                            if hasattr(col.default, "arg"):
                                val = col.default.arg
                                if isinstance(val, (int, float)):
                                    default_clause = f" DEFAULT {val}"
                                elif isinstance(val, bool):
                                    default_clause = f" DEFAULT {1 if val else 0}"
                                elif isinstance(val, str):
                                    default_clause = f" DEFAULT '{val}'"
                        elif col.nullable:
                            default_clause = " DEFAULT NULL"

                        alter_query = f"ALTER TABLE {table_name} ADD COLUMN {col.name} {col_type}{default_clause}"
                        logger.info(f"🔧 Aplicando migración: {alter_query}")
                        try:
                            conn.execute(text(alter_query))
                            conn.commit()
                            added_columns_count += 1
                            migrations_applied.append(f"{table_name}.{col.name}")
                        except Exception as alter_err:
                            logger.error(f"Error agregando columna {table_name}.{col.name}: {alter_err}")

            # 3. Validar integridad de la base de datos
            try:
                check_result = conn.execute(text("PRAGMA integrity_check")).scalar()
                logger.info(f"Integridad SQLite: {check_result}")
            except Exception as e:
                logger.warning(f"Aviso en integrity check: {e}")

    summary = {
        "success": True,
        "backup_path": backup_file,
        "columns_added": added_columns_count,
        "migrations_applied": migrations_applied,
        "tables_verified": len(existing_tables)
    }

    logger.info(f"✅ Esquema verificado con éxito. Columnas nuevas añadidas: {added_columns_count}")
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    print("======================================================================")
    print("        FRUVER POS - MIGRACIÓN Y EVOLUCIÓN DE ESQUEMA SQLITE          ")
    print("======================================================================")
    result = auto_upgrade_schema()
    print(f"Estado: {'ÉXITO' if result['success'] else 'FALLO'}")
    if result.get("backup_path"):
        print(f"Backup creado: {result['backup_path']}")
    print(f"Columnas añadidas: {result['columns_added']}")
    if result.get("migrations_applied"):
        print(f"Detalle: {', '.join(result['migrations_applied'])}")
    print("======================================================================")
