import sys
import os
from pathlib import Path

# Configurar stdout UTF-8 seguro para terminales Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Agregar backend al path si se ejecuta directamente
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.core.config import settings
from app.core.database import engine, Base
import app.models


def mask_url(url: str) -> str:
    """Oculta la contraseña en la URL para impresión segura."""
    if "@" in url and "://" in url:
        prefix, rest = url.split("://", 1)
        auth, host = rest.split("@", 1)
        if ":" in auth:
            user, _ = auth.split(":", 1)
            return f"{prefix}://{user}:******@{host}"
        return f"{prefix}://******@{host}"
    return url


def test_connection():
    masked = mask_url(settings.effective_database_url)
    print("=" * 60)
    print("  VERIFICADOR DE CONEXION A BASE DE DATOS - INVENTARIO FRUVER")
    print("=" * 60)
    print(f"[*] URL Objetivo: {masked}")
    print(f"[*] Motor / Dialecto: {engine.dialect.name}")
    print(f"[*] NullPool activo: {settings.DB_USE_NULLPOOL}")
    print("-" * 60)

    try:
        print("[1/3] Intentando conectar con el servidor...")
        with engine.connect() as conn:
            print("   -> Conexión establecida exitosamente.")
            
            print("[2/3] Ejecutando consulta de prueba...")
            if engine.dialect.name == "sqlite":
                res = conn.execute(text("SELECT sqlite_version();")).scalar()
                print(f"   -> SQLite Version: {res}")
            elif engine.dialect.name == "postgresql":
                res = conn.execute(text("SELECT version();")).scalar()
                print(f"   -> PostgreSQL Version: {res[:60]}...")
            elif engine.dialect.name == "mysql":
                res = conn.execute(text("SELECT VERSION();")).scalar()
                print(f"   -> MySQL Version: {res}")
            else:
                conn.execute(text("SELECT 1;"))
                print("   -> Query 'SELECT 1' ejecutada con éxito.")

        print("[3/3] Sincronizando/Verificando tablas del modelo...")
        Base.metadata.create_all(bind=engine)
        print("   -> Tablas verificadas exitosamente:")
        for table_name in Base.metadata.tables.keys():
            print(f"      - {table_name}")

        print("=" * 60)
        print("[OK] CONEXION Y MODELO DE BASE DE DATOS OPERACIONALES 100% OK")
        print("=" * 60)
        return True

    except Exception as e:
        print("\n" + "!" * 60)
        print("[ERROR] AL CONECTAR CON LA BASE DE DATOS:")
        print(f"Detalle: {e}")
        print("!" * 60)
        return False


if __name__ == "__main__":
    success = test_connection()
    sys.exit(0 if success else 1)
