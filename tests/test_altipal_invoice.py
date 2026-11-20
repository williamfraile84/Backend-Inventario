import pytest
from pathlib import Path
from app.services.invoice_parser_service import PDFInvoiceEngine

@pytest.mark.anyio
async def test_altipal_invoice_parsing():
    pdf_path = Path(__file__).resolve().parent.parent.parent / "ejemplo factura encriptada DIAN" / "424e5f5458dae1ae9141360268b746b12c190ed52a5104603ec4da675088d15bed86e887532c74d150a25cea96923dee.pdf"
    if not pdf_path.exists():
        pytest.skip(f"Muestra de factura DIAN no encontrada en {pdf_path} (archivo privado excluido en .gitignore)")

    with open(pdf_path, "rb") as f:
        file_bytes = f.read()

    engine = PDFInvoiceEngine()
    data = await engine.extract_invoice(file_bytes, pdf_path.name)

    assert "ALTIPAL" in (data.get("proveedor") or "").upper()
    assert data.get("numero_factura") == "VL01-2090226"
    items = data.get("items", [])
    assert len(items) == 10

    # Item 1: JAB PROTEX AVENA 110GR x72Un - DIAN code 94 (Unidad)
    item1 = items[0]
    assert item1["unidades_por_presentacion"] == 1.0
    assert item1["presentacion"] == "Unidad"
    assert item1["cantidad"] == 12.0
    assert item1["precio_unitario"] == 3269.30
    assert item1["descuento_valor"] == 9588.99
    assert item1["modo_descuento"] == "valor"
    assert abs(item1["descuento"] - 24.44) < 0.05

    # Item 6: JAB PROTEX LIMPIEZA PROFUNDA 110GR x24Dp x3Un - DIAN code ZZ (triopack)
    item6 = items[5]
    print("\nITEM 6 KEYS AND VALUES:")
    for k, v in item6.items():
        print(f"  {k}: {v}")

    assert item6["unidades_por_presentacion"] == 3.0
    assert item6["presentacion"] == "Paquete"
    assert item6["cantidad"] == 4.0
