import sys
import os
import time
import asyncio
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Any
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.core.config import settings
from app.core.logging_config import setup_logging
from app.core.database import init_db, engine, SessionLocal
from app.core.security import get_file_media_type, get_current_user
from app.services.pos_service import POSService
from app.services.migration_service import DatabaseMigrationService
from app.services.storage_migration_service import StorageMigrationService
from app.services.storage.storage_factory import get_storage_provider
from app.routers import auth, products, admin, catalog, invoices, auditoria, empresa, roles
from app.routers.empresa import seed_default_empresa_if_empty

logger = setup_logging()


def warmup_vision_engine():
    """Pre-calienta el motor de visión e IA (Google Gemini / XML / DIAN) en segundo plano para evitar latencia."""
    try:
        from app.services.invoice_parser_service import VisionEngineFactory
        engine_inst = VisionEngineFactory.get_engine(is_xml=False, provider=settings.VISION_PROVIDER)
        logger.info(f"Pre-calentamiento de motor de visión ({getattr(engine_inst, 'engine_name', 'Vision')}) completado.")
    except Exception as e:
        logger.warning(f"Aviso durante el pre-calentamiento del motor de visión: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Startup: Inicializar BD persistente con reintentos para resiliencia ante poolers (Supabase)
    logger.info("Iniciando base de datos persistente y directorios de almacenamiento...")
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            init_db()
            os.makedirs(str(settings.STORAGE_DIR), exist_ok=True)
            logger.info(f"Base de datos conectada exitosamente: [{engine.dialect.name.upper()}] {settings.mask_database_url()}")
            break
        except Exception as db_err:
            if attempt < max_retries:
                logger.warning(f"Intento {attempt}/{max_retries} al inicializar BD falló ({db_err}). Reintentando en 2s...")
                time.sleep(2)
            else:
                logger.error(f"Error crítico al conectar a base de datos tras {max_retries} intentos: {db_err}")

    # 2. Migración automática opcional entre motores de BD (si AUTO_MIGRATE_DB=True)
    if settings.AUTO_MIGRATE_DB:
        logger.info("Ejecutando migración automática de base de datos (AUTO_MIGRATE_DB=True)...")
        migration_res = DatabaseMigrationService.run_auto_migration_if_enabled()
        if migration_res.get("success"):
            logger.info(f"Migración completada: {migration_res.get('records_migrated')}")
        else:
            logger.error(f"Fallo en migración automática: {migration_res.get('error')}")

    # 3. Migración automática opcional de Storage (si AUTO_MIGRATE_STORAGE=True)
    if getattr(settings, "AUTO_MIGRATE_STORAGE", False):
        logger.info("Ejecutando sincronización automática de almacenamiento (AUTO_MIGRATE_STORAGE=True)...")
        storage_res = StorageMigrationService.run_auto_storage_migration_if_enabled()
        if storage_res.get("success"):
            logger.info(f"Migración de Storage completada: {storage_res.get('stats')}")
        else:
            logger.error(f"Fallo en sincronización de Storage: {storage_res.get('error')}")

    # 4. Asegurar empresa por defecto si está vacía
    try:
        with SessionLocal() as db:
            seed_default_empresa_if_empty(db)
    except Exception as emp_err:
        logger.warning(f"Aviso verificando empresa inicial: {emp_err}")

    # 5. Pre-calentamiento del motor de visión e IA en segundo plano sin bloquear arranque
    is_testing = os.getenv("TESTING", "").lower() in ("1", "true") or settings.APP_ENV == "testing"
    if not is_testing:
        threading.Thread(target=warmup_vision_engine, daemon=True).start()

    # 6. Conexión de integración con el sistema POS externo en segundo plano
    pos_service = None
    if not is_testing:
        logger.info("Iniciando conexión con el sistema POS en background...")
        pos_service = POSService.get_instance()
        try:
            asyncio.create_task(pos_service.initialize())
        except Exception as e:
            logger.error(f"Advertencia inicializando POS Service: {e}")

    logger.info(f"Inventario Fruver Backend listo.")
    logger.info(f"Base de Datos conectada: [{engine.dialect.name.upper()}] {settings.mask_database_url()}")
    logger.info(f"Storage de Archivos: [{settings.STORAGE_PROVIDER.upper()}]")
    yield

    # Shutdown: Cerrar sesiones y liberar recursos
    if pos_service:
        logger.info("Cerrando sesión POS y liberando recursos...")
        try:
            await pos_service.close()
        except Exception as e:
            logger.error(f"Error al cerrar POS Service: {e}")


app = FastAPI(
    title=settings.APP_NAME,
    version="2.6.0",
    description="API para gestión de inventario, consulta omnidireccional de productos, edición de precios con reglas comerciales (%), ingesta inteligente de facturas DIAN (QR, XML, Gemini Vision) y sincronización con POS.",
    lifespan=lifespan
)

# Compresión GZIP automática para respuestas JSON y assets estáticos > 1KB
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Middleware de Cabeceras de Seguridad y Protección de Recursos
@app.middleware("http")
async def add_security_and_performance_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(self), microphone=()"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response

# Configuración CORS permisiva / configurable por variable de entorno
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inclusión de rutas API desacopladas con soporte unificado (/api y /api/v1)
all_routers = [
    auth.router,
    products.router,
    catalog.router,
    invoices.router,
    admin.router,
    auditoria.router,
    empresa.router,
    roles.router,
]

for r in all_routers:
    app.include_router(r, prefix="/api")
    app.include_router(r, prefix="/api/v1", include_in_schema=False)


@app.get("/health", tags=["Salud"])
@app.get("/api/health", include_in_schema=False)
@app.get("/api/v1/health", include_in_schema=False)
async def health_check():
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "env": settings.APP_ENV,
        "version": "2.6.0",
        "db_engine": engine.dialect.name,
        "default_margin": settings.DEFAULT_PROFIT_MARGIN,
        "rounding_base": settings.ROUNDING_BASE,
        "storage_provider": settings.STORAGE_PROVIDER
    }


