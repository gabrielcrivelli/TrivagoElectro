# scraper.py
import re, io, time, random
from typing import Dict, List, Tuple, Optional, Callable
from datetime import datetime

import pandas as pd
import requests
from bs4 import BeautifulSoup
from pdfminer.high_level import extract_text as pdf_extract_text

# Opcional: curl_cffi para reducir 403 (si está disponible)
try:
    from curl_cffi import requests as curl_requests
    HAVE_CURLCFFI = True
except Exception:
    HAVE_CURLCFFI = False

# Opcional: pypdfium2 + PIL + pytesseract para OCR de folletos escaneados
try:
    import pypdfium2 as pdfium
    from PIL import Image
    HAVE_PDFIUM = True
except Exception:
    HAVE_PDFIUM = False

try:
    import pytesseract
    HAVE_TESS = True
except Exception:
    HAVE_TESS = False

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
]

PRICE_CSS = [
    ".woocommerce-Price-amount.amount",".price",".product-price",".prices",
    ".vtex-product-price-1-x-sellingPrice","[class*='price' i]","[class*='precio' i]","span[data-price]"
]
CARD_SELECTORS = [
    ".product-item","li.product",".product",".product-card",".grid-item",".product-box",
    ".vtex-product-summary-2-x-container",".ais-InfiniteHits-item"
]
TITLE_SELECTORS = [
    ".product-name",".product-title",".vtex-product-summary-2-x-productBrand",".vtex-product-summary-2-x-productNameContainer",
    "h1","h2","h3","a[title]"
]
PRICE_PAT = re.compile(r"\$?\s*\d[\d\.\,]*")

def s(x): return "" if x is None else str(x).strip()

def strip_decimal_and_non_digits(text: str) -> Optional[str]:
    if text is None: return None
    keep = re.sub(r"[^\d\.,]", "", str(text))
    if "," in keep:
        keep = keep.split(",", 1)[0]
    else:
        keep = re.sub(r"\.\d{1,2}\s*$", "", keep)
    digits = re.sub(r"\D", "", keep)
    return digits or None  # convierte “4.999.000,00” -> “4999000”, “6225.0” -> “6225”. [web:523]

def plain_from_float(v: float) -> str:
    return str(int(float(v)))  # 6225.0 -> “6225” sin decimales. [web:523]

def normalize_spaces(txt: str) -> str:
    return re.sub(r"\s+", " ", txt or "").strip()

def mk_variants_for_match(term: str) -> List[str]:
    base = normalize_spaces(term)
    v = [base]
    v2 = re.sub(r"[^A-Za-z0-9 ÁÉÍÓÚÜÑáéíóúüñ\-_/\.]", " ", base)
    v2 = normalize_spaces(v2)
    if v2 and v2.lower() not in [x.lower() for x in v]: v.append(v2)
    v3 = base.replace("/", " ").replace('"', " ").replace("'", " ")
    if v3.lower() not in [x.lower() for x in v]: v.append(normalize_spaces(v3))
    return v

def text_matches_any_variant(text: str, variants: List[str]) -> bool:
    lt = normalize_spaces(text).lower()
    for v in variants:
        if all(tok in lt for tok in normalize_spaces(v).lower().split()):
            return True
    return False

# ---------------- HTTP endurecido con fallback curl_cffi ----------------
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
    def __init__(self, delay_range=(2,5), log=None, cancel_cb=None):
        self.delay_range = delay_range
        self.log = log or (lambda *_: None)
        self.cancel_cb = cancel_cb or (lambda: False)
        self.rs = requests.Session()
        self.crs = curl_requests.Session() if HAVE_CURLCFFI else None

    def _prep(self, url):
        base = re.match(r"^https?://[^/]+", url)
        if base:
            hdr = browser_headers(base.group(0))
            self.rs.headers.clear(); self.rs.headers.update(hdr)
            if self.crs:
                self.crs.headers.clear(); self.crs.headers.update(hdr)

    def get(self, url, params=None, timeout=25):
        if self.cancel_cb(): raise RuntimeError("cancelled")
        self._prep(url)
        self.log(f"GET {url}" + (f" params={params}" if params else ""))
        try:
            r = self.rs.get(url, params=params, timeout=timeout, allow_redirects=True)
            self.log(f"HTTP {r.status_code} {r.url}")
            r.raise_for_status()
            time.sleep(random.uniform(*self.delay_range))
            return r
        except requests.HTTPError as e:
            if self.crs and getattr(e.response, "status_code", 0) == 403:
                r2 = self.crs.get(url, params=params, timeout=timeout, allow_redirects=True, impersonate="chrome124")
                self.log(f"HTTP {r2.status_code} {r2.url} (curl_cffi)")
                r2.raise_for_status()
                time.sleep(random.uniform(*self.delay_range))
                return r2
            raise

