import sys
from pathlib import Path

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.security import create_access_token
from app.core.database import get_user_by_username, create_user, update_user

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def setup_test_users():
    """Garantiza la existencia de usuarios de prueba para cada rol RBAC."""
    roles_to_setup = [
        ("test_admin", "Admin De Pruebas", "admin", "AdminPass123!"),
        ("test_supervisor", "Supervisor De Pruebas", "supervisor", "SuperPass123!"),
        ("test_cajero", "Cajero De Pruebas", "cajero", "CajeroPass123!"),
        ("test_inactivo", "Usuario Inactivo", "cajero", "InactivoPass123!"),
    ]
    for username, full_name, role, password in roles_to_setup:
        user = get_user_by_username(username)
        if not user:
            create_user(
                username=username,
                full_name=full_name,
                password=password,
                role=role
            )
    
    # Desactivar explícitamente el usuario test_inactivo
    inact = get_user_by_username("test_inactivo")
    if inact and inact["is_active"]:
        update_user(inact["id"], is_active=False)


def get_auth_header(username: str, role: str) -> dict:
    token = create_access_token(data={"sub": username, "role": role})
    return {"Authorization": f"Bearer {token}"}


def test_unauthenticated_requests_are_rejected():
    """Valida que endpoints protegidos retornen 401 Unauthorized sin credenciales."""
    endpoints = [
        ("GET", "/api/v1/empresa"),
        ("GET", "/api/v1/empresa/status"),
        ("GET", "/api/v1/roles"),
        ("GET", "/api/v1/roles/permisos"),
        ("GET", "/api/v1/auditoria"),
        ("GET", "/api/v1/catalog/departments"),
        ("GET", "/api/v1/invoices/history"),
        ("GET", "/api/admin/users"),
        ("GET", "/api/storage/nonexistent_invoice.pdf"),
    ]
    for method, path in endpoints:
        res = client.request(method, path)
        assert res.status_code == 401, f"Fallo en {method} {path}: status {res.status_code}"
        assert "WWW-Authenticate" in res.headers or "detail" in res.json()


def test_cajero_permissions_and_restrictions():
    """Valida que el rol 'cajero' solo tenga acceso de consulta y sea bloqueado en mutaciones."""
    headers = get_auth_header("test_cajero", "cajero")

    # 1. Puede consultar catálogo y permisos (can_lookup=True)
    res_permisos = client.get("/api/v1/roles/permisos", headers=headers)
    assert res_permisos.status_code == 200

    res_roles = client.get("/api/v1/roles", headers=headers)
    assert res_roles.status_code == 200

    # 2. BLOQUEADO: No puede gestionar usuarios (can_manage_users=False -> 403)
    res_users = client.get("/api/admin/users", headers=headers)
    assert res_users.status_code == 403
    assert "Permiso denegado" in res_users.json()["detail"] or "requerido" in res_users.json()["detail"]

    # 3. BLOQUEADO: No puede ver bitácoras de auditoría (can_view_audit=False -> 403)
    res_audit = client.get("/api/v1/auditoria", headers=headers)
    assert res_audit.status_code == 403

    # 4. BLOQUEADO: No puede procesar facturas (can_process_invoices=False -> 403)
    res_proc = client.post("/api/v1/invoices/consult-dian", headers=headers, json={"dian_url": "https://catalogo-vpfe.dian.gov.co/document/searchqr?documentkey=123"})
    assert res_proc.status_code == 403

    # 5. BLOQUEADO: No puede modificar precios masivos (can_edit_bulk=False -> 403)
    res_bulk = client.post("/api/v1/products/bulk-update-prices", headers=headers, json={"item_ids": ["1"], "new_price": 5000})
    assert res_bulk.status_code == 403


