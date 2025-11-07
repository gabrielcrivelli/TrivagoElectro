# app.py (solo bloque de utilidades y /api/vendors actualizado)

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
VENDORS_FILE = BASE_DIR / "VENDEDORES.txt"  # nuevo: archivo plano en raíz

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
PROMPT_FILE = BASE_DIR / "prompt-2.txt"

def to_str(x): return "" if x is None else str(x).strip()  # [web:219]

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
            # completar URL desde defaults si falta
            if not url:
                url = DEFAULT_VENDORS.get(name, "")
            vendors[name] = url
    return vendors or None  # [attached_file:214]

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
    return out or None  # [attached_file:1]

@app.route("/api/vendors", methods=["GET"])
def get_vendors():
    # prioridad: VENDEDORES.txt -> prompt-2.txt -> defaults
    v_from_txt = parse_vendors_file(VENDORS_FILE)
    if v_from_txt:
        return jsonify({"vendors": v_from_txt})
    v_from_prompt = parse_vendors_from_prompt(PROMPT_FILE)
    if v_from_prompt:
        return jsonify({"vendors": v_from_prompt})
    return jsonify({"vendors": DEFAULT_VENDORS})
