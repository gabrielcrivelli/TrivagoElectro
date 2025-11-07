# scraper.py
import re, io, time, random
from typing import Dict, List, Tuple, Optional, Callable
from datetime import datetime

import pandas as pd
import requests
from bs4 import BeautifulSoup
from pdfminer.high_level import extract_text as pdf_extract_text

# Opcional: curl_cffi para superar 403 por fingerprint (si está disponible)
try:
    from curl_cffi import requests as curl_requests
    HAVE_CURLCFFI = True
except Exception:
    HAVE_CURLCFFI = False

# Opcional: pypdfium2 (rasterizar PDF) + Pillow para OCR
try:
    import pypdfium2 as pdfium
    from PIL import Image
    HAVE_PDFIUM = True
except Exception:
    HAVE_PDFIUM = False

# Opcional: pytesseract como OCR local (requiere binario tesseract en el sistema)
try:
    import pytesseract
    HAVE_TESS = True
except Exception:
    HAVE_TESS = False

from urllib.parse import urlparse, parse_qs

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
]  # cabeceras de navegador modernas ayudan contra 403. [web:477]

PRICE_CSS = [
    ".price",".product-price",".prices",".vtex-product-price-1-x-sellingPrice",
    ".woocommerce-Price-amount.amount","[class*='price' i]","[class*='precio' i]","span[data-price]"
]  # selectores comunes de precio en tiendas. [web:459]

CARD_SELECTORS = [
    ".product-item","li.product",".product",".product-card",".grid-item",".product-box",
    ".vtex-product-summary-2-x-container",".ais-InfiniteHits-item"
]  # tarjetas de producto típicas en Woo/Magento/VTEX. [web:459]

TITLE_SELECTORS = [
    ".product-name",".product-title",".vtex-product-summary-2-x-productBrand",".vtex-product-summary-2-x-productNameContainer",
    "h1","h2","h3","a[title]"
]  # selectores frecuentes de título dentro de la card. [web:459]

PRICE_PAT = re.compile(r"\$?\s*\d[\d\.\,]*")  # patrón monetario simple. [web:459]

def s(x): return "" if x is None else str(x).strip()  # utilitario básico. [web:459]

def normalize_spaces(txt: str) -> str:
    return re.sub(r"\s+", " ", txt or "").strip()  # limpia espacios para comparaciones. [web:459]

def mk_variants_for_match(term: str) -> List[str]:
    base = normalize_spaces(term)
    v = [base]
    v2 = re.sub(r"[^A-Za-z0-9 ÁÉÍÓÚÜÑáéíóúüñ\-_/\.]", " ", base)
    v2 = normalize_spaces(v2)
    if v2 and v2.lower() not in [x.lower() for x in v]:
        v.append(v2)
    # variantes adicionales que eviten signos conflictivos (/, ", etc.)
    v3 = base.replace("/", " ").replace('"', " ").replace("'", " ")
    if v3.lower() not in [x.lower() for x in v]:
        v.append(normalize_spaces(v3))
    return v  # variantes robustas para matching en cards. [web:459]

def text_matches_any_variant(text: str, variants: List[str]) -> bool:
    lt = normalize_spaces(text).lower()
    for v in variants:
        if all(tok in lt for tok in normalize_spaces(v).lower().split()):
            return True
    return False  # exige que la card mencione el término (tokens incluidos). [web:459]

def plain_from_text(text: str) -> Optional[str]:
    # entero plano sin signos ni separadores ni decimales
    norm = re.sub(r"[^\d\.,]", "", text or "")
    if not norm: 
        digits = re.sub(r"[^\d]", "", text or "")
        return digits or None
    try:
        if "," in norm and "." in norm:
            if norm.rfind(",") > norm.rfind("."):
                norm = norm.replace(".", "").replace(",", ".")
            else:
                norm = norm.replace(",", "")
        elif "," in norm and "." not in norm:
            parts = norm.split(",")
            norm = norm.replace(",", ".") if len(parts[-1]) <= 2 else norm.replace(",", "")
        elif "." in norm and "," not in norm and len(norm.replace(".", "")) >= 5:
            norm = norm.replace(".", "")
        return str(int(float(norm)))
    except Exception:
        digits = re.sub(r"[^\d]", "", text or "")
        return digits or None  # entrega entero plano siempre que sea posible. [web:459]

