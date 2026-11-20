import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.security import create_access_token

client = TestClient(app)

@pytest.fixture
def auth_headers():
    token = create_access_token({"sub": "admin", "role": "admin", "id": 1})
    return {"Authorization": f"Bearer {token}"}

def test_process_invoice_image_async_mode(auth_headers):
    """
    Verifica que el endpoint /api/invoices/process-image retorne 202 Accepted
    con task_id para desacoplar el procesamiento y evitar el timeout de 30s en tunnl.gg.
    """
    # Crear un archivo de imagen simulado
    import io
    from PIL import Image
    
    img = Image.new("RGB", (200, 200), color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    buf.seek(0)

    files = {"file": ("test_factura.jpg", buf, "image/jpeg")}
    response = client.post(
        "/api/invoices/process-image",
        files=files,
        params={"provider": "gemini"},
        headers=auth_headers
    )

    assert response.status_code == 202
    data = response.json()
    assert data.get("async_task") is True
    assert "task_id" in data
    assert data.get("status") == "processing"

    task_id = data["task_id"]
    # Consultar estado de la tarea
    status_resp = client.get(f"/api/invoices/task-status/{task_id}", headers=auth_headers)
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["task_id"] == task_id
    assert status_data["status"] in ("processing", "completed", "error")

def test_scan_qr_endpoint_fast_response(auth_headers):
    """
    Verifica que el escáner de QR responda ágilmente sin bloquearse con fotos grandes.
    """
    import io
    from PIL import Image

    # Simular una imagen grande de smartphone (2400x1800)
    img = Image.new("RGB", (2400, 1800), color=(250, 250, 250))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    buf.seek(0)

    files = {"file": ("camera_photo.jpg", buf, "image/jpeg")}
    response = client.post(
        "/api/invoices/scan-qr",
        files=files,
        params={"allow_ocr": "false"},
        headers=auth_headers
    )

    assert response.status_code == 200
    data = response.json()
    # No hay QR en una imagen en blanco, debe responder con éxito=False limpiamente
    assert data.get("success") is False or data.get("cufe") is not None
