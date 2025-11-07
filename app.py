import os, json, re
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from datetime import datetime
from scraper import PriceScraper
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__, static_folder="static", static_url_path="/")
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
PROMPT_FILE = "prompt-2.txt"

def parse_vendors_from_prompt(path: str):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    block = re.search(r"Vendedores a considerar[\\s\\S]*?(Carrefour[\\s\\S]*?Vital)", content, re.IGNORECASE)
    if not block:
        return None
    lines = [x.strip() for x in block.group(1).splitlines() if x.strip()]
    names = []
    for ln in lines:
        nm = re.sub(r"[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ ]", "", ln).strip()
        if nm and nm not in names:
            names.append(nm)
    result = {}
    for nm in names:
        if nm in DEFAULT_VENDORS:
            result[nm] = DEFAULT_VENDORS[nm]
        else:
            result[nm] = ""
    return result or None

@app.route("/")
def root():
    return send_from_directory("static", "index.html")

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()})

@app.errorhandler(404)
def not_found(e):
    # Si la ruta empieza por /api, devolver JSON
    if request.path.startswith("/api/"):
        return jsonify({"success": False, "error": "Not Found", "path": request.path}), 404
    # Si no es /api, servir index.html (SPA)
    return send_from_directory("static", "index.html"), 200

@app.errorhandler(500)
def server_error(e):
    if request.path.startswith("/api/"):
        return jsonify({"success": False, "error": "Internal Server Error"}), 500
    return jsonify({"success": False, "error": "Internal Error"}), 500

@app.route("/api/vendors", methods=["GET"])
def get_vendors():
    vendors = parse_vendors_from_prompt(PROMPT_FILE) or DEFAULT_VENDORS
    return jsonify({"vendors": vendors})

@app.route("/api/scrape", methods=["POST"])
def scrape():
    try:
        data = request.get_json(force=True, silent=False)
        products = data.get("products", [])
        vendors = data.get("vendors") or (parse_vendors_from_prompt(PROMPT_FILE) or DEFAULT_VENDORS)
        headless = bool(data.get("headless", True))
        min_delay = int(data.get("min_delay", 2))
        max_delay = int(data.get("max_delay", 5))
        include_official = bool(data.get("include_official", False))

        scraper = PriceScraper(headless=headless, delay_range=(min_delay, max_delay))
        df = scraper.scrape_all_vendors(products, vendors, include_official_site=include_official)

        ordered_cols = [
            "Producto", "Marca", "Carrefour", "Cetrogar", "CheekSA", "Frávega", "Libertad",
            "Masonline", "Megatone", "Musimundo", "Naldo", "Vital", "Marca (Sitio oficial)", "Fecha de Consulta"
        ]
        for col in ordered_cols:
            if col not in df.columns:
                df[col] = "ND"
        df = df[ordered_cols]
        return jsonify({"success": True, "rows": df.to_dict(orient="records")})
    except Exception as ex:
        # Nunca devolver HTML aquí; siempre JSON para el frontend
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
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
        client = gspread.authorize(creds)
        headers = list(rows[0].keys()) if rows else [
            "Producto","Marca","Carrefour","Cetrogar","CheekSA","Frávega","Libertad",
            "Masonline","Megatone","Musimundo","Naldo","Vital","Marca (Sitio oficial)","Fecha de Consulta"
        ]
        values = [headers] + [[r.get(h, "") for h in headers] for r in rows]
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