def pretty_from_plain(plain: str) -> str:
    try:
        val = int(plain)
        out = f"$ {val:,.2f}"
        return out.replace(",", "_").replace(".", ",").replace("_", ".")
    except Exception:
        return ""  # solo para visual; la columna “(num)” es la fuente de verdad. [web:459]

# -------------------- Cliente HTTP con fallback curl_cffi --------------------

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
}  # cabeceras realistas reducen 403. [web:477]

def browser_headers(domain: str) -> dict:
    ua = random.choice(UA_POOL)
    h = dict(DEFAULT_HEADERS)
    h["user-agent"] = ua
    h["sec-ch-ua"] = '"Chromium";v="120", "Google Chrome";v="120", "Not:A-Brand";v="99"'
    h["sec-ch-ua-platform"] = '"Windows"'
    h["sec-ch-ua-mobile"] = "?0"
    h["referer"] = f"{domain.rstrip('/')}/"
    return h  # fingerprint coherente con navegadores actuales. [web:477]

class HttpClient:
    def __init__(self, delay_range=(2,5), log=None, cancel_cb=None):
        self.delay_range = delay_range
        self.log = log or (lambda *_: None)
        self.cancel_cb = cancel_cb or (lambda: False)
        # dos sesiones: requests y curl_cffi (si existe)
        self.rs = requests.Session()
        self.crs = curl_requests.Session() if HAVE_CURLCFFI else None  # impersona Chrome. [web:477]

    def _prep(self, url):
        base = re.match(r"^https?://[^/]+", url)
        if base:
            hdr = browser_headers(base.group(0))
            self.rs.headers.clear(); self.rs.headers.update(hdr)
            if self.crs:
                self.crs.headers.clear(); self.crs.headers.update(hdr)

    def get(self, url, params=None, timeout=25):
        if self.cancel_cb():
            raise RuntimeError("cancelled")
        self._prep(url)
        self.log(f"GET {url}" + (f" params={params}" if params else ""))
        try:
            r = self.rs.get(url, params=params, timeout=timeout, allow_redirects=True)
            self.log(f"HTTP {r.status_code} {r.url}")
            r.raise_for_status()
            time.sleep(random.uniform(*self.delay_range))
            return r
        except requests.HTTPError as e:
            # si 403 y hay curl_cffi, reintentar con fingerprint de navegador real
            if self.crs and getattr(e.response, "status_code", 0) == 403:
                r2 = self.crs.get(url, params=params, timeout=timeout, allow_redirects=True, impersonate="chrome124")
                self.log(f"HTTP {r2.status_code} {r2.url} (curl_cffi)")
                r2.raise_for_status()
                time.sleep(random.uniform(*self.delay_range))
                return r2
            raise

# =============================== Scraper ===============================

