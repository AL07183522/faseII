# Changelog

Todos los cambios relevantes de este proyecto se documentan en este archivo.

| | |
|---|---|
| **Actividad** | FASE II |
| **Alumno** | Manuel de Jesus Ramiro Velázquez Tapia |
| **Matrícula** | AL07183522 |
| **Docente** | Luis Ariel Vazquez Piña |

## 2. Contenedor Docker funcional

### Imagen construida correctamente
- Reconstrucción desde cero (`docker compose build --no-cache`) el 2026-11-01 sobre el commit `68c1b3f`,
  en 60 s, sin errores. Tras corregir la validación de valores extremos se reconstruyó el backend:

  | Imagen | ID | Tamaño | Base |
  |---|---|---|---|
  | `faseii-backend:1.0.0` | `bb22ae9ebde2` | 660 MB | `python:3.14-slim` (multi-etapa) |
  | `faseii-frontend:1.0.0` | `b4a4d379285a` | 49.7 MB | `nginx:1.27-alpine` |

- Durante la construcción del backend se entrena el modelo (`Modelo guardado en
  modelos/modelo_random_forest.joblib`) y se ejecutan las pruebas unitarias (`19 passed`). Si una
  prueba falla, la imagen no se genera.
- El backend corre como usuario sin privilegios (`api`, UID 1000) y solo incluye las dependencias de
  ejecución, el modelo entrenado, el dataset y el código de la API.

### Ejecución local comprobable
- Comandos:

  ```bash
  docker compose up -d --build
  docker compose ps
  bash fuentes/prueba_integracion.sh
  ```

- Resultado obtenido (Docker 24.0.6, Compose v5.6.1, macOS arm64):

  | Contenedor | Estado | Puertos |
  |---|---|---|
  | `fraude-backend` | Up (healthy) | `8000 → 8000` |
  | `fraude-frontend` | Up (healthy) | `8080 → 80` |

- Aplicación web en <http://localhost:8080>; API y Swagger en <http://localhost:8000/docs>.
- El frontend solo arranca cuando el *healthcheck* del backend (`/api/salud`) está en verde
  (`depends_on: condition: service_healthy`).
- Prueba de integración tras la reconstrucción: «Todas las pruebas de integración pasaron».
- Evidencia gráfica: `imagenes/app_frontend_prediccion.jpg` y `imagenes/api_swagger_docs.jpg`.

## 3. Documento de validación y pruebas

El detalle completo está en [DOCUMENTACION.md](DOCUMENTACION.md), sección 5.

### Pruebas funcionales realizadas

| Tipo | Herramienta | Casos | Resultado |
|---|---|---|---|
| Unitarias de la API | pytest (`fuentes/backend/tests/test_api.py`) | 19: salud, ficha del modelo, ejemplos, estructura de la predicción, fraude con más probabilidad que legítima, validaciones 422 y valores extremos | ✅ 19 passed (en local y en cada `docker build`) |
| Integración extremo a extremo | `fuentes/prueba_integracion.sh` (navegador → nginx → API → modelo) | 9 comprobaciones: estáticos, proxy `/api`, ejemplo con 12 variables, predicciones, 422 y 404 | ✅ 9/9 |
| Funcional en navegador | Interfaz en `localhost:8080` | Conexión con la API, formulario dinámico, carga de ejemplo y evaluación | ✅ Ejemplo de fraude: 89.5 %, riesgo alto → bloquear |
| Resiliencia | `docker stop` / `docker start` del backend | Caída y recuperación | ✅ La página sigue disponible, muestra «API no disponible» y se recupera sola |
| Rendimiento | 200 peticiones `POST /api/predecir` | Latencia y memoria | ✅ Media 28.7 ms, p95 31.0 ms; backend ~154 MiB |

### Casos extremos evaluados

Ejecutados contra `http://localhost:8080/api/predecir` (a través de nginx) el 2026-11-01.

**Entradas inválidas — se rechazan correctamente:**

| Caso | Respuesta |
|---|---|
| JSON vacío `{}` | 422, indica las 12 variables faltantes |
| Cuerpo vacío | 422 |
| JSON mal formado | 422 (`JSON decode error`) |
| Arreglo en lugar de objeto | 422 |
| `null` o lista en una variable | 422 (`Input should be a valid number`) |
| Texto no numérico (`"alto"`) | 422 |
| Variable faltante o variable extra | 422 |
| `Content-Type: text/plain` | 422 |
| Cuerpo de 1 MB con un campo desconocido | 422 |
| `GET` en `/api/predecir` | 405 |
| Ruta inexistente `/api/no-existe` | 404 |
| Backend detenido | 502 desde nginx; la interfaz muestra un mensaje claro |

