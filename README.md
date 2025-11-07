# Price Scraper App (Argentina)

## Requisitos
- Python 3.10+ y pip
- Google Chrome/Chromium (para Selenium) y permisos de instalación del ChromeDriver vía webdriver-manager
- (Opcional) credentials.json para Google Sheets

## Instalación local
python -m venv venv
source venv/bin/activate # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py

abrir http://localhost:5000

## Cargar vendedores automáticamente
Si el archivo `VENDEDORES.txt` está en la raíz del proyecto, el backend cargará la lista sugerida (Carrefour, Cetrogar, CheekSA, Frávega, Libertad, Masonline, Megatone, Musimundo, Naldo, Vital) y podrás editar/añadir URLs desde la UI en “Vendedores” [archivo requerido en raíz].