class PriceScraper:
    def __init__(self, headless: bool = True, delay_range: Tuple[int,int] = (2,5)):
        self.client: Optional[HttpClient] = None
        self.delay_range = delay_range

    # ---------- extracción confiable dentro de “cards” ----------
    def _extract_from_cards(self, soup: BeautifulSoup, term: str, log) -> Tuple[Optional[str], Optional[str]]:
        variants = mk_variants_for_match(term)
        for cs in CARD_SELECTORS:
            for card in soup.select(cs):
                card_txt = card.get_text(" ", strip=True)
                title_ok = text_matches_any_variant(card_txt, variants)
                if not title_ok:
                    for ts in TITLE_SELECTORS:
                        t = card.select_one(ts)
                        if t and text_matches_any_variant(t.get_text(" ", strip=True), variants):
                            title_ok = True; break
                if not title_ok:
                    continue
                # buscar precio dentro del card
                for pc in PRICE_CSS:
                    el = card.select_one(pc)
                    if el:
                        pnum = plain_from_text(el.get_text(" ", strip=True))
                        if pnum:
                            ptxt = pretty_from_plain(pnum)
                            log(f"Card {cs} {pc} -> {ptxt} ({pnum})")
                            return ptxt, pnum
                # patrón local si no hubo selectores
                m = PRICE_PAT.search(card_txt)
                if m:
                    pnum = plain_from_text(m.group(0))
                    if pnum:
                        ptxt = pretty_from_plain(pnum)
                        log(f"Card {cs} patrón -> {ptxt} ({pnum})")
                        return ptxt, pnum
        return None, None  # evita “50” por textos ajenos al producto. [web:459]

    # ---------------------- VTEX (API pública) ----------------------
    def _try_vtex(self, base: str, term: str, log):
        api = f"{base.rstrip('/')}/api/catalog_system/pub/products/search"
        r = self.client.get(api, params={"_from": 0, "_to": 9, "ft": term})
        try:
            data = r.json()
        except Exception:
            return None, None
        if not isinstance(data, list) or not data:
            log("VTEX: sin resultados"); return None, None
        for prod in data:
            for it in (prod.get("items") or []):
                for sel in (it.get("sellers") or []):
                    offer = sel.get("commertialOffer") or {}
                    if offer.get("Price") is not None:
                        pnum = plain_from_text(str(offer["Price"]))
                        if pnum:
                            log(f"VTEX: precio={offer['Price']}")
                            return pretty_from_plain(pnum), pnum
        for prod in data:
            pr = (prod.get("priceRange") or {}).get("sellingPrice", {})
            if pr.get("lowPrice") is not None:
                pnum = plain_from_text(str(pr["lowPrice"]))
                if pnum:
                    log(f"VTEX: lowPrice={pr['lowPrice']}")
                    return pretty_from_plain(pnum), pnum
        return None, None  # guía VTEX: ft/_from/_to. [web:361]

    # ---------------------- WooCommerce Store API ----------------------
    def _try_woo_storeapi(self, base: str, term: str, log):
        # Store API pública (wc/store/products?search=...) en sitios con Woo Blocks
        url = f"{base.rstrip('/')}/wp-json/wc/store/products"
        try:
            r = self.client.get(url, params={"search": term, "per_page": 5})
            items = r.json() if r.content else []
            if isinstance(items, list):
                variants = mk_variants_for_match(term)
                for it in items:
                    name = s(it.get("name"))
                    sku = s(it.get("sku"))
                    if text_matches_any_variant(f"{name} {sku}", variants):
                        prices = it.get("prices") or {}
                        # Store API expone centavos en muchos temas; normalizar
                        raw = prices.get("price") or prices.get("regular_price") or prices.get("sale_price")
                        if raw is not None:
                            # si viene en centavos (string), convertir a entero plano
                            raw_s = str(raw)
                            if raw_s.isdigit() and len(raw_s) >= 3:
                                pnum = str(int(int(raw_s) / 100))
                            else:
                                pnum = plain_from_text(raw_s)
                            if pnum:
                                ptxt = pretty_from_plain(pnum)
                                log(f"Woo Store API -> {ptxt} ({pnum})")
                                return ptxt, pnum
        except Exception as e:
            log(f"Woo Store API error: {e}")
        return None, None  # documentación Store API pública. [web:468]

    # ---------------------- WordPress / Woo (HTML) ----------------------
    def _find_wp_search(self, html: str, base: str) -> Optional[str]:
        soup = BeautifulSoup(html, "html.parser")
        form = soup.find("form", attrs={"role": "search"}) or soup.find("form", class_=re.compile("search", re.I))
        return (form.get("action") if form else None) or base.rstrip("/") + "/"

    def _try_wordpress(self, base: str, term: str, log):
        # 1) intentar Store API primero
        ptxt, pnum = self._try_woo_storeapi(base, term, log)
        if ptxt and pnum:
            return ptxt, pnum
        # 2) HTML: formulario s=... y s&post_type=product
        r = self.client.get(base.rstrip("/") + "/")
        action = self._find_wp_search(r.text, base)
        for params in ({"s": term}, {"s": term, "post_type": "product"}):
            try:
                rr = self.client.get(action, params=params)
                soup = BeautifulSoup(rr.text, "html.parser")
                ptxt, pnum = self._extract_from_cards(soup, term, log)
                if ptxt and pnum:
                    return ptxt, pnum
            except Exception as e:
                log(f"WordPress error {params}: {e}")
        return None, None  # flujo recomendado: Store API y luego cards HTML. [web:468][web:459]

    # ---------------------- Magento (HTML) ----------------------
    def _try_magento_html(self, base: str, term: str, log):
        url = f"{base.rstrip('/')}/catalogsearch/result/"
        r = self.client.get(url, params={"q": term})
        soup = BeautifulSoup(r.text, "html.parser")
        ptxt, pnum = self._extract_from_cards(soup, term, log)
        if ptxt and pnum:
            return ptxt, pnum
        # último recurso global
        m = PRICE_PAT.search(soup.get_text(" ", strip=True))
        if m:
            pnum = plain_from_text(m.group(0))
            if pnum:
                return pretty_from_plain(pnum), pnum
        return None, None  # prioriza cards para evitar falsos “50”. [web:459][web:422]

    # ---------------------- Genérico (HTML) ----------------------
    def _try_generic(self, base: str, term: str, log):
        for path in ["/search","/buscar","/busca","/s","/busqueda"]:
            try:
                rr = self.client.get(f"{base.rstrip('/')}{path}", params={"q": term})
                soup = BeautifulSoup(rr.text, "html.parser")
                ptxt, pnum = self._extract_from_cards(soup, term, log)
                if ptxt and pnum:
                    return ptxt, pnum
                m = PRICE_PAT.search(soup.get_text(" ", strip=True))
                if m:
                    pnum = plain_from_text(m.group(0))
                    if pnum:
                        return pretty_from_plain(pnum), pnum
            except Exception as e:
                log(f"Genérico error {path}: {e}")
        return None, None  # cards primero, luego patrón global como fallback. [web:459]

    # ---------------------- Folletos / PDF (+OCR) ----------------------
    def _pdf_text_from_url(self, url: str, log) -> str:
        r = self.client.get(url, timeout=45)
        bio = io.BytesIO(r.content)
        try:
            txt = pdf_extract_text(bio) or ""
            log(f"PDF extraído ({len(txt)} chars) {url}")
            return txt
        except Exception as e:
            log(f"PDF error {e} {url}")
            return ""  # pdfminer falla en escaneados; se rasteriza con PDFium. [web:485][web:487]

    def _pdf_ocr_pages(self, url: str, log, scale=2.2) -> str:
        if not HAVE_PDFIUM:
            return ""
        text_all = []
        try:
            pdf = pdfium.PdfDocument(io.BytesIO(self.client.get(url, timeout=45).content))
            for i in range(len(pdf)):
                page = pdf.get_page(i)
                # “zoom”: render_topil con scale>2 para mejorar OCR
                img = page.render_topil(scale=scale, greyscale=False)
                if HAVE_TESS:
                    ocr_txt = pytesseract.image_to_string(img, lang="spa+eng")
                else:
                    ocr_txt = ""  # si no hay Tesseract, puede conectarse a una API OCR externa
                text_all.append(ocr_txt)
            return "\n".join(text_all)
        except Exception as e:
            log(f"OCR error {e} {url}")
            return ""  # pypdfium2: rasterización para OCR confiable. [web:485][web:494]

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
        return list(dict.fromkeys(links))  # PDF directos desde páginas de folletos. [web:459]

    def _try_brochures(self, base: str, term: str, log):
        # rastrear páginas comunes de ofertas/folletos
        pages = [base] + [f"{base.rstrip('/')}/{p}" for p in ["ofertas","oferta","promociones","folleto","folletos","catalogo","catalogos"]]
        pdfs = []
        for u in pages:
            try:
                html = self.client.get(u).text
                pdfs.extend(self._extract_pdf_links(html, base))
            except Exception as e:
                log(f"Folleto error {u}: {e}")
        # leer PDFs: si el texto es corto, rasterizar y hacer OCR
        variants = mk_variants_for_match(term)
        for purl in pdfs[:12]:
            plain = ""
            txt = self._pdf_text_from_url(purl, log)
            if len(txt) < 200 and HAVE_PDFIUM:
                txt = self._pdf_ocr_pages(purl, log, scale=2.2)
            tl = txt.lower()
            for m in PRICE_PAT.finditer(txt):
                window = tl[max(0, m.start()-200): m.end()+200]
                if any(all(tok in window for tok in v.lower().split()) for v in variants):
                    pnum = plain_from_text(m.group(0))
                    if pnum:
                        return pretty_from_plain(pnum), pnum
        return None, None  # rasterizar+OCR para PDFs escaneados. [web:485][web:494]

    # ---------------------- Estrategias por vendedor ----------------------
    def _detect_platform_order(self, vendor_name: str) -> List[str]:
        vn = (vendor_name or "").lower()
        if vn in ["cheeksa","cheek","vital"]:
            return ["brochures","wordpress","generic","vtex","magento"]  # folletos primero en Cheek/Vital
        if vn in ["megatone"]:
            return ["wordpress","generic","magento","vtex"]
        if vn in ["musimundo"]:
            return ["vtex","magento","wordpress","generic"]  # reforzar VTEX y evitar patrón global
        return ["vtex","magento","wordpress","generic"]  # orden por defecto

    def _search_vendor_once(self, vendor_name: str, base: str, term: str, log):
        for strat in self._detect_platform_order(vendor_name):
            try:
                if strat == "vtex":
                    log(f"[{vendor_name}] estrategia=VTEX ft={term}")
                    res = self._try_vtex(base, term, log)
                elif strat == "magento":
                    log(f"[{vendor_name}] estrategia=Magento q={term}")
                    res = self._try_magento_html(base, term, log)
                elif strat == "wordpress":
                    log(f"[{vendor_name}] estrategia=WordPress q={term}")
                    res = self._try_wordpress(base, term, log)
                elif strat == "brochures":
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

    # ---------------------- Variantes de búsqueda ----------------------
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
        # sanitizar variantes conflictivas con / o comillas
        out, seen = [], set()
        for v in vs:
            for cand in mk_variants_for_match(v):
                if cand and cand not in seen:
                    out.append(cand); seen.add(cand)
        return out[:10]  # variantes robustas contra símbolos. [web:459]

    # ---------------------- Orquestación principal ----------------------
    def scrape_all_vendors(self, products: List[Dict], vendors: Dict[str,str], include_official_site: bool = False, return_logs: bool = False, cancel_cb: Optional[Callable[[], bool]] = None):
        logs: List[str] = []
        def log(msg: str): logs.append(msg)

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
                if cancel_cb and cancel_cb(): raise RuntimeError("cancelled")
                url = s(url)
                price_txt, price_num = None, None
                for term in self._variants(p):
                    price_txt, price_num = self._search_vendor_once(vn, url, term, log)
                    if price_txt and price_num: break
                row[vn] = price_txt or "ND"
                row[f"{vn} (num)"] = price_num or ""  # entero plano sin signo, puntos ni comas
            rows.append(row)

        df = pd.DataFrame(rows)
        return (df, logs) if return_logs else (df, [])
