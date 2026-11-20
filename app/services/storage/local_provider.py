import os
from typing import Optional, List
from fastapi import HTTPException
from fastapi.responses import FileResponse, Response
from app.services.storage.base import BaseStorageProvider
from app.core.security import get_file_media_type

class LocalStorageProvider(BaseStorageProvider):
    """Proveedor de almacenamiento en disco local con streaming y validación de rutas."""

    def __init__(self, base_dir: str = "./storage"):
        self.base_dir = os.path.abspath(base_dir)
        os.makedirs(self.base_dir, exist_ok=True)

    def _resolve_safe_path(self, relative_path: str) -> str:
        clean = relative_path.replace("\\", "/").lstrip("/")
        full = os.path.normpath(os.path.join(self.base_dir, clean))
        if not full.startswith(self.base_dir):
            raise HTTPException(status_code=403, detail="Acceso denegado: ruta no autorizada.")
        return full

    async def save_file(self, relative_path: str, file_bytes: bytes, content_type: Optional[str] = None) -> str:
        safe_path = self._resolve_safe_path(relative_path)
        os.makedirs(os.path.dirname(safe_path), exist_ok=True)
        with open(safe_path, "wb") as f:
            f.write(file_bytes)
        return relative_path.replace("\\", "/").lstrip("/")

    async def get_file_bytes(self, relative_path: str) -> Optional[bytes]:
        safe_path = self._resolve_safe_path(relative_path)
        if not os.path.isfile(safe_path):
            return None
        with open(safe_path, "rb") as f:
            return f.read()

    async def delete_file(self, relative_path: str) -> bool:
        safe_path = self._resolve_safe_path(relative_path)
        if os.path.isfile(safe_path):
            try:
                os.remove(safe_path)
                return True
            except Exception:
                return False
        return False

    async def file_exists(self, relative_path: str) -> bool:
        safe_path = self._resolve_safe_path(relative_path)
        return os.path.isfile(safe_path)

    def list_all_files(self, subfolder: str = "") -> List[str]:
        target_dir = os.path.normpath(os.path.join(self.base_dir, subfolder.replace("\\", "/").lstrip("/")))
        if not os.path.exists(target_dir):
            return []
        found = []
        for root, _, files in os.walk(target_dir):
            for file in files:
                if file.startswith("."):
                    continue
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, self.base_dir).replace("\\", "/")
                found.append(rel_path)
        return found

    def get_public_url(self, relative_path: str) -> str:
        clean = relative_path.replace("\\", "/").lstrip("/")
        return f"/api/storage/{clean}"

    def get_response(self, relative_path: str, inline: bool = True) -> Response:
        safe_path = self._resolve_safe_path(relative_path)
        if not os.path.isfile(safe_path):
            raise HTTPException(status_code=404, detail="Archivo no encontrado en almacenamiento local.")
        media_type = get_file_media_type(safe_path)
        filename = os.path.basename(safe_path)
        content_disposition_type = "inline" if inline else "attachment"
        headers = {"Content-Disposition": f"{content_disposition_type}; filename=\"{filename}\""}
        return FileResponse(safe_path, media_type=media_type, headers=headers)
