import re
import io
import time
import random
from typing import Dict, List, Tuple, Optional, Callable
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from pdfminer.high_level import extract_text as pdf_extract_text
import pandas as pd
from urllib.parse import urlparse, parse_qs

# ------------------ Utilidades generales ------------------

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
]

# Selectores típicos de precio en tiendas
PRICE_CSS = [
    ".price",
    ".product-price",
    ".prices",
    ".vtex-product-price-1-x-sellingPrice",
    ".woocommerce-Price-amount.amount",
    "[class*='price' i]",
    "[class*='precio' i]",
    "span[data-price]"
]

# Patrón monetario simple
PRICE_PAT = re.compile(r"\$?\s*\d[\d\.\,]*")

def s(x): 
    return "" if x is None else str(x).strip()

# Formatea texto de precio legible, ej: "$ 123.456,78"
def format_price_display(value_float: float) -> str:
    try:
        out = f"$ {value_float:,.2f}"
        # convertir a formato ES (coma decimal, punto miles)
        return out.replace(",", "_").replace(".", ",").replace("_", ".")
    except Exception:
        return ""

# Convierte texto/valor a número plano sin puntos ni comas y SIN decimales
def to_plain_int_str_from_text(text: str) -> Optional[str]:
    """
    Intenta interpretar el texto como precio con separadores y decimales, 
    lo convierte a float y devuelve su parte entera como string de dígitos.
    Si no puede parsear, devuelve solo dígitos del texto.
    """
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw:
        return None
    # Normalización para float
    norm = re.sub(r"[^\d\.,]", "", raw)
    if not norm:
        return None
    try:
        # casos con coma y punto
        if "," in norm and "." in norm:
            # si la última coma está a la derecha del último punto, la coma es decimal y el punto es miles
            if norm.rfind(",") > norm.rfind("."):
                norm = norm.replace(".", "").replace(",", ".")
            else:
                # asume punto decimal
                norm = norm.replace(",", "")
        elif "," in norm and "." not in norm:
            # si hay coma, puede ser decimal o miles; asume coma decimal si dos dígitos a derecha
            parts = norm.split(",")
            if len(parts[-1]) <= 2:
                norm = norm.replace(",", ".")
            else:
                norm = norm.replace(",", "")
        elif "." in norm and "," not in norm:
            # si hay muchos puntos, probablemente sean miles
            if len(norm.replace(".", "")) >= 5:
                norm = norm.replace(".", "")
        val = float(norm)
        ival = int(val)  # omitir decimales
        return str(ival)
    except Exception:
        # fallback: solo dígitos (podría incluir decimales pegados; se omiten por diseño aquí)
        digits = re.sub(r"[^\d]", "", raw)
        if not digits:
            return None
        return digits

def to_plain_int_str_from_float(value_float: float) -> str:
    try:
        return str(int(float(value_float)))
    except Exception:
        return ""

# ------------------ Cliente HTTP endurecido (anti-403) ------------------

DEFAULT_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "accept-language": "es-AR,es;q=0.9,en-US;q=0.8,en;q=0.7",
    "cache-control": "no-cache",
    "upgrade-insecure-requests": "1",
    "sec-fetch-site": "none",
    "sec-fetch-mode": "navigate",
    "sec-fetch-user": "?1",
    "sec-fetch-dest": "document",
    "pragma": "no-cache",
}

def browser_headers(domain: str) -> dict:
    ua = random.choice(UA_POOL)
    h = dict(DEFAULT_HEADERS)
    h["user-agent"] = ua
    h["sec-ch-ua"] = '"Chromium";v="120", "Google Chrome";v="120", "Not:A-Brand";v="99"'
    h["sec-ch-ua-platform"] = '"Windows"'
    h["sec-ch-ua-mobile"] = "?0"
    h["referer"] = f"{domain.rstrip('/')}/"
    return h

