/* static/script.js */
const API_BASE = window.API_BASE || "";
const MAX_PER_BATCH = 5;

/* Tabs accesibles (WAI-ARIA) */
const tabs = document.querySelectorAll(".tabs button");
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
    if (e.key === "ArrowRight") { e.preventDefault(); const n = tabs[(idx+1)%tabs.length]; n.focus(); activateTab(n); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); const p = tabs[(idx-1+tabs.length)%tabs.length]; p.focus(); activateTab(p); }
    else if (e.key === "Home") { e.preventDefault(); tabs[0].focus(); activateTab(tabs[0]); }
    else if (e.key === "End") { e.preventDefault(); tabs[tabs.length-1].focus(); activateTab(tabs[tabs.length-1]); }
  });
});
function activateTab(btn){
  tabs.forEach(b=>{
    b.classList.remove("active"); b.setAttribute("tabindex","-1");
    const p = document.getElementById(b.dataset.tab);
    if (p){ p.classList.remove("active"); p.hidden = true; }
  });
  btn.classList.add("active"); btn.setAttribute("tabindex","0");
  const panel = document.getElementById(btn.dataset.tab);
  if (panel){ panel.classList.add("active"); panel.hidden = false; }
}

/* Refs UI */
const productsBody = document.querySelector("#productsTable tbody");
const vendorsBody  = document.querySelector("#vendorsTable tbody");
const statusDiv    = document.getElementById("status");
const resultsTable = document.getElementById("resultsTable");
const resultsBody  = resultsTable.querySelector("tbody");
const runLog       = document.getElementById("runLog");

/* Mapear índice de columna por encabezado */
function headerIndexMap(){
  const map = {};
  const ths = resultsTable.tHead.rows[0].cells;
  for (let i=0;i<ths.length;i++){
    const name = ths[i].textContent.trim();
    map[name] = i;
  }
  return map;
}

/* Botones y acciones */
document.getElementById("addProduct").onclick = () => addProductRow();
const addVendorBtn = document.getElementById("addVendor");
if (addVendorBtn) addVendorBtn.onclick  = () => addVendorRow({ name: "", url: "" });
document.getElementById("start").onclick        = runSearch;
document.getElementById("stop").onclick         = stopSearch;
document.getElementById("clearResults").onclick = () => { resultsBody.innerHTML = ""; resultsStore = []; runLog.textContent = ""; };
document.getElementById("toCSV").onclick        = exportCSV;
document.getElementById("copyTable").onclick    = copyTable;
document.getElementById("toSheets").onclick     = exportToSheets;

/* Helpers UI */
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

/* Importación CSV/XLSX y pegado */
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
      producto: toS(row[iProd]),
      marca: toS(row[iMar]).toUpperCase(),
      modelo: toS(row[iMod]).toUpperCase(),
      capacidad: toS(row[iCap]).toUpperCase().replace(/\s+/g,""),
      ean: toS(row[iEAN]).replace(/\D/g,""),
    };
    if (Object.values(obj).some(v => (v||"").length)) out.push(obj);
  }
  return out;
}
function appendProducts(arr){ if (!arr||!arr.length) return; for (const p of arr) addProductRow(p); }

/* Estado, saludo y ejecución */
let resultsStore = [];
let abortRun = false;
let currentRunId = null;

function keyOf(r){ return [r["Producto"]||"", r["Marca"]||""].join("||"); }

/* Merge por patch que no pisa ND/vacío */
function mergeRows(incoming){
  const map = new Map(resultsStore.map(r => [keyOf(r), r]));
  const isBase = k => k === "Producto" || k === "Marca" || k === "Fecha de Consulta" || k === "Marca (Sitio oficial)";
  const hasValue = v => {
    if (v === null || v === undefined) return false;
    const s = String(v).trim();
    return s !== "" && s.toUpperCase() !== "ND";
  };

  for (const row of incoming){
    const k = keyOf(row);
    if (!map.has(k)) map.set(k, { ...row });
    else {
      const dest = map.get(k);
      for (const [kk, vv] of Object.entries(row)){
        if (isBase(kk)) { dest[kk] = vv; continue; }
        if (hasValue(vv)) dest[kk] = vv;
      }
    }
  }

  resultsStore = [...map.values()];
}

