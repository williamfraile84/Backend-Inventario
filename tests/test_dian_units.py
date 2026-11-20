import sys
import os
from pathlib import Path

# Configurar entorno de pruebas antes de importar app
os.environ["TESTING"] = "1"
os.environ["APP_ENV"] = "testing"

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings
settings.APP_ENV = "testing"

import pytest
from app.services.dian_unit_service import DIANUnitService
from fastapi.testclient import TestClient
from app.main import app
from app.core.security import create_access_token
from app.core.database import init_db


def test_dian_catalog_loading():
    """Valida la carga del catálogo completo oficial DIAN (1101 unidades)."""
    catalog = DIANUnitService.get_catalog()
    assert len(catalog) >= 1000
    assert "KGM" in catalog
    assert "LBR" in catalog
    assert "GRM" in catalog
    assert "DZN" in catalog
    assert "BX" in catalog
    assert "WSD" in catalog
    assert "EA" in catalog
    assert "NIU" in catalog
    assert "C62" in catalog


def test_dian_unit_resolution():
    """Valida la normalización de códigos DIAN y detección de empaques."""
    # KGM -> Kg, Peso
    res_kg = DIANUnitService.resolve_unit("KGM")
    assert res_kg["unidad_estandar"] == "Kg"
    assert res_kg["magnitud"] == "PESO"
    assert res_kg["unidades_por_presentacion"] == 1.0

    # LBR -> Libra, Peso, factor 0.5 kg
    res_lb = DIANUnitService.resolve_unit("LBR")
    assert res_lb["unidad_estandar"] == "Libra"
    assert res_lb["factor_base"] == 0.5

    # DZN -> Docena, 12 unidades inherentes
    res_dzn = DIANUnitService.resolve_unit("DZN", "HUEVOS FRESCOS")
    assert res_dzn["unidad_estandar"] == "Docena"
    assert res_dzn["presentacion"] == "Docena"
    assert res_dzn["unidades_por_presentacion"] == 12.0
    assert res_dzn["es_multipack"] is True

    # WSD con empaque detectado en descripción: DISPLAY X 6 UND
    res_wsd = DIANUnitService.resolve_unit("WSD", "GALLETA OREO GOLDEN DISPLAY X 6 UND 54GR")
    assert res_wsd["unidades_por_presentacion"] == 6.0
    assert res_wsd["es_multipack"] is True


def test_cross_unit_conversions():
    """Valida la conversión matemática entre unidades DIAN y POS."""
    # 1. Libras (LBR) a Kilos (KG): 10 lb a $2.500 -> 5 kg a $5.000
    conv_lb_kg = DIANUnitService.calculate_cross_unit_conversion("LBR", "KG", 10.0, 2500.0)
    assert conv_lb_kg["conversion_requerida"] is True
    assert conv_lb_kg["cantidad_convertida"] == 5.0
    assert conv_lb_kg["costo_convertido"] == 5000.0

    # 2. Kilos (KG) a Libras (LB): 5 kg a $5.000 -> 10 lb a $2.500
    conv_kg_lb = DIANUnitService.calculate_cross_unit_conversion("KG", "LB", 5.0, 5000.0)
    assert conv_kg_lb["conversion_requerida"] is True
    assert conv_kg_lb["cantidad_convertida"] == 10.0
    assert conv_kg_lb["costo_convertido"] == 2500.0

    # 3. Docenas (DZN) a Unidades (UN): 5 dzn a $12.000 -> 60 un a $1.000
    conv_dzn_un = DIANUnitService.calculate_cross_unit_conversion("DZN", "UN", 5.0, 12000.0)
    assert conv_dzn_un["conversion_requerida"] is True
    assert conv_dzn_un["cantidad_convertida"] == 60.0
    assert conv_dzn_un["costo_convertido"] == 1000.0

    # 4. Caja x 24 a Unidades (UN): 2 cajas a $48.000 -> 48 un a $2.000
    conv_caja_un = DIANUnitService.calculate_cross_unit_conversion("CAJA", "UN", 2.0, 48000.0, units_per_pack=24.0)
    assert conv_caja_un["conversion_requerida"] is True
    assert conv_caja_un["cantidad_convertida"] == 48.0
    assert conv_caja_un["costo_convertido"] == 2000.0

    # 5. Gramos (GRM) a Kilos (KG): 500 g a $8/g ($4.000 total) -> 0.5 kg a $8.000/kg
    conv_gr_kg = DIANUnitService.calculate_cross_unit_conversion("GRM", "KG", 500.0, 8.0)
    assert conv_gr_kg["conversion_requerida"] is True
    assert conv_gr_kg["cantidad_convertida"] == 0.5
    assert conv_gr_kg["costo_convertido"] == 8000.0


def test_dian_units_endpoints():
    """Valida los endpoints REST de unidades DIAN y conversión."""
    init_db()
    client = TestClient(app)
    token = create_access_token({"sub": "admin", "role": "admin"})
    headers = {"Authorization": f"Bearer {token}"}

    # GET /dian-units
    res = client.get("/api/catalog/dian-units?query=libra", headers=headers)
    assert res.status_code == 200
    items = res.json()
    assert len(items) >= 1
    assert any(i["code"] == "LBR" for i in items)

    # POST /dian-units/convert
    res_conv = client.post(
        "/api/catalog/dian-units/convert",
        json={
            "from_unit": "LBR",
            "to_unit": "KG",
            "quantity": 20.0,
            "unit_cost": 3000.0,
            "units_per_pack": 1.0
        },
        headers=headers
    )
    assert res_conv.status_code == 200
    data = res_conv.json()
    assert data["cantidad_convertida"] == 10.0
    assert data["costo_convertido"] == 6000.0
