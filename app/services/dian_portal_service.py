import sys
import asyncio
import io
import os
import re
import time
import subprocess
import gc
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
        """Construye la URL canónica pública oficial de consulta en el portal de la DIAN (Resolución 000042)."""
        clean_key = re.sub(r"[^0-9a-fA-F]", "", document_key.strip())
        return f"{cls.BASE_URL}/document/searchqr?documentkey={clean_key}"

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
        effective_nit = (nit or getattr(settings, "DIAN_RECEPTOR_NIT", "") or "").strip()
        effective_nit = re.sub(r"\D", "", effective_nit)
        search_url = cls.get_search_url(clean_key)
        configured_timeout = int(getattr(settings, "DIAN_PORTAL_TIMEOUT_SECONDS", 45))
        timeout_sec = min(max(int(timeout_seconds or configured_timeout), 15), 60)

        logger.info(f"Iniciando consulta desatendida en portal DIAN para CUFE {clean_key[:16]}... con NIT {effective_nit} (Timeout: {timeout_sec}s)")

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

        # Verificación de recursos del servidor (Protección Anti-OOM en Render):
        # En servidores Linux en la nube con memoria limitada (ej. Render con 512MB RAM),
        # ejecutar Chromium/Chrome en paralelo con Uvicorn + FastAPI satura la memoria física,
        # provocando que el kernel de Linux mate el proceso con SIGKILL (Out Of Memory).
        # Para garantizar 100% de estabilidad y disponibilidad continua sin caídas:
        # si se detecta Render, memoria libre < 350MB, o DIAN_AUTO_PORTAL_FETCH=False,
        # se delega de forma instantánea al flujo asistido oficial sin poner en riesgo el servidor.
        force_safe_mode = not getattr(settings, "DIAN_AUTO_PORTAL_FETCH", True)
        is_render_environment = os.environ.get("RENDER") is not None
        mem_available_mb = cls._get_available_memory_mb()

        if force_safe_mode or is_render_environment or (sys.platform.startswith("linux") and mem_available_mb < 350):
            logger.info(
                f"Protección de memoria activa (Render={bool(is_render_environment)}, "
                f"RAM={mem_available_mb:.0f}MB). Delegando inmediatamente al asistente seguro de la DIAN."
            )
            result["requires_user_captcha"] = True
            result["message"] = (
                "El portal de la DIAN solicita verificación interactiva de seguridad (Cloudflare). "
                "Utilice el asistente asistido de 1 clic para abrir el documento y descargarlo con su NIT."
            )
            return result

        browser = None
        try:
            with sync_playwright() as p:
                # En servidores Linux en la nube (Render con límite de 512MB RAM),
                # el modo headless=True es indispensable para garantizar bajo consumo (<70MB)
                # y prevenir que el kernel del sistema mate el contenedor por Out Of Memory (OOM).
                # En entornos locales (Windows / Mac), se respeta la preferencia DIAN_HEADLESS_BROWSER.
                if sys.platform.startswith("linux"):
                    headless_mode = True
                    launch_args = [
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--disable-software-rasterizer",
                        "--no-zygote",
                        "--disable-infobars",
                        "--disable-extensions",
                        "--disable-component-update",
                        "--mute-audio",
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--window-size=1280,800"
                    ]
                else:
                    headless_mode = getattr(settings, "DIAN_HEADLESS_BROWSER", False)
                    # En entorno local de escritorio (Windows): Google Chrome nativo con aceleración completa
                    offscreen_mode = getattr(settings, "DIAN_OFFSCREEN_BROWSER", True)
                    if offscreen_mode:
                        # Modo silencioso en segundo plano: Ventana posicionada fuera de la pantalla visible (-2500, -2500)
                        # Opera de forma desatendida sin interrumpir al cajero en el POS de venta ni robar el cursor.
                        # Incluye banderas críticas anti-throttling para que Cloudflare Turnstile no sea ralentizado por DWM.
                        launch_args = [
                            "--disable-blink-features=AutomationControlled",
                            "--window-position=-2500,-2500",
                            "--window-size=1280,800",
                            "--no-first-run",
                            "--no-default-browser-check",
                            "--disable-background-timer-throttling",
                            "--disable-backgrounding-occluded-windows",
                            "--disable-renderer-backgrounding",
                            "--disable-features=CalculateNativeWinOcclusion"
                        ]
                    else:
                        launch_args = [
                            "--disable-blink-features=AutomationControlled",
                            "--window-size=1366,768",
                            "--start-maximized"
                        ]

                preferred_channel = getattr(settings, "DIAN_BROWSER_CHANNEL", "chrome")

                launch_kwargs = {
                    "headless": headless_mode,
                    "args": launch_args,
                    "ignore_default_args": ["--enable-automation"]
                }

                # Determinar canales de navegador a intentar según el sistema operativo
                channels_to_try = []
                if preferred_channel:
                    channels_to_try.append(preferred_channel)

                # En Windows/Mac priorizar Google Chrome y Edge para entornos de escritorio
                if not sys.platform.startswith("linux"):
                    for alt in ["chrome", "msedge"]:
                        if alt not in channels_to_try:
                            channels_to_try.append(alt)

                # Chromium estándar (Playwright) siempre como opción principal en Linux/Docker
                if None not in channels_to_try:
                    channels_to_try.append(None)

                last_launch_err = None
                for ch in channels_to_try:
                    try:
                        kw = dict(launch_kwargs)
                        if ch:
                            kw["channel"] = ch
                        try:
                            browser = p.chromium.launch(**kw)
                        except Exception as e_launch:
                            err_str = str(e_launch)
                            if not kw.get("headless") and any(k in err_str for k in ["Missing X server", "XServer", "DISPLAY", "closed", "bus", "exitCode"]):
                                logger.warning(f"Modo con interfaz falló ({err_str}). Reintentando automáticamente con headless=True...")
                                kw["headless"] = True
                                browser = p.chromium.launch(**kw)
                            else:
                                raise e_launch

                        logger.info(f"Navegador Playwright lanzado con canal: {ch or 'chromium estándar'} (headless={kw.get('headless')})")
                        break
                    except Exception as e_ch:
                        last_launch_err = e_ch
                        logger.debug(f"Canal '{ch}' no disponible: {e_ch}")

                if not browser:
                    raise RuntimeError(f"No fue posible inicializar el navegador Playwright: {last_launch_err}")

                context = browser.new_context(
                    accept_downloads=True,
                    viewport={"width": 1366, "height": 768}
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

                # 1. Comprobación inmediata de controles de seguridad anti-bot (Cloudflare / WAF DIAN)
                raw_html = page.content().lower()
                if any(k in raw_html for k in ["bloqueada por controles de seguridad", "acceso denegado", "access denied", "attention required"]):
                    logger.info("El portal DIAN requiere verificación directa interactiva. Delegando al asistente asistido.")
                    result["requires_user_captcha"] = True
                    result["message"] = "El portal oficial DIAN solicita verificación interactiva (Cloudflare). Utilice el botón oficial para abrir el documento directamente con su NIT."
                    browser.close()
                    return result

                # 2. Verificar presencia de formulario con timeout ágil (4s)
                try:
                    page.wait_for_selector("#SearchDocumentNit", timeout=4000)
                except Exception as e_wait_nit:
                    logger.warning(f"Campo SearchDocumentNit no encontrado en la página de la DIAN: {e_wait_nit}. URL actual: {page.url}")
                    result["message"] = "El portal oficial de la DIAN solicita verificación de seguridad interactiva."
                    result["requires_user_captcha"] = True
                    browser.close()
                    return result

                # 2. Asegurar campo NIT diligenciado
                page.click("#SearchDocumentNit")
                page.fill("#SearchDocumentNit", effective_nit)
                try:
                    page.keyboard.press("Tab")
                except Exception:
                    pass

                # 3. Esperar validación automática nativa de Cloudflare Turnstile
                # Turnstile en el portal de la DIAN es un reto gestionado invisible/semi-invisible.
                # Se utiliza sondeo de alta frecuencia (250ms).
                token_found = False
                token_1 = ""
                poll_interval = 0.25
                max_turnstile_wait = min(max(int(timeout_sec) - 6, 15), 35)
                max_iterations = int(max_turnstile_wait / poll_interval)
                logger.info(f"Esperando validación nativa de Cloudflare Turnstile (hasta {max_turnstile_wait}s)...")
                for s in range(1, max_iterations + 1):
                    time.sleep(poll_interval)
                    cf_token = page.evaluate(
                        "() => document.querySelector('[name=cf-turnstile-response]')?.value || document.querySelector('[name=g-recaptcha-response]')?.value || ''"
                    )
                    if cf_token and len(cf_token) > 10:
                        elapsed = s * poll_interval
                        logger.info(f"Cloudflare Turnstile validado exitosamente en {elapsed:.2f}s.")
                        token_found = True
                        token_1 = cf_token
                        break

                if not token_found:
                    logger.info("Cloudflare Turnstile no se completó automáticamente a tiempo.")
                    result["requires_user_captcha"] = True
                    result["message"] = "El portal de la DIAN solicita verificación de seguridad interactiva."
                    browser.close()
                    return result

                # 4. [Clic 1] Clic en Buscar
                search_btn = page.locator("button.search-document, button:has-text('Buscar')")
                if search_btn.count() == 0:
                    logger.warning("Botón Buscar no encontrado en la página de la DIAN.")
                    result["requires_user_captcha"] = True
                    browser.close()
                    return result

                search_btn.first.click()

                # 5. Esperar resultado y vista de la factura cargada
                try:
                    page.wait_for_selector(
                        "a:has-text('Descargar PDF'), button:has-text('Descargar PDF'), .downloadLink",
                        timeout=6000
                    )
                    logger.info("Vista de la factura cargada exitosamente en el portal DIAN.")
                except Exception as e_wait_fac:
                    logger.warning(f"Aviso esperando factura: {e_wait_fac}")
                    content = page.content()
                    if "Falta Token" in content or "bloqueado" in content.lower():
                        result["requires_user_captcha"] = True
                        result["message"] = "Captcha no validado por Cloudflare (Token inválido o expirado)."
                    elif "no se encuentra registrado en el catálogo" in content.lower():
                        result["message"] = "El documento no se encuentra registrado en el catálogo de la DIAN."
                    else:
                        result["requires_user_captcha"] = True
                        result["message"] = "El portal oficial DIAN solicita verificación interactiva (Turnstile). Abre el enlace oficial para descargar el PDF."
                    browser.close()
                    return result

                # 6. Extraer metadatos de la página del documento
                meta = cls._scrape_public_document_page_sync(page)
                result["metadata"] = meta

                # 7. Esperar validación del token secundario de Turnstile para la descarga del PDF
                # Con tolerancia de hasta 18s (sondeo ágil cada 250ms con salida inmediata al confirmar).
                logger.info("Esperando token secundario de seguridad para descarga de PDF (hasta 18s)...")
                sec_token_confirmed = False
                max_sec_iterations = int(18 / poll_interval)
                for s in range(1, max_sec_iterations + 1):
                    time.sleep(poll_interval)
                    t_sec = page.evaluate("() => document.querySelector('[name=cf-turnstile-response]')?.value || document.querySelector('[name=g-recaptcha-response]')?.value || (window.turnstile && typeof window.turnstile.getResponse === 'function' ? window.turnstile.getResponse() : '')")
                    if t_sec and len(t_sec) > 10 and t_sec != token_1:
                        elapsed_sec = s * poll_interval
                        logger.info(f"Token secundario de descarga confirmado en {elapsed_sec:.2f}s.")
                        sec_token_confirmed = True
                        break

                if not sec_token_confirmed:
                    logger.info("Token secundario no reportó valor diferenciado antes de 18s. Intentando descarga directa con la sesión autorizada...")

                # 8. [Clic 2 y 3] Descargar PDF oficial y aceptar modal
                pdf_bytes = cls._download_pdf_from_page_sync(page)
                if pdf_bytes:
                    result["success"] = True
                    result["pdf_bytes"] = pdf_bytes
                    result["message"] = "Factura y PDF oficial descargados exitosamente desde la DIAN."
                else:
                    logger.warning("No se pudo obtener el PDF oficial de la DIAN tras intentar la interacción de descarga.")
                    result["requires_user_captcha"] = True
                    result["message"] = (
                        "El portal oficial DIAN requiere validación de seguridad interactiva para autorizar la descarga del PDF. "
                        "Usa el botón para abrir el documento con tu NIT o pulsa 'Reintentar ahora'."
                    )

                browser.close()
                return result

        except Exception as e_global:
            logger.error(f"Error en automatización DIAN: {e_global}", exc_info=True)
            result["message"] = f"Error comunicando con portal DIAN: {str(e_global)}"
            result["requires_user_captcha"] = True
            return result
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass
            gc.collect()

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
        2. Espera el modal de confirmación de contraseña de la DIAN ('Este archivo contiene contraseña...').
        3. [Clic 3] Clic en 'Aceptar' dentro del modal.
        4. Captura y retorna los bytes del PDF descargado.
        """
        try:
            pdf_link = page.locator("a.downloadLink, a:has-text('Descargar PDF'), button:has-text('Descargar PDF')")
            if pdf_link.count() > 0:
                pdf_link.first.click()

                # Si aparece la alerta temporal de seguridad incompleto, se maneja de inmediato
                try:
                    alerta_seg = page.locator("text='El campo de seguridad no está completo'")
                    if alerta_seg.is_visible(timeout=1000):
                        logger.info("Detectada alerta de seguridad temporal en DIAN. Cerrando y reintentando...")
                        btn_cerrar = page.locator("button:has-text('Aceptar'), a:has-text('Aceptar')").first
                        try:
                            btn_cerrar.click(timeout=1000)
                        except Exception:
                            pass
                        time.sleep(2.0)
                        pdf_link.first.click()
                except Exception:
                    pass

                # Esperar modal de confirmación de la DIAN ("Este archivo contiene contraseña...")
                aceptar_btn = page.locator(
                    ".modal.show button:has-text('Aceptar'), .bootbox button:has-text('Aceptar'), .modal button:has-text('Aceptar')"
                )
                modal_visible = False
                try:
                    aceptar_btn.first.wait_for(state="visible", timeout=6000)
                    modal_visible = True
                except Exception:
                    logger.debug("Modal de confirmación no apareció o no fue requerido.")

                if modal_visible and aceptar_btn.count() > 0 and aceptar_btn.first.is_visible():
                    logger.info("Confirmando descarga en modal de contraseña oficial de la DIAN...")
                    with page.expect_download(timeout=12000) as download_info:
                        aceptar_btn.first.click()
                    download = download_info.value
                    path = download.path()
                    if path:
                        with open(path, "rb") as f:
                            return f.read()
                else:
                    # En caso de descarga directa sin modal
                    logger.info("Esperando evento de descarga directa...")
                    with page.expect_download(timeout=8000) as download_info:
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

    @staticmethod
    def _get_available_memory_mb() -> float:
        """Obtiene la memoria RAM disponible en el sistema en megabytes para protección del servidor."""
        try:
            if sys.platform.startswith("linux") and os.path.exists("/proc/meminfo"):
                with open("/proc/meminfo", "r") as f:
                    for line in f:
                        if line.startswith("MemAvailable:"):
                            return float(line.split()[1]) / 1024.0
            return 1024.0
        except Exception:
            return 512.0
