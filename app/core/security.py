import os
import re
import uuid
import mimetypes
from typing import Set, Optional, Dict, Any
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, UploadFile
import bcrypt
try:
    import jwt
except ImportError:
    from jose import jwt
from app.core.config import settings

ALLOWED_DOC_EXTENSIONS: Set[str] = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".xml", ".xlsx", ".xls"}
MAX_FILE_SIZE_BYTES = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024

# Firmas binarias (Magic Bytes) conocidas para prevención de spoofing
MAGIC_BYTES_MAP = {
    ".pdf": [b"%PDF"],
    ".png": [b"\x89PNG\r\n\x1a\n", b"\x89PNG"],
    ".jpg": [b"\xff\xd8\xff"],
    ".jpeg": [b"\xff\xd8\xff"],
    ".webp": [b"RIFF"],
    ".xlsx": [b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"],
    ".xls": [b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"],
    ".xml": [b"<?xml", b"<fe:Invoice", b"<Invoice", b"\xef\xbb\xbf<?xml"]
}


def sanitize_filename(filename: str) -> str:
    """Limpia el nombre del archivo para prevenir ataques de Path Traversal."""
    base_name = os.path.basename(filename)
    clean_name = re.sub(r"[^a-zA-Z0-9_\.\-]", "_", base_name)
    return clean_name


def generate_secure_storage_path(subfolder: str, original_filename: str) -> tuple[str, str]:
    clean_name = sanitize_filename(original_filename)
    unique_prefix = uuid.uuid4().hex[:8]
    relative_path = f"{subfolder}/{unique_prefix}_{clean_name}"
    return relative_path, clean_name


def validate_file_content_header(header_bytes: bytes, ext: str) -> bool:
    """Verifica la firma binaria real del archivo."""
    if not header_bytes or ext not in MAGIC_BYTES_MAP:
        return True
    expected = MAGIC_BYTES_MAP[ext]
    return any(header_bytes.startswith(sig) for sig in expected)


def validate_file_upload(file: UploadFile, allowed_extensions: Set[str] = ALLOWED_DOC_EXTENSIONS, header_bytes: Optional[bytes] = None) -> str:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Nombre de archivo inválido.")
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Formato no permitido ({ext}). Aceptados: {', '.join(sorted(allowed_extensions))}"
        )
    if header_bytes and not validate_file_content_header(header_bytes, ext):
        raise HTTPException(
            status_code=400,
            detail=f"El contenido del archivo no corresponde a un archivo {ext} genuino."
        )
    return ext


def get_file_media_type(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    mime, _ = mimetypes.guess_type(filename)
    if mime:
        return mime
    if ext == ".pdf":
        return "application/pdf"
    if ext == ".png":
        return "image/png"
    if ext in [".jpg", ".jpeg"]:
        return "image/jpeg"
    if ext == ".webp":
        return "image/webp"
    if ext == ".xlsx":
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if ext == ".xls":
        return "application/vnd.ms-excel"
    if ext == ".xml":
        return "application/xml"
    return "application/octet-stream"


# ==========================================
# Criptografía Bcrypt y Tokens JWT
# ==========================================

def get_password_hash(password: str) -> str:
    pwd_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return plain_password == hashed_password


def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    now_utc = datetime.now(timezone.utc)
    if expires_delta:
        expire = now_utc + expires_delta
    else:
        expire = now_utc + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    if "jti" not in to_encode:
        to_encode["jti"] = uuid.uuid4().hex
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except Exception:
        return None

# Re-exportaciones de compatibilidad para routers
from app.core.dependencies import get_current_user, get_current_admin, require_admin, require_permission, get_db


