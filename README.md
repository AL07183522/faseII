# faseII

| | |
|---|---|
| **Actividad** | FASE II |
| **Alumno** | Manuel de Jesus Ramiro Velázquez Tapia |
| **Matrícula** | AL07183522 |
| **Docente** | Luis Ariel Vazquez Piña |

Detección de fraude transaccional en banca digital con aprendizaje automático.

El fraude es un problema desbalanceado (12.4% de fraudes en el dataset) y con costos asimétricos:
un fraude no detectado (FN) cuesta ~$1,500 USD y una falsa alarma (FP) ~$25 USD. Por eso los modelos
se evalúan con recall, precision, F2, PR-AUC y costo total, no con accuracy.

## Estructura

```
├── README.md
├── CHANGELOG.md        # Documentación técnica, validación y pruebas
├── MANUAL_NUBE.MD      # Manual de despliegue en AWS (ECS Fargate + Bedrock)
├── requirements.txt
├── docker-compose.yml
├── datos/              # Dataset y métricas generadas
├── fuentes/            # Modelo, backend (FastAPI), frontend (nginx) y pruebas
└── imagenes/           # Gráficas y capturas
```

## Ejecución con Docker

```bash
docker compose up -d --build
```

- Aplicación web: http://localhost:8080
- API y Swagger: http://localhost:8000/docs
- Pruebas de integración: `bash fuentes/prueba_integracion.sh`

Detalles de arquitectura y pruebas en [CHANGELOG.md](CHANGELOG.md). Despliegue en AWS paso a paso
(ECS Fargate + Amazon Bedrock) en [MANUAL_NUBE.MD](MANUAL_NUBE.MD).

## Entrenamiento en local

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python fuentes/modelo_random_forest.py
```
