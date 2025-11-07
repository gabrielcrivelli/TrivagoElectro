import re, time, random
from typing import Dict, List, Tuple, Optional
from datetime import datetime

import requests
from bs4 import BeautifulSoup
import pandas as pd

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
]

PRICE_CSS = [
    ".price", ".product-price", ".prices", ".vtex-product-price-1-x-sellingPrice",
    "[class*='price' i]", "[class*='precio' i]", "span[data-price]"
]

def s(x): return "" if x is None else str(x).strip()

def clean_price_text(text: str) -> Optional[str]:
    if not isinstance(text, str):
        return None
    raw = re.sub(r"[^\d.,]", "", text)
    if not raw:
        return None
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        parts = raw.split(",")
        raw = raw.replace(",", ".") if len(parts[-1]) <= 2 else raw.replace(",", "")
    elif "." in raw and len(raw.replace(".", "")) >= 5:
        raw = raw.replace(".", "")
    try:
        val = float(raw)
        out = f"$ {val:,.2f}"
        return out.replace(",", "_").replace(".", ",").replace("_", ".")
    except Exception:
        return None

class HttpClient:
    def __init__(self, delay_range: Tuple[int,int]=(2,5)):
        self.session = requests.Session()
        self.session.headers.update({"accept": "text/html,application/json"})
        self.delay_range = delay_range

    def get(self, url, params=None, timeout=25):
        self.session.headers.update({"user-agent": random.choice(UA_POOL)})
        r = self.session.get(url, params=params, timeout=timeout)
        time.sleep(random.uniform(*self.delay_range))
        r.raise_for_status()
        return r

class PriceScraper:
    def __init__(self, headless: bool=True, delay_range: Tuple[int,int]=(2,5)):
        # headless no se usa en HTTP, queda por compatibilidad de API
        self.client = HttpClient(delay_range=delay_range)

    # --------- VTEX ----------
    def _try_vtex(self, base: str, term: str) -> Optional[str]:
        # /api/catalog_system/pub/products/search con ft y paginación simple
        api = f"{base.rstrip('/')}/api/catalog_system/pub/products/search"
        r = self.client.get(api, params={"_from": 0, "_to": 9, "ft": term})
        data = r.json()
        if not isinstance(data, list) or not data:
            return None
        # buscar el primer precio disponible en items/sellers/commertialOffer
        for prod in data:
            items = prod.get("items") or []
            for it in items:
                sellers = it.get("sellers") or []
                for sel in sellers:
                    offer = sel.get("commertialOffer") or {}
                    price = offer.get("Price")
                    if price and float(price) > 0:
                        return clean_price_text(str(price))
        # algunos VTEX exponen priceRange
        for prod in data:
            pr = (prod.get("priceRange") or {}).get("sellingPrice", {})
            low = pr.get("lowPrice")
            if low:
                return clean_price_text(str(low))
        return None

    # --------- Magento ----------
    def _try_magento_html(self, base: str, term: str) -> Optional[str]:
        url = f"{base.rstrip('/')}/catalogsearch/result/"
        r = self.client.get(url, params={"q": term})
        soup = BeautifulSoup(r.text, "html.parser")
        # intentar varias clases de precio
        for css in PRICE_CSS:
            el = soup.select_one(css)
            if el:
                p = clean_price_text(el.get_text(" ", strip=True))
                if p:
                    return p
        # buscar patrones $ 123.456,78
        m = re.search(r"\$\s*\d[\d\.\,]*", soup.get_text(" ", strip=True))
        if m:
            return clean_price_text(m.group(0))
        return None

    # --------- Genérico ----------
    def _try_generic(self, base: str, term: str) -> Optional[str]:
        for path in ["/search", "/buscar", "/busca", "/s", "/busqueda"]:
            url = f"{base.rstrip('/')}{path}"
            try:
                r = self.client.get(url, params={"q": term})
            except Exception:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for css in PRICE_CSS:
                el = soup.select_one(css)
                if el:
                    p = clean_price_text(el.get_text(" ", strip=True))
                    if p:
                        return p
            m = re.search(r"\$\s*\d[\d\.\,]*", soup.get_text(" ", strip=True))
            if m:
                return clean_price_text(m.group(0))
        return None

    def _detect_platform_order(self, domain: str) -> List[str]:
        # preferimos VTEX por prevalencia en retailers ARG; luego Magento; luego genérico
        return ["vtex", "magento", "generic"]

    def _search_vendor_once(self, base: str, term: str) -> Optional[str]:
        for strategy in self._detect_platform_order(base):
            try:
                if strategy == "vtex":
                    res = self._try_vtex(base, term)
                elif strategy == "magento":
                    res = self._try_magento_html(base, term)
                else:
                    res = self._try_generic(base, term)
                if res:
                    return res
            except requests.HTTPError:
                continue
            except Exception:
                continue
        return None

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
        # deduplicar
        out, seen = [], set()
        for v in vs:
            if v and v not in seen:
                out.append(v); seen.add(v)
        return out

    def _search_in_vendor(self, vendor_name: str, vendor_url: str, product: Dict) -> str:
        base = vendor_url or ""
        variants = self._variants(product)
        for term in variants:
            price = self._search_vendor_once(base, term)
            if price:
                return price
        return "ND"

    def scrape_all_vendors(self, products: List[Dict], vendors: Dict[str, str], include_official_site: bool = False):
        now = datetime.now().strftime("%d/%m/%Y %H:%M")
        rows = []
        for p in products or []:
            row = {
                "Producto": s(p.get("producto")),
                "Marca": s(p.get("marca")),
                "Marca (Sitio oficial)": "ND",
                "Fecha de Consulta": now
            }
            for vn, url in (vendors or {}).items():
                row[vn] = self._search_in_vendor(vn, url, p) or "ND"
            rows.append(row)
        return pd.DataFrame(rows)
