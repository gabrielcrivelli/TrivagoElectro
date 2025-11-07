# scraper.py
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

# ============================ Utilidades ============================

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
]

# Selectores frecuentes de precio
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

# Contenedores de tarjeta de producto
CARD_SELECTORS = [
    ".product-item", "li.product", ".product", ".product-card", ".grid-item", ".product-box",
    ".vtex-product-summary-2-x-container", ".ais-InfiniteHits-item"
]

# Posibles títulos/nombres de producto dentro de la tarjeta
TITLE_SELECTORS = [
    ".product-name", ".product-title", ".vtex-product-summary-2-x-productBrand", ".vtex-product-summary-2-x-productNameContainer",
    "h1", "h2", "h3", "a[title]"
]

# Patrón monetario simple
PRICE_PAT = re.compile(r"\$?\s*\d[\d\.\,]*")

def s(x):
    return "" if x is None else str(x).strip()

def normalize_spaces(txt: str) -> str:
    return re.sub(r"\s+", " ", txt or "").strip()

def format_price_display_from_plain(plain: str) -> str:
    """Recibe entero plano como string y devuelve texto tipo $ 123.456,00"""
    try:
        val = int(plain)
        out = f"$ {val:,.2f}"
        return out.replace(",", "_").replace(".", ",").replace("_", ".")
    except Exception:
        return ""

def to_plain_int_str_from_text(text: str) -> Optional[str]:
    """
    Interpreta un texto de precio y devuelve la parte entera como string sin separadores/decimales.
    Si no puede parsear, devuelve solo dígitos.
    """
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw:
        return None
    norm = re.sub(r"[^\d\.,]", "", raw)
    if not norm:
        return None
    try:
        # , y . combinados
        if "," in norm and "." in norm:
            if norm.rfind(",") > norm.rfind("."):
                norm = norm.replace(".", "").replace(",", ".")
            else:
                norm = norm.replace(",", "")
        elif "," in norm and "." not in norm:
            parts = norm.split(",")
            if len(parts[-1]) <= 2:
                norm = norm.replace(",", ".")
            else:
                norm = norm.replace(",", "")
        elif "." in norm and "," not in norm:
            if len(norm.replace(".", "")) >= 5:
                norm = norm.replace(".", "")
        val = float(norm)
        return str(int(val))
    except Exception:
        digits = re.sub(r"[^\d]", "", raw)
        return digits or None

def to_plain_from_float(v: float) -> str:
    try:
        return str(int(float(v)))
    except Exception:
        return ""

def mk_variants_for_match(term: str) -> List[str]:
    """
    Genera variantes simples para comparación en texto:
    - tal cual
    - sin tildes ni símbolos
    - colapsando espacios
    """
    base = normalize_spaces(term)
    v = [base]
    # remover símbolos comunes
    v2 = re.sub(r"[^A-Za-z0-9 ÁÉÍÓÚÜÑáéíóúüñ\-_/\.]", " ", base)
    v2 = normalize_spaces(v2)
    if v2 and v2.lower() not in [x.lower() for x in v]:
        v.append(v2)
    return v

def text_matches_any_variant(text: str, variants: List[str]) -> bool:
    lt = normalize_spaces(text).lower()
    for v in variants:
        if all(tok in lt for tok in normalize_spaces(v).lower().split()):
            return True
    return False

# ===================== Cliente HTTP (anti-403) ======================

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

# ============================ Scraper ===============================

