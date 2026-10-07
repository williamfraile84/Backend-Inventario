import asyncio
import io
import os
import re
import time
from typing import Dict, Any, Optional

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    sync_playwright = None
    PLAYWRIGHT_AVAILABLE = False

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger("DIANPortalService")


class DIANPortalService:
    """
    Servicio de automatización e integración con el portal oficial de la DIAN:
    https://catalogo-vpfe.dian.gov.co

    Utiliza sync_playwright ejecutado dentro de asyncio.to_thread para garantizar
    compatibilidad total con Windows y el bucle de eventos de Uvicorn/FastAPI.
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
        Punto de entrada asíncrono para FastAPI. Ejecuta el proceso en un hilo aislado
        (asyncio.to_thread) para evitar conflictos de bucle de eventos en Windows.
        """
        return await asyncio.to_thread(
            cls._fetch_document_sync,
            document_key=document_key,
            nit=nit,
            timeout_seconds=timeout_seconds
        )

    @classmethod
    def _fetch_document_sync(
        cls,
        document_key: str,
        nit: Optional[str] = None,
        timeout_seconds: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Automatización desatendida del flujo DIAN Catálogo VPFE:
        1. Abre Google Chrome del sistema en modo visible/asistido.
        2. Digita el NIT en el formulario (#SearchDocumentNit).
        3. Espera de 3 a 5 segundos a que Cloudflare Turnstile valide automáticamente.
        4. Clic 1: Botón 'Buscar' (button.search-document) -> Ingresa a ShowDocumentToPublic.
        5. Extrae metadatos públicos de la página.
        6. Clic 2: Enlace 'Descargar PDF' (a.downloadLink).
        7. Clic 3: Botón 'Aceptar' en el modal de contraseña de la DIAN.
        8. Captura y retorna los bytes del PDF oficial.
        """
        clean_key = re.sub(r"[^0-9a-fA-F]", "", document_key.strip())
        effective_nit = (nit or getattr(settings, "DIAN_RECEPTOR_NIT", "40327379")).strip()
        effective_nit = re.sub(r"\D", "", effective_nit)
        search_url = cls.get_search_url(clean_key)
        timeout_sec = timeout_seconds or getattr(settings, "DIAN_PORTAL_TIMEOUT_SECONDS", 30)

        logger.info(f"Iniciando consulta desatendida en portal DIAN para CUFE {clean_key[:16]}... con NIT {effective_nit}")

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

        if not PLAYWRIGHT_AVAILABLE or sync_playwright is None:
            logger.warning("Playwright sync no está instalado o disponible en este entorno.")
            result["message"] = (
                "El paquete de automatización Playwright no está disponible en este servidor. "
                "Por favor ingrese al portal oficial de la DIAN mediante el enlace proporcionado o cargue el documento manualmente."
            )
            result["requires_user_captcha"] = True
            return result

        browser = None
        try:
            with sync_playwright() as p:
                headless_mode = getattr(settings, "DIAN_HEADLESS_BROWSER", False)
                preferred_channel = getattr(settings, "DIAN_BROWSER_CHANNEL", "chrome")

                launch_kwargs = {
                    "headless": headless_mode,
                    "args": [
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--window-size=1280,800"
                    ]
                }

                # Priorizar Google Chrome del sistema para pasar Turnstile limpiamente
                channels_to_try = [preferred_channel] if preferred_channel else []
                for alt in ["chrome", "msedge"]:
                    if alt not in channels_to_try:
                        channels_to_try.append(alt)
                channels_to_try.append(None)

                for ch in channels_to_try:
                    try:
                        kw = dict(launch_kwargs)
                        if ch:
                            kw["channel"] = ch
                        browser = p.chromium.launch(**kw)
                        logger.info(f"Navegador Playwright lanzado con canal: {ch or 'chromium estándar'}")
                        break
                    except Exception as e_ch:
                        logger.debug(f"Canal '{ch}' no disponible: {e_ch}")

                if not browser:
                    browser = p.chromium.launch(**launch_kwargs)

                context = browser.new_context(
                    accept_downloads=True,
                    viewport={"width": 1280, "height": 800}
                )
                context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

                page = context.new_page()

                try:
                    page.goto(search_url, timeout=timeout_sec * 1000, wait_until="domcontentloaded")
                except Exception as e_nav:
                    logger.warning(f"Timeout o error navegando a portal DIAN: {e_nav}")
                    result["message"] = f"No se pudo conectar al portal de la DIAN ({e_nav})."
                    result["requires_user_captcha"] = True
                    browser.close()
                    return result

                # 1. Verificar presencia de formulario
                try:
                    page.wait_for_selector("#SearchDocumentNit", timeout=10000)
                except Exception:
                    logger.warning("Campo SearchDocumentNit no encontrado en la página de la DIAN.")
                    result["message"] = "Estructura del portal DIAN no reconocida o portal no disponible temporalmente."
                    result["requires_user_captcha"] = True
                    browser.close()
                    return result

                # 2. Asegurar campo NIT diligenciado
                page.click("#SearchDocumentNit")
                page.fill("#SearchDocumentNit", effective_nit)

                # 3. Esperar validación automática de Turnstile (de 3 a 5 segundos)
                token_found = False
                for s in range(1, 8):
                    time.sleep(1)
                    cf_token = page.evaluate(
                        "() => document.querySelector('[name=cf-turnstile-response]')?.value || ''"
                    )
                    if cf_token and len(cf_token) > 10:
                        logger.info(f"Cloudflare Turnstile validado automáticamente en {s}s.")
                        token_found = True
                        break

                # Asistir clic en widget Turnstile si aún no se completó
                if not token_found:
                    logger.info("Asistiendo clic interactivo en widget Turnstile...")
                    for f in page.frames:
                        if "challenges.cloudflare.com" in f.url or "turnstile" in f.url:
                            try:
                                fe = f.frame_element()
                                b = fe.bounding_box()
                                if b:
                                    page.mouse.click(b['x'] + 30, b['y'] + 32)
                            except Exception:
                                pass
                            break

                    for s in range(1, 5):
                        time.sleep(1)
                        cf_token = page.evaluate(
                            "() => document.querySelector('[name=cf-turnstile-response]')?.value || ''"
                        )
                        if cf_token and len(cf_token) > 10:
                            logger.info(f"Cloudflare Turnstile validado tras clic en segundo {s}.")
                            token_found = True
                            break

                if not token_found:
                    logger.info("Cloudflare Turnstile no se completó automáticamente.")
                    result["requires_user_captcha"] = True
                    result["message"] = "El portal de la DIAN solicita verificación de seguridad interactiva."
                    browser.close()
                    return result

                # 4. [Clic 1] Clic en Buscar
                search_btn = page.locator("button.search-document")
                if not search_btn.is_visible():
                    result["requires_user_captcha"] = True
                    browser.close()
                    return result

                search_btn.click()

                # 5. Esperar resultado y vista ShowDocumentToPublic
                try:
                    page.wait_for_url("**/ShowDocumentToPublic**", timeout=15000)
                except Exception:
                    content = page.content()
                    if "Falta Token" in content:
                        result["requires_user_captcha"] = True
                        result["message"] = "Captcha no validado por Cloudflare."
                    elif "no se encuentra registrado" in content or "no encontrado" in content.lower():
                        result["message"] = "El documento no fue encontrado en el catálogo de la DIAN."
                    else:
                        result["requires_user_captcha"] = True
                        result["message"] = "No se pudo acceder a la vista del documento en la DIAN."
                    browser.close()
                    return result

                # 6. Extraer metadatos de ShowDocumentToPublic
                meta = cls._scrape_public_document_page_sync(page)
                result["metadata"] = meta

                # Esperar 2 segundos token secundario en ShowDocumentToPublic
                for _ in range(3):
                    time.sleep(1)
                    t_sec = page.evaluate("() => document.querySelector('[name=cf-turnstile-response]')?.value || ''")
                    if t_sec and len(t_sec) > 10:
                        break

                # 7. [Clic 2 y 3] Descargar PDF oficial y aceptar modal
                pdf_bytes = cls._download_pdf_from_page_sync(page)
                if pdf_bytes:
                    result["success"] = True
                    result["pdf_bytes"] = pdf_bytes
                    result["message"] = "Factura y PDF oficial descargados exitosamente desde la DIAN."
                else:
                    result["message"] = "Se accedió al documento pero no se pudo descargar el archivo PDF automáticamente."

                browser.close()
                return result

        except Exception as e_global:
            logger.error(f"Error en automatización DIAN: {e_global}", exc_info=True)
            result["message"] = f"Error comunicando con portal DIAN: {str(e_global)}"
            result["requires_user_captcha"] = True
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass
            return result

    @classmethod
    def _scrape_public_document_page_sync(cls, page) -> Dict[str, Any]:
        """Extrae los metadatos visibles en ShowDocumentToPublic."""
        meta: Dict[str, Any] = {}
        try:
            body_text = page.inner_text("body")

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
    def _download_pdf_from_page_sync(cls, page) -> Optional[bytes]:
        """
        Descarga el PDF oficial interactuando con la interfaz:
        1. [Clic 2] Clic en 'Descargar PDF' (a.downloadLink).
        2. Espera el modal de contraseña de la DIAN ('Este archivo contiene contraseña...').
        3. [Clic 3] Clic en 'Aceptar' dentro del modal.
        4. Captura y retorna los bytes del PDF descargado.
        """
        try:
            pdf_link = page.locator("a.downloadLink, a:has-text('Descargar PDF'), button:has-text('Descargar PDF')")
            if pdf_link.count() > 0:
                pdf_link.first.click()

                # Esperar modal de confirmación de la DIAN
                aceptar_btn = page.locator(
                    "button:has-text('Aceptar'), a:has-text('Aceptar'), .modal button.btn-primary, .bootbox button.btn-primary"
                )
                try:
                    aceptar_btn.first.wait_for(state="visible", timeout=8000)
                except Exception:
                    logger.debug("Modal de confirmación no apareció o no fue requerido.")

                if aceptar_btn.count() > 0 and aceptar_btn.first.is_visible():
                    with page.expect_download(timeout=15000) as download_info:
                        aceptar_btn.first.click()
                    download = download_info.value
                    path = download.path()
                    if path:
                        with open(path, "rb") as f:
                            return f.read()
                else:
                    # En caso de descarga directa sin modal
                    with page.expect_download(timeout=10000) as download_info:
                        pdf_link.first.click()
                    download = download_info.value
                    path = download.path()
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
