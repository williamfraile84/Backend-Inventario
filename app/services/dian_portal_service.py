import asyncio
import io
import re
from typing import Dict, Any, Optional
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    async_playwright = None
    PLAYWRIGHT_AVAILABLE = False

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger("DIANPortalService")


class DIANPortalService:
    """
    Servicio de automatización e integración con el portal oficial de la DIAN:
    https://catalogo-vpfe.dian.gov.co
    """

    BASE_URL = "https://catalogo-vpfe.dian.gov.co"

    @classmethod
    def get_search_url(cls, document_key: str) -> str:
        """Construye la URL directa de consulta en el portal de la DIAN."""
        clean_key = re.sub(r"[^0-9a-fA-F]", "", document_key.strip())
        return f"{cls.BASE_URL}/User/SearchDocument?DocumentKey={clean_key}"

    @classmethod
    async def fetch_document(
        cls,
        document_key: str,
        nit: Optional[str] = None,
        timeout_seconds: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Intenta consultar el documento y descargar el PDF oficial de forma desatendida.
        Retorna los bytes del PDF y metadatos si tiene éxito, o un reporte estructurado
        para el asistente de usuario si Cloudflare Turnstile requiere interacción.
        """
        clean_key = re.sub(r"[^0-9a-fA-F]", "", document_key.strip())
        effective_nit = (nit or getattr(settings, "DIAN_RECEPTOR_NIT", "40327379")).strip()
        effective_nit = re.sub(r"\D", "", effective_nit)
        search_url = cls.get_search_url(clean_key)
        timeout_sec = timeout_seconds or getattr(settings, "DIAN_PORTAL_TIMEOUT_SECONDS", 25)

        logger.info(f"Iniciando consulta en portal DIAN para CUFE {clean_key[:16]}... con NIT {effective_nit}")

        result: Dict[str, Any] = {
            "success": False,
            "document_key": clean_key,
            "nit": effective_nit,
            "dian_url": search_url,
            "requires_user_captcha": False,
            "pdf_bytes": None,
            "metadata": {},
            "message": ""
        }

        if not PLAYWRIGHT_AVAILABLE or async_playwright is None:
            logger.warning("Playwright no está instalado o disponible en este entorno.")
            result["message"] = (
                "El paquete de automatización Playwright no está disponible en este servidor. "
                "Por favor ingrese al portal oficial de la DIAN mediante el enlace proporcionado o cargue el documento manualmente."
            )
            result["requires_user_captcha"] = True
            return result

        try:
            async with async_playwright() as p:
                headless_mode = getattr(settings, "DIAN_HEADLESS_BROWSER", True)
                browser = await p.chromium.launch(
                    headless=headless_mode,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-dev-shm-usage"
                    ]
                )
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                    viewport={"width": 1280, "height": 800}
                )
                await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

                page = await context.new_page()

                try:
                    await page.goto(search_url, timeout=timeout_sec * 1000, wait_until="domcontentloaded")
                except Exception as e_nav:
                    logger.warning(f"Timeout o error navegando a DIAN: {e_nav}")
                    result["message"] = f"No se pudo conectar al portal de la DIAN ({e_nav})."
                    result["requires_user_captcha"] = True
                    await browser.close()
                    return result

                # 1. Verificar presencia de formulario
                try:
                    await page.wait_for_selector("#DocumentKey", timeout=8000)
                except Exception:
                    logger.warning("Campo DocumentKey no encontrado en la página de la DIAN.")
                    result["message"] = "Estructura del portal DIAN no reconocida o portal no disponible."
                    result["requires_user_captcha"] = True
                    await browser.close()
                    return result

                # 2. Asegurar campo NIT diligenciado
                await page.fill("#SearchDocumentNit", effective_nit)

                # 3. Esperar token Turnstile (hasta 6 segundos)
                token_found = False
                for _ in range(6):
                    cf_token = await page.evaluate(
                        "() => document.querySelector('[name=cf-turnstile-response]')?.value || ''"
                    )
                    if cf_token and len(cf_token) > 10:
                        token_found = True
                        break
                    await asyncio.sleep(1)

                if not token_found:
                    logger.info("Cloudflare Turnstile no se auto-completó en modo desatendido.")
                    result["requires_user_captcha"] = True
                    result["message"] = "Cloudflare Turnstile requiere verificación. Puedes abrir el portal en 1 clic y descargar el PDF."
                    await browser.close()
                    return result

                # 4. Enviar formulario de búsqueda
                search_btn = page.locator("button.search-document")
                if not await search_btn.is_visible():
                    result["requires_user_captcha"] = True
                    await browser.close()
                    return result

                await search_btn.click()

                # 5. Esperar resultado
                try:
                    await page.wait_for_url("**/ShowDocumentToPublic**", timeout=12000)
                except Exception:
                    # Verificar si hubo error en página
                    content = await page.content()
                    if "Falta Token" in content:
                        result["requires_user_captcha"] = True
                        result["message"] = "Captcha no validado por Cloudflare."
                    elif "no se encuentra registrado" in content or "no encontrado" in content.lower():
                        result["message"] = "El documento no fue encontrado en el catálogo de la DIAN."
                    else:
                        result["requires_user_captcha"] = True
                        result["message"] = "No se pudo acceder a la vista del documento."
                    await browser.close()
                    return result

                # 6. Extraer metadatos de ShowDocumentToPublic
                meta = await cls._scrape_public_document_page(page)
                result["metadata"] = meta

                # 7. Descargar PDF oficial
                pdf_bytes = await cls._download_pdf_from_page(page)
                if pdf_bytes:
                    result["success"] = True
                    result["pdf_bytes"] = pdf_bytes
                    result["message"] = "Factura y PDF oficial descargados exitosamente desde la DIAN."
                else:
                    result["message"] = "Se obtuvieron metadatos pero no se pudo descargar el PDF automáticamente."

                await browser.close()
                return result

        except Exception as e_global:
            logger.error(f"Error en automatización DIAN: {e_global}")
            result["message"] = f"Error comunicando con portal DIAN: {str(e_global)}"
            result["requires_user_captcha"] = True
            return result

    @classmethod
    async def _scrape_public_document_page(cls, page) -> Dict[str, Any]:
        """Extrae los metadatos visibles en ShowDocumentToPublic."""
        meta: Dict[str, Any] = {}
        try:
            body_text = await page.inner_text("body")
            
            # Serie y Folio
            serie_match = re.search(r"Serie:\s*([A-Za-z0-9_-]+)", body_text)
            folio_match = re.search(r"Folio:\s*([0-9]+)", body_text)
            meta["serie"] = serie_match.group(1).strip() if serie_match else ""
            meta["folio"] = folio_match.group(1).strip() if folio_match else ""
            meta["numero_factura"] = f"{meta['serie']}{meta['folio']}".strip() or meta["folio"]

            # Fecha de emisión
            date_match = re.search(r"Fecha de emisión[^:]*:\s*([0-9]{2,4}[-/][0-9]{2}[-/][0-9]{2,4})", body_text)
            if date_match:
                meta["fecha"] = date_match.group(1).strip()

            # Emisor
            emisor_nit_match = re.search(r"DATOS DEL EMISOR.*?NIT:\s*([0-9.-]+)", body_text, re.DOTALL)
            emisor_nom_match = re.search(r"DATOS DEL EMISOR.*?Nombre:\s*([^\r\n]+)", body_text, re.DOTALL)
            if emisor_nit_match:
                meta["nit_emisor"] = re.sub(r"\D", "", emisor_nit_match.group(1))
            if emisor_nom_match:
                meta["proveedor"] = emisor_nom_match.group(1).strip()

            # Receptor
            rec_nit_match = re.search(r"DATOS DEL RECEPTOR.*?NIT:\s*([0-9.-]+)", body_text, re.DOTALL)
            rec_nom_match = re.search(r"DATOS DEL RECEPTOR.*?Nombre:\s*([^\r\n]+)", body_text, re.DOTALL)
            if rec_nit_match:
                meta["nit_receptor"] = re.sub(r"\D", "", rec_nit_match.group(1))
            if rec_nom_match:
                meta["nombre_receptor"] = rec_nom_match.group(1).strip()

            # Totales
            iva_match = re.search(r"IVA:\s*\$?\s*([0-9.,]+)", body_text)
            tot_match = re.search(r"Total:\s*\$?\s*([0-9.,]+)", body_text)
            if iva_match:
                meta["total_impuestos"] = cls._parse_cop(iva_match.group(1))
            if tot_match:
                meta["total"] = cls._parse_cop(tot_match.group(1))

        except Exception as e_scr:
            logger.warning(f"Error scraping metadatos públicos DIAN: {e_scr}")

        return meta

    @classmethod
    async def _download_pdf_from_page(cls, page) -> Optional[bytes]:
        """Busca el botón 'Descargar PDF' y captura el archivo descargado."""
        try:
            pdf_link = page.locator("a:has-text('Descargar PDF'), button:has-text('Descargar PDF')")
            if await pdf_link.count() > 0:
                async with page.expect_download(timeout=10000) as download_info:
                    await pdf_link.first.click()
                download = await download_info.value
                path = await download.path()
                if path:
                    with open(path, "rb") as f:
                        return f.read()
        except Exception as e_dl:
            logger.warning(f"No se pudo descargar el PDF automáticamente: {e_dl}")
        return None

    @staticmethod
    def _parse_cop(val: str) -> float:
        try:
            s = val.replace("$", "").replace(".", "").replace(",", ".").strip()
            return float(s)
        except Exception:
            return 0.0

