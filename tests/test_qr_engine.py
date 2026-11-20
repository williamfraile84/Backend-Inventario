import io
import pytest
import numpy as np
import cv2
from PIL import Image
from app.services.dian_qr_service import DIANQRService

def test_dian_qr_detection_on_photo_simulation():
    encoder = cv2.QRCodeEncoder.create()
    sample_cufe = "4762e53ca28b77ae5860413db8ae362e7685800954c8afbdd47208fcfdeda4600b6df7a27aa23dac5d00b568ae3b3c0c"
    sample_url = f"https://catalogo-vpfe.dian.gov.co/User/SearchDocument?DocumentKey={sample_cufe}"
    qr_img = encoder.encode(sample_url)
    assert qr_img is not None

    bg = np.ones((2400, 1800, 3), dtype=np.uint8) * 230
    for i in range(2400):
        bg[i, :] = np.clip(bg[i, :] - int(i * 50 / 2400), 40, 255)

    qr_bgr = cv2.cvtColor(qr_img, cv2.COLOR_GRAY2BGR) if len(qr_img.shape) == 2 else qr_img
    qr_resized = cv2.resize(qr_bgr, (220, 220))
    qr_blurred = cv2.GaussianBlur(qr_resized, (3, 3), 0.7)
    bg[1800:2020, 1300:1520] = qr_blurred

    is_success, buffer = cv2.imencode(".jpg", bg, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    assert is_success
    jpg_bytes = buffer.tobytes()

    decoded_text = DIANQRService.detect_and_decode_qr(jpg_bytes, "foto_factura.jpg")
    assert decoded_text is not None
    assert sample_cufe in decoded_text

    result = DIANQRService.process_file_qr(jpg_bytes, "foto_factura.jpg")
    assert result["has_qr"] is True
    assert result["cufe"] == sample_cufe
    assert result["is_dian_qr"] is True

def test_ocr_multiline_cufe_fallback():
    # Simulate an image where the QR is damaged/missing, but the CUFE is printed in text across lines
    sample_cufe = "424e5f5458dae1ae9141360268b746b12c190ed52a5104603ec4da675088d15bed86e887532c74d150a25cea96923dee"
    
    # Create white canvas and put text with OpenCV
    img = np.ones((400, 800, 3), dtype=np.uint8) * 255
    cv2.putText(img, "FACTURA ELECTRONICA DE VENTA", (50, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
    cv2.putText(img, "CUFE:", (50, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)
    # Split across two lines like a real ticket receipt
    cv2.putText(img, sample_cufe[:48], (50, 160), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
    cv2.putText(img, sample_cufe[48:], (50, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)

    is_success, buffer = cv2.imencode(".png", img)
    assert is_success
    img_bytes = buffer.tobytes()

    result = DIANQRService.process_file_qr(img_bytes, "ticket.png")
    print("\nOCR Fallback result:", result)
    assert result["has_qr"] is True
    assert result["cufe"] == sample_cufe

def test_dian_qr_multiline_fields_and_clean_url():
    raw_dian_text = """NumFac:EVN212145
FecFac:2026-08-31
HorFac:17:51:42-05:00
NitFac:822007117-7
DocAdq:40327379
ValFac:118558.00
ValIva:14834.67
ValOtroIm:0.00
ValTolFac:133392.67
CUFE:834f79ffc53a0d4b78098fcf8c6d2a5f0959742a32ddbe8264d91682af3036b14100baf751fb0cd7b19fe5346658e1f0
QRCode:https://catalogo-vpfe.dian.gov.co/document/searchqr?documentkey=834f79ffc53a0d4b78098fcf8c6d2a5f0959742a32ddbe8264d91682af3036b14100baf751fb0cd7b19fe5346658e1f0"""

    parsed = DIANQRService.parse_dian_qr(raw_dian_text)
    assert parsed["has_qr"] is True
    assert parsed["is_dian_qr"] is True
    assert parsed["cufe"] == "834f79ffc53a0d4b78098fcf8c6d2a5f0959742a32ddbe8264d91682af3036b14100baf751fb0cd7b19fe5346658e1f0"
    assert parsed["numero_factura"] == "EVN212145"
    assert parsed["fecha"] == "2026-08-31"
    assert parsed["nit_emisor"] == "8220071177"
    assert parsed["nit_receptor"] == "40327379"
    assert parsed["subtotal"] == 118558.00
    assert parsed["total_impuestos"] == 14834.67
    assert parsed["total"] == 133392.67
    # dian_url MUST be a valid HTTP URL without multiline characters
    assert "\n" not in parsed["dian_url"]
    assert "catalogo-vpfe.dian.gov.co" in parsed["dian_url"]
    assert parsed["dian_url"].startswith("https://catalogo-vpfe.dian.gov.co/")

def test_dian_qr_extraction_from_real_encrypted_pdf():
    import os
    sample_path = os.path.join(
        os.path.dirname(__file__),
        "../../ejemplo factura encriptada DIAN/424e5f5458dae1ae9141360268b746b12c190ed52a5104603ec4da675088d15bed86e887532c74d150a25cea96923dee.pdf"
    )
    if os.path.exists(sample_path):
        with open(sample_path, "rb") as f:
            pdf_bytes = f.read()
        res = DIANQRService.process_file_qr(pdf_bytes, "factura.pdf", allow_ocr_fallback=False)
        assert res["has_qr"] is True
        assert res["is_dian_qr"] is True
        assert res["cufe"] == "424e5f5458dae1ae9141360268b746b12c190ed52a5104603ec4da675088d15bed86e887532c74d150a25cea96923dee"
        assert res["numero_factura"] == "VL012090226"
        assert res["nit_receptor"] == "40327379"
        assert res["total"] == 287481.98



