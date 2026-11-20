import sys
import os
import asyncio
from pathlib import Path

# Configurar stdout UTF-8 seguro para terminales Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Agregar backend al path si se ejecuta directamente
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings
from app.services.storage.storage_factory import get_storage_provider


async def test_storage():
    print("=" * 60)
    print("  VERIFICADOR DE ALMACENAMIENTO (STORAGE) - INVENTARIO FRUVER")
    print("=" * 60)
    provider_name = settings.STORAGE_PROVIDER
    print(f"[*] Proveedor configurado: {provider_name.upper()}")

    if provider_name in ("s3", "supabase", "aws_s3", "r2", "minio"):
        print(f"[*] Endpoint S3: {settings.S3_ENDPOINT_URL or '(Predeterminado AWS)'}")
        print(f"[*] Bucket: {settings.S3_BUCKET_NAME}")
        print(f"[*] Región: {settings.S3_REGION_NAME}")
        key_masked = (settings.S3_ACCESS_KEY_ID[:4] + "******") if settings.S3_ACCESS_KEY_ID and len(settings.S3_ACCESS_KEY_ID) > 4 else ("Definido" if settings.S3_ACCESS_KEY_ID else "No configurado")
        print(f"[*] Access Key ID: {key_masked}")
    else:
        print(f"[*] Directorio Base: {settings.STORAGE_DIR}")
    print("-" * 60)

    try:
        provider = get_storage_provider()

        if hasattr(provider, "ensure_bucket_exists"):
            print("[1/4] Verificando existencia de bucket / directorio...")
            provider.ensure_bucket_exists()
            print("   -> Bucket / directorio verificado.")

        test_rel_path = "temp/test_probe.txt"
        test_content = b"PROBE_INVENTARIO_FRUVER_STORAGE_OK"

        print(f"[2/4] Probando escritura de archivo en '{test_rel_path}'...")
        saved_key = await provider.save_file(test_rel_path, test_content, content_type="text/plain")
        print(f"   -> Archivo guardado con clave: {saved_key}")

        print("[3/4] Probando lectura del archivo guardado...")
        read_bytes = await provider.get_file_bytes(test_rel_path)
        if read_bytes != test_content:
            raise ValueError(f"Contenido no coincide. Esperado {test_content}, recibido {read_bytes}")
        print("   -> Lectura correcta (bytes coinciden 100%).")

        print("[4/4] Limpiando archivo de prueba...")
        deleted = await provider.delete_file(test_rel_path)
        print(f"   -> Eliminación completada ({deleted}).")

        print("=" * 60)
        print("[OK] PROVEEDOR DE ALMACENAMIENTO OPERACIONAL 100% OK")
        print("=" * 60)
        return True

    except Exception as e:
        print("\n" + "!" * 60)
        print("[ERROR] EN EL SERVICIO DE ALMACENAMIENTO:")
        print(f"Detalle: {e}")
        print("!" * 60)
        print("\nSugerencias de depuración:")
        print(" 1. Si usas Supabase Storage vía protocolo S3, crea una 'Access Key' en:")
        print("    Supabase Dashboard -> Project Settings -> Storage -> S3 Access Keys.")
        print(" 2. El endpoint S3 debe ser: https://<project-ref>.storage.supabase.co/storage/v1/s3")
        print(" 3. Asegúrate de que el bucket (ej: 'storage') esté creado en Supabase Storage.")
        print(" 4. Para volver a almacenamiento local temporalmente: STORAGE_PROVIDER=local en .env")
        return False


if __name__ == "__main__":
    success = asyncio.run(test_storage())
    sys.exit(0 if success else 1)
