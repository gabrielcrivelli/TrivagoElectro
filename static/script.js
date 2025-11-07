const API_BASE = window.API_BASE || "";
const MAX_PER_BATCH = 5;

/* Tabs accesibles */
const tabs = document.querySelectorAll(".tabs button");
const sections = document.querySelectorAll(".tab");
const tablist = document.querySelector(".tabs");
if (tablist) tablist.setAttribute("role", "tablist");
tabs.forEach((btn, i) => {
  btn.setAttribute("role", "tab");
  btn.setAttribute("tabindex", btn.classList.contains("active") ? "0" : "-1");
  const panel = document.getElementById(btn.dataset.tab);
  if (panel) {
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("aria-labelledby", `tab-${i}`);
    btn.id = `tab-${i}`;
    panel.hidden = !btn.classList.contains("active");
  }
  btn.addEventListener("click", () => activateTab(btn));
  btn.addEventListener("keydown", (e) => {
    const idx = [...tabs].indexOf(btn);
    if (e.key === "ArrowRight") { e.preventDefault(); const next = tabs[(idx+1)%tabs.length]; next.focus(); activateTab(next); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); const prev = tabs[(idx-1+tabs.length)%tabs.length]; prev.focus(); activateTab(prev); }
    else if (e.key === "Home") { e.preventDefault(); tabs[0].focus(); activateTab(tabs[0]); }
    else if (e.key === "End") { e.preventDefault(); tabs[tabs.length-1].focus(); activateTab(tabs[tabs.length-1]); }
  });
});
function activateTab(btn) {
  tabs.forEach(b => {
    b.classList.remove("active"); b.setAttribute("tabindex","-1");
    const p = document.getElementById(b.dataset.tab);
    if (p) { p.classList.remove("active"); p.hidden = true; }
  });
  btn.classList.add("active"); btn.setAttribute("tabindex","0");
  const panel = document.getElementById(btn.dataset.tab);
  if (panel) { panel.classList.add("active"); panel.hidden = false; }
}

/* UI refs */
const productsBody = document.querySelector("#productsTable tbody");
const vendorsBody  = document.querySelector("#vendorsTable tbody");
const statusDiv    = document.getElementById("status");
const resultsBody  = document.querySelector("#resultsTable tbody");

/* CTA */
const startBtn = document.getElementById("start");
if (startBtn) {
  startBtn.textContent = "INICIAR BÚSQUEDA";
  startBtn.classList.add("btn-cta");
  startBtn.setAttribute("aria-label", "Iniciar búsqueda");
}

/* Botones básicos */
document.getElementById("addProduct").onclick = () => addProductRow();
const addVendorBtn = document.getElementById("addVendor");
if (addVendorBtn) addVendorBtn.onclick  = () => addVendorRow({ name: "", url: "" });

const loadSamplesBtn = document.getElementById("loadSamples");
if (loadSamplesBtn) {
  loadSamplesBtn.onclick = () => {
    const samples = [
      { producto: "Aire acondicionado SPLIT CD HITACHI frío / calor 3200w", marca: "Hitachi", modelo: "SPLIT 3200W", capacidad: "3200W", ean: "" },
      { producto: "Aire acondicionado split Hisense Frio / Calor 3400W AS12HR4SVRKG", marca: "Hisense", modelo: "AS12HR4SVRKG", capacidad: "3400W", ean: "" }
    ];
    productsBody.innerHTML = "";
    for (const p of samples) addProductRow(p);
  };
}

document.getElementById("start").onclick        = runSearch;
document.getElementById("clearResults").onclick = () => { resultsBody.innerHTML = ""; };
document.getElementById("toCSV").onclick        = exportCSV;
document.getElementById("copyTable").onclick    = copyTable;
document.getElementById("toSheets").onclick     = exportToSheets;

/* Filas dinámicas */
function addProductRow(p = {}) {
  const tr = document.createElement("tr");
  tr.innerHTML = `
    <td><input type="text" value="${(p.producto||"")}" placeholder="Nombre completo" /></td>
    <td><input type="text" value="${(p.marca||"")}" placeholder="Marca" /></td>
    <td><input type="text" value="${(p.modelo||"")}" placeholder="Modelo" /></td>
    <td><input type="text" value="${(p.capacidad||"")}" placeholder="Capacidad" /></td>
    <td><input type="text" value="${(p.ean||"")}" placeholder="EAN/Código" /></td>
    <td><button class="secondary">Eliminar</button></td>
  `;
  tr.querySelector("button").onclick = () => tr.remove();
  productsBody.appendChild(tr);
}
function addVendorRow(v = { name: "", url: "" }) {
  const tr = document.createElement("tr");
  tr.innerHTML = `
    <td><input type="text" value="${(v.name||"")}" placeholder="Nombre" /></td>
    <td><input type="text" value="${(v.url||"")}" placeholder="https://..." /></td>
    <td><button class="secondary">Eliminar</button></td>
  `;
  tr.querySelector("button").onclick = () => tr.remove();
  vendorsBody.appendChild(tr);
}

