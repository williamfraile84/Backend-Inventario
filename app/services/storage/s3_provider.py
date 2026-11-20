import io
import os
from typing import Optional, List
from fastapi import HTTPException
from fastapi.responses import StreamingResponse, Response
from app.services.storage.base import BaseStorageProvider
from app.core.security import get_file_media_type

try:
    import boto3
    from botocore.exceptions import ClientError
    from botocore.config import Config
except ImportError:
    boto3 = None
    ClientError = Exception
    Config = None

class S3StorageProvider(BaseStorageProvider):
    """Proveedor S3 universal (Supabase Storage, Cloudflare R2, AWS S3, MinIO)."""

    def __init__(
        self,
        endpoint_url: Optional[str] = None,
        region_name: str = "us-east-1",
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
        bucket_name: str = "fruver-pos-storage",
        use_ssl: bool = True,
        public_url_prefix: Optional[str] = None,
        presigned_expiration: int = 3600
    ):
        self.bucket_name = bucket_name
        self.public_url_prefix = public_url_prefix
        self.presigned_expiration = presigned_expiration
        self.client = None

        if boto3 and access_key_id and secret_access_key:
            boto_config = Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"}
            )
            self.client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                region_name=region_name,
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
                use_ssl=use_ssl,
                config=boto_config
            )

    def ensure_bucket_exists(self):
        """Verifica o crea el bucket si no existe (idempotente)."""
        if not self.client:
            return
        try:
            self.client.head_bucket(Bucket=self.bucket_name)
        except Exception:
            try:
                self.client.create_bucket(Bucket=self.bucket_name)
            except Exception:
                pass

    def list_all_files(self, prefix: str = "") -> List[str]:
        """Lista todos los archivos presentes en el bucket S3 con un prefijo dado."""
        if not self.client:
            return []
        clean_prefix = prefix.replace("\\", "/").lstrip("/")
        files = []
        try:
            paginator = self.client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket_name, Prefix=clean_prefix):
                for obj in page.get("Contents", []):
                    key = obj.get("Key", "")
                    if key and not key.endswith("/"):
                        files.append(key)
        except Exception:
            pass
        return files

    async def save_file(self, relative_path: str, file_bytes: bytes, content_type: Optional[str] = None) -> str:
        if not self.client:
            raise RuntimeError("Cliente S3 no configurado o credenciales ausentes.")
        clean_key = relative_path.replace("\\", "/").lstrip("/")
        mime = content_type or get_file_media_type(clean_key)
        self.client.put_object(
            Bucket=self.bucket_name,
            Key=clean_key,
            Body=file_bytes,
            ContentType=mime
        )
        return clean_key

    async def get_file_bytes(self, relative_path: str) -> Optional[bytes]:
        if not self.client:
            return None
        clean_key = relative_path.replace("\\", "/").lstrip("/")
        try:
            resp = self.client.get_object(Bucket=self.bucket_name, Key=clean_key)
            return resp["Body"].read()
        except Exception:
            return None

    async def delete_file(self, relative_path: str) -> bool:
        if not self.client:
            return False
        clean_key = relative_path.replace("\\", "/").lstrip("/")
        try:
            self.client.delete_object(Bucket=self.bucket_name, Key=clean_key)
            return True
        except Exception:
            return False

    async def file_exists(self, relative_path: str) -> bool:
        if not self.client:
            return False
        clean_key = relative_path.replace("\\", "/").lstrip("/")
        try:
            self.client.head_object(Bucket=self.bucket_name, Key=clean_key)
            return True
        except Exception:
            return False

    def get_public_url(self, relative_path: str) -> str:
        clean_key = relative_path.replace("\\", "/").lstrip("/")
        if self.public_url_prefix:
            return f"{self.public_url_prefix.rstrip('/')}/{clean_key}"
        if self.client:
            try:
                return self.client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": self.bucket_name, "Key": clean_key},
                    ExpiresIn=self.presigned_expiration
                )
            except Exception:
                pass
        return f"/api/storage/{clean_key}"

    def get_response(self, relative_path: str, inline: bool = True) -> Response:
        clean_key = relative_path.replace("\\", "/").lstrip("/")
        if not self.client:
            raise HTTPException(status_code=500, detail="Servicio S3 no disponible.")
        try:
            resp = self.client.get_object(Bucket=self.bucket_name, Key=clean_key)
            media_type = resp.get("ContentType", get_file_media_type(clean_key))
            filename = os.path.basename(clean_key)
            content_disposition_type = "inline" if inline else "attachment"
            headers = {"Content-Disposition": f"{content_disposition_type}; filename=\"{filename}\""}
            return StreamingResponse(resp["Body"], media_type=media_type, headers=headers)
        except Exception as e:
            raise HTTPException(status_code=404, detail=f"Archivo '{clean_key}' no encontrado en S3: {e}")
