import os, re, json
from pathlib import Path
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from scraper import PriceScraper
import gspread
from oauth2client.service_account import ServiceAccountCredentials

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
VENDORS_FILE = BASE_DIR / "VENDEDORES.txt"
PROMPT_FILE = BASE_DIR / "prompt-2.txt"

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
CORS(app)

DEFAULT_VENDORS = {
    "Carrefour": "https://www.carrefour.com.ar",
    "Cetrogar": "https://www.cetrogar.com.ar",
    "CheekSA": "https://cheeksa.com.ar",
    "Frávega": "https://www.fravega.com",
    "Libertad": "https://www.hiperlibertad.com.ar",
    "Masonline": "https://www.masonline.com.ar",
    "Megatone": "https://www.megatone.net",
    "Musimundo": "https://www.musimundo.com",
    "Naldo": "https://www.naldo.com.ar",
    "Vital": "https://www.vital.com.ar"
}

def to_str(x): 
    return "" if x is None else str(x).strip()

def parse_vendors_file(path: Path):
    if not path.exists():
        return None
    vendors = {}
    seps = ["|", ",", ";", "\t", " — ", " – ", " - ", "->", "=>"]
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for raw in f.readlines():
            line = to_str(raw)
            if not line or line.startswith("#"):
                continue
            name, url = line, ""
            for sep in seps:
                if sep in line:
                    name, url = [to_str(p) for p in line.split(sep, 1)]
                    break
            if not url:
                url = DEFAULT_VENDORS.get(name, "")
            vendors[name] = url
    return vendors or None

def parse_vendors_from_prompt(path: Path):
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    m = re.search(r"Vendedores a considerar[\s\S]*?(Carrefour[\s\S]*?Vital)", content, re.IGNORECASE)
    if not m:
        return None
    names = [to_str(x) for x in m.group(1).splitlines() if to_str(x)]
    out = {}
    for nm in names:
        nm_clean = re.sub(r"[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ ]", "", nm).strip()
        if nm_clean:
            out[nm_clean] = DEFAULT_VENDORS.get(nm_clean, "")
    return out or None

def sanitize_products(products):
    safe = []
    for p in products or []:
        p = p or {}
        safe.append({
            "producto": to_str(p.get("producto")),
            "marca": to_str(p.get("marca")),
            "modelo": to_str(p.get("modelo")),
            "capacidad": to_str(p.get("capacidad")),
            "ean": to_str(p.get("ean"))
        })
    return safe

@app.route("/", methods=["GET", "HEAD"])
def root():
    return app.send_static_file("index.html")

@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({"success": False, "error": "Not Found", "path": request.path}), 404
    return app.send_static_file("index.html"), 200

@app.route("/static/<path:filename>", methods=["GET", "HEAD"])
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()})

@app.route("/api/vendors", methods=["GET"])
def get_vendors():
    v_from_txt = parse_vendors_file(VENDORS_FILE)
    if v_from_txt:
        return jsonify({"vendors": v_from_txt})
    v_from_prompt = parse_vendors_from_prompt(PROMPT_FILE)
    if v_from_prompt:
        return jsonify({"vendors": v_from_prompt})
    return jsonify({"vendors": DEFAULT_VENDORS})

@app.route("/api/scrape", methods=["POST"])
def scrape():
    try:
        data = request.get_json(force=True, silent=False)
        if not isinstance(data, dict):
            return jsonify({"success": False, "error": "Cuerpo JSON inválido"}), 400

        products = sanitize_products(data.get("products", []))
        vendors = data.get("vendors")
        if not vendors or not isinstance(vendors, dict) or len(vendors) == 0:
            vendors = parse_vendors_file(VENDORS_FILE) or parse_vendors_from_prompt(PROMPT_FILE) or DEFAULT_VENDORS
        if not vendors:
            return jsonify({"success": False, "error": "No hay vendedores configurados"}), 400
        if not products:
            return jsonify({"success": False, "error": "No se enviaron productos"}), 400

        headless = bool(data.get("headless", True))
        min_delay = int(data.get("min_delay", 2))
        max_delay = int(data.get("max_delay", 5))
        include_official = bool(data.get("include_official", False))

        scraper = PriceScraper(headless=headless, delay_range=(min_delay, max_delay))
        df = scraper.scrape_all_vendors(products, vendors, include_official_site=include_official)

        ordered = [
            "Producto","Marca","Carrefour","Cetrogar","CheekSA","Frávega","Libertad",
            "Masonline","Megatone","Musimundo","Naldo","Vital","Marca (Sitio oficial)","Fecha de Consulta"
        ]
        for c in ordered:
            if c not in df.columns:
                df[c] = "ND"
        df = df[ordered]
        return jsonify({"success": True, "rows": df.to_dict(orient="records")})
    except Exception as ex:
        return jsonify({"success": False, "error": str(ex)}), 400

@app.route("/api/export/sheets", methods=["POST"])
def export_sheets():
    try:
        data = request.get_json(force=True, silent=False)
        rows = data.get("rows", [])
        sheet_name = data.get("sheet_name", "Comparación Precios Electrodomésticos")
        creds_path = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
        creds_b64 = os.getenv("GOOGLE_CREDENTIALS_BASE64", "")
        if creds_b64 and not os.path.exists(creds_path):
            import base64
            with open(creds_path, "wb") as f:
                f.write(base64.b64decode(creds_b64))

        scope = ["https://spreadsheets.google.com/feeds","https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
        client = gspread.authorize(creds)

        headers = list(rows[0].keys()) if rows else [
            "Producto","Marca","Carrefour","Cetrogar","CheekSA","Frávega","Libertad",
            "Masonline","Megatone","Musimundo","Naldo","Vital","Marca (Sitio oficial)","Fecha de Consulta"
        ]
        values = [headers] + [[(r.get(h, "") if r.get(h, "") is not None else "") for h in headers] for r in rows]

        try:
            sheet = client.open(sheet_name); ws = sheet.sheet1; ws.clear()
        except Exception:
            sheet = client.create(sheet_name); ws = sheet.sheet1

        ws.update("A1", values)
        try:
            ws.format("A1:Z1", {"textFormat": {"bold": True}})
        except Exception:
            pass

        return jsonify({"success": True, "sheet_url": sheet.url})
    except Exception as ex:
        return jsonify({"success": False, "error": str(ex)}), 400

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
