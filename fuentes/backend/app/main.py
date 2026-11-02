"""API REST de detección de fraude transaccional.

Expone el modelo Random Forest entrenado por fuentes/modelo_random_forest.py.

Endpoints:
    GET  /api/salud     Estado del servicio y del modelo.
    GET  /api/modelo    Variables, umbral de decisión y métricas del modelo.
    GET  /api/ejemplo   Transacción real del dataset (legítima o fraude).
    POST /api/predecir  Probabilidad de fraude y decisión para una transacción.
"""

import math
import os
from pathlib import Path
from typing import Annotated, Literal

import joblib
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

# En el contenedor las rutas llegan por variables de entorno; en local se usan
# las del repositorio (fuentes/backend/app/main.py -> raíz).
RAIZ = Path(__file__).resolve().parent.parent.parent.parent
RUTA_MODELO = Path(os.getenv("RUTA_MODELO") or RAIZ / "modelos" / "modelo_random_forest.joblib")
RUTA_DATOS = Path(os.getenv("RUTA_DATOS") or RAIZ / "datos" / "datos_fraude.csv")

COSTO_FN = 1500
COSTO_FP = 25

artefacto = joblib.load(RUTA_MODELO)
MODELO = artefacto["modelo"]
COLUMNAS = artefacto["columnas"]
UMBRAL = float(os.getenv("UMBRAL_DECISION", artefacto["umbral"]))
DATOS = pd.read_csv(RUTA_DATOS)

app = FastAPI(
    title="API de detección de fraude",
    description="Random Forest para clasificar transacciones bancarias como legítimas o fraude.",
    version="1.0.0",
)


def _json_seguro(valor):
    """Convierte NaN/Infinity a texto: el JSON estándar no los admite."""
    if isinstance(valor, float) and not math.isfinite(valor):
        return str(valor)
    if isinstance(valor, dict):
        return {k: _json_seguro(v) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_json_seguro(v) for v in valor]
    return valor


@app.exception_handler(RequestValidationError)
async def error_de_validacion(request: Request, error: RequestValidationError):
    # El detalle de error repite la entrada recibida; si traía NaN o Infinity,
    # la respuesta 422 por defecto no se podría serializar y terminaría en 500.
    return JSONResponse(status_code=422, content={"detail": _json_seguro(jsonable_encoder(error.errors()))})


# En el dataset ninguna variable supera |10.06|. Se acepta hasta 100 para no
# rechazar atípicos plausibles, y se rechazan NaN, infinitos y valores enormes
# (que además desbordan el float32 que usa scikit-learn internamente).
LIMITE_VALOR = 100.0
Valor = Annotated[float, Field(ge=-LIMITE_VALOR, le=LIMITE_VALOR, allow_inf_nan=False)]


class Transaccion(BaseModel):
    """Variables de la transacción, en la misma escala que el dataset."""

    model_config = ConfigDict(extra="forbid")

    monto_transaccion: Valor
    frecuencia_24h: Valor
    desvio_monto_habitual: Valor
    distancia_geografica_ip: Valor
    score_dispositivo_nuevo: Valor
    intentos_fallidos_previos: Valor
    antiguedad_cuenta_meses: Valor
    hora_transaccion_riesgo: Valor
    tipo_comercio_riesgo: Valor
    uso_vpn_proxy: Valor
    transaccion_internacional: Valor
    ratio_limite_credito: Valor


class Prediccion(BaseModel):
    probabilidad_fraude: float = Field(description="Probabilidad estimada de fraude (0 a 1).")
    umbral: float = Field(description="Umbral de decisión aplicado.")
    es_fraude: bool
    nivel_riesgo: Literal["bajo", "medio", "alto"]
    accion: str


def nivel_de_riesgo(probabilidad: float) -> str:
    if probabilidad < UMBRAL:
        return "bajo"
    if probabilidad < 0.5:
        return "medio"
    return "alto"


ACCIONES = {
    "bajo": "Aprobar transacción",
    "medio": "Retener y solicitar autenticación reforzada",
    "alto": "Bloquear y escalar a mesa de fraude",
}


@app.get("/api/salud")
def salud():
    return {"estado": "ok", "modelo_cargado": MODELO is not None}


@app.get("/api/modelo")
def info_modelo():
    return {
        "algoritmo": type(MODELO).__name__,
        "hiperparametros": {
            k: MODELO.get_params()[k]
            for k in ("n_estimators", "max_depth", "min_samples_split", "min_samples_leaf", "class_weight")
        },
        "variables": COLUMNAS,
        "umbral": UMBRAL,
        "costos_usd": {"falso_negativo": COSTO_FN, "falso_positivo": COSTO_FP},
        "metricas_prueba": artefacto["metricas"],
    }


@app.get("/api/ejemplo", response_model=Transaccion)
def ejemplo(tipo: Literal["legitima", "fraude"] = Query("legitima")):
    clase = 1 if tipo == "fraude" else 0
    fila = DATOS[DATOS["es_fraude"] == clase].sample(1).iloc[0]
    return fila[COLUMNAS].to_dict()


@app.post("/api/predecir", response_model=Prediccion)
def predecir(transaccion: Transaccion):
    entrada = pd.DataFrame([transaccion.model_dump()])[COLUMNAS]
    try:
        probabilidad = float(MODELO.predict_proba(entrada)[0, 1])
    except Exception as error:  # pragma: no cover - fallo inesperado del modelo
        raise HTTPException(status_code=500, detail=f"Error al predecir: {error}")
    nivel = nivel_de_riesgo(probabilidad)
    return Prediccion(
        probabilidad_fraude=round(probabilidad, 4),
        umbral=round(UMBRAL, 4),
        es_fraude=probabilidad >= UMBRAL,
        nivel_riesgo=nivel,
        accion=ACCIONES[nivel],
    )