class HttpClient:
    def __init__(self, delay_range: Tuple[int,int]=(2,5), log: Optional[Callable[[str], None]]=None, cancel_cb: Optional[Callable[[], bool]]=None):
        self.session = requests.Session()
        self.delay_range = delay_range
        self.log = log or (lambda *_: None)
        self.cancel_cb = cancel_cb or (lambda: False)

    def get(self, url, params=None, timeout=25):
        if self.cancel_cb():
            raise RuntimeError("cancelled")
        base = re.match(r"^https?://[^/]+", url)
        if base:
            self.session.headers.clear()
            self.session.headers.update(browser_headers(base.group(0)))
        self.log(f"GET {url}" + (f" params={params}" if params else ""))
        r = self.session.get(url, params=params, timeout=timeout, allow_redirects=True)
        self.log(f"HTTP {r.status_code} {r.url}")
        time.sleep(random.uniform(*self.delay_range))
        r.raise_for_status()
        return r

# ------------------ Scraper principal ------------------

class PriceScraper:
    def __init__(self, headless: bool = True, delay_range: Tuple[int, int] = (2, 5)):
        self.client: Optional[HttpClient] = None
        self.delay_range = delay_range

    # ---------- VTEX ----------
    def _try_vtex(self, base: str, term: str, log) -> Tuple[Optional[str], Optional[str]]:
        api = f"{base.rstrip('/')}/api/catalog_system/pub/products/search"
        r = self.client.get(api, params={"_from": 0, "_to": 9, "ft": term})
        try:
            data = r.json()
        except Exception:
            return None, None
        if not isinstance(data, list) or not data:
            log("VTEX: sin resultados")
            return None, None
        for prod in data:
            items = prod.get("items") or []
            for it in items:
                sellers = it.get("sellers") or []
                for sel in sellers:
                    offer = sel.get("commertialOffer") or {}
                    price = offer.get("Price")
                    if price is not None:
                        ptxt = format_price_display(float(price))
                        pnum = to_plain_int_str_from_float(float(price))
                        log(f"VTEX: precio={price}")
                        return ptxt, pnum
        # priceRange como alternativa
        for prod in data:
            pr = (prod.get("priceRange") or {}).get("sellingPrice", {})
            low = pr.get("lowPrice")
            if low is not None:
                ptxt = format_price_display(float(low))
                pnum = to_plain_int_str_from_float(float(low))
                log(f"VTEX: lowPrice={low}")
                return ptxt, pnum
        return None, None

    # ---------- Magento ----------
    def _try_magento_html(self, base: str, term: str, log) -> Tuple[Optional[str], Optional[str]]:
        url = f"{base.rstrip('/')}/catalogsearch/result/"
        r = self.client.get(url, params={"q": term})
        soup = BeautifulSoup(r.text, "html.parser")
        # Intentar por selectores
        for css in PRICE_CSS:
            el = soup.select_one(css)
            if el:
                txt = el.get_text(" ", strip=True)
                ptxt = format_price_display(float(to_plain_int_str_from_text(txt) or "0"))
                pnum = to_plain_int_str_from_text(txt)
                if pnum:
                    log(f"Magento: {css} -> {ptxt} ({pnum})")
                    return ptxt, pnum
        # Búsqueda global
        m = PRICE_PAT.search(soup.get_text(" ", strip=True))
        if m:
            txt = m.group(0)
            ptxt = format_price_display(float(to_plain_int_str_from_text(txt) or "0"))
            pnum = to_plain_int_str_from_text(txt)
            if pnum:
                log(f"Magento: patrón global -> {ptxt} ({pnum})")
                return ptxt, pnum
        return None, None

    # ---------- WordPress/WooCommerce ----------
    def _find_wp_search(self, html: str, base: str) -> Optional[str]:
        soup = BeautifulSoup(html, "html.parser")
        form = soup.find("form", attrs={"role": "search"}) or soup.find("form", class_=re.compile("search", re.I))
        if not form:
            return None
        action = form.get("action") or base.rstrip("/") + "/"
        return action

    def _try_wordpress(self, base: str, term: str, log) -> Tuple[Optional[str], Optional[str]]:
        # 1) Detectar formulario
        r = self.client.get(base.rstrip("/") + "/")
        action = self._find_wp_search(r.text, base) or base.rstrip("/") + "/"
        # 2) Probar ?s=term y ?s=term&post_type=product
        for params in ({"s": term}, {"s": term, "post_type": "product"}):
            try:
                rr = self.client.get(action, params=params)
                soup = BeautifulSoup(rr.text, "html.parser")
                el = soup.select_one(".woocommerce-Price-amount.amount") or soup.select_one("[class*='woocommerce-Price-amount']")
                if el:
                    txt = el.get_text(" ", strip=True)
                    pnum = to_plain_int_str_from_text(txt)
                    if pnum:
                        ptxt = format_price_display(float(pnum))
                        log(f"WordPress/Woo: {action} {params} -> {ptxt} ({pnum})")
                        return ptxt, pnum
                # fallback global
                m = PRICE_PAT.search(soup.get_text(" ", strip=True))
                if m:
                    txt = m.group(0)
                    pnum = to_plain_int_str_from_text(txt)
                    if pnum:
                        ptxt = format_price_display(float(pnum))
                        log(f"WordPress: patrón -> {ptxt} ({pnum})")
                        return ptxt, pnum
            except Exception as e:
                log(f"WordPress error {action} {params}: {e}")
        return None, None

    # ---------- Genérico ----------
    def _try_generic(self, base: str, term: str, log) -> Tuple[Optional[str], Optional[str]]:
        for path in ["/search", "/buscar", "/busca", "/s", "/busqueda"]:
            url = f"{base.rstrip('/')}{path}"
            try:
                r = self.client.get(url, params={"q": term})
            except Exception as e:
                log(f"Genérico: error {path}: {e}")
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            txt_all = soup.get_text(" ", strip=True)
            for css in PRICE_CSS:
                el = soup.select_one(css)
                if el:
                    txt = el.get_text(" ", strip=True)
                    pnum = to_plain_int_str_from_text(txt)
                    if pnum:
                        ptxt = format_price_display(float(pnum))
                        log(f"Genérico: {path} {css} -> {ptxt} ({pnum})")
                        return ptxt, pnum
            m = PRICE_PAT.search(txt_all)
            if m:
                txt = m.group(0)
                pnum = to_plain_int_str_from_text(txt)
                if pnum:
                    ptxt = format_price_display(float(pnum))
                    log(f"Genérico: {path} patrón -> {ptxt} ({pnum})")
                    return ptxt, pnum
        return None, None

    # ---------- Folletos / PDF ----------
    def _pdf_text_from_url(self, url: str, log) -> str:
        r = self.client.get(url, timeout=45)
        content = r.content
        bio = io.BytesIO(content)
        try:
            txt = pdf_extract_text(bio) or ""
            log(f"PDF extraído ({len(txt)} chars) {url}")
            return txt
        except Exception as e:
            log(f"PDF error {e} {url}")
            return ""

    def _extract_pdf_from_wa(self, url: str) -> Optional[str]:
        try:
            q = parse_qs(urlparse(url).query)
            t = " ".join(q.get("text", []))
            m = re.search(r"https?://[^\s]+?\.pdf", t)
            return m.group(0) if m else None
        except Exception:
            return None

    def _try_brochures(self, base: str, term: str, log) -> Tuple[Optional[str], Optional[str]]:
        candidates = [base] + [f"{base.rstrip('/')}/{p}" for p in ["ofertas","oferta","promociones","folleto","folletos","catalogo","catalogos"]]
        links = []
        for u in candidates:
            try:
                r = self.client.get(u)
                soup = BeautifulSoup(r.text, "html.parser")
                # anchors
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if "wa.me/?" in href:
                        pdf = self._extract_pdf_from_wa(href)
                        if pdf:
                            links.append(pdf)
                    elif href.lower().endswith(".pdf"):
                        full = href if href.startswith("http") else (base.rstrip("/") + "/" + href.lstrip("/"))
                        links.append(full)
                # iframes (visores)
                for iframe in soup.find_all("iframe", src=True):
                    src = iframe["src"]
                    if src.lower().endswith(".pdf"):
                        links.append(src)
            except Exception as e:
                log(f"Folleto error {u}: {e}")
        # Descargar y buscar término
        for purl in links[:10]:
            txt = self._pdf_text_from_url(purl, log)
            if term.lower() in txt.lower():
                for pr in PRICE_PAT.findall(txt):
                    pnum = to_plain_int_str_from_text(pr)
                    if pnum:
                        ptxt = format_price_display(float(pnum))
                        log(f"Folleto PDF hit {purl} -> {ptxt} ({pnum})")
                        return ptxt, pnum
        return None, None

    # ---------- Selección de estrategia ----------
    def _detect_platform_order(self, vendor_name: str, domain: str) -> List[str]:
        vn = (vendor_name or "").lower()
        if vn in ["cheeksa", "megatone"]:
            return ["wordpress", "brochures", "vtex", "magento", "generic"]
        if vn in ["vital"]:
            return ["brochures", "wordpress", "vtex", "magento", "generic"]
        if vn in ["musimundo"]:
            return ["vtex", "magento", "wordpress", "generic"]
        return ["vtex", "magento", "wordpress", "generic"]

    def _search_vendor_once(self, vendor_name: str, base: str, term: str, log) -> Tuple[Optional[str], Optional[str]]:
        for strategy in self._detect_platform_order(vendor_name, base):
            try:
                if strategy == "vtex":
                    log(f"[{vendor_name}] estrategia=VTEX ft={term}")
                    res = self._try_vtex(base, term, log)
                elif strategy == "magento":
                    log(f"[{vendor_name}] estrategia=Magento q={term}")
                    res = self._try_magento_html(base, term, log)
                elif strategy == "wordpress":
                    log(f"[{vendor_name}] estrategia=WordPress q={term}")
                    res = self._try_wordpress(base, term, log)
                elif strategy == "brochures":
                    log(f"[{vendor_name}] estrategia=Folletos term={term}")
                    res = self._try_brochures(base, term, log)
                else:
                    log(f"[{vendor_name}] estrategia=Genérico q={term}")
                    res = self._try_generic(base, term, log)
                if res and res[0] and res[1]:
                    return res
            except requests.HTTPError as e:
                log(f"HTTPError {e}")
            except Exception as e:
                log(f"Error {e}")
        return None, None

    # ---------- Variantes de búsqueda ----------
    def _variants(self, p: Dict) -> List[str]:
        marca = s(p.get("marca"))
        modelo = s(p.get("modelo"))
        producto = s(p.get("producto"))
        capacidad = s(p.get("capacidad"))
        ean = s(p.get("ean"))
        vs = []
        if ean: vs.append(ean)
        if marca and modelo: vs.append(f"{marca} {modelo}")
        if modelo: vs.append(modelo)
        if producto: vs.append(producto)
        if marca and capacidad: vs.append(f"{marca} {capacidad}")
        out, seen = [], set()
        for v in vs:
            if v and v not in seen:
                out.append(v); seen.add(v)
        return out[:8]

    # ---------- Orquestación ----------
    def scrape_all_vendors(self, products: List[Dict], vendors: Dict[str, str], include_official_site: bool = False, return_logs: bool = False, cancel_cb: Optional[Callable[[], bool]] = None):
        logs: List[str] = []
        def log(msg: str):
            logs.append(msg)

        self.client = HttpClient(delay_range=self.delay_range, log=log, cancel_cb=cancel_cb)
        date_only = datetime.now().strftime("%d/%m/%Y")

        rows = []
        for p in (products or []):
            base_row = {
                "Producto": s(p.get("producto")),
                "Marca": s(p.get("marca")),
                "Marca (Sitio oficial)": "ND",
                "Fecha de Consulta": date_only
            }
            row = dict(base_row)
            for vn, url in (vendors or {}).items():
                if cancel_cb and cancel_cb():
                    raise RuntimeError("cancelled")
                url = s(url)
                price_txt, price_num = None, None
                for term in self._variants(p):
                    price_txt, price_num = self._search_vendor_once(vn, url, term, log)
                    if price_txt and price_num:
                        break
                row[vn] = price_txt or "ND"
                row[f"{vn} (num)"] = price_num or ""  # num plano sin decimales
            rows.append(row)

        df = pd.DataFrame(rows)
        return (df, logs) if return_logs else (df, [])