/* Escritura inmediata: crear fila si no existe y actualizar solo la celda del vendedor */
function ensureRowAndSetCell(rowObj, vendorName){
  const headMap = headerIndexMap();
  const idx = headMap[vendorName];
  if (idx == null) return;

  const k = keyOf(rowObj);
  const esc = (v) => (window.CSS && window.CSS.escape ? window.CSS.escape(v) : v);
  let tr = resultsBody.querySelector(`tr[data-key="${esc(k)}"]`);

  if (!tr){
    tr = document.createElement("tr");
    tr.dataset.key = k;

    // construir celdas base + todas las columnas visibles como ND por defecto
    const headers = [...resultsTable.tHead.rows[0].cells].map(x => x.textContent.trim());
    for (const h of headers){
      const td = document.createElement("td");
      if (h === "Producto") td.textContent = rowObj["Producto"] || "";
      else if (h === "Marca") td.textContent = rowObj["Marca"] || "";
      else if (h === "Fecha de Consulta") td.textContent = rowObj["Fecha de Consulta"] || "";
      else if (h === "Marca (Sitio oficial)") td.textContent = rowObj["Marca (Sitio oficial)"] || "ND";
      else td.textContent = "ND";
      tr.appendChild(td);
    }
    resultsBody.appendChild(tr);
  }

  // preferir número plano si llega la columna "(num)"
  const numKey = `${vendorName} (num)`;
  const value = rowObj[numKey] && String(rowObj[numKey]).trim() ? rowObj[numKey] : (rowObj[vendorName] ?? "ND");
  tr.children[idx].textContent = value && String(value).trim() !== "" ? String(value) : "ND";
}

/* Log y helpers */
function logLine(text, cls=""){
  const ts = new Date().toLocaleTimeString();
  const div = document.createElement("div");
  div.className = cls; div.textContent = `[${ts}] ${text}`;
  runLog.appendChild(div);
  runLog.scrollTop = runLog.scrollHeight;
}
function setStatus(msg){ statusDiv.textContent = msg; }
function timeGreeting(nombre = "Alberto"){
  const h = new Date().getHours();
  if (h >= 6 && h < 12) return `Buen día ${nombre}`;
  if (h >= 12 && h < 20) return `Buenas tardes ${nombre}`;
  return `Buenas noches ${nombre}`;
}
function newRunId(){ return Math.random().toString(36).slice(2) + Date.now().toString(36); }

async function stopSearch(){
  abortRun = true;
  logLine("Solicitud de cancelación enviada…", "warn");
  if (currentRunId){
    try{
      await safeJsonFetch(`${API_BASE}/api/cancel`, {
        method:"POST", headers:{ "Content-Type":"application/json" },
        body: JSON.stringify({ run_id: currentRunId })
      });
      logLine("Cancelación confirmada por el servidor.", "ok");
    }catch(e){
      logLine(`Error al cancelar: ${e.message}`, "err");
    }
  }
}

