from typing import Optional
from app.core.config import settings
from app.services.storage.base import BaseStorageProvider
from app.services.storage.local_provider import LocalStorageProvider
from app.services.storage.s3_provider import S3StorageProvider

_storage_provider_instance: Optional[BaseStorageProvider] = None

def create_storage_provider(provider_name: Optional[str] = None) -> BaseStorageProvider:
    """
    Factoría para instanciar el proveedor de almacenamiento activo.
    Opciones: 'local', 's3' ('supabase', 'supabase_storage', 'r2', 'aws_s3', 'minio').
    """
    ptype = (provider_name or settings.STORAGE_PROVIDER).lower().strip()

    if ptype in ("s3", "supabase", "supabase_storage", "aws_s3", "r2", "minio"):
        return S3StorageProvider(
            endpoint_url=settings.S3_ENDPOINT_URL,
            region_name=settings.S3_REGION_NAME,
            access_key_id=settings.S3_ACCESS_KEY_ID,
            secret_access_key=settings.S3_SECRET_ACCESS_KEY,
            bucket_name=settings.S3_BUCKET_NAME,
            use_ssl=settings.S3_USE_SSL,
            public_url_prefix=settings.S3_PUBLIC_URL_PREFIX,
            presigned_expiration=settings.S3_PRESIGNED_EXPIRATION_SECONDS,
        )

    # Predeterminado: Almacenamiento local seguro
    return LocalStorageProvider(base_dir=str(settings.STORAGE_DIR))

def get_storage_provider(force_new: bool = False) -> BaseStorageProvider:
    global _storage_provider_instance
    if _storage_provider_instance is None or force_new:
        _storage_provider_instance = create_storage_provider()
    return _storage_provider_instance

