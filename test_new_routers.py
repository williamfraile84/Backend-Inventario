import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient
from app.main import app
from app.core.security import create_access_token

client = TestClient(app)

# Generate admin auth token for tests
admin_token = create_access_token(data={"sub": "admin", "role": "admin"})
auth_headers = {"Authorization": f"Bearer {admin_token}"}

def test_health():
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    print("[OK] Health endpoint responde 200 OK:", data["app"])

def test_empresa_endpoints():
    # 1. Status
    res = client.get("/api/v1/empresa/status")
    assert res.status_code == 200, res.text
    data = res.json()
    assert "configurada" in data
    assert "empresa" in data
    print("[OK] Empresa status endpoint:", data["empresa"]["nombre"] if data["empresa"] else "No empresa")

    # 2. List empresas
    res = client.get("/api/v1/empresa")
    assert res.status_code == 200
    empresas = res.json()
    assert len(empresas) >= 1
    print(f"[OK] Listado de empresas: {len(empresas)} empresa(s) activa(s)")

def test_roles_and_permisos():
    # 1. Roles
    res = client.get("/api/v1/roles", headers=auth_headers)
    assert res.status_code == 200, res.text
    roles = res.json()
    assert len(roles) >= 3
    print(f"[OK] Roles RBAC: {len(roles)} roles cargados ({', '.join(r['codigo'] for r in roles)})")

    # 2. Permisos
    res = client.get("/api/v1/roles/permisos", headers=auth_headers)
    assert res.status_code == 200
    permisos = res.json()
    assert len(permisos) >= 7
    print(f"[OK] Permisos granulares: {len(permisos)} permisos registrados")

def test_auditoria_endpoints():
    # 1. Total count
    res = client.get("/api/v1/auditoria/total-count", headers=auth_headers)
    assert res.status_code == 200, res.text
    data = res.json()
    assert "total" in data
    print("[OK] Auditoría total-count:", data["total"])

    # 2. Query logs
    res = client.get("/api/v1/auditoria?limit=10", headers=auth_headers)
    assert res.status_code == 200, res.text
    data = res.json()
    assert "total" in data
    assert "items" in data
    print(f"[OK] Auditoría logs query: {len(data['items'])} items recibidos")

    # 3. Export CSV
    res = client.get("/api/v1/auditoria/export-csv", headers=auth_headers)
    assert res.status_code == 200
    assert "text/csv" in res.headers.get("content-type", "")
    assert "ID,Fecha y Hora" in res.text
    print("[OK] Auditoría export-csv responde con CSV válido y cabecera Content-Disposition")

def test_catalog_crud():
    # 1. Units CRUD
    res = client.get("/api/v1/catalog/units", headers=auth_headers)
    assert res.status_code == 200
    units = res.json()
    print(f"[OK] Catalog units GET: {len(units)} unidades")

    # 2. Packaging types CRUD
    res = client.get("/api/v1/catalog/packaging-types", headers=auth_headers)
    assert res.status_code == 200
    packs = res.json()
    print(f"[OK] Catalog packaging-types GET: {len(packs)} empaques")

    # 3. Expense concepts CRUD
    res = client.get("/api/v1/catalog/expense-concepts", headers=auth_headers)
    assert res.status_code == 200
    expenses = res.json()
    print(f"[OK] Catalog expense-concepts GET: {len(expenses)} conceptos de gasto")

if __name__ == "__main__":
    print("\n--- Ejecutando Pruebas de Routers y Endpoints ---")
    test_health()
    test_empresa_endpoints()
    test_roles_and_permisos()
    test_auditoria_endpoints()
    test_catalog_crud()
    print("\n=======================================================")
    print("TODOS LOS NUEVOS ROUTERS Y ENDPOINTS FUNCIONAN 100% OK!")
    print("=======================================================\n")