/* Precarga vendedores */
async function loadVendorsFromAPI() {
  const data = await safeJsonFetch(`${API_BASE}/api/vendors`);
  vendorsBody.innerHTML = "";
  const vendors = data.vendors || {};
  Object.keys(vendors).forEach(name => addVendorRow({ name, url: vendors[name] }));
}

/* Importar CSV/XLSX y pegado */
const fileInput = document.getElementById("fileInput");
const parseBtn  = document.getElementById("parseFile");
const openPaste = document.getElementById("openPaste");
const pasteBox  = document.getElementById("pasteBox");
const pasteArea = document.getElementById("pasteArea");
const importPasteBtn = document.getElementById("importPaste");
const closePasteBtn  = document.getElementById("closePaste");

if (parseBtn) parseBtn.onclick = () => {
  if (!fileInput.files || !fileInput.files[0]) { alert("Selecciona un archivo .csv o .xlsx"); return; }
  const f = fileInput.files[0];
  const ext = (f.name.split(".").pop() || "").toLowerCase();
  if (ext === "csv") readCSVFile(f);
  else if (ext === "xlsx") readXLSXFile(f);
  else alert("Formato no soportado. Usa .csv o .xlsx");
};
if (openPaste) openPaste.onclick = () => { pasteBox.style.display = "block"; };
if (closePasteBtn) closePasteBtn.onclick = () => { pasteBox.style.display = "none"; pasteArea.value = ""; };
if (importPasteBtn) importPasteBtn.onclick = () => {
  const text = pasteArea.value || "";
  if (!text.trim()) { alert("Nada para importar"); return; }
  const rows = parseClipboardTable(text);
  const objects = normalizeRows(rows);
  appendProducts(objects);
  pasteArea.value = "";
  pasteBox.style.display = "none";
};

function readCSVFile(file) {
  const reader = new FileReader();
  reader.onload = (e) => {
    const text = e.target.result || "";
    const rows = parseCSV(text);
    const objects = normalizeRows(rows);
    appendProducts(objects);
  };
  reader.readAsText(file, "utf-8");
}
function readXLSXFile(file) {
  if (!window.XLSX) { alert("Para XLSX, incluye SheetJS (xlsx.full.min.js) en index.html"); return; }
  const reader = new FileReader();
  reader.onload = (e) => {
    const data = new Uint8Array(e.target.result);
    const wb = XLSX.read(data, { type: "array" });
    const ws = wb.Sheets[wb.SheetNames[0]];
    const rows = XLSX.utils.sheet_to_json(ws, { header: 1, defval: "" });
    const objects = normalizeRows(rows);
    appendProducts(objects);
  };
  reader.readAsArrayBuffer(file);
}
function parseCSV(text) {
  const lines = text.replace(/\r/g, "").split("\n").filter(x => x.trim().length);
  return lines.map(line => {
    const out = []; let cur = "", inQ = false;
    for (let i=0; i<line.length; i++) {
      const ch = line[i];
      if (ch === '"') { if (inQ && line[i+1] === '"') { cur += '"'; i++; } else { inQ = !inQ; } }
      else if (!inQ && (ch === "," || ch === ";")) { out.push(cur); cur = ""; }
      else { cur += ch; }
    }
    out.push(cur);
    return out.map(c => c.trim());
  });
}
function parseClipboardTable(text) {
  const rows = text.replace(/\r/g, "").split("\n").filter(r => r.trim().length);
  return rows.map(r => r.split("\t").map(c => c.trim()));
}

/* Normalización */
const toS = v => (v == null ? "" : String(v).trim());
function normalizeRows(rows) {
  if (!rows || !rows.length) return [];
  const header = rows[0].map(h => (h||"").toString().toLowerCase());
  const hasHeader = ["producto","marca","modelo","capacidad","ean","ean/código","codigo","código"].some(k => header.includes(k));
  const start = hasHeader ? 1 : 0;

  const idx = (name) => {
    const pos = rows[0].findIndex(h => (h||"").toString().toLowerCase() === name.toLowerCase());
    return pos >= 0 ? pos : -1;
  };

  const iProd = hasHeader ? idx("producto")  : 0;
  const iMar  = hasHeader ? idx("marca")     : 1;
  const iMod  = hasHeader ? idx("modelo")    : 2;
  const iCap  = hasHeader ? idx("capacidad") : 3;
  const iEAN  = hasHeader ? (idx("ean")>=0?idx("ean"):idx("ean/código")>=0?idx("ean/código"):idx("codigo")>=0?idx("codigo"):idx("código")) : 4;

  const out = [];
  for (let r = start; r < rows.length; r++) {
    const row = rows[r] || [];
    const obj = {
      producto: cleanProduct(row[iProd]),
      marca: cleanBrand(row[iMar]),
      modelo: cleanModel(row[iMod]),
      capacidad: cleanCapacity(row[iCap]),
      ean: cleanEAN(row[iEAN]),
    };
    if (Object.values(obj).some(v => (v||"").length)) out.push(obj);
  }
  return out;
}
function cleanProduct(v){ return toS(v).replace(/\s+/g," "); }
function cleanBrand(v){ return toS(v).toUpperCase().replace(/\s+/g," "); }
function cleanModel(v){ return toS(v).toUpperCase().replace(/\s+/g," "); }
function cleanCapacity(v){
  let s = toS(v).toUpperCase().replace(/\s+/g,"");
  s = s.replace(/KBTU/gi,"BTU").replace(/BTHU/gi,"BTU");
  return s;
}
function cleanEAN(v){ return toS(v).replace(/\D/g,""); }
function appendProducts(arr){ if (!arr||!arr.length) return; for (const p of arr) addProductRow(p); }

