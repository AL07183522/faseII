# Changelog

Todos los cambios relevantes de este proyecto se documentan en este archivo.

| | |
|---|---|
| **Actividad** | FASE II |
| **Alumno** | Manuel de Jesus Ramiro Velázquez Tapia |
| **Matrícula** | AL07183522 |
| **Docente** | Luis Ariel Vazquez Piña |

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
  con 10 pruebas unitarias (pytest).
- Frontend web (`fuentes/frontend`) servido por nginx con proxy inverso `/api` hacia el backend.
- Contenerización: Dockerfile multi-etapa del backend (entrena el modelo y ejecuta las pruebas en el
  build), Dockerfile del frontend, `docker-compose.yml` con healthchecks y `.dockerignore`.
- Prueba de integración extremo a extremo `fuentes/prueba_integracion.sh`.
- `DOCUMENTACION.md`: arquitectura, construcción, ejecución local, pruebas y despliegue en la nube.
- `MANUAL_NUBE.MD`: manual de despliegue en AWS (ECR, ECS Fargate, ALB, Service Connect) con integración
  de Amazon Bedrock para explicar predicciones, requerimientos, estrategia y herramientas de documentación.

### Cambiado
- `modelo_random_forest.py` guarda el modelo entrenado y su umbral en `modelos/modelo_random_forest.joblib`.
