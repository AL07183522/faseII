// Frontend del detector de fraude. Todas las llamadas van a /api, que nginx
// redirige al contenedor del backend (mismo origen, sin CORS).
const API = "/api";

const campos = document.getElementById("campos");
const formulario = document.getElementById("formulario");
const resultado = document.getElementById("resultado");
const estadoApi = document.getElementById("estado-api");
const infoModelo = document.getElementById("info-modelo");

let variables = [];

async function llamarApi(ruta, opciones = {}) {
  const respuesta = await fetch(`${API}${ruta}`, {
    headers: { "Content-Type": "application/json" },
    ...opciones,
  });
  // Si el backend está caído, nginx responde 502 con HTML, no con JSON.
  const datos = await respuesta.json().catch(() => {
    throw new Error(`Error ${respuesta.status}: el servicio de la API no responde.`);
  });
  if (!respuesta.ok) {
    const detalle = Array.isArray(datos.detail)
      ? datos.detail.map((d) => `${d.loc.at(-1)}: ${d.msg}`).join("; ")
      : datos.detail;
    throw new Error(`Error ${respuesta.status}: ${detalle}`);
  }
  return datos;
}

function crearCampos() {
  campos.innerHTML = variables
    .map(
      (v) => `
      <label>
        <span>${v.replaceAll("_", " ")}</span>
        <input type="number" step="any" name="${v}" required>
      </label>`
    )
    .join("");
}

function mostrarModelo(modelo) {
  const p = modelo.hiperparametros;
  const m = modelo.metricas_prueba;
  const filas = {
    Algoritmo: modelo.algoritmo,
    Árboles: p.n_estimators,
    "Profundidad máx.": p.max_depth,
    "Umbral de decisión": modelo.umbral.toFixed(2),
    "ROC-AUC (prueba)": m.roc_auc,
    "PR-AUC (prueba)": m.pr_auc,
    "Recall (prueba)": m.umbral_optimo_costo.recall,
  };
  infoModelo.innerHTML = Object.entries(filas)
    .map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`)
    .join("");
}

function mostrarResultado(r) {
  const porcentaje = (r.probabilidad_fraude * 100).toFixed(1);
  resultado.className = `resultado riesgo-${r.nivel_riesgo}`;
  resultado.innerHTML = `
    <div class="veredicto">${r.es_fraude ? "⚠️ Posible fraude" : "✅ Transacción legítima"}</div>
    <div class="barra"><div style="width:${porcentaje}%"></div>
      <span class="marca" style="left:${r.umbral * 100}%" title="Umbral"></span></div>
    <p><strong>${porcentaje}%</strong> de probabilidad de fraude (umbral ${(r.umbral * 100).toFixed(0)}%)</p>
    <p>Riesgo <strong>${r.nivel_riesgo}</strong> → ${r.accion}</p>`;
}

function mostrarError(error) {
  resultado.className = "resultado error";
  resultado.textContent = error.message;
}

document.querySelectorAll("[data-ejemplo]").forEach((boton) =>
  boton.addEventListener("click", async () => {
    try {
      const ejemplo = await llamarApi(`/ejemplo?tipo=${boton.dataset.ejemplo}`);
      for (const [clave, valor] of Object.entries(ejemplo)) {
        formulario.elements[clave].value = valor.toFixed(4);
      }
      resultado.className = "resultado vacio";
      resultado.textContent = `Ejemplo ${boton.dataset.ejemplo === "fraude" ? "de fraude" : "legítimo"} cargado. Pulsa «Evaluar».`;
    } catch (error) {
      mostrarError(error);
    }
  })
);

formulario.addEventListener("submit", async (evento) => {
  evento.preventDefault();
  const transaccion = Object.fromEntries(variables.map((v) => [v, Number(formulario.elements[v].value)]));
  try {
    mostrarResultado(await llamarApi("/predecir", { method: "POST", body: JSON.stringify(transaccion) }));
  } catch (error) {
    mostrarError(error);
  }
});

async function iniciar() {
  try {
    await llamarApi("/salud");
    const modelo = await llamarApi("/modelo");
    variables = modelo.variables;
    crearCampos();
    mostrarModelo(modelo);
    estadoApi.textContent = "API conectada";
    estadoApi.classList.add("ok");
  } catch (error) {
    estadoApi.textContent = "API no disponible";
    estadoApi.classList.add("caida");
    mostrarError(error);
  }
}

iniciar();