def test_supervisor_permissions_and_restrictions():
    """Valida que el rol 'supervisor' pueda ver auditoría y operar, pero no administrar usuarios."""
    headers = get_auth_header("test_supervisor", "supervisor")

    # 1. Puede consultar catálogo y permisos
    res_perm = client.get("/api/v1/roles/permisos", headers=headers)
    assert res_perm.status_code == 200

    # 2. Puede ver bitácora de auditoría (can_view_audit=True)
    res_audit = client.get("/api/v1/auditoria", headers=headers)
    assert res_audit.status_code == 200

    # 3. Puede listar facturas (can_lookup=True)
    res_inv = client.get("/api/v1/invoices/history", headers=headers)
    assert res_inv.status_code == 200

    # 4. BLOQUEADO: No puede administrar usuarios (can_manage_users=False -> 403)
    res_users = client.get("/api/admin/users", headers=headers)
    assert res_users.status_code == 403


def test_admin_full_access():
    """Valida que el rol 'admin' tenga acceso irrestricto a todos los módulos."""
    headers = get_auth_header("test_admin", "admin")

    # 1. Acceso a gestión de usuarios
    res_users = client.get("/api/admin/users", headers=headers)
    assert res_users.status_code == 200

    # 2. Acceso a auditoría
    res_audit = client.get("/api/v1/auditoria", headers=headers)
    assert res_audit.status_code == 200

    # 3. Acceso a configuración de empresa
    res_empresa = client.get("/api/v1/empresa", headers=headers)
    assert res_empresa.status_code == 200

    # 4. Acceso a roles y permisos
    res_roles = client.get("/api/v1/roles", headers=headers)
    assert res_roles.status_code == 200


def test_deactivated_user_blocked():
    """Valida que un usuario desactivado reciba 403 Forbidden aunque su token sea válido."""
    headers = get_auth_header("test_inactivo", "cajero")
    res = client.get("/api/v1/roles/permisos", headers=headers)
    assert res.status_code == 403
    assert "desactivada" in res.json()["detail"].lower()


def test_user_permissions_update_persistence():
    """Valida que la modificación granular de permisos se persista fielmente en la base de datos."""
    admin_headers = get_auth_header("test_admin", "admin")
    cajero_user = get_user_by_username("test_cajero")
    assert cajero_user is not None

    user_id = cajero_user["id"]
    new_perms = {
        "can_lookup": True,
        "can_edit_single": True,
        "can_edit_bulk": False,
        "can_manage_users": False,
        "can_view_audit": True,
        "can_process_invoices": True,
        "can_sync_pos": True,
    }

    # 1. Actualizar usuario vía PUT
    res_update = client.put(
        f"/api/admin/users/{user_id}",
        headers=admin_headers,
        json={
            "full_name": "Cajero Actualizado Con Permisos",
            "role": "cajero",
            "permissions": new_perms,
            "is_active": True,
        }
    )
    assert res_update.status_code == 200, res_update.text
    data_up = res_update.json()
    assert data_up["permissions"]["can_process_invoices"] is True
    assert data_up["permissions"]["can_sync_pos"] is True
    assert data_up["permissions"]["can_edit_single"] is True

    # 2. Re-consultar desde base de datos vía GET /api/admin/users para confirmar persistencia real
    res_list = client.get("/api/admin/users", headers=admin_headers)
    assert res_list.status_code == 200
    users_list = res_list.json()
    saved_cajero = next((u for u in users_list if u["id"] == user_id), None)
    assert saved_cajero is not None
    assert saved_cajero["permissions"]["can_process_invoices"] is True
    assert saved_cajero["permissions"]["can_sync_pos"] is True
    assert saved_cajero["permissions"]["can_view_audit"] is True

    # 3. Restaurar permisos originales para no afectar otras pruebas
    client.put(
        f"/api/admin/users/{user_id}",
        headers=admin_headers,
        json={
            "full_name": "Cajero De Pruebas",
            "role": "cajero",
            "permissions": {
                "can_lookup": True,
                "can_edit_single": False,
                "can_edit_bulk": False,
                "can_manage_users": False,
                "can_view_audit": False,
                "can_process_invoices": False,
                "can_sync_pos": False,
            },
            "is_active": True,
        }
    )