@app.get("/api/storage/{file_path:path}", tags=["Almacenamiento"])
@app.get("/api/v1/storage/{file_path:path}", include_in_schema=False)
async def serve_storage_file(
    file_path: str,
    download: bool = False,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Ruta universal para servir archivos desde cualquier proveedor
    (Local, Supabase Storage / S3) con streaming y validación de seguridad estricta.
    """
    clean_path = file_path.replace("\\", "/").lstrip("/")
    if ".." in clean_path or "\x00" in clean_path or clean_path.startswith("/"):
        raise HTTPException(status_code=403, detail="Acceso denegado: ruta de archivo no autorizada.")

    provider = get_storage_provider()

    # Si existe en el proveedor activo (S3 / Supabase / etc.), servirlo
    if await provider.file_exists(clean_path):
        return provider.get_response(clean_path, inline=not download)

    # Fallback a disco local si aún no se ha sincronizado a la nube con protección Path Traversal
    local_path = os.path.normpath(os.path.join(str(settings.STORAGE_DIR), clean_path))
    abs_base = os.path.abspath(str(settings.STORAGE_DIR))
    abs_local = os.path.abspath(local_path)
    if not abs_local.startswith(abs_base):
        raise HTTPException(status_code=403, detail="Acceso denegado: ruta de archivo no autorizada.")

    if os.path.isfile(local_path):
        media_type = get_file_media_type(local_path)
        filename = os.path.basename(local_path)
        disposition = "inline" if not download else "attachment"
        return FileResponse(local_path, media_type=media_type, headers={"Content-Disposition": f'{disposition}; filename="{filename}"'})

    raise HTTPException(status_code=404, detail="Archivo no encontrado en almacenamiento.")


# Servir Frontend SPA (Single Page Application) con fallback para rutas cliente (/catalogo, /admin, etc.)
env_dist = os.getenv("FRONTEND_DIST_DIR")
possible_dist_dirs = [
    Path(env_dist) if env_dist else None,
    Path(__file__).resolve().parent.parent.parent / "frontend" / "dist",
    Path(__file__).resolve().parent.parent / "frontend" / "dist",
    Path(__file__).resolve().parent.parent / "dist",
    Path(__file__).resolve().parent.parent / "static",
]
frontend_dist = next((p for p in possible_dist_dirs if p and (p / "index.html").is_file()), None)

if frontend_dist and frontend_dist.exists():
    assets_dir = frontend_dist / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa_frontend(request: Request, full_path: str):
        # Omitir rutas de API, docs o esquemas que deban ser 404 si no existen
        if (
            full_path.startswith("api/")
            or full_path == "api"
            or full_path.startswith("docs")
            or full_path.startswith("openapi")
            or full_path.startswith("health")
        ):
            raise HTTPException(status_code=404, detail="Not Found")

        req_origin = request.headers.get("origin")
        cors_headers = {
            "Access-Control-Allow-Origin": req_origin if req_origin else "*",
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        }

        # Si el archivo exacto existe en dist (ej. favicon.ico, manifest.json, sw.js)
        target_file = frontend_dist / full_path
        if full_path and target_file.is_file():
            # Archivos de control y shell que NUNCA deben cachearse para forzar actualizaciones inmediatas
            if full_path == "sw.js":
                return FileResponse(
                    target_file,
                    media_type="application/javascript",
                    headers={
                        "Cache-Control": "no-cache, no-store, must-revalidate",
                        "Pragma": "no-cache",
                        "Expires": "0",
                        "Service-Worker-Allowed": "/",
                        **cors_headers,
                    },
                )
            if full_path in ("manifest.json", "manifest.webmanifest"):
                return FileResponse(
                    target_file,
                    media_type="application/manifest+json",
                    headers={
                        "Cache-Control": "no-cache, no-store, must-revalidate",
                        "Pragma": "no-cache",
                        "Expires": "0",
                        **cors_headers,
                    },
                )
            if full_path == "index.html" or full_path.endswith(".html"):
                return FileResponse(
                    target_file,
                    media_type="text/html",
                    headers={
                        "Cache-Control": "no-cache, no-store, must-revalidate",
                        "Pragma": "no-cache",
                        "Expires": "0",
                        **cors_headers,
                    },
                )
            return FileResponse(target_file, headers=cors_headers)

        # Fallback a index.html para react-router con no-cache estricto para evitar bloqueos de versiones antiguas
        index_file = frontend_dist / "index.html"
        if index_file.is_file():
            return FileResponse(
                index_file,
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )

        raise HTTPException(status_code=404, detail="Frontend index.html no disponible")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.SERVER_HOST, port=settings.SERVER_PORT, reload=True)