**Valores numéricos extremos (válidos para el esquema):**

| Caso | Respuesta | Observación |
|---|---|---|
| Todas las variables en 0 (valor medio) | 200, prob. 0.2507, riesgo medio | Comportamiento esperado con umbral 0.14 |
| Todas en +10 desviaciones | 200, prob. 0.6252, riesgo alto | El Random Forest no extrapola: fuera del rango de entrenamiento usa la última división de cada árbol |
| Todas en −10 desviaciones | 200, prob. 0.6487, riesgo alto | Ídem |
| Todas en +100 (límite permitido) | 200, prob. 0.6252 | Igual que +10: confirma que el Random Forest no extrapola |
| Todas en +100.01 | 422 (`less_than_equal`) | Fuera del rango permitido [−100, 100] |
| Todas en ±1,000,000 | 422 | Fuera del rango permitido |
| Entero en lugar de decimal (`3`) | 200 | Conversión correcta |
| Número como texto (`"1.5"`) | 200 | Pydantic convierte el texto a número (modo flexible) |
| Booleano (`true`) | 200 | Pydantic lo convierte a `1.0` (modo flexible) |

**Defectos encontrados y corregidos:**

| Caso | Antes | Después |
|---|---|---|
| `Infinity` / `-Infinity` | **500** `Input X contains infinity…` | ✅ 422 `finite_number` |
| Valor muy grande (`1e308`) | **500** (desborda `float32` dentro de scikit-learn) | ✅ 422 `less_than_equal` |
| `NaN` | **200** con una predicción (prob. 0.1448) | ✅ 422 `finite_number` |

Corrección: cada variable admite solo números finitos entre −100 y 100 (en el dataset ninguna supera
|10.06|), y un manejador de errores convierte `NaN`/`Infinity` a texto para que la respuesta 422 sea
JSON válido. Se añadieron 9 pruebas unitarias (`test_predecir_rechaza_valores_extremos` y
`test_predecir_acepta_valores_en_el_limite`); la prueba de integración sigue pasando (9/9).

## [Sin publicar]

### Añadido
- Estructura inicial del proyecto: `datos/`, `fuentes/` e `imagenes/`.
- `CHANGELOG.md`.
- Dataset `datos/datos_fraude.csv` (2,500 transacciones sintéticas, 12 variables, 12.4% fraude).
- Modelo Random Forest en `fuentes/modelo_random_forest.py` con partición estratificada 70/30,
  evaluación por costo (FN = $1,500, FP = $25) y umbral óptimo elegido por validación cruzada.
- Métricas en `datos/metricas_random_forest.json` y gráficas en `imagenes/`.
- `requirements.txt` y `.gitignore`.
- API REST con FastAPI (`fuentes/backend`): `/api/salud`, `/api/modelo`, `/api/ejemplo` y `/api/predecir`,
  con 19 pruebas unitarias (pytest).
- Frontend web (`fuentes/frontend`) servido por nginx con proxy inverso `/api` hacia el backend.
- Contenerización: Dockerfile multi-etapa del backend (entrena el modelo y ejecuta las pruebas en el
  build), Dockerfile del frontend, `docker-compose.yml` con healthchecks y `.dockerignore`.
- Prueba de integración extremo a extremo `fuentes/prueba_integracion.sh`.
- `DOCUMENTACION.md`: arquitectura, construcción, ejecución local, pruebas y despliegue en la nube.
- `MANUAL_NUBE.MD`: manual de despliegue en AWS (ECR, ECS Fargate, ALB, Service Connect) con integración
  de Amazon Bedrock para explicar predicciones, requerimientos, estrategia y herramientas de documentación.

### Cambiado
- `modelo_random_forest.py` guarda el modelo entrenado y su umbral en `modelos/modelo_random_forest.joblib`.

### Corregido
- La API devolvía 500 con `Infinity` o valores enormes y aceptaba `NaN`; ahora responde 422. Las variables
  se limitan a números finitos en [−100, 100].
