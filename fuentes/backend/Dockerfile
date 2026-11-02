# Imagen del backend: entrena el modelo durante la construcción y lo sirve con FastAPI.
# Contexto de construcción: raíz del repositorio (ver docker-compose.yml).

# ---- Etapa 1: entrenamiento ----
FROM python:3.14-slim AS entrenamiento
WORKDIR /proyecto
COPY fuentes/backend/requirements.txt fuentes/backend/requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY datos/datos_fraude.csv datos/
COPY fuentes/modelo_random_forest.py fuentes/
RUN mkdir imagenes && python fuentes/modelo_random_forest.py
# Las pruebas de la API se ejecutan en la construcción: si fallan, la imagen no se crea.
COPY fuentes/backend fuentes/backend
RUN cd fuentes/backend && pytest -q -p no:cacheprovider

# ---- Etapa 2: ejecución ----
FROM python:3.14-slim
WORKDIR /app
RUN useradd --create-home --uid 1000 api
COPY fuentes/backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=entrenamiento /proyecto/modelos/modelo_random_forest.joblib modelos/
COPY datos/datos_fraude.csv datos/
COPY fuentes/backend/app app
ENV RUTA_MODELO=/app/modelos/modelo_random_forest.joblib \
    RUTA_DATOS=/app/datos/datos_fraude.csv \
    PYTHONUNBUFFERED=1
USER api
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/salud')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