/* Ejecución incremental por vendedor con modo 'escritura inmediata' */
async function runSearch(){
  const allProducts = collectProducts();
  const allVendors  = collectVendors();
  if (!allProducts.length){ alert("Agrega al menos un producto"); return; }

  const products = allProducts.slice(0, MAX_PER_BATCH);
  if (allProducts.length > MAX_PER_BATCH){
    alert(`Se procesarán ${MAX_PER_BATCH} ítems en este lote. Añade el resto en un nuevo lote.`);
  }

  runLog.textContent = ""; resultsStore = []; resultsBody.innerHTML = "";
  setStatus("Ejecutando búsqueda...");
  abortRun = false;
  currentRunId = newRunId();

  logLine(timeGreeting("Alberto"), "ok");
  logLine(`Lote de ${products.length} producto(s), ${Object.keys(allVendors).length} vendedor(es). run_id=${currentRunId}`, "warn");

  const immediate = document.getElementById("immediate") ? document.getElementById("immediate").value === "true" : true;

  for (const [name, url] of Object.entries(allVendors)){
    if (abortRun){ logLine("Ejecución detenida por el usuario.", "err"); break; }
    if (!name) continue;
    logLine(`Iniciando ${name} …`, "warn");
    try{
      const payload = {
        run_id: currentRunId,
        products,
        vendor: { name, url },
        headless: document.getElementById("headless").value === "true",
        min_delay: parseInt(document.getElementById("minDelay").value || "2", 10),
        max_delay: parseInt(document.getElementById("maxDelay").value || "5", 10),
        include_official: document.getElementById("official").value === "true"
      };
      const data = await safeJsonFetch(`${API_BASE}/api/scrape_vendor`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      if (!data.success) throw new Error(data.error || `Falló ${name}`);

      // Escritura inmediata: actualizar solo la celda del vendedor
      if (immediate){
        (data.rows || []).forEach(r => ensureRowAndSetCell(r, name));
      }

      // Merge en memoria (patch) y repintado si immediate == false
      mergeRows(data.rows || []);
      if (!immediate){
        const tbody = resultsBody; tbody.innerHTML = "";
        for (const r of resultsStore){
          const td = (k) => `<td>${(r[k] ?? "ND") || "ND"}</td>`;
          const tr = `
            <tr data-key="${keyOf(r)}">
              ${td("Producto")}${td("Marca")}${td("Carrefour")}${td("Cetrogar")}${td("CheekSA")}
              ${td("Frávega")}${td("Libertad")}${td("Masonline")}${td("Megatone")}
              ${td("Musimundo")}${td("Naldo")}${td("Vital")}${td("Marca (Sitio oficial)")}
              ${td("Fecha de Consulta")}
            </tr>`;
          tbody.insertAdjacentHTML("beforeend", tr);
        }
      }

      (data.log || []).forEach(line => logLine(line));
      logLine(`OK ${name}`, "ok");
    }catch(e){
      logLine(`ERROR ${name}: ${e.message}`, "err");
    }
  }

  setStatus(abortRun ? "Cancelado" : "Completado");
  logLine(abortRun ? "Lote cancelado." : "Lote finalizado.", abortRun ? "err" : "ok");
}

/* Fetch robusto */
async function safeJsonFetch(url, options = {}){
  const res = await fetch(url, options);
  const ct = res.headers.get("content-type") || "";
  if (!res.ok){
    const body = await res.text();
    throw new Error(`HTTP ${res.status} : ${body.slice(0,300)}`);
  }
  if (!ct.includes("application/json")){
    const text = await res.text();
    throw new Error(`Respuesta no-JSON desde ${url}: ${text.slice(0,300)}`);
  }
  return res.json();
}

/* Exportar desde resultsStore (incluye columnas (num)) */
function exportCSV(){
  if (!resultsStore.length){ alert("Sin datos"); return; }
  const headers = Object.keys(resultsStore[0]);
  const lines = [];
  lines.push(headers.map(h => `"${h.replaceAll('"','""')}"`).join(","));
  for (const r of resultsStore){
    const row = headers.map(h => `"${String(r[h] ?? "").replaceAll('"','""')}"`).join(",");
    lines.push(row);
  }
  const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "comparacion_precios.csv"; a.click();
}
async function exportToSheets(){
  if (!resultsStore.length){ alert("Sin datos"); return; }
  await safeJsonFetch(`${API_BASE}/api/export/sheets`, { 
    method:"POST", headers:{ "Content-Type":"application/json" }, 
    body: JSON.stringify({ rows: resultsStore, sheet_name: (document.getElementById("sheetName")?.value || "Comparación Precios Electrodomésticos") })
  });
  alert("Exportado a Google Sheets");
}
function copyTable(){
  const sel = window.getSelection(); const range = document.createRange();
  range.selectNode(document.getElementById("resultsTable")); sel.removeAllRanges(); sel.addRange(range);
  document.execCommand("copy"); sel.removeAllRanges(); alert("Tabla copiada al portapapeles");
}

/* Colectores */
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

/* Inicio */
loadVendorsFromAPI();