# ============================== Scraper ==============================
class PriceScraper:
    def __init__(self, headless: bool = True, delay_range: Tuple[int,int]=(2,5)):
        self.client: Optional[HttpClient] = None
        self.delay_range = delay_range

    # ---------- extracción confiable desde “cards” ----------
    def _extract_from_cards(self, soup: BeautifulSoup, term: str) -> Optional[str]:
        variants = mk_variants_for_match(term)
        for cs in CARD_SELECTORS:
            for card in soup.select(cs):
                ctxt = card.get_text(" ", strip=True)
                title_ok = text_matches_any_variant(ctxt, variants)
                if not title_ok:
                    for ts in TITLE_SELECTORS:
                        t = card.select_one(ts)
                        if t and text_matches_any_variant(t.get_text(" ", strip=True), variants):
                            title_ok = True; break
                if not title_ok: continue
                for ps in PRICE_CSS:
                    el = card.select_one(ps)
                    if el:
                        p = strip_decimal_and_non_digits(el.get_text(" ", strip=True))
                        if p: return p
                m = PRICE_PAT.search(ctxt)
                if m:
                    p = strip_decimal_and_non_digits(m.group(0))
                    if p: return p
        return None

    # ------------------------ VTEX (API) ------------------------
    def _try_vtex(self, base: str, term: str, log):
        api = f"{base.rstrip('/')}/api/catalog_system/pub/products/search"
        r = self.client.get(api, params={"_from": 0, "_to": 9, "ft": term})
        try: data = r.json()
        except Exception: return None, None
        if not isinstance(data, list) or not data:
            log("VTEX: sin resultados"); return None, None
        for prod in data:
            for it in (prod.get("items") or []):
                for sel in (it.get("sellers") or []):
                    offer = (sel.get("commertialOffer") or {})
                    if offer.get("Price") is not None:
                        pnum = plain_from_float(offer["Price"])
                        return f"$ {int(pnum):,}".replace(",", ".") + ",00", pnum
        for prod in data:
            pr = (prod.get("priceRange") or {}).get("sellingPrice", {})
            if pr.get("lowPrice") is not None:
                pnum = plain_from_float(pr["lowPrice"])
                return f"$ {int(pnum):,}".replace(",", ".") + ",00", pnum
        return None, None

    # --------------------- Magento (HTML) ---------------------
    def _try_magento_html(self, base: str, term: str, log):
        url = f"{base.rstrip('/')}/catalogsearch/result/"
        r = self.client.get(url, params={"q": term})
        soup = BeautifulSoup(r.text, "html.parser")
        price = self._extract_from_cards(soup, term)
        if price: return f"$ {int(price):,}".replace(",", ".") + ",00", price
        m = PRICE_PAT.search(soup.get_text(" ", strip=True))
        if m:
            price = strip_decimal_and_non_digits(m.group(0))
            if price: return f"$ {int(price):,}".replace(",", ".") + ",00", price
        return None, None

    # ---------------- WordPress / WooCommerce ----------------
    def _find_wp_search(self, html: str, base: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        form = soup.find("form", attrs={"role":"search"}) or soup.find("form", class_=re.compile("search", re.I))
        return (form.get("action") if form else None) or base.rstrip("/") + "/"

    def _try_wordpress(self, base: str, term: str, log):
        r = self.client.get(base.rstrip("/") + "/")
        action = self._find_wp_search(r.text, base)
        for params in ({"s": term}, {"s": term, "post_type": "product"}):
            rr = self.client.get(action, params=params)
            soup = BeautifulSoup(rr.text, "html.parser")
            price = self._extract_from_cards(soup, term)
            if price: return f"$ {int(price):,}".replace(",", ".") + ",00", price
        return None, None

    # ------------------------ Genérico ------------------------
    def _try_generic(self, base: str, term: str, log):
        for path in ["/search","/buscar","/busca","/s","/busqueda"]:
            try:
                rr = self.client.get(f"{base.rstrip('/')}{path}", params={"q": term})
                soup = BeautifulSoup(rr.text, "html.parser")
                price = self._extract_from_cards(soup, term)
                if price: return f"$ {int(price):,}".replace(",", ".") + ",00", price
                m = PRICE_PAT.search(soup.get_text(" ", strip=True))
                if m:
                    price = strip_decimal_and_non_digits(m.group(0))
                    if price: return f"$ {int(price):,}".replace(",", ".") + ",00", price
            except Exception as e:
                log(f"Genérico error {path}: {e}")
        return None, None

    # -------------------- Folletos / PDF (+OCR) --------------------
    def _extract_pdf_links(self, html: str, base: str) -> List[str]:
        soup = BeautifulSoup(html, "html.parser")
        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.lower().endswith(".pdf"):
                links.append(href if href.startswith("http") else (base.rstrip("/") + "/" + href.lstrip("/")))
        for iframe in soup.find_all("iframe", src=True):
            src = iframe["src"]
            if src.lower().endswith(".pdf"):
                links.append(src)
        return list(dict.fromkeys(links))

    def _pdf_text_from_url(self, url: str, log) -> str:
        r = self.client.get(url, timeout=45)
        bio = io.BytesIO(r.content)
        try:
            txt = pdf_extract_text(bio) or ""
            log(f"PDF extraído ({len(txt)} chars) {url}")
            return txt
        except Exception as e:
            log(f"PDF error {e} {url}")
            return ""

    def _pdf_ocr_pages(self, url: str, log, scale=2.2) -> str:
        if not HAVE_PDFIUM: return ""
        txts = []
        try:
            doc = pdfium.PdfDocument(io.BytesIO(self.client.get(url, timeout=45).content))
            for i in range(len(doc)):
                page = doc.get_page(i)
                img = page.render_topil(scale=scale, greyscale=False)
                ocr = pytesseract.image_to_string(img, lang="spa+eng") if HAVE_TESS else ""
                txts.append(ocr)
        except Exception as e:
            log(f"OCR error {e} {url}")
        return "\n".join(txts)

    def _try_brochures(self, base: str, term: str, log):
        pages = [base] + [f"{base.rstrip('/')}/{p}" for p in ["ofertas","oferta","promociones","folleto","folletos","catalogo","catalogos"]]
        pdfs = []
        for u in pages:
            try:
                html = self.client.get(u).text
                pdfs.extend(self._extract_pdf_links(html, base))
            except Exception as e:
                log(f"Folleto error {u}: {e}")
        variants = mk_variants_for_match(term)
        for purl in pdfs[:12]:
            txt = self._pdf_text_from_url(purl, log)
            if len(txt) < 200 and HAVE_PDFIUM:
                txt = self._pdf_ocr_pages(purl, log, scale=2.2)
            tl = txt.lower()
            for m in PRICE_PAT.finditer(txt):
                window = tl[max(0, m.start()-200): m.end()+200]
                if any(all(tok in window for tok in v.lower().split()) for v in variants):
                    p = strip_decimal_and_non_digits(m.group(0))
                    if p: return f"$ {int(p):,}".replace(",", ".") + ",00", p
        return None, None

    # ---------------- Orden de estrategias por vendedor ----------------
    def _detect_platform_order(self, vendor_name: str) -> List[str]:
        vn = (vendor_name or "").lower()
        if vn in ["cheeksa","cheek","vital"]: return ["brochures","wordpress","generic","vtex","magento"]
        if vn in ["megatone"]: return ["wordpress","generic","magento","vtex"]
        if vn in ["musimundo"]: return ["vtex","magento","wordpress","generic"]
        return ["vtex","magento","wordpress","generic"]

    def _search_vendor_once(self, vendor_name: str, base: str, term: str, log):
        for strat in self._detect_platform_order(vendor_name):
            try:
                if strat == "vtex": log(f"[{vendor_name}] estrategia=VTEX ft={term}"); res = self._try_vtex(base, term, log)
                elif strat == "magento": log(f"[{vendor_name}] estrategia=Magento q={term}"); res = self._try_magento_html(base, term, log)
                elif strat == "wordpress": log(f"[{vendor_name}] estrategia=WordPress q={term}"); res = self._try_wordpress(base, term, log)
                elif strat == "brochures": log(f"[{vendor_name}] estrategia=Folletos term={term}"); res = self._try_brochures(base, term, log)
                else: log(f"[{vendor_name}] estrategia=Genérico q={term}"); res = self._try_generic(base, term, log)
                if res and res[0] and res[1]: return res
            except requests.HTTPError as e:
                log(f"HTTPError {e}")
            except Exception as e:
                log(f"Error {e}")
        return None, None

    def _variants(self, p: Dict) -> List[str]:
        marca = s(p.get("marca")); modelo = s(p.get("modelo"))
        producto = s(p.get("producto")); capacidad = s(p.get("capacidad"))
        ean = s(p.get("ean"))
        vs = []
        if ean: vs.append(ean)
        if marca and modelo: vs.append(f"{marca} {modelo}")
        if modelo: vs.append(modelo)
        if producto: vs.append(producto)
        if marca and capacidad: vs.append(f"{marca} {capacidad}")
        out, seen = [], set()
        for v in vs:
            for cand in mk_variants_for_match(v):
                if cand and cand not in seen:
                    out.append(cand); seen.add(cand)
        return out[:10]

    def scrape_all_vendors(self, products: List[Dict], vendors: Dict[str,str], include_official_site: bool=False, return_logs: bool=False, cancel_cb: Optional[Callable[[], bool]]=None):
        logs: List[str] = []
        def log(msg: str): logs.append(msg)

        self.client = HttpClient(delay_range=self.delay_range, log=log, cancel_cb=cancel_cb)
        date_only = datetime.now().strftime("%d/%m/%Y")

        rows = []
        for p in (products or []):
            base_row = {"Producto": s(p.get("producto")), "Marca": s(p.get("marca")), "Marca (Sitio oficial)": "ND", "Fecha de Consulta": date_only}
            row = dict(base_row)
            for vn, url in (vendors or {}).items():
                price_txt, price_num = None, None
                for term in self._variants(p):
                    price_txt, price_num = self._search_vendor_once(vn, url, term, log)
                    if price_txt and price_num: break
                row[vn] = price_txt or "ND"
                row[f"{vn} (num)"] = price_num or ""  # entero plano sin decimales/separadores
            rows.append(row)

        df = pd.DataFrame(rows)
        return (df, logs) if return_logs else (df, [])
