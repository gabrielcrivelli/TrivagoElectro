/* Configuración de API:
   Si frontend y backend están en dominios distintos, define window.API_BASE en index.html:
   <script>window.API_BASE="https://tu-api.onrender.com";</script>
*/
const API_BASE = window.API_BASE || "";

/* ---------------- Tabs accesibles (sin Deploy) ---------------- */
const tabs = document.querySelectorAll(".tabs button");
const sections = document.querySelectorAll(".tab");
const tablist = document.querySelector(".tabs");
if (tablist) tablist.setAttribute("role", "tablist"); // [A11Y]

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
    if (e.key === "ArrowRight") {
      e.preventDefault();
      const next = tabs[(idx + 1) % tabs.length];
      next.focus(); activateTab(next);
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      const prev = tabs[(idx - 1 + tabs.length) % tabs.length];
      prev.focus(); activateTab(prev);
    } else if (e.key === "Home") {
      e.preventDefault(); tabs[0].focus(); activateTab(tabs[0]);
    } else if (e.key === "End") {
      e.preventDefault(); tabs[tabs.length - 1].focus(); activateTab(tabs[tabs.length - 1]);
    }
  });
});

function activateTab(btn) {
  tabs.forEach(b => {
    b.classList.remove("active");
    b.setAttribute("tabindex", "-1");
    const p = document.getElementById(b.dataset.tab);
    if (p) { p.classList.remove("active"); p.hidden = true; }
  });
  btn.classList.add("active");
  btn.setAttribute("tabindex", "0");
  const panel = document.getElementById(btn.dataset.tab);
  if (panel) { panel.classList.add("active"); panel.hidden = false; }
}

/* ---------------- Elementos de la UI ---------------- */
const productsBody = document.querySelector("#productsTable tbody");
const vendorsBody  = document.querySelector("#vendorsTable tbody");
const statusDiv    = document.getElementById("status");
const resultsBody  = document.querySelector("#resultsTable tbody");

/* CTA: INICIAR BÚSQUEDA */
const startBtn = document.getElementById("start");
if (startBtn) {
  startBtn.textContent = "INICIAR BÚSQUEDA";
  startBtn.classList.add("btn-cta");
  startBtn.setAttribute("aria-label", "Iniciar búsqueda");
}

document.getElementById("addProduct").onclick = () => addProductRow();
const addVendorBtn = document.getElementById("addVendor");
if (addVendorBtn) addVendorBtn.onclick  = () => addVendorRow({ name: "", url: "" });

/* Botón (opcional) de muestras */
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

/* ---------------- Filas dinámicas ---------------- */
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

/* ---------------- Precarga de vendedores ---------------- */
async function loadVendorsFromAPI() {
  const data = await safeJsonFetch(`${API_BASE}/api/vendors`);
  vendorsBody.innerHTML = "";
  const vendors = data.vendors || {};
  Object.keys(vendors).forEach(name => addVendorRow({ name, url: vendors[name] }));
}

/* ---------------- Utilidades de recolección ---------------- */
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

function setStatus(msg) { statusDiv.textContent = msg; }

/* ---------------- Ejecución de búsqueda ---------------- */
async function runSearch() {
  const products = collectProducts();
  const vendors  = collectVendors();
  if (!products.length) { alert("Agrega al menos un producto"); return; }
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

/* ---------------- Fetch robusto ---------------- */
async function safeJsonFetch(url, options = {}) {
  const res = await fetch(url, options);
  const ct = res.headers.get("content-type") || "";
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`HTTP ${res.status} : ${body.slice(0, 300)}`);
  }
  if (!ct.includes("application/json")) {
    const text = await res.text();
    throw new Error(`Respuesta no-JSON desde ${url}: ${text.slice(0, 300)}`);
  }
  return res.json();
}

/* ---------------- Render de resultados ---------------- */
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

/* ---------------- Exportar ---------------- */
function exportCSV() {
  const table = document.getElementById("resultsTable");
  const rows = [...table.querySelectorAll("tr")].map(tr => [...tr.children].map(td => {
    const text = td.textContent.replaceAll('"', '""');
    return `"${text}"`;
  }).join(","));
  const blob = new Blob([rows.join("\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "comparacion_precios.csv";
  a.click();
}

async function exportToSheets() {
  const name = document.getElementById("sheetName").value.trim() || "Comparación Precios Electrodomésticos";
  const rows = [];
  const table = document.getElementById("resultsTable");
  const headers = [...table.tHead.rows[0].children].map(th => th.textContent.trim());
  for (const tr of table.tBodies[0].rows) {
    const obj = {};
    [...tr.children].forEach((td, i) => obj[headers[i]] = td.textContent);
    rows.push(obj);
  }
  await safeJsonFetch(`${API_BASE}/api/export/sheets`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rows, sheet_name: name })
  });
  alert("Exportado a Google Sheets");
}

function copyTable() {
  const sel = window.getSelection();
  const range = document.createRange();
  range.selectNode(document.getElementById("resultsTable"));
  sel.removeAllRanges(); sel.addRange(range);
  document.execCommand("copy");
  sel.removeAllRanges();
  alert("Tabla copiada al portapapeles");
}

/* ---------------- Inicialización ---------------- */
loadVendorsFromAPI(); // Precarga vendedores desde VENDEDORES.txt/prompt/defaults [MDN Fetch]
