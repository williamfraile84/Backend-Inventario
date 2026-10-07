import asyncio
import json
import re
import ssl
import time
import urllib.request
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
import httpx
import html
from bs4 import BeautifulSoup

from app.core.config import settings
from app.models.schemas import ProductLookupResponse, ProductItem, ProductSearchResponse, PriceUpdateResponse

logger = logging.getLogger("POS_Service")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

_CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "categories_catalog.json"
_LOCAL_CATALOG: Dict[str, Any] = {}
if _CATALOG_PATH.exists():
    try:
        with open(_CATALOG_PATH, "r", encoding="utf-8") as _f:
            _LOCAL_CATALOG = json.load(_f)
        logger.info(f"✅ Catálogo de categorías cargado exitosamente: {len(_LOCAL_CATALOG)} departamentos maestros.")
    except Exception as _e:
        logger.warning(f"⚠️ No se pudo pre-cargar categories_catalog.json: {_e}")

def clean_pos_text(text: Optional[str]) -> str:
    """Decodifica entidades HTML (&Eacute;, &eacute;, &aacute;, etc.) y caracteres UTF-8."""
    if not text:
        return ""
    res = html.unescape(str(text))
    if "&" in res and ";" in res:
        res = html.unescape(res)
    return res.strip()


def parse_cop_currency(text: str) -> float:
    """Parsea representaciones monetarias colombianas como '$1.700', '$ 2,500.50' a float."""
    if not text:
        return 0.0
    cleaned = re.sub(r'[^0-9,.-]', '', text)
    if not cleaned:
        return 0.0
    
    if '.' in cleaned and ',' in cleaned:
        parts = cleaned.split(',')
        cleaned = parts[0].replace('.', '') + '.' + parts[1]
    elif '.' in cleaned and ',' not in cleaned:
        cleaned = cleaned.replace('.', '')
    elif ',' in cleaned and '.' not in cleaned:
        if len(cleaned.split(',')[1]) == 2:
            cleaned = cleaned.replace(',', '.')
        else:
            cleaned = cleaned.replace(',', '')
    try:
        return float(cleaned)
    except ValueError:
        return 0.0

def format_cop_currency(val: float) -> str:
    """Formatea valor float a representación COP con separador de miles con punto."""
    if val is None:
        val = 0.0
    if val == int(val):
        formatted = f"{int(val):,}".replace(",", ".")
    else:
        formatted = f"{val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"${formatted}"

POS_DEPARTMENTS: Dict[str, str] = {
    "D06": "Agro y Jardín",
    "D07": "Alimentos - Bebidas, Pasabocas y dulces",
    "D57": "Alimentos - Carnes y Charcuteria",
    "D58": "Alimentos - Despensa",
    "D56": "Alimentos - Frutas y Verduras",
    "D59": "Alimentos - Lacteos y congelados",
    "D36": "Alimentos - Panaderías",
    "D08": "Animales y Mascotas",
    "D09": "Antigüedades y otros",
    "D12": "Belleza y Cuidado Personal",
    "D13": "Boletas, cursos e infoproductos",
    "D14": "Cámaras y Accesorios",
    "D15": "Carros y Vehículo Pesados",
    "DH1": "Categorías prohibidas",
    "D16": "Celulares y Teléfonos",
    "D17": "Computación",
    "D84": "Cosméticos, Maquillaje y Spa",
    "D19": "Deportes y Fisioterapia",
    "D97": "Eléctricos",
    "D18": "Electrodomésticos y videojuegos",
    "D21": "Electrónica, Audio y Video",
    "D32": "Equipamiento Médico y odontológico",
    "D46": "Farmacia",
    "D89": "Ferretería y construcción",
    "D91": "Herramientas y maquinaria",
    "D24": "Hogar, muebles e iluminación",
    "D93": "Iluminación y Decoración",
    "D25": "Industrias y Oficinas",
    "D26": "Inmuebles",
    "D05": "Instrumentos Musicales",
    "D98": "Joyas, Bisutería y Cacharrería",
    "D23": "Juegos y Juguetes",
    "D27": "Libros, Revistas",
    "D60": "Limpieza y Aseo",
    "D37": "Moda Hombre",
    "D11": "Moda infantil y Bebés",
    "D38": "Moda Mujer",
    "D61": "Motos y Accesorios",
    "D34": "Otros Adultos",
    "D10": "Papelería, Miscelanea y Piñateria",
    "D29": "Piñatería y Fiestas",
    "D90": "Pinturas , Pisos y acabados",
    "D63": "Productos naturales",
    "D54": "Productos religiosos",
    "D43": "Relojes y Gafas",
    "D35": "Restaurante",
    "D33": "Servicios",
    "D51": "Velas y Aromas",
    "D40": "Vinos y Licores",
    "DP01": "General"
}