/* Colección y ejecución */
function collectProducts() {
  const rows = [...productsBody.querySelectorAll("tr")];
  return rows.map(r => {
    const [producto, marca, modelo, capacidad, ean] = [...r.querySelectorAll("input")].map(i => (i.value||"").trim());
    return { producto, marca, modelo, capacidad, ean };
  }).filter(p => p.producto || p.modelo);
}
function collectVendors() {
  const rows = [...vendorsBody.querySelectorAll("tr")];
  const map = {};
  rows.forEach(r => {
    const [name, url] = [...r.querySelectorAll("input")].map(i => (i.value||"").trim());
    if (name) map[name] = url || "";
  });
  return map;
}
function setStatus(msg){ statusDiv.textContent = msg; }

async function runSearch() {
  const allProducts = collectProducts();
  const vendors  = collectVendors();
  if (!allProducts.length) { alert("Agrega al menos un producto"); return; }
  const products = allProducts.slice(0, MAX_PER_BATCH);
  if (allProducts.length > MAX_PER_BATCH) {
    alert(`Se procesarán ${MAX_PER_BATCH} ítems en este lote. Añade el resto en un nuevo lote.`);
  }

  setStatus("Ejecutando búsqueda...");
  const payload = {
    products,
    vendors: Object.keys(vendors).length ? vendors : undefined,
    headless: document.getElementById("headless").value === "true",
    min_delay: parseInt(document.getElementById("minDelay").value || "2", 10),
    max_delay: parseInt(document.getElementById("maxDelay").value || "5", 10),
    include_official: document.getElementById("official").value === "true"
  };

  try {
    const data = await safeJsonFetch(`${API_BASE}/api/scrape`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (!data.success) throw new Error(data.error || "La búsqueda falló");
    renderResults(data.rows || []);
    setStatus("Completado");
    document.querySelector('button[data-tab="results"]')?.click();
  } catch (e) {
    setStatus("Error durante la búsqueda");
    alert("Error: " + e.message);
  }
}

/* Fetch robusto */
async function safeJsonFetch(url, options = {}) {
  const res = await fetch(url, options);
  const ct = res.headers.get("content-type") || "";
  if (!res.ok) { const body = await res.text(); throw new Error(`HTTP ${res.status} : ${body.slice(0, 300)}`); }
  if (!ct.includes("application/json")) { const text = await res.text(); throw new Error(`Respuesta no-JSON desde ${url}: ${text.slice(0, 300)}`); }
  return res.json();
}

/* Render y export */
function renderResults(rows) {
  resultsBody.innerHTML = "";
  for (const r of rows) {
    const td = (k) => `<td>${(r[k] ?? "ND") || "ND"}</td>`;
    const tr = `
      <tr>
        ${td("Producto")}${td("Marca")}${td("Carrefour")}${td("Cetrogar")}${td("CheekSA")}
        ${td("Frávega")}${td("Libertad")}${td("Masonline")}${td("Megatone")}
        ${td("Musimundo")}${td("Naldo")}${td("Vital")}${td("Marca (Sitio oficial)")}
        ${td("Fecha de Consulta")}
      </tr>`;
    resultsBody.insertAdjacentHTML("beforeend", tr);
  }
}
function exportCSV() {
  const table = document.getElementById("resultsTable");
  const rows = [...table.querySelectorAll("tr")].map(tr => [...tr.children].map(td => `"${td.textContent.replaceAll('"','""')}"`).join(","));
  const blob = new Blob([rows.join("\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "comparacion_precios.csv"; a.click();
}
async function exportToSheets() {
  const name = document.getElementById("sheetName").value.trim() || "Comparación Precios Electrodomésticos";
  const rows = []; const table = document.getElementById("resultsTable");
  const headers = [...table.tHead.rows[0].children].map(th => th.textContent.trim());
  for (const tr of table.tBodies[0].rows) {
    const obj = {}; [...tr.children].forEach((td,i) => obj[headers[i]] = td.textContent); rows.push(obj);
  }
  await safeJsonFetch(`${API_BASE}/api/export/sheets`, { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify({ rows, sheet_name: name }) });
  alert("Exportado a Google Sheets");
}
function copyTable() {
  const sel = window.getSelection(); const range = document.createRange();
  range.selectNode(document.getElementById("resultsTable")); sel.removeAllRanges(); sel.addRange(range);
  document.execCommand("copy"); sel.removeAllRanges(); alert("Tabla copiada al portapapeles");
}

/* Init */
loadVendorsFromAPI();
