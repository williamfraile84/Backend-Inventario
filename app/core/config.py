import os
import urllib.parse
from pathlib import Path
from typing import Optional, List, Union
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ENV_FILE_PATH = str(BASE_DIR / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=[ENV_FILE_PATH, ".env"],
        env_file_encoding="utf-8",
        extra="allow"
    )

    BASE_DIR: Path = BASE_DIR
    APP_NAME: str = "Fruver POS Manager"
    APP_ENV: str = "development"
    API_V1_PREFIX: str = "/api"
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8000

    # ==========================================
    # Base de Datos Agnóstica (PostgreSQL/Supabase, SQLite, MySQL, Oracle)
    # ==========================================
    DATABASE_URL: Optional[str] = None
    DB_ENGINE: str = "sqlite"
    DB_USER: Optional[str] = None
    DB_PASSWORD: Optional[str] = None
    DB_HOST: Optional[str] = None
    DB_PORT: Optional[int] = None
    DB_NAME: Optional[str] = None
    DB_SSLMODE: Optional[str] = None
    DB_USE_NULLPOOL: bool = False

    # Parámetros de Migración de Base de Datos
    AUTO_MIGRATE_DB: bool = False
    MIGRATE_SOURCE_URL: Optional[str] = None
    MIGRATE_TARGET_URL: Optional[str] = None
    MIGRATE_RESET_DESTINATION: bool = False

    # ==========================================
    # Almacenamiento Multi-Proveedor (Local, S3 / Supabase Storage, Cloudflare R2, MinIO)
    # ==========================================
    STORAGE_PROVIDER: str = "local"
    STORAGE_DIR: Path = BASE_DIR / "storage"
    S3_ENDPOINT_URL: Optional[str] = None
    S3_REGION_NAME: str = "us-east-1"
    S3_ACCESS_KEY_ID: Optional[str] = None
    S3_SECRET_ACCESS_KEY: Optional[str] = None
    S3_BUCKET_NAME: str = "fruver-pos-storage"
    S3_USE_SSL: bool = True
    S3_PUBLIC_URL_PREFIX: Optional[str] = None
    S3_PRESIGNED_EXPIRATION_SECONDS: int = 3600

    # Control de Migración y Sincronización de Archivos
    AUTO_MIGRATE_STORAGE: bool = False
    MIGRATE_STORAGE_SOURCE: str = "local"
    MIGRATE_STORAGE_TARGET: Optional[str] = None
    MIGRATE_STORAGE_OVERWRITE: bool = False

    # ==========================================
    # Seguridad, JWT, CORS y Sesiones
    # ==========================================
    SECRET_KEY: str = "fruver-pos-super-secret-key-change-in-production-2026!"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    SESSION_INACTIVITY_TIMEOUT_MINUTES: Optional[int] = 480
    ENFORCE_DB_SESSION_VALIDATION: bool = False
    CORS_ORIGINS: List[str] = ["*"]

    # Administrador Inicial de la App
    APP_ADMIN_USER: str = "admin"
    APP_ADMIN_PASS: str = "fruver2026"

    # ==========================================
    # Integración con Sistema POS Externo (csopos.co / softwarepos.online)
    # ==========================================
    POS_PRIMARY_URL: str = "https://csopos.co/"
    POS_SECONDARY_URL: str = "https://softwarepos.online/"
    POS_USER: str = "caja1"
    POS_PASS: str = "pass"
    POS_STORE: str = "yanuba"
    POS_REGISTER_NUM: int = 1
    POS_HEADLESS: bool = True

    # ==========================================
    # Reglas de Negocio Fruver (Precios, Margen % y Redondeo a Centenas $100)
    # ==========================================
    DEFAULT_PROFIT_MARGIN: float = 30.0
    ROUNDING_BASE: int = 100

    # ==========================================
    # Archivos y Visión / OCR (Preparado para la Nube)
    # ==========================================
    MAX_UPLOAD_SIZE_MB: int = 15
    VISION_PROVIDER: str = "auto"
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.8-flash"
    OCR_MAX_IMAGE_DIMENSION: int = 1600
    OCR_USE_SERVER_MODEL: bool = False
    OCR_REC_MODEL_PATH: Optional[str] = None
    OCR_USE_RAPID_TABLE: bool = True
    OCR_INTRA_OP_THREADS: int = 4
    OCR_INTER_OP_THREADS: int = 1
    OCR_REC_BATCH_NUM: int = 16
    OCR_ENABLE_PERSPECTIVE_WARP: bool = True
    CONFIDENCE_FALLBACK_THRESHOLD: float = 0.70
    DIAN_RECEPTOR_NIT: str = "40327379"
    DIAN_PORTAL_TIMEOUT_SECONDS: int = 25
    DIAN_HEADLESS_BROWSER: bool = False
    DIAN_BROWSER_CHANNEL: str = "chrome"

    @property
    def effective_database_url(self) -> str:
        """Construye la URL SQLAlchemy según configuración activa."""
        if self.DATABASE_URL and self.DATABASE_URL.strip():
            url = self.DATABASE_URL.strip()
            if url.startswith("postgres://"):
                url = "postgresql+psycopg2://" + url[len("postgres://"):]
            elif url.startswith("postgresql://") and not url.startswith("postgresql+"):
                url = "postgresql+psycopg2://" + url[len("postgresql://"):]
            return url

        engine_clean = (self.DB_ENGINE or "sqlite").lower().strip()

        if engine_clean == "sqlite":
            db_file = self.DB_NAME or str(self.BASE_DIR / "fruver_pos.db")
            return f"sqlite:///{db_file}"

        user_enc = urllib.parse.quote_plus(self.DB_USER) if self.DB_USER else ""
        pass_enc = urllib.parse.quote_plus(self.DB_PASSWORD) if self.DB_PASSWORD else ""
        auth = f"{user_enc}:{pass_enc}@" if (user_enc or pass_enc) else ""
        host = self.DB_HOST or "localhost"

        if engine_clean in ("postgresql", "postgres", "supabase"):
            port = self.DB_PORT or 5432
            db = self.DB_NAME or "postgres"
            query_params = []
            if self.DB_SSLMODE:
                query_params.append(f"sslmode={self.DB_SSLMODE}")
            query = f"?{'&'.join(query_params)}" if query_params else ""
            return f"postgresql+psycopg2://{auth}{host}:{port}/{db}{query}"

        if engine_clean in ("mysql", "mariadb"):
            port = self.DB_PORT or 3306
            db = self.DB_NAME or "fruver_db"
            return f"mysql+pymysql://{auth}{host}:{port}/{db}"

        if engine_clean == "oracle":
            port = self.DB_PORT or 1521
            db = self.DB_NAME or "ORCL"
            return f"oracle+oracledb://{auth}{host}:{port}/?service_name={db}"

        port_str = f":{self.DB_PORT}" if self.DB_PORT else ""
        db_str = f"/{self.DB_NAME}" if self.DB_NAME else ""
        return f"{engine_clean}://{auth}{host}{port_str}{db_str}"

    def mask_database_url(self) -> str:
        """Enmascara la contraseña de la base de datos para logs seguros."""
        return self.mask_url(self.effective_database_url)

    @staticmethod
    def mask_url(url: str) -> str:
        """Enmascara cualquier URL que contenga usuario:contraseña."""
        if not url:
            return ""
        if "@" in url and "://" in url:
            prefix, rest = url.split("://", 1)
            auth, host_part = rest.split("@", 1)
            if ":" in auth:
                user, _ = auth.split(":", 1)
                return f"{prefix}://{user}:******@{host_part}"
            return f"{prefix}://******@{host_part}"
        return url


settings = Settings()

# Asegurar directorios de almacenamiento en modo local
if settings.STORAGE_PROVIDER == "local":
    os.makedirs(str(settings.STORAGE_DIR / "invoices"), exist_ok=True)
    os.makedirs(str(settings.STORAGE_DIR / "exports"), exist_ok=True)
    os.makedirs(str(settings.STORAGE_DIR / "temp"), exist_ok=True)
    os.makedirs(str(BASE_DIR / "models_ocr"), exist_ok=True)
