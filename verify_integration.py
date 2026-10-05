import asyncio
import os
import sys

# Ensure backend root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_spa_routes():
    print("--- 1. Testing SPA Fallback on /catalogo ---")
    r = client.get("/catalogo")
    print("GET /catalogo status:", r.status_code)
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    assert "<!DOCTYPE html>" in r.text or "<html" in r.text
    print("[OK] SPA Fallback works! /catalogo returned index.html")

    print("\n--- 2. Testing SPA Fallback on /admin ---")
    r2 = client.get("/admin")
    assert r2.status_code == 200
    print("[OK] /admin returned index.html")

    print("\n--- 3. Testing API 404 behavior for unknown API route ---")
    r3 = client.get("/api/unknown_endpoint_xyz")
    assert r3.status_code == 404
    print("[OK] Unknown API route returned 404 as expected")

async def test_backend_categories_and_resync():
    print("\n--- 4. Testing POS Department and Category Integration ---")
    from app.services.pos_service import POSService
    from app.services.product_service import ProductService

    pos = POSService.get_instance()
    await pos.initialize()

    deps = pos.get_departments()
    print(f"Total POS departments: {len(deps)}")
    assert len(deps) >= 50, f"Expected at least 50 departments, got {len(deps)}"
    d07 = next((d for d in deps if d["code"] == "D07"), None)
    print("D07 department:", d07)
    assert d07 is not None and "Alimentos" in d07["name"]

    print("\n--- 5. Testing get_categories_by_department for D07 ---")
    cats_d07 = await pos.get_categories_by_department("D07")
    print(f"Total categories for D07: {len(cats_d07)}")
    gaseosas = next((c for c in cats_d07 if "gaseosa" in c["name"].lower()), None)
    print("Gaseosas category in D07:", gaseosas)
    assert gaseosas is not None, "Gaseosas not found in D07"

    print("\n--- 6. Testing search_categories_pos for 'Coca' ---")
    search_res = await pos.search_categories_pos("Coca")
    print(f"Search results for 'Coca': {search_res}")

    print("\n--- 7. Testing Resyncing COCA-COLA ZERO 1.5L (ID: 3) ---")
    lookup = await pos.lookup_product_by_barcode("7702535011799")
    print(f"Barcode 7702535011799 in POS: found={lookup.found}, item_id={lookup.item_id}, name={lookup.name}")
    prod_svc = ProductService.get_instance()
    try:
        resynced = await prod_svc.resync_product_with_pos(
            product_id=3,
            category_code="C3677",
            department_code="D07"
        )
        print("Resync result pos_item_id:", resynced.get("pos_item_id"))
        print("POS Sync response:", resynced.get("pos_sync"))
    except Exception as e:
        print("Resync note/error:", e)

if __name__ == "__main__":
    test_spa_routes()
    asyncio.run(test_backend_categories_and_resync())