class POSService:
    _instance: Optional['POSService'] = None

    def __init__(self):
        self.http_client: Optional[httpx.AsyncClient] = None
        self.is_initialized = False
        self.last_error: Optional[str] = None
        
        # Gestión de dominios primario y secundario para Failover
        self.primary_url = settings.POS_PRIMARY_URL.rstrip('/') + '/'
        self.secondary_url = settings.POS_SECONDARY_URL.rstrip('/') + '/'
        self.base_url = self.primary_url

        # Caché de alta velocidad en memoria (TTL: 5 minutos)
        self._product_cache: Dict[str, Tuple[ProductLookupResponse, float]] = {}
        self._cache_ttl: float = 300.0  # 5 minutos
        self._categories_by_dep_cache: Dict[str, List[Dict[str, str]]] = {}

        # Locks y tareas de fondo
        self._init_lock = asyncio.Lock()
        self._auth_lock = asyncio.Lock()
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._last_active_ts: float = 0.0

    @classmethod
    def get_instance(cls) -> 'POSService':
        if cls._instance is None:
            cls._instance = POSService()
        return cls._instance

    # -------------------------------------------------------------------------
    # SALUD Y CONMUTACIÓN DE DOMINIOS (FAILOVER)
    # -------------------------------------------------------------------------
    async def test_domain_health(self, url: str) -> bool:
        """Verificación rápida de conectividad al dominio del POS (timeout 2.0s)."""
        target = f"{url.rstrip('/')}/index.php/login"
        try:
            req = urllib.request.Request(
                target,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            )
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            def _check():
                with urllib.request.urlopen(req, timeout=2.0, context=ctx) as resp:
                    return resp.status in (200, 301, 302, 303)

            return await asyncio.to_thread(_check)
        except Exception:
            return False

    async def ensure_active_domain(self, force_switch: bool = False) -> str:
        current = self.base_url
        alternate = self.secondary_url if current == self.primary_url else self.primary_url

        if force_switch:
            logger.warning(f"🔄 Forzando conmutación a {alternate}...")
            if await self.test_domain_health(alternate):
                self.base_url = alternate
                self._recreate_http_client()
                logger.info(f"✅ Conmutado exitosamente a dominio de respaldo: {self.base_url}")
                return self.base_url
            if await self.test_domain_health(current):
                self.base_url = current
                self._recreate_http_client()
                return self.base_url
            self._raise_both_domains_down()

        if await self.test_domain_health(current):
            return self.base_url

        logger.warning(f"⚠️ Dominio {current} no responde. Conmutando a {alternate}...")
        if await self.test_domain_health(alternate):
            self.base_url = alternate
            self._recreate_http_client()
            logger.info(f"✅ Conmutación automática a: {self.base_url}")
            return self.base_url

        self._raise_both_domains_down()

    def _recreate_http_client(self):
        limits = httpx.Limits(max_keepalive_connections=30, max_connections=60, keepalive_expiry=60.0)
        self.http_client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json, text/javascript, text/html, */*; q=0.01",
            },
            timeout=httpx.Timeout(8.0, connect=3.0),
            limits=limits,
            verify=False,
            follow_redirects=True
        )

    def _raise_both_domains_down(self):
        err_msg = (
            "Error crítico: Ambos dominios del POS (csopos.co y softwarepos.online) "
            "se encuentran temporalmente fuera de línea. Verifique su conexión a internet."
        )
        self.last_error = err_msg
        logger.error(f"❌ {err_msg}")
        raise RuntimeError(err_msg)

    # -------------------------------------------------------------------------
    # CACHÉ EN MEMORIA (0ms)
    # -------------------------------------------------------------------------
    def _get_from_cache(self, barcode: str) -> Optional[ProductLookupResponse]:
        item = self._product_cache.get(barcode)
        if item:
            cached_resp, timestamp = item
            if time.time() - timestamp < self._cache_ttl:
                return cached_resp
            else:
                del self._product_cache[barcode]
        return None

    def _save_to_cache(self, barcode: str, response: ProductLookupResponse):
        if response.found:
            self._product_cache[barcode] = (response, time.time())
            if response.modal_barcode and response.modal_barcode != barcode:
                self._product_cache[response.modal_barcode] = (response, time.time())
            if response.item_id and response.item_id != barcode:
                self._product_cache[str(response.item_id)] = (response, time.time())

    def _invalidate_cache(self, barcode: Optional[str] = None):
        if barcode:
            self._product_cache.pop(barcode, None)
        else:
            self._product_cache.clear()
        logger.info(f"🧹 Caché invalidada{' para ' + barcode if barcode else ' completamente'}.")

    # -------------------------------------------------------------------------
    # INICIALIZACIÓN Y GESTIÓN DE SESIÓN HTTP ULTRARRÁPIDA
    # -------------------------------------------------------------------------
    async def initialize(self):
        """Inicia el motor HTTP persistente y autentica la sesión de alta velocidad."""
        async with self._init_lock:
            if self.is_initialized and self.http_client:
                return
            try:
                self.last_error = None
                await self.ensure_active_domain()
                self._recreate_http_client()
                
                logger.info(f"⚡ Iniciando motor HTTP de alta velocidad en {self.base_url}...")
                await self._login_direct()
                self.is_initialized = True
                self._last_active_ts = time.time()

                # Iniciar tarea periódica de heartbeat para mantener la sesión siempre lista
                if self._heartbeat_task is None or self._heartbeat_task.done():
                    self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
                
                logger.info("✅ POS Service inicializado exitosamente con motor HTTP directo.")
            except Exception as e:
                self.is_initialized = False
                self.last_error = str(e)
                logger.error(f"❌ Error al inicializar POS Service: {e}")
                if "Ambos dominios" not in str(e):
                    try:
                        logger.warning("Reintentando inicialización en dominio alternativo...")
                        await self.ensure_active_domain(force_switch=True)
                        await self._login_direct()
                        self.is_initialized = True
                        self.last_error = None
                    except Exception as retry_err:
                        self.last_error = str(retry_err)

    async def _login_direct(self):
        """Ejecuta login directo mediante HTTP POST en ~180ms."""
        async with self._auth_lock:
            login_url = f"{self.base_url}index.php/login"
            login_data = {
                "username": settings.POS_USER,
                "password": settings.POS_PASS,
                "store": settings.POS_STORE
            }
            resp = await self.http_client.post(login_url, data=login_data, timeout=10.0)
            if resp.status_code == 200:
                self._last_active_ts = time.time()
                logger.info(f"✅ Autenticación directa completada en {self.base_url}")
                return True
            raise RuntimeError(f"Fallo de inicio de sesión POS (HTTP {resp.status_code})")

    async def _ensure_logged_in(self, response: Optional[httpx.Response] = None):
        """Si una respuesta indica sesión expirada o han pasado más de 15 minutos, refresca."""
        needs_refresh = False
        if response is not None:
            if "/index.php/login" in str(response.url) or "login" in response.text[:200].lower():
                needs_refresh = True
        elif time.time() - self._last_active_ts > 900:  # 15 minutos
            needs_refresh = True

        if needs_refresh:
            logger.warning("🔄 Sesión POS expirada o inactiva. Re-autenticando en segundo plano...")
            await self._login_direct()

    async def _heartbeat_loop(self):
        """Mantiene la conexión y sesión HTTP activa cada 3 minutos en segundo plano."""
        while True:
            try:
                await asyncio.sleep(180)  # Cada 3 min
                if self.http_client and self.is_initialized:
                    r = await self.http_client.get(f"{self.base_url}index.php/home/index", timeout=4.0)
                    if "/index.php/login" in str(r.url):
                        await self._login_direct()
                    self._last_active_ts = time.time()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Heartbeat POS omitido: {e}")

    async def get_status(self) -> Dict[str, Any]:
        active_domain = "csopos.co" if "csopos.co" in self.base_url else "softwarepos.online"
        return {
            "is_ready": self.is_initialized,
            "is_logged_in": self.is_initialized,
            "base_url": self.base_url,
            "active_domain": active_domain,
            "active_page": f"{self.base_url}index.php/sales",
            "engine": "Direct High-Speed HTTP",
            "timestamp": datetime.now().isoformat(),
            "last_error": self.last_error
        }

    # -------------------------------------------------------------------------
    # CONSULTA ULTRA-RÁPIDA DE PRODUCTOS (0ms Caché, ~150ms Red Directa)
    # -------------------------------------------------------------------------
    async def lookup_product_by_barcode(self, barcode: str) -> ProductLookupResponse:
        """
        Consulta de alta velocidad sin bloqueos ni renderizados pesados:
        1. Consulta memoria caché (0ms).
        2. Consulta directa a la caja de ventas `/sales/add` (~150ms).
        3. Si no está en ventas, busca en catálogo `/items/search` (~180ms).
        4. Cancela la venta en background sin bloquear la respuesta.
        """
        barcode = barcode.strip()
        if not barcode:
            return ProductLookupResponse(found=False, barcode=barcode, message="Código de barras vacío.")

        # 1. Caché instantánea en memoria (0ms)
        cached = self._get_from_cache(barcode)
        if cached:
            return cached

        for attempt in range(2):
            try:
                if not self.is_initialized or not self.http_client:
                    await self.initialize()

                # 2. Intento 1: /sales/add directo (obtiene precio, stock, nombre e item_id)
                sales_res = await self._lookup_via_sales_add(barcode)
                if sales_res and sales_res.found:
                    self._save_to_cache(barcode, sales_res)
                    return sales_res

                # 3. Intento 2: /items/search en catálogo
                items_res = await self._lookup_via_items_search(barcode)
                if items_res and items_res.found:
                    self._save_to_cache(barcode, items_res)
                    return items_res

                return ProductLookupResponse(
                    found=False,
                    barcode=barcode,
                    message=f"No se encontró ningún producto para el código '{barcode}'."
                )

            except Exception as e:
                err_str = str(e)
                logger.warning(f"Error en intento {attempt + 1} de consulta para {barcode}: {err_str}")
                if "Ambos dominios" in err_str:
                    raise
                if attempt == 0:
                    try:
                        await self.ensure_active_domain(force_switch=True)
                        await self._login_direct()
                    except Exception as failover_err:
                        if "Ambos dominios" in str(failover_err):
                            raise
                else:
                    raise

    async def _lookup_via_sales_add(self, barcode: str) -> Optional[ProductLookupResponse]:
        """Consulta ultra-rápida simulando entrada en caja registradora (~150ms)."""
        try:
            r = await self.http_client.post(
                f"{self.base_url}index.php/sales/add",
                data={"item": barcode},
                headers={"X-Requested-With": "XMLHttpRequest"},
                timeout=3.5
            )
            if r.status_code == 200:
                await self._ensure_logged_in(r)
                data = r.json()
                cart = data.get("cart", {})
                if cart:
                    first_item = list(cart.values())[0]
                    item_id = str(first_item.get("item_id", ""))
                    name = clean_pos_text(first_item.get("name", ""))
                    item_number = clean_pos_text(first_item.get("item_number", "") or barcode)
                    price_val = float(first_item.get("price", 0.0))
                    stock = str(first_item.get("quantity", "0"))
                    category = clean_pos_text(first_item.get("category", "General") or "General")

                    # Limpieza no bloqueante en background
                    asyncio.create_task(self._cancel_sale_async())

                    return ProductLookupResponse(
                        found=True,
                        item_id=item_id,
                        barcode=barcode,
                        modal_barcode=item_number,
                        name=name,
                        category=category,
                        unit_price=price_val,
                        formatted_price=format_cop_currency(price_val),
                        stock=stock,
                        message="Producto consultado exitosamente del POS."
                    )
        except Exception as e:
            logger.debug(f"_lookup_via_sales_add omitido: {e}")
        return None

    async def _cancel_sale_async(self):
        """Envía cancelación de venta a la caja de forma asíncrona sin frenar el flujo."""
        try:
            if self.http_client:
                await self.http_client.post(
                    f"{self.base_url}index.php/sales/cancel_sale",
                    headers={"X-Requested-With": "XMLHttpRequest"},
                    timeout=2.0
                )
        except Exception:
            pass

    async def _lookup_via_items_search(self, barcode: str) -> Optional[ProductLookupResponse]:
        """Consulta directa en catálogo `/items/search` (~180ms)."""
        try:
            r = await self.http_client.post(
                f"{self.base_url}index.php/items/search",
                data={"search": barcode, "category_id": 0, "limit": 10, "offset": 0},
                timeout=3.5
            )
            if r.status_code == 200:
                await self._ensure_logged_in(r)
                data = r.json()
                manage_table = data.get("manage_table", "")
                rows = re.findall(r'<tr[^>]*>(.*?)</tr>', manage_table, re.DOTALL | re.IGNORECASE)
                for row_html in rows:
                    if "No hay artículos que mostrar" in row_html or "No hay artículos" in row_html:
                        continue
                    cols = re.findall(r'<td[^>]*>(.*?)</td>', row_html, re.DOTALL | re.IGNORECASE)
                    if len(cols) >= 6:
                        clean_cols = [clean_pos_text(re.sub(r'<[^>]+>', '', c)) for c in cols]
                        id_display = clean_cols[1]
                        name = clean_cols[2]
                        category = clean_cols[3] if len(clean_cols) > 3 else "General"
                        sale_raw = clean_cols[5] if len(clean_cols) > 5 else "0"
                        stock_raw = clean_cols[6] if len(clean_cols) > 6 else "0"

                        item_id_m = re.search(r'id=["\']item_(\d+)["\']', row_html) or re.search(r'items/view/(\d+)', row_html)
                        item_id = item_id_m.group(1) if item_id_m else id_display

                        sale_val = parse_cop_currency(sale_raw)
                        return ProductLookupResponse(
                            found=True,
                            item_id=item_id,
                            barcode=barcode,
                            modal_barcode=id_display or barcode,
                            name=name,
                            category=category,
                            unit_price=sale_val,
                            formatted_price=format_cop_currency(sale_val),
                            stock=stock_raw,
                            message="Producto localizado en catálogo."
                        )
        except Exception as e:
            logger.debug(f"_lookup_via_items_search omitido: {e}")
        return None

    # -------------------------------------------------------------------------
    # BÚSQUEDA EN CATÁLOGO PARA EDICIÓN MASIVA (~200ms)
    # -------------------------------------------------------------------------
    async def search_products(self, query: str = "") -> ProductSearchResponse:
        """Busca productos en catálogo con respuesta acelerada. Si query está vacío, retorna los primeros ítems."""
        query_clean = (query or "").strip()

        for attempt in range(2):
            try:
                if not self.is_initialized or not self.http_client:
                    await self.initialize()

                r = await self.http_client.post(
                    f"{self.base_url}index.php/items/search",
                    data={"search": query_clean, "category_id": 0, "limit": 60, "offset": 0},
                    timeout=5.0
                )
                if r.status_code == 200:
                    await self._ensure_logged_in(r)
                    data = r.json()
                    manage_table = data.get("manage_table", "")
                    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', manage_table, re.DOTALL | re.IGNORECASE)
                    items: List[ProductItem] = []
                    for row_html in rows:
                        if "No hay artículos que mostrar" in row_html or "No hay artículos" in row_html:
                            continue
                        cols = re.findall(r'<td[^>]*>(.*?)</td>', row_html, re.DOTALL | re.IGNORECASE)
                        if len(cols) >= 6:
                            clean_cols = [clean_pos_text(re.sub(r'<[^>]+>', '', c)) for c in cols]
                            id_disp = clean_cols[1]
                            name = clean_cols[2]
                            category = clean_cols[3] if len(clean_cols) > 3 else ""
                            cost_raw = clean_cols[4] if len(clean_cols) > 4 else "0"
                            sale_raw = clean_cols[5] if len(clean_cols) > 5 else "0"
                            stock_raw = clean_cols[6] if len(clean_cols) > 6 else "0"

                            item_id_m = re.search(r'id=["\']item_(\d+)["\']', row_html) or re.search(r'items/view/(\d+)', row_html)
                            item_id = item_id_m.group(1) if item_id_m else id_disp

                            sale_val = parse_cop_currency(sale_raw)
                            items.append(ProductItem(
                                item_id=item_id,
                                barcode=id_disp,
                                name=name,
                                category=category,
                                cost_price=parse_cop_currency(cost_raw),
                                sale_price=sale_val,
                                formatted_sale_price=format_cop_currency(sale_val),
                                stock=stock_raw
                            ))

                    return ProductSearchResponse(query=query_clean, total=len(items), items=items)

            except Exception as e:
                if "Ambos dominios" in str(e):
                    raise
                if attempt == 0:
                    await self.ensure_active_domain(force_switch=True)
                    await self._login_direct()
                else:
                    raise

    async def get_pos_item_details(self, item_id: str) -> Optional[Dict[str, Any]]:
        """Obtiene el detalle completo de un producto en CSOPOS mediante items/view/{item_id}."""
        if not item_id or str(item_id).strip() in ["-1", "", "None"]:
            return None

        target_id = str(item_id).strip()
        for attempt in range(2):
            try:
                if not self.is_initialized or not self.http_client:
                    await self.initialize()

                r = await self.http_client.get(f"{self.base_url}index.php/items/view/{target_id}", timeout=6.0)
                if r.status_code == 200:
                    await self._ensure_logged_in(r)
                    soup = BeautifulSoup(r.text, "html.parser")

                    def get_val(selector):
                        el = soup.select_one(selector)
                        return el.get("value", "").strip() if el else ""

                    name = clean_pos_text(get_val('input[name="name"]'))
                    item_number = clean_pos_text(get_val('input[name="item_number"]'))
                    cost_price_raw = get_val('input[name="cost_price"]')
                    unit_price_raw = get_val('input[name="unit_price"]')
                    items_discount = get_val('input[name="items_discount"]')
                    description_el = soup.select_one('textarea[name="description"]')
                    description = clean_pos_text(description_el.text if description_el else "")

                    # Categoría
                    category_code = ""
                    category_name = ""
                    cat_select = soup.select_one('select[name="category"]')
                    if cat_select:
                        sel_opt = cat_select.select_one('option[selected]')
                        if sel_opt:
                            category_code = sel_opt.get("value", "").strip()
                            category_name = clean_pos_text(sel_opt.text)
                    if not category_code:
                        category_code = get_val('input[name="category"]')

                    # Unidad
                    unit_code = "UN"
                    unit_select = soup.select_one('select[name="unit"]')
                    if unit_select:
                        sel_u = unit_select.select_one('option[selected]')
                        if sel_u:
                            unit_code = sel_u.get("value", "").strip()
                    if not unit_code:
                        unit_code = get_val('input[name="unit"]') or "UN"

                    # Stock
                    stock_raw = get_val('input[name="locations[1][quantity]"]') or "0"

                    # Códigos adicionales
                    additional_numbers = []
                    add_inputs = soup.select('input[name="item_numbers[]"]')
                    for inp in add_inputs:
                        v = clean_pos_text(inp.get("value", ""))
                        if v and v not in additional_numbers:
                            additional_numbers.append(v)

                    cost_val = float(cost_price_raw) if cost_price_raw else 0.0
                    unit_val = float(unit_price_raw) if unit_price_raw else 0.0
                    try:
                        stock_val = float(stock_raw)
                    except ValueError:
                        stock_val = 0.0

                    profit_pct = 30.0
                    try:
                        profit_pct = float(items_discount)
                    except (ValueError, TypeError):
                        pass

                    return {
                        "item_id": target_id,
                        "name": name,
                        "item_number": item_number or None,
                        "category": category_name or "General",
                        "category_code": category_code or None,
                        "cost_price": cost_val,
                        "unit_price": unit_val,
                        "formatted_sale_price": format_cop_currency(unit_val),
                        "unit_code": unit_code.upper(),
                        "stock_quantity": stock_val,
                        "description": description,
                        "profit_percentage": profit_pct,
                        "additional_numbers": additional_numbers
                    }
                return None
            except Exception as e:
                if attempt == 0:
                    await self.ensure_active_domain(force_switch=True)
                    await self._login_direct()
                else:
                    logger.warning(f"Error obteniendo detalles del item {target_id} en POS: {e}")
                    return None
        return None

    # -------------------------------------------------------------------------
    # ACTUALIZACIÓN DE PRECIOS ULTRA-RÁPIDA (~160ms)
    # -------------------------------------------------------------------------
    async def update_single_price(
        self,
        barcode: str,
        new_price: float,
        item_id: Optional[str] = None,
        modal_barcode: Optional[str] = None
    ) -> PriceUpdateResponse:
        """Modifica el precio individual de forma instantánea e invalida la caché."""
        search_barcode = (modal_barcode or barcode or "").strip()
        
        for attempt in range(2):
            try:
                if not self.is_initialized or not self.http_client:
                    await self.initialize()

                # Si no se tiene item_id, resolverlo de inmediato
                target_item_id = item_id
                if not target_item_id:
                    lookup = await self.lookup_product_by_barcode(search_barcode)
                    if lookup.found and lookup.item_id:
                        target_item_id = lookup.item_id
                    else:
                        raise ValueError(f"No se localizó el identificador único para '{search_barcode}'.")

                price_str = f"{new_price:.2f}" if isinstance(new_price, float) and new_price % 1 != 0 else str(int(new_price))
                payload = {
                    "item_ids[]": [target_item_id],
                    "unit_price": price_str,
                    "submit": "Enviar"
                }

                r = await self.http_client.post(
                    f"{self.base_url}index.php/items/bulk_update/",
                    data=payload,
                    headers={"X-Requested-With": "XMLHttpRequest"},
                    timeout=5.0
                )

                if r.status_code == 200:
                    await self._ensure_logged_in(r)
                    self._invalidate_cache(search_barcode)
                    if barcode and barcode != search_barcode:
                        self._invalidate_cache(barcode)

                    logger.info(f"✅ Precio modificado con éxito a {new_price} para {search_barcode} (ID: {target_item_id}).")
                    return PriceUpdateResponse(
                        success=True,
                        message=f"Precio actualizado exitosamente a {format_cop_currency(new_price)}.",
                        updated_count=1,
                        new_price=new_price,
                        formatted_new_price=format_cop_currency(new_price)
                    )

                raise RuntimeError("El servidor POS no confirmó la actualización del precio.")

            except Exception as e:
                if "Ambos dominios" in str(e):
                    raise
                if attempt == 0:
                    await self.ensure_active_domain(force_switch=True)
                    await self._login_direct()
                else:
                    raise

    async def bulk_update_prices(self, item_ids: List[str], new_price: float) -> PriceUpdateResponse:
        """Modificación masiva directa de precios en una sola petición HTTP (~160ms)."""
        if not item_ids:
            return PriceUpdateResponse(
                success=False,
                message="No se seleccionó ningún producto para actualizar.",
                updated_count=0,
                new_price=new_price,
                formatted_new_price=format_cop_currency(new_price)
            )

        for attempt in range(2):
            try:
                if not self.is_initialized or not self.http_client:
                    await self.initialize()

                price_str = f"{new_price:.2f}" if isinstance(new_price, float) and new_price % 1 != 0 else str(int(new_price))
                payload = {
                    "item_ids[]": item_ids,
                    "unit_price": price_str,
                    "submit": "Enviar"
                }

                r = await self.http_client.post(
                    f"{self.base_url}index.php/items/bulk_update/",
                    data=payload,
                    headers={"X-Requested-With": "XMLHttpRequest"},
                    timeout=8.0
                )

                if r.status_code == 200:
                    await self._ensure_logged_in(r)
                    self._invalidate_cache()

                    logger.info(f"✅ Modificación masiva exitosa para {len(item_ids)} productos a {new_price}.")
                    return PriceUpdateResponse(
                        success=True,
                        message=f"Precios actualizados masivamente a {format_cop_currency(new_price)} ({len(item_ids)} productos).",
                        updated_count=len(item_ids),
                        new_price=new_price,
                        formatted_new_price=format_cop_currency(new_price)
                    )

                raise RuntimeError("Fallo al aplicar la modificación masiva en el servidor POS.")

            except Exception as e:
                if "Ambos dominios" in str(e):
                    raise
                if attempt == 0:
                    await self.ensure_active_domain(force_switch=True)
                    await self._login_direct()
                else:
                    raise

    def get_departments(self) -> List[Dict[str, str]]:
        """Retorna los 50 departamentos maestros del POS con sus identificadores ordenados alfabéticamente."""
        if _LOCAL_CATALOG:
            deps = [{"code": k, "name": v["department_name"]} for k, v in _LOCAL_CATALOG.items()]
            return sorted(deps, key=lambda d: d["name"].lower())
        return sorted([{"code": k, "name": v} for k, v in POS_DEPARTMENTS.items()], key=lambda d: d["name"].lower())

    async def get_categories_by_department(self, department_code: str) -> List[Dict[str, str]]:
        """Obtiene las categorías asignadas a un departamento desde el catálogo local o POS con caché en memoria."""
        if not department_code:
            return []
        dep_code = department_code.strip()

        # 1. Catálogo local pre-indexado (0ms)
        if _LOCAL_CATALOG and dep_code in _LOCAL_CATALOG:
            cats = _LOCAL_CATALOG[dep_code].get("categories", [])
            if cats:
                return cats

        # 2. Caché en memoria
        if dep_code in self._categories_by_dep_cache:
            return self._categories_by_dep_cache[dep_code]

        # 3. Fallback HTTP al POS
        for attempt in range(2):
            try:
                if not self.is_initialized or not self.http_client:
                    await self.initialize()

                url = f"{self.base_url}index.php/category/get_categories_by_department?department={dep_code}"
                r = await self.http_client.get(url, timeout=6.0)
                if r.status_code == 200:
                    await self._ensure_logged_in(r)
                    raw_data = r.json()
                    results = []
                    for item in raw_data:
                        if isinstance(item, dict) and "code" in item and "name" in item:
                            results.append({
                                "code": str(item["code"]).strip(),
                                "name": clean_pos_text(str(item["name"])),
                                "department_code": dep_code,
                                "department_name": clean_pos_text(POS_DEPARTMENTS.get(dep_code, ""))
                            })
                    self._categories_by_dep_cache[dep_code] = results
                    return results
                return []
            except Exception as e:
                if attempt == 0:
                    await self.ensure_active_domain(force_switch=True)
                    await self._login_direct()
                else:
                    logger.warning(f"Error consultando categorías de departamento {dep_code}: {e}")
                    return []

    async def search_categories_pos(self, term: str) -> List[Dict[str, Any]]:
        """Búsqueda global y rápida de categorías en catálogo local y POS con autocompletado omnidireccional."""
        term_clean = (term or "").strip().lower()
        if not term_clean:
            return []

        # 1. Búsqueda local de alta velocidad en catálogo maestro (0ms)
        local_results = []
        if _LOCAL_CATALOG:
            for dep_k, dep_data in _LOCAL_CATALOG.items():
                for cat in dep_data.get("categories", []):
                    c_name = cat.get("name", "").lower()
                    c_code = cat.get("code", "").lower()
                    d_name = cat.get("department_name", "").lower()
                    if term_clean in c_name or term_clean == c_code or (len(term_clean) >= 3 and term_clean in d_name):
                        local_results.append({
                            "code": cat.get("code"),
                            "name": clean_pos_text(cat.get("name")),
                            "department_code": cat.get("department_code") or dep_k,
                            "department_name": clean_pos_text(cat.get("department_name") or dep_data.get("department_name", ""))
                        })
                        if len(local_results) >= 60:
                            break
                if len(local_results) >= 60:
                    break

        if local_results:
            return local_results

        # 2. Fallback a POS externo
        for attempt in range(2):
            try:
                if not self.is_initialized or not self.http_client:
                    await self.initialize()

                url = f"{self.base_url}index.php/category/categories_shop?term={term.strip()}"
                r = await self.http_client.get(url, timeout=5.0)
                if r.status_code == 200:
                    await self._ensure_logged_in(r)
                    raw_data = r.json()
                    out = []
                    for row in raw_data:
                        data_obj = row.get("data") or {}
                        cat_code = data_obj.get("categoria_code") or ""
                        cat_name = clean_pos_text(data_obj.get("categoria") or row.get("value") or "")
                        dep_code = data_obj.get("departament_code") or ""
                        dep_name = clean_pos_text(data_obj.get("departament") or POS_DEPARTMENTS.get(dep_code, ""))
                        if cat_code or cat_name:
                            out.append({
                                "code": cat_code,
                                "name": cat_name,
                                "department_code": dep_code,
                                "department_name": dep_name
                            })
                    return out
                return []
            except Exception as e:
                if attempt == 0:
                    await self.ensure_active_domain(force_switch=True)
                    await self._login_direct()
                else:
                    logger.warning(f"Error buscando categorías para {term}: {e}")
                    return []

    async def resolve_category_code(self, category_text_or_code: str, department_code: Optional[str] = None) -> Tuple[str, str]:
        """Resuelve el código interno C... requerido por el POS y su departamento."""
        target = (category_text_or_code or "").strip()
        if not target:
            return "C3677", department_code or "D07"

        # Si ya es un código de formato C####
        if re.match(r"^C\d+$", target, re.IGNORECASE):
            if not department_code and _LOCAL_CATALOG:
                for dep_k, dep_v in _LOCAL_CATALOG.items():
                    for c in dep_v.get("categories", []):
                        if c.get("code", "").upper() == target.upper():
                            return target.upper(), dep_k
            return target.upper(), department_code or ""

        # Búsqueda directa en catálogo local maestro
        if _LOCAL_CATALOG:
            for dep_k, dep_v in _LOCAL_CATALOG.items():
                if department_code and dep_k != department_code:
                    continue
                for c in dep_v.get("categories", []):
                    if c.get("name", "").strip().lower() == target.lower():
                        return c["code"], dep_k
            for dep_k, dep_v in _LOCAL_CATALOG.items():
                if department_code and dep_k != department_code:
                    continue
                for c in dep_v.get("categories", []):
                    if target.lower() in c.get("name", "").strip().lower():
                        return c["code"], dep_k

        # Si vino nombre, buscar en el endpoint del POS
        results = await self.search_categories_pos(target)
        for r in results:
            if r["name"].strip().lower() == target.lower():
                return r["code"], r["department_code"] or department_code or ""

        if results:
            return results[0]["code"], results[0]["department_code"] or department_code or ""

        # Fallback a Gaseosas o primer válido
        return "C3677", department_code or "D07"

    async def create_or_update_pos_item(
        self,
        item_data: Dict[str, Any],
        item_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Crea o actualiza un producto directamente en el sistema POS externo (csopos.co)."""
        cat_raw = (item_data.get("category_code") or item_data.get("category") or "").strip()
        dep_raw = (item_data.get("department_code") or "").strip()
        cat_code, dep_code = await self.resolve_category_code(cat_raw, dep_raw)

        for attempt in range(2):
            try:
                if not self.is_initialized or not self.http_client:
                    await self.initialize()

                is_update = bool(item_id and str(item_id).strip() not in ["-1", "", "None"])
                target_id = str(item_id).strip() if is_update else "-1"
                save_url = f"{self.base_url}index.php/items/save/{target_id}"

                payload = {
                    "item_number": (item_data.get("item_number") or "").strip(),
                    "product_id": (item_data.get("product_id") or "").strip(),
                    "name": item_data.get("name", "").strip(),
                    "category_general": dep_code,
                    "category": cat_code,
                    "size_combined": "pequena",
                    "size": "",
                    "colour": "",
                    "model": "",
                    "marca": "",
                    "reorder_level": "0",
                    "expiration_date": "",
                    "expiration_day": "select_day",
                    "description": item_data.get("description", "") or "",
                    "tax_included": "1",
                    "personalized": "",
                    "sell_negative": "yes",
                    "_item": "",
                    "_item_id": "",
                    "_quantity": "",
                    "shop_online": "1",
                    "discount_online_item": "",
                    "quantity_item_promote": "",
                    "description_short": "",
                    "keywords": "",
                    "url_video": "",
                    "cost_price": f"{float(item_data.get('cost_price', 0.0)):.2f}",
                    "items_discount": str(item_data.get("profit_percentage", 30.0)),
                    "unit_price": f"{float(item_data.get('unit_price', 0.0)):.2f}",
                    "suggested_price": "",
                    "promo_price": "",
                    "promo_quantity": "",
                    "start_date": "",
                    "end_date": "",
                    "commission_value": "0",
                    "commission_type": "percent",
                    "quantity_unit_sale": "1",
                    "locations[1][quantity]": str(int(float(item_data.get("stock_quantity", 0.0)))) if float(item_data.get("stock_quantity", 0.0)).is_integer() else f"{float(item_data.get('stock_quantity', 0.0)):.2f}",
                    "locations[1][subcategory_data_quantity][]": str(int(float(item_data.get("stock_quantity", 0.0)))) if float(item_data.get("stock_quantity", 0.0)).is_integer() else f"{float(item_data.get('stock_quantity', 0.0)):.2f}",
                    "locations[1][subcategory_data_custom1][]": "",
                    "locations[1][subcategory_data_custom2][]": "",
                    "unit": (item_data.get("unit_code") or "UN").strip().upper(),
                    "locations[1][quantity_warehouse]": "",
                    "locations[1][quantity_transfer]": "",
                    "locations[1][type_transfer]": "Stock a Bodega",
                    "locations[1][location]": "",
                    "redirect": "0",
                    "sale_or_receiving": "sale",
                    "submit": "Enviar"
                }

                # Agregar números adicionales de artículo si están presentes
                add_nums = item_data.get("additional_numbers") or []
                if add_nums:
                    payload["item_numbers[]"] = [str(n).strip() for n in add_nums if str(n).strip()]

                r = await self.http_client.post(
                    save_url,
                    data=payload,
                    headers={"X-Requested-With": "XMLHttpRequest"},
                    timeout=10.0
                )

                if r.status_code == 200:
                    await self._ensure_logged_in(r)
                    self._invalidate_cache()
                    try:
                        resp_json = r.json()
                        pos_id = str(resp_json.get("item_id", ""))
                        msg = resp_json.get("message", "")
                        success = bool(resp_json.get("success", False))

                        if success and pos_id and pos_id not in ["-1", "-1.0"]:
                            return {"success": True, "item_id": pos_id, "message": msg or "Producto guardado con éxito en el POS."}
                        else:
                            return {"success": False, "item_id": "-1", "message": msg or "El servidor POS rechazó la creación del producto."}
                    except Exception as parse_err:
                        return {"success": False, "item_id": "-1", "message": f"Respuesta no válida del POS: {parse_err}"}

                return {"success": False, "item_id": "-1", "message": f"Servidor POS respondió con código {r.status_code}"}

            except Exception as e:
                if "Ambos dominios" in str(e):
                    return {"success": False, "item_id": "-1", "message": str(e)}
                if attempt == 0:
                    await self.ensure_active_domain(force_switch=True)
                    await self._login_direct()
                else:
                    return {"success": False, "item_id": "-1", "message": f"Error conectando con POS: {str(e)}"}

    async def close(self):
        """Cierra el cliente HTTP y cancela tareas en segundo plano."""
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        if self.http_client:
            try:
                await self.http_client.aclose()
            except Exception:
                pass
        self.is_initialized = False
