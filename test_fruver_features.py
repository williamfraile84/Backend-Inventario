import sys
from pathlib import Path

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core.config import settings
from app.services.pricing_engine import redondear_centena_cercana, calcular_costos_item_factura
from app.services.migration_service import DatabaseMigrationService
from app.core.database import init_db
from app.models.usuario import Usuario, User
from app.models.producto import Producto, ProductAdditionalNumber

def test_config_and_env():
    print("--- Probando Configuración y BaseSettings ---")
    assert settings.APP_NAME == "Fruver POS Manager"
    assert settings.DEFAULT_PROFIT_MARGIN == 30.0
    assert settings.ROUNDING_BASE == 100
    assert "sqlite" in settings.effective_database_url.lower()
    
    # Test masked url
    masked = settings.mask_url("postgresql+psycopg2://admin:supersecret@localhost:5432/fruver")
    assert "supersecret" not in masked
    assert "******" in masked
    print(f"[OK] BaseSettings y enmascaramiento seguro: {masked}")

def test_percentage_and_rounding():
    print("\n--- Probando Cálculos Porcentuales (%) y Redondeo a Centenas ($100 COP) ---")
    # Reglas exactas de sistema_fruver_actual
    assert redondear_centena_cercana(251) == 300
    assert redondear_centena_cercana(249) == 200
    assert redondear_centena_cercana(250) == 300
    assert redondear_centena_cercana(1120) == 1100
    assert redondear_centena_cercana(1150) == 1200
    print("[OK] Redondeo a centenas ($100 COP) validado.")

    # Cálculo de costo y margen
    res = calcular_costos_item_factura(
        costo_original=2000.0,
        cantidad=1.0,
        porcentaje_margen=30.0,
        base_redondeo=100
    )
    assert res["costo_unitario_inventario"] == 2000.0
    # Costo 2000 * 1.30 = 2600
    assert res["precio_venta_final"] == 2600
    print(f"[OK] Cálculo de margen (+30%): Costo 2000 -> Venta {res['precio_venta_final']}")

def test_3nf_models_and_migration():
    print("\n--- Probando Normalización 3NF y Modelos Relacionales ---")
    init_db()
    assert User is Usuario
    print("[OK] Modelos relacionales 3NF inicializados.")

    # Test migration service interface
    res = DatabaseMigrationService.run_auto_migration_if_enabled()
    assert res.get("success") is True
    print("[OK] Servicio de Migración Agnóstica validado.")

def main():
    test_config_and_env()
    test_percentage_and_rounding()
    test_3nf_models_and_migration()
    print("\n=======================================================")
    print("¡TODAS LAS NUEVAS CARACTERÍSTICAS VERIFICADAS CON ÉXITO!")
    print("=======================================================")

if __name__ == "__main__":
    main()
