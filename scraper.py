import re, time, random, io
from typing import Dict, List, Tuple, Optional, Callable
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from pdfminer.high_level import extract_text as pdf_extract_text
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
PRICE_PAT = re.compile(r"\$?\s*\d[\d\.\,]*")

def s(x): return "" if x is None else str(x).strip()

def clean_price_text(text: str) -> Optional[str]:
    m = PRICE_PAT.search(text or "")
    if not m: 
        return None
    raw = re.sub(r"[^\d.,]", "", m.group(0))
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

def clean_price_num(text: str) -> Optional[str]:
    m = PRICE_PAT.search(text or "")
    if not m: 
        return None
    digits = re.sub(r"[^\d]", "", m.group(0))
    return digits or None

class HttpClient:
    def __init__(self, delay_range: Tuple[int,int]=(2,5), log=None, cancel_cb: Optional[Callable[[], bool]]=None):
        self.session = requests.Session()
        self.session.headers.update({"accept": "text/html,application/json"})
        self.delay_range = delay_range
        self.log = log or (lambda *_: None)
        self.cancel_cb = cancel_cb or (lambda: False)

    def get(self, url, params=None, timeout=25):
        if self.cancel_cb():
            raise RuntimeError("cancelled")
        self.session.headers.update({"user-agent": random.choice(UA_POOL)})
        self.log(f"GET {url}" + (f" params={params}" if params else ""))
        r = self.session.get(url, params=params, timeout=timeout)
        self.log(f"HTTP {r.status_code} {url}")
        time.sleep(random.uniform(*self.delay_range))
        r.raise_for_status()
        return r

