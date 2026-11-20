import sys
from pathlib import Path
from datetime import timedelta

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.security import (
    create_access_token,
    verify_password,
    get_password_hash,
    sanitize_filename,
    generate_secure_storage_path,
    validate_file_content_header,
    MAGIC_BYTES_MAP,
)

client = TestClient(app)


def test_storage_requires_authentication():
    """Valida que el endpoint de almacenamiento de archivos rechace accesos sin autenticar."""
    res = client.get("/api/storage/test_invoice.pdf")
    assert res.status_code == 401
    assert "WWW-Authenticate" in res.headers or "detail" in res.json()


def test_storage_path_traversal_prevention():
    """Valida que el endpoint de almacenamiento impida ataques de Path Traversal."""
    admin_token = create_access_token(data={"sub": "admin", "role": "admin"})
    headers = {"Authorization": f"Bearer {admin_token}"}

    traversal_payloads = [
        "%2e%2e/%2e%2e/etc/passwd",
        "%2e%2e%2f%2e%2e%2fwindows%2fwin.ini",
        "nested/%2e%2e/%2e%2e/%2e%2e/%2e%2e/etc/shadow",
        "storage/%2e%2e/%2e%2e/boot.ini",
    ]

    for payload in traversal_payloads:
        res = client.get(f"/api/storage/{payload}", headers=headers)
        # Debe retornar 403 (Ruta denegada / no autorizada) o 404 seguro, nunca exponer archivos del sistema
        assert res.status_code in [403, 404], f"Payload inseguro {payload} retornó {res.status_code}"


def test_bcrypt_strict_verification():
    """Valida que las contraseñas usen exclusivamente Bcrypt estricto sin fallback inseguro."""
    pwd = "ClaveSeguraFruver2026!"
    hashed = get_password_hash(pwd)

    # 1. Contraseña correcta coincide
    assert verify_password(pwd, hashed) is True

    # 2. Contraseña incorrecta no coincide
    assert verify_password("ClaveErronea123", hashed) is False

    # 3. Fallback de texto plano ELIMINADO: texto plano vs texto plano DEBE retornar False
    assert verify_password("plain123", "plain123") is False

    # 4. Strings vacíos o nulos son rechazados de forma segura
    assert verify_password("", hashed) is False
    assert verify_password(pwd, "") is False
    assert verify_password(None, None) is False

    # 5. Hashes malformados no generan excepciones no controladas
    assert verify_password(pwd, "invalid_hash_string_not_bcrypt") is False


def test_jwt_token_security():
    """Valida la integridad, expiración y rechazo de tokens adulterados."""
    # 1. Token inválido/malformado
    res_bad = client.get("/api/v1/roles", headers={"Authorization": "Bearer token_completamente_invalido"})
    assert res_bad.status_code == 401

    # 2. Token expirado
    expired_token = create_access_token(
        data={"sub": "admin", "role": "admin"},
        expires_delta=timedelta(seconds=-10)
    )
    res_exp = client.get("/api/v1/roles", headers={"Authorization": f"Bearer {expired_token}"})
    assert res_exp.status_code == 401

    # 3. Header de autorización con formato incorrecto
    res_format = client.get("/api/v1/roles", headers={"Authorization": "TokenSinPrefijoBearer"})
    assert res_format.status_code == 401


def test_filename_sanitization():
    """Valida la desinfección de nombres de archivo contra Path Traversal."""
    # Casos de path traversal y caracteres peligrosos
    assert sanitize_filename("../../../etc/passwd") == "passwd"
    assert sanitize_filename("..\\..\\windows\\system32\\cmd.exe") == "cmd.exe"
    
    clean_custom = sanitize_filename("factura#001! [agosto].pdf")
    assert "/" not in clean_custom
    assert "\\" not in clean_custom
    assert ".." not in clean_custom

    # Generación de ruta segura con prefijo UUID aleatorio
    rel_path, clean_name = generate_secure_storage_path("invoices", "factura_fruver.pdf")
    assert rel_path.startswith("invoices/")
    assert clean_name == "factura_fruver.pdf"
    assert len(rel_path.split("/")[1]) > len(clean_name)  # Incluye prefijo único


def test_magic_bytes_file_validation():
    """Valida la verificación de firmas binarias reales para prevenir archivos camuflados."""
    # 1. PDF genuino vs ejecutable camuflado
    assert validate_file_content_header(b"%PDF-1.7\r\nstream", ".pdf") is True
    assert validate_file_content_header(b"MZ\x90\x00\x03\x00\x00\x00", ".pdf") is False

    # 2. PNG genuino vs script malicioso camuflado
    assert validate_file_content_header(b"\x89PNG\r\n\x1a\n\x00\x00", ".png") is True
    assert validate_file_content_header(b"<?php echo 'malware'; ?>", ".png") is False

    # 3. JPEG genuino
    assert validate_file_content_header(b"\xff\xd8\xff\xe0\x00\x10JFIF", ".jpg") is True
    assert validate_file_content_header(b"\xff\xd8\xff\xe1\x00\x18Exif", ".jpeg") is True

    # 4. XML DIAN genuino
    assert validate_file_content_header(b"<?xml version=\"1.0\" encoding=\"UTF-8\"?>", ".xml") is True
    assert validate_file_content_header(b"<Invoice xmlns=\"urn:oasis:names:specification:ubl:schema:xsd:Invoice-2\">", ".xml") is True

    # 5. XLSX genuino (formato ZIP PK)
    assert validate_file_content_header(b"PK\x03\x04\x14\x00\x06\x00", ".xlsx") is True