class PriceScraper:
    def __init__(self, headless: bool = True, delay_range: Tuple[int, int] = (2, 5)):
        self.client: Optional[HttpClient] = None
        self.delay_range = delay_range

    # ------------------- Verificación en tarjetas -------------------
    def _extract_from_cards(self, soup: BeautifulSoup, term: str, log) -> Tuple[Optional[str], Optional[str]]:
        """
        Devuelve (texto, entero_plano) si encuentra un precio dentro de una 'card'
        cuyo contenido textual incluya el término (o variantes), evitando precios sueltos.
        """
        variants = mk_variants_for_match(term)
        for cs in CARD_SELECTORS:
            for card in soup.select(cs):
                card_txt = card.get_text(" ", strip=True)
                # Si el card no menciona el término, probar títulos internos
                title_ok = text_matches_any_variant(card_txt, variants)
                if not title_ok:
                    for ts in TITLE_SELECTORS:
                        t = card.select_one(ts)
                        if t and text_matches_any_variant(t.get_text(" ", strip=True), variants):
                            title_ok = True
                            break
                if not title_ok:
                    continue
                # Dentro del card, buscar precio por selectores
                for pc in PRICE_CSS:
                    el = card.select_one(pc)
                    if el:
                        txt = el.get_text(" ", strip=True)
                        pnum = to_plain_int_str_from_text(txt)
                        if pnum:
                            ptxt = format_price_display_from_plain(pnum)
                            log(f"Card {cs} {pc} -> {ptxt} ({pnum})")
                            return ptxt, pnum
                # Si no hay selectores, patrón local en el card
                m = PRICE_PAT.search(card_txt)
                if m:
                    pnum = to_plain_int_str_from_text(m.group(0))
                    if pnum:
                        ptxt = format_price_display_from_plain(pnum)
                        log(f"Card {cs} patrón -> {ptxt} ({pnum})")
                        return ptxt, pnum
        return None, None

    # ------------------------ VTEX (API) ----------------------------
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
                        pnum = to_plain_from_float(float(price))
                        ptxt = format_price_display_from_plain(pnum)
                        log(f"VTEX: precio={price}")
                        return ptxt, pnum
        # priceRange lowPrice
        for prod in data:
            pr = (prod.get("priceRange") or {}).get("sellingPrice", {})
            low = pr.get("lowPrice")
            if low is not None:
                pnum = to_plain_from_float(float(low))
                ptxt = format_price_display_from_plain(pnum)
                log(f"VTEX: lowPrice={low}")
                return ptxt, pnum
        return None, None

    # --------------------- Magento (HTML) ---------------------------
    def _try_magento_html(self, base: str, term: str, log) -> Tuple[Optional[str], Optional[str]]:
        url = f"{base.rstrip('/')}/catalogsearch/result/"
        r = self.client.get(url, params={"q": term})
        soup = BeautifulSoup(r.text, "html.parser")
        # Prioridad: tarjetas
        ptxt, pnum = self._extract_from_cards(soup, term, log)
        if ptxt and pnum:
            log(f"Magento verificado -> {ptxt} ({pnum})")
            return ptxt, pnum
        # Último recurso: patrón global en toda la página (evita falsos positivos si no hay cards)
        m = PRICE_PAT.search(soup.get_text(" ", strip=True))
        if m:
            pnum = to_plain_int_str_from_text(m.group(0))
            if pnum:
                ptxt = format_price_display_from_plain(pnum)
                log(f"Magento patrón global -> {ptxt} ({pnum})")
                return ptxt, pnum
        return None, None

    # ----------------- WordPress / WooCommerce ---------------------
    def _find_wp_search(self, html: str, base: str) -> Optional[str]:
        soup = BeautifulSoup(html, "html.parser")
        form = soup.find("form", attrs={"role": "search"}) or soup.find("form", class_=re.compile("search", re.I))
        if not form:
            return None
        action = form.get("action") or base.rstrip("/") + "/"
        return action

    def _try_wordpress(self, base: str, term: str, log) -> Tuple[Optional[str], Optional[str]]:
        # 1) detectar formulario
        r = self.client.get(base.rstrip("/") + "/")
        action = self._find_wp_search(r.text, base) or base.rstrip("/") + "/"
        # 2) s y s+post_type=product
        for params in ({"s": term}, {"s": term, "post_type": "product"}):
            try:
                rr = self.client.get(action, params=params)
                soup = BeautifulSoup(rr.text, "html.parser")
                ptxt, pnum = self._extract_from_cards(soup, term, log)
                if ptxt and pnum:
                    log(f"WordPress/Woo verificado -> {ptxt} ({pnum})")
                    return ptxt, pnum
                # último recurso: patrón global
                m = PRICE_PAT.search(soup.get_text(" ", strip=True))
                if m:
                    pnum = to_plain_int_str_from_text(m.group(0))
                    if pnum:
                        ptxt = format_price_display_from_plain(pnum)
                        log(f"WordPress patrón global -> {ptxt} ({pnum})")
                        return ptxt, pnum
            except Exception as e:
                log(f"WordPress error {action} {params}: {e}")
        return None, None

    # ------------------------- Genérico ----------------------------
    def _try_generic(self, base: str, term: str, log) -> Tuple[Optional[str], Optional[str]]:
        for path in ["/search", "/buscar", "/busca", "/s", "/busqueda"]:
            url = f"{base.rstrip('/')}{path}"
            try:
                r = self.client.get(url, params={"q": term})
            except Exception as e:
                log(f"Genérico: error {path}: {e}")
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            ptxt, pnum = self._extract_from_cards(soup, term, log)
            if ptxt and pnum:
                log(f"Genérico verificado {path} -> {ptxt} ({pnum})")
                return ptxt, pnum
            m = PRICE_PAT.search(soup.get_text(" ", strip=True))
            if m:
                pnum = to_plain_int_str_from_text(m.group(0))
                if pnum:
                    ptxt = format_price_display_from_plain(pnum)
                    log(f"Genérico patrón {path} -> {ptxt} ({pnum})")
                    return ptxt, pnum
        return None, None

    # --------------------- Folletos / PDF --------------------------
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
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if "wa.me/?" in href:
                        pdf = self._extract_pdf_from_wa(href)
                        if pdf:
                            links.append(pdf)
                    elif href.lower().endswith(".pdf"):
                        full = href if href.startswith("http") else (base.rstrip("/") + "/" + href.lstrip("/"))
                        links.append(full)
                for iframe in soup.find_all("iframe", src=True):
                    src = iframe["src"]
                    if src.lower().endswith(".pdf"):
                        links.append(src)
            except Exception as e:
                log(f"Folleto error {u}: {e}")
        # Descargar y verificar proximidad (término cerca del precio)
        term_l = term.lower()
        for purl in links[:12]:
            txt = self._pdf_text_from_url(purl, log)
            if not txt:
                continue
            txt_l = txt.lower()
            if term_l not in txt_l:
                continue
            # buscar precio cercano al término (±200 caracteres)
            for m in PRICE_PAT.finditer(txt):
                pos = m.start()
                window = txt_l[max(0, pos-200): pos+200]
                if term_l in window:
                    pnum = to_plain_int_str_from_text(m.group(0))
                    if pnum:
                        ptxt = format_price_display_from_plain(pnum)
                        log(f"Folleto verificado {purl} -> {ptxt} ({pnum})")
                        return ptxt, pnum
        return None, None

    # ----------------- Orden de estrategias por vendor --------------
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

    # ---------------- Variantes de términos por producto ------------
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
            v2 = normalize_spaces(v)
            if v2 and v2 not in seen:
                out.append(v2); seen.add(v2)
        return out[:8]

    # ------------------------- Orquestación -------------------------
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
                row[f"{vn} (num)"] = price_num or ""  # entero plano sin separadores/decimales
            rows.append(row)

        df = pd.DataFrame(rows)
        return (df, logs) if return_logs else (df, [])
