import time, random, re
from datetime import datetime
from typing import List, Dict, Tuple, Optional

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager
import pandas as pd
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("scraper")

def s(x):  # safe string
    return "" if x is None else str(x).strip()

class PriceScraper:
    def __init__(self, headless: bool = True, delay_range: Tuple[int, int] = (2, 5)):
        self.headless = headless
        self.delay_range = delay_range
        self.driver = None
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        ]

    def _init_driver(self):
        ua = random.choice(self.user_agents)
        opts = Options()
        if self.headless:
            opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument(f"user-agent={ua}")
        opts.add_argument("--disable-gpu")
        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=opts)
        self.driver.implicitly_wait(8)

    def _delay(self):
        time.sleep(random.uniform(*self.delay_range))

    def _search_box(self):
        selectors = [
            'input[type="search"]','input[name*="search" i]','input[name*="buscar" i]',
            'input[placeholder*="Buscar" i]','#search-input','.search-input','#searchbox'
        ]
        for css in selectors:
            try:
                el = self.driver.find_element(By.CSS_SELECTOR, css)
                if el.is_displayed() and el.is_enabled():
                    return el
            except Exception:
                continue
        return None

    def _find_prices(self):
        prices = []
        selectors = ['.price','[class*="precio" i]','[class*="price" i]','.product-price','[data-price]','span[class*="precio" i]']
        for css in selectors:
            try:
                els = self.driver.find_elements(By.CSS_SELECTOR, css)
                for e in els:
                    tx = s(e.text)
                    if tx and (("$" in tx) or re.search(r"\d{2,}", tx)):
                        prices.append(tx)
                if prices:
                    break
            except Exception:
                continue
        return prices

    def _clean_price(self, text) -> Optional[str]:
        if not isinstance(text, str):
            return None
        raw = re.sub(r"[^0-9.,]", "", text)
        if not raw:
            return None
        if "," in raw and "." in raw:
            raw = raw.replace(".", "").replace(",", ".") if raw.rfind(",") > raw.rfind(".") else raw.replace(",", "")
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

    def _variants(self, p: Dict) -> List[str]:
        marca = s(p.get("marca"))
        modelo = s(p.get("modelo"))
        producto = s(p.get("producto"))
        capacidad = s(p.get("capacidad"))
        ean = s(p.get("ean"))
        vs = []
        if marca and modelo: vs.append(f"{marca} {modelo}")
        if marca and modelo and len(modelo) >= 4: vs.append(f"{marca} {modelo[:4]}")
        if producto: vs.append(producto)
        if modelo: vs.append(modelo)
        if marca and capacidad: vs.append(f"{marca} {capacidad}")
        if ean: vs.append(ean)
        uniq, seen = [], set()
        for v in vs:
            if v and v not in seen:
                uniq.append(v); seen.add(v)
        return uniq[:6]

    def _search_in_vendor(self, vendor_name: str, vendor_url: str, product: Dict, max_retries: int = 3) -> str:
        variants = self._variants(product)
        if not s(vendor_url):
            return "ND"
        for attempt in range(max_retries):
            try:
                self.driver.get(vendor_url)
                self._delay()
                for v in variants:
                    sb = self._search_box()
                    if not sb:
                        return "ND"
                    sb.clear()
                    sb.send_keys(v)
                    try: sb.submit()
                    except Exception: sb.send_keys("\n")
                    self._delay()
                    prices = self._find_prices()
                    for pt in prices:
                        clean = self._clean_price(pt)
                        if clean: return clean
                return "ND"
            except TimeoutException:
                if attempt == max_retries - 1: return "ND"
                time.sleep(1)
            except Exception:
                if attempt == max_retries - 1: return "ND"
                time.sleep(1)
        return "ND"

    def scrape_all_vendors(self, products: List[Dict], vendors: Dict[str, str], include_official_site: bool = False):
        self._init_driver()
        rows = []
        try:
            for p in products or []:
                row = {
                    "Producto": s(p.get("producto")),
                    "Marca": s(p.get("marca")),
                    "Marca (Sitio oficial)": "ND",
                    "Fecha de Consulta": datetime.now().strftime("%d/%m/%Y %H:%M")
                }
                for vn, url in (vendors or {}).items():
                    url = s(url)
                    if not url:
                        row[vn] = "ND"; continue
                    price = self._search_in_vendor(vn, url, p)
                    row[vn] = price or "ND"
                    self._delay()
                rows.append(row)
        finally:
            try: self.driver.quit()
            except Exception: pass
        return pd.DataFrame(rows)
