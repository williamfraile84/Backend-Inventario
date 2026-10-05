#!/usr/bin/env python3
"""
==============================================================================
Script de Descarga de Modelos OCR - Fruver POS Manager
==============================================================================
Descarga los pesos neuronales del modelo Server PP-OCRv4 (ONNX, ~90.5 MB)
para el reconocimiento de texto y facturas de alta precisión.

Uso:
    python backend/scripts/download_models.py
    python backend/scripts/download_models.py --force
"""

import os
import sys
import time
import argparse
import urllib.request
import urllib.error
from pathlib import Path

# Soporte seguro para codificación en Windows terminal (evita UnicodeEncodeError)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Directorio base del backend
BACKEND_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = BACKEND_DIR / "models_ocr"

MODEL_FILENAME = "ch_PP-OCRv4_rec_server_infer.onnx"
EXPECTED_MIN_SIZE_BYTES = 80 * 1024 * 1024  # Al menos 80 MB

# Enlaces de descarga (con mirrors de respaldo)
MODEL_URLS = [
    {
        "name": "Hugging Face (SWHL/RapidOCR)",
        "url": "https://huggingface.co/SWHL/RapidOCR/resolve/main/PP-OCRv4/ch_PP-OCRv4_rec_server_infer.onnx"
    },
    {
        "name": "Hugging Face Mirror",
        "url": "https://hf-mirror.com/SWHL/RapidOCR/resolve/main/PP-OCRv4/ch_PP-OCRv4_rec_server_infer.onnx"
    }
]


def format_size(bytes_num: int) -> str:
    """Formatea bytes a MB legible."""
    return f"{bytes_num / (1024 * 1024):.2f} MB"


def download_with_progress(url: str, target_path: Path) -> bool:
    """Descarga un archivo con barra de progreso en consola."""
    temp_path = target_path.with_suffix(".tmp")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FruverPOS-ModelDownloader/1.0"
    }

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as response, open(temp_path, "wb") as out_file:
            total_size = int(response.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 1024 * 1024  # 1 MB chunks
            start_time = time.time()

            print(f"[*] Conectado. Tamaño total reportado: {format_size(total_size) if total_size else 'Desconocido'}")
            print(f"[*] Descargando en: {target_path.name}...\n")

            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                out_file.write(chunk)
                downloaded += len(chunk)

                elapsed = time.time() - start_time
                speed = (downloaded / (1024 * 1024)) / elapsed if elapsed > 0 else 0

                if total_size > 0:
                    percent = (downloaded / total_size) * 100
                    bar_length = 35
                    filled = int(bar_length * downloaded // total_size)
                    bar = "#" * filled + "-" * (bar_length - filled)
                    sys.stdout.write(
                        f"\r    [{bar}] {percent:5.1f}% | {format_size(downloaded)} / {format_size(total_size)} | {speed:5.2f} MB/s"
                    )
                else:
                    sys.stdout.write(
                        f"\r    Descargado: {format_size(downloaded)} | {speed:5.2f} MB/s"
                    )
                sys.stdout.flush()

            print("\n")

        # Validación mínima de tamaño
        if downloaded < EXPECTED_MIN_SIZE_BYTES:
            print(f"[!] Advertencia: El archivo descargado parece incompleto ({format_size(downloaded)}).")
            if temp_path.exists():
                temp_path.unlink()
            return False

        # Renombrar atómicamente el archivo temporal
        if target_path.exists():
            target_path.unlink()
        temp_path.rename(target_path)
        return True

    except Exception as e:
        print(f"\n[ERROR] Durante la descarga: {e}")
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Descarga el modelo neuronal Server PP-OCRv4 para OCR de alta precisión."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Forzar descarga y sobrescribir el modelo si ya existe."
    )
    parser.add_argument(
        "--dest",
        type=str,
        default=str(MODELS_DIR),
        help=f"Directorio de destino (por defecto: {MODELS_DIR})"
    )
    args = parser.parse_args()

    dest_dir = Path(args.dest)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target_file = dest_dir / MODEL_FILENAME

    print("=" * 70)
    print("  DESCARGA DE MODELOS OCR - FRUVER POS MANAGER")
    print("=" * 70)
    print(f"[*] Destino: {target_file}")

    if target_file.exists() and not args.force:
        current_size = target_file.stat().st_size
        if current_size >= EXPECTED_MIN_SIZE_BYTES:
            print("\n[OK] El modelo ya se encuentra instalado:")
            print(f"   Archivo: {target_file.name}")
            print(f"   Tamano : {format_size(current_size)}")
            print(f"   Ruta   : {target_file}")
            print("\n[*] Si deseas forzar la descarga de nuevo, ejecuta:")
            print("   python backend/scripts/download_models.py --force")
            print("\n[*] Para activar el modelo Server en tu backend, asegurate de tener en backend/.env:")
            print("   OCR_USE_SERVER_MODEL=true\n")
            return 0
        else:
            print(f"\n[!] El modelo existente parece corrupto o incompleto ({format_size(current_size)}). Reintentando...")

    print("\n[*] Iniciando descarga del modelo Server PP-OCRv4 (~90.5 MB)...")

    success = False
    for mirror in MODEL_URLS:
        print(f"\n[*] Probando fuente: {mirror['name']}")
        print(f"    URL: {mirror['url']}")
        if download_with_progress(mirror['url'], target_file):
            success = True
            break
        print("[!] Intentando siguiente fuente...")

    if success:
        final_size = target_file.stat().st_size
        print("=" * 70)
        print("[OK] MODELO DESCARGADO E INSTALADO CON EXITO")
        print(f"   Archivo: {target_file.name} ({format_size(final_size)})")
        print(f"   Ubicacion: {target_file}")
        print("\n[*] Siguiente paso para activarlo en tu backend:")
        print("   1. Abre o edita tu archivo 'backend/.env'")
        print("   2. Configura: OCR_USE_SERVER_MODEL=true")
        print("=" * 70)
        return 0
    else:
        print("=" * 70)
        print("[ERROR] No se pudo descargar el modelo desde ninguna de las fuentes.")
        print("   Puedes descargarlo manualmente desde:")
        print(f"   {MODEL_URLS[0]['url']}")
        print(f"   Y colocarlo en: {dest_dir}")
        print("=" * 70)
        return 1


if __name__ == "__main__":
    sys.exit(main())