class PriceScraper:
    def __init__(self, headless: bool=True, delay_range: Tuple[int,int]=(2,5)):
        self.client = None
        self.delay_range = delay_range

    # --------- VTEX ----------
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
                    if price and float(price) > 0:
                        log(f"VTEX: precio={price}")
                        return f"$ {float(price):,.2f}".replace(",", "_").replace(".", ",").replace("_", "."), re.sub(r"[^\d]", "", str(price))
        # priceRange
        for prod in data:
            pr = (prod.get("priceRange") or {}).get("sellingPrice", {})
            low = pr.get("lowPrice")
            if low:
                log(f"VTEX: lowPrice={low}")
                return f"$ {float(low):,.2f}".replace(",", "_").replace(".", ",").replace("_", "."), re.sub(r"[^\d]", "", str(low))
        return None, None

    # --------- Magento ----------
    def _try_magento_html(self, base: str, term: str, log) -> Tuple[Optional[str], Optional[str]]:
        url = f"{base.rstrip('/')}/catalogsearch/result/"
        r = self.client.get(url, params={"q": term})
        soup = BeautifulSoup(r.text, "html.parser")
        txt = soup.get_text(" ", strip=True)
        # CSS
        for css in PRICE_CSS:
            el = soup.select_one(css)
            if el:
                t = el.get_text(" ", strip=True)
                ptxt = clean_price_text(t)
                pnum = clean_price_num(t)
                if ptxt and pnum:
                    log(f"Magento: {css} -> {ptxt} ({pnum})")
                    return ptxt, pnum
        # patrón global
        m = PRICE_PAT.search(txt)
        if m:
            ptxt = clean_price_text(m.group(0))
            pnum = clean_price_num(m.group(0))
            if ptxt and pnum:
                log(f"Magento: patrón global -> {ptxt} ({pnum})")
                return ptxt, pnum
        return None, None

    # --------- Genérico ----------
    def _try_generic(self, base: str, term: str, log) -> Tuple[Optional[str], Optional[str]]:
        for path in ["/search", "/buscar", "/busca", "/s", "/busqueda"]:
            url = f"{base.rstrip('/')}{path}"
            try:
                r = self.client.get(url, params={"q": term})
            except Exception as e:
                log(f"Genérico: error {path}: {e}")
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            txt = soup.get_text(" ", strip=True)
            for css in PRICE_CSS:
                el = soup.select_one(css)
                if el:
                    t = el.get_text(" ", strip=True)
                    ptxt = clean_price_text(t)
                    pnum = clean_price_num(t)
                    if ptxt and pnum:
                        log(f"Genérico: {path} {css} -> {ptxt} ({pnum})")
                        return ptxt, pnum
            m = PRICE_PAT.search(txt)
            if m:
                ptxt = clean_price_text(m.group(0))
                pnum = clean_price_num(m.group(0))
                if ptxt and pnum:
                    log(f"Genérico: {path} patrón -> {ptxt} ({pnum})")
                    return ptxt, pnum
        return None, None

    # --------- Folletos (CheekSA/Vital) ----------
    def _pdf_text_from_url(self, url: str, log) -> str:
        # descarga parcial y extrae texto con pdfminer
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

    def _try_brochures(self, base: str, term: str, log) -> Tuple[Optional[str], Optional[str]]:
        candidates = [base] + [f"{base.rstrip('/')}/{p}" for p in ["ofertas","oferta","folleto","folletos","catalogo","catalogos"]]
        links = []
        for u in candidates:
            try:
                r = self.client.get(u)
                soup = BeautifulSoup(r.text, "html.parser")
                # enlaces a PDF y a visores conocidos
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if any(k in href.lower() for k in ["folleto","catalogo","oferta","ofertas","promo","promocion"]):
                        links.append(href if href.startswith("http") else (base.rstrip("/") + "/" + href.lstrip("/")))
                # visores embebidos
                for iframe in soup.find_all("iframe", src=True):
                    src = iframe["src"]
                    if any(x in src for x in ["publitas", "flipsnack", "issuu", "fliphtml5", "flowpaper"]):
                        links.append(src)
            except Exception as e:
                log(f"Folleto error {u}: {e}")
        # normalizar lista y dar prioridad a PDF
        uniq = []
        for h in links:
            if h not in uniq:
                uniq.append(h)
        pdfs = [h for h in uniq if h.lower().endswith(".pdf")]
        others = [h for h in uniq if h not in pdfs]
        # PDFs: buscar término y precio cercano
        for purl in pdfs[:5]:
            txt = self._pdf_text_from_url(purl, log)
            if term.lower() in txt.lower():
                # precio más cercano
                prices = PRICE_PAT.findall(txt)
                best = None
                for pr in prices:
                    pnum = clean_price_num(pr)
                    ptxt = clean_price_text(pr)
                    if pnum and ptxt:
                        best = (ptxt, pnum); break
                if best:
                    log(f"Folleto PDF hit {purl} -> {best[0]} ({best[1]})")
                    return best
        # Visores/otras páginas: buscar patrón
        for o in others[:5]:
            try:
                r = self.client.get(o)
                text = BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True)
                if term.lower() in text.lower():
                    ptxt = clean_price_text(text)
                    pnum = clean_price_num(text)
                    if ptxt and pnum:
                        log(f"Folleto visor hit {o} -> {ptxt} ({pnum})")
                        return ptxt, pnum
            except Exception as e:
                log(f"Folleto visor error {o}: {e}")
        return None, None

    def _detect_platform_order(self, vendor_name: str, domain: str) -> List[str]:
        # Para CheekSA/Vital, priorizar folletos; luego VTEX/Magento/genérico
        if vendor_name.lower() in ["cheeksa", "vital"]:
            return ["brochures", "vtex", "magento", "generic"]
        return ["vtex", "magento", "generic"]

    def _search_vendor_once(self, vendor_name: str, base: str, term: str, log) -> Tuple[Optional[str], Optional[str]]:
        for strategy in self._detect_platform_order(vendor_name, base):
            try:
                if strategy == "vtex":
                    log(f"[{vendor_name}] estrategia=VTEX ft={term}")
                    res = self._try_vtex(base, term, log)
                elif strategy == "magento":
                    log(f"[{vendor_name}] estrategia=Magento q={term}")
                    res = self._try_magento_html(base, term, log)
                elif strategy == "brochures":
                    log(f"[{vendor_name}] estrategia=Folletos term={term}")
                    res = self._try_brochures(base, term, log)
                else:
                    log(f"[{vendor_name}] estrategia=Genérico q={term}")
                    res = self._try_generic(base, term, log)
                if res and (res[0] and res[1]):
                    return res
            except requests.HTTPError as e:
                log(f"HTTPError {e}")
                continue
            except Exception as e:
                log(f"Error {e}")
                continue
        return None, None

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
        return out

    def scrape_all_vendors(self, products: List[Dict], vendors: Dict[str, str], include_official_site: bool = False, return_logs: bool = False, cancel_cb: Optional[Callable[[], bool]] = None):
        logs: List[str] = []
        def log(msg): 
            logs.append(msg)

        self.client = HttpClient(delay_range=self.delay_range, log=log, cancel_cb=cancel_cb)
        date_only = datetime.now().strftime("%d/%m/%Y")

        rows = []
        for p in products or []:
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
                # registrar num plano en columna separada
                row[f"{vn} (num)"] = price_num or ""
            rows.append(row)

        df = pd.DataFrame(rows)
        return (df, logs) if return_logs else (df, [])
