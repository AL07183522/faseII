"""Pruebas de la API de detección de fraude.

Ejecutar desde la raíz del repositorio:
    .venv/bin/pytest fuentes/backend/tests -v
"""

import pytest
from fastapi.testclient import TestClient

from app.main import COLUMNAS, DATOS, UMBRAL, app

cliente = TestClient(app)


def transaccion_del_dataset(clase):
    """Primera transacción del dataset con la clase indicada."""
    return DATOS[DATOS["es_fraude"] == clase].iloc[0][COLUMNAS].to_dict()


def test_salud():
    respuesta = cliente.get("/api/salud")
    assert respuesta.status_code == 200
    assert respuesta.json() == {"estado": "ok", "modelo_cargado": True}


def test_info_modelo():
    datos = cliente.get("/api/modelo").json()
    assert datos["algoritmo"] == "RandomForestClassifier"
    assert datos["hiperparametros"]["n_estimators"] == 150
    assert datos["hiperparametros"]["max_depth"] == 7
    assert len(datos["variables"]) == 12
    assert 0 < datos["umbral"] < 1


@pytest.mark.parametrize("tipo", ["legitima", "fraude"])
def test_ejemplo_devuelve_las_12_variables(tipo):
    respuesta = cliente.get("/api/ejemplo", params={"tipo": tipo})
    assert respuesta.status_code == 200
    assert set(respuesta.json()) == set(COLUMNAS)


def test_ejemplo_tipo_invalido():
    assert cliente.get("/api/ejemplo", params={"tipo": "otro"}).status_code == 422


def test_predecir_estructura_respuesta():
    respuesta = cliente.post("/api/predecir", json=transaccion_del_dataset(0))
    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert 0 <= datos["probabilidad_fraude"] <= 1
    assert datos["umbral"] == round(UMBRAL, 4)
    assert datos["es_fraude"] == (datos["probabilidad_fraude"] >= datos["umbral"])
    assert datos["nivel_riesgo"] in {"bajo", "medio", "alto"}


def test_fraude_recibe_mas_probabilidad_que_legitima_en_promedio():
    def probabilidad_media(clase):
        filas = DATOS[DATOS["es_fraude"] == clase].head(30)[COLUMNAS]
        return sum(
            cliente.post("/api/predecir", json=fila.to_dict()).json()["probabilidad_fraude"]
            for _, fila in filas.iterrows()
        ) / len(filas)

    assert probabilidad_media(1) > probabilidad_media(0)


def test_predecir_falta_variable():
    transaccion = transaccion_del_dataset(0)
    transaccion.pop("monto_transaccion")
    assert cliente.post("/api/predecir", json=transaccion).status_code == 422


def test_predecir_variable_extra():
    transaccion = transaccion_del_dataset(0) | {"campo_desconocido": 1}
    assert cliente.post("/api/predecir", json=transaccion).status_code == 422


def test_predecir_valor_no_numerico():
    transaccion = transaccion_del_dataset(0) | {"frecuencia_24h": "alto"}
    assert cliente.post("/api/predecir", json=transaccion).status_code == 422
