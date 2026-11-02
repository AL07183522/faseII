#!/usr/bin/env bash
# Prueba de integración extremo a extremo con los contenedores levantados
# (docker compose up -d). Todas las llamadas pasan por el frontend (nginx),
# que las reenvía al backend, igual que lo hace el navegador.
#
# Uso: bash fuentes/prueba_integracion.sh [URL_FRONTEND]
set -u

URL="${1:-http://localhost:8080}"
fallos=0

comprobar() {
  local nombre="$1" esperado="$2" obtenido="$3"
  if [[ "$obtenido" == *"$esperado"* ]]; then
    echo "OK    $nombre"
  else
    echo "FALLO $nombre (esperado: $esperado, obtenido: $obtenido)"
    fallos=$((fallos + 1))
  fi
}

codigo() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

comprobar "Frontend sirve index.html"          "200" "$(codigo "$URL/")"
comprobar "Frontend sirve app.js"              "200" "$(codigo "$URL/app.js")"
comprobar "Proxy /api/salud -> backend"        '"estado":"ok"' "$(curl -s "$URL/api/salud")"
comprobar "Proxy /api/modelo -> backend"       '"RandomForestClassifier"' "$(curl -s "$URL/api/modelo")"

legitima=$(curl -s "$URL/api/ejemplo?tipo=legitima")
fraude=$(curl -s "$URL/api/ejemplo?tipo=fraude")
comprobar "Ejemplo legítimo tiene 12 variables" "12" "$(grep -o '":' <<<"$legitima" | wc -l | tr -d ' ')"

pred_legitima=$(curl -s -X POST "$URL/api/predecir" -H 'Content-Type: application/json' -d "$legitima")
pred_fraude=$(curl -s -X POST "$URL/api/predecir" -H 'Content-Type: application/json' -d "$fraude")
comprobar "Predicción de ejemplo legítimo"     '"probabilidad_fraude"' "$pred_legitima"
comprobar "Predicción de ejemplo de fraude"    '"probabilidad_fraude"' "$pred_fraude"
echo "      legítima -> $pred_legitima"
echo "      fraude   -> $pred_fraude"

comprobar "JSON incompleto devuelve 422"       "422" "$(codigo -X POST "$URL/api/predecir" -H 'Content-Type: application/json' -d '{"monto_transaccion": 1}')"
comprobar "Ruta inexistente de la API da 404"  "404" "$(codigo "$URL/api/no-existe")"

echo
if [[ $fallos -eq 0 ]]; then
  echo "Todas las pruebas de integración pasaron."
else
  echo "$fallos prueba(s) fallaron."
  exit 1
fi
