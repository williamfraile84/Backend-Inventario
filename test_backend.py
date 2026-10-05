import sys
import os
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core.database import init_db, get_all_users, get_user_by_username, create_user, add_audit_log, get_audit_logs
from app.core.security import create_access_token, decode_access_token, verify_password
from app.models.schemas import ProductLookupResponse, UserResponse

def test():
    print("1. Probando inicializacion de Base de Datos SQLite...")
    init_db()
    users = get_all_users()
    print(f"[OK] Usuarios encontrados ({len(users)}): {[u['username'] for u in users]}")

    admin = get_user_by_username("admin")
    assert admin is not None, "El usuario admin no fue creado!"
    print(f"[OK] Usuario Admin verificado con rol: {admin['role']}, permisos: {admin['permissions']}")

    print("2. Probando verificacion de contrasena y JWT...")
    assert verify_password("fruver2026", admin["password_hash"]), "Fallo en verificacion de contrasena"
    token = create_access_token({"sub": "admin"})
    payload = decode_access_token(token)
    assert payload.get("sub") == "admin", "Fallo en JWT token"
    print("[OK] JWT Token generado y decodificado exitosamente.")

    print("3. Probando logs de auditoria...")
    add_audit_log(
        username="admin",
        action_type="test_action",
        barcode="7702001001234",
        item_name="PAPA PASTUSA",
        old_price=2000,
        new_price=2500,
        formatted_new_price="$2.500",
        details="Prueba de auditoria"
    )
    logs = get_audit_logs(10)
    print(f"[OK] Registros de auditoria guardados: {len(logs)} registros.")

    print("4. Probando esquemas Pydantic...")
    lookup = ProductLookupResponse(
        found=True,
        barcode="7702001001234",
        name="PAPA PASTUSA",
        unit_price=2500.0,
        formatted_price="$2.500"
    )
    assert lookup.found is True
    print("[OK] Esquemas Pydantic validados con exito.")
    print("TODAS LAS PRUEBAS DE BACKEND PASARON EXITOSAMENTE!")

if __name__ == "__main__":
    test()

