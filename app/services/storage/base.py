from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any
from fastapi.responses import Response

class BaseStorageProvider(ABC):
    """Interfaz abstracta para todos los proveedores de almacenamiento."""

    @abstractmethod
    async def save_file(self, relative_path: str, file_bytes: bytes, content_type: Optional[str] = None) -> str:
        """Guarda un archivo y retorna su identificador o ruta relativa canónica."""
        pass

    @abstractmethod
    async def get_file_bytes(self, relative_path: str) -> Optional[bytes]:
        """Obtiene el contenido binario del archivo."""
        pass

    @abstractmethod
    async def delete_file(self, relative_path: str) -> bool:
        """Elimina un archivo si existe."""
        pass

    @abstractmethod
    async def file_exists(self, relative_path: str) -> bool:
        """Comprueba si un archivo existe en el almacenamiento."""
        pass

    @abstractmethod
    def get_public_url(self, relative_path: str) -> str:
        """Retorna una URL pública directa o firmada para descargar/visualizar el archivo."""
        pass

    @abstractmethod
    def get_response(self, relative_path: str, inline: bool = True) -> Response:
        """Retorna un objeto Response de FastAPI optimizado (FileResponse o StreamingResponse)."""
        pass

