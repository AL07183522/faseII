# Manual de despliegue en la nube — AWS con Amazon Bedrock

Este manual explica cómo desplegar en **Amazon Web Services (AWS)** el detector de fraude
(frontend nginx + API FastAPI con el modelo Random Forest), y cómo integrar **Amazon Bedrock**
para añadir explicaciones en lenguaje natural de cada predicción.

> Complementa a [CHANGELOG.md](CHANGELOG.md), que describe la arquitectura, la construcción de
> los contenedores y las pruebas en local.

---

## 0. Papel de Amazon Bedrock en este despliegue

**Amazon Bedrock no aloja modelos propios de scikit-learn.** Es un servicio administrado para usar
modelos fundacionales (Claude de Anthropic, Llama, Mistral, Titan, etc.) mediante API, y su
*Custom Model Import* solo admite ciertas arquitecturas de modelos de lenguaje de pesos abiertos. Un
`RandomForestClassifier` serializado con `joblib` no puede ejecutarse dentro de Bedrock.

Por eso la solución en AWS se divide en dos capas:

| Capa | Servicio AWS | Qué hace |
|---|---|---|
| Aplicación y modelo de fraude | **Amazon ECS sobre AWS Fargate** (contenedores), **Amazon ECR** (imágenes), **Application Load Balancer** | Ejecuta los dos contenedores que ya construimos y validamos en local, sin cambios. |
| IA generativa | **Amazon Bedrock** (Claude Opus 5.5) | Recibe la transacción y la probabilidad calculada por el Random Forest y redacta una explicación para el analista de fraude. |

Así el Random Forest sigue tomando la decisión (rápida, ~29 ms, auditable y sin costo por petición) y
Bedrock aporta el valor que un modelo tabular no da: explicar el resultado en lenguaje natural.

---

## 1. Arquitectura objetivo

```
                         Internet
                            │ HTTP/HTTPS
                            ▼
                ┌────────────────────────┐
                │ Application Load       │  SG: alb-sg (80/443 desde 0.0.0.0/0)
                │ Balancer (fraude-alb)  │
                └───────────┬────────────┘
                            │ :80  (health check  GET /)
      ┌─────────────────────┼─────────────────────────────────────────────────┐
      │ ECS cluster fraude-cluster (Fargate, ARM64/Graviton)                  │
      │                     ▼                                                 │
      │   ┌───────────────────────────┐   Service Connect    ┌──────────────────────────┐
      │   │ Servicio fraude-frontend  │   http://backend:8000│ Servicio fraude-backend  │
      │   │ nginx  (SG: frontend-sg)  │─────────────────────▶│ FastAPI (SG: backend-sg) │
      │   └───────────────────────────┘   namespace          │ Random Forest .joblib    │
      │                                   fraude.local       └────────────┬─────────────┘
      └──────────────────────────────────────────────────────────────────┼────────────┘
                                                                         │ IAM task role
                     ┌──────────────┐     ┌──────────────┐               ▼
                     │ Amazon ECR   │     │ CloudWatch   │      ┌──────────────────┐
                     │ (imágenes)   │     │ Logs         │      │ Amazon Bedrock   │
                     └──────────────┘     └──────────────┘      │ Claude Opus 5.5  │
                                                                └──────────────────┘
```

Puntos clave:

- **Solo el balanceador es público.** El backend no tiene puerto abierto a Internet: únicamente acepta
  tráfico del grupo de seguridad del frontend.
- **ECS Service Connect** registra el backend con el nombre `backend` en el *namespace*
  `fraude.local`. Por eso el `nginx.conf` del frontend (`proxy_pass http://backend:8000/api/`) funciona
  en AWS **exactamente igual que en Docker Compose**, sin modificar las imágenes.
- El backend accede a Bedrock con un **rol de IAM de la tarea**: no se guardan llaves de AWS en el
  contenedor.

---

## 2. Requerimientos técnicos

### 2.1 Cuenta y permisos

| Requisito | Detalle |
|---|---|
| Cuenta de AWS | Con facturación activa. Se recomienda un presupuesto con alerta en **AWS Budgets** antes de empezar. |
| Identidad para desplegar | Usuario de IAM Identity Center (SSO) o usuario IAM con permisos sobre ECR, ECS, EC2 (VPC/SG), Elastic Load Balancing, IAM (crear roles y `iam:PassRole`), CloudWatch Logs, Cloud Map y Bedrock. Para un entorno académico basta `AdministratorAccess`; en producción, permisos mínimos. |
| Región | `us-east-1` (Virginia) en este manual. Debe ser una región donde **Claude Opus 5.5 esté disponible en Bedrock**; se comprueba en la consola de Bedrock → *Model catalog*. |
| Acceso al modelo en Bedrock | Habilitar el acceso al modelo de Anthropic en *Bedrock → Model access* (paso 3). |

### 2.2 Herramientas en el equipo local

| Herramienta | Versión probada / mínima | Estado en este equipo |
|---|---|---|
| Docker Desktop + Buildx | Docker 24.0.6, Compose v5.6.1 | ✅ Instalado |
| Git | cualquiera reciente | ✅ Instalado |
| GitHub CLI (`gh`) | 2.103.3 | ✅ Instalado y autenticado |
| **AWS CLI v2** | 2.x | ❌ **Falta instalar** (`brew install awscli`) |
| `jq` (opcional) | 1.6+ | Para leer salidas JSON de la CLI |

### 2.3 Recursos de cómputo

Dimensionados con las mediciones locales (backend ~154 MiB de RAM, frontend ~8 MiB):

| Servicio | vCPU | Memoria | Tareas | Arquitectura |
|---|---|---|---|---|
| `fraude-backend` | 0.5 | 1 GB | 1 mínimo, 4 máximo (autoescalado) | ARM64 (Graviton) |
| `fraude-frontend` | 0.25 | 0.5 GB | 1 mínimo, 2 máximo | ARM64 (Graviton) |

> Se usa **ARM64** porque las imágenes se construyen en un Mac con Apple Silicon (arm64) y Fargate
> Graviton es más económico que x86. Si se prefiere x86, construir con `--platform linux/amd64` y
> cambiar `cpuArchitecture` a `X86_64` en las definiciones de tarea.

### 2.4 Red

- VPC con al menos **dos subredes en zonas de disponibilidad distintas** (requisito del ALB).
  En este manual se usa la **VPC por defecto** (subredes públicas) para simplificar.
- Tres grupos de seguridad encadenados: `alb-sg` → `frontend-sg` → `backend-sg`.
- En producción: tareas en subredes privadas con NAT Gateway o *VPC endpoints* (ECR, Logs, Bedrock).

### 2.5 Costos

Los servicios usados se cobran por uso (Fargate por vCPU/GB-hora, ALB por hora y LCU, ECR por GB
almacenado, CloudWatch por GB de logs, Bedrock por tokens de entrada y salida). Estimar con la
[AWS Pricing Calculator](https://calculator.aws/) y **eliminar los recursos al terminar** (paso 15).

---

## 3. Estrategia de despliegue

### 3.1 Opciones evaluadas

| Estrategia | Servicio AWS | Ventajas | Desventajas | Decisión |
|---|---|---|---|---|
| **Contenedores orquestados (CaaS)** | **ECS + Fargate** | Usa las mismas imágenes validadas en local; sin servidores que administrar; red privada entre servicios; autoescalado; integración nativa con IAM, ALB y CloudWatch. | Más piezas que configurar que un PaaS. | ✅ **Elegida** |
| PaaS de contenedores | AWS App Runner | El más simple: una imagen → una URL HTTPS. | Comunicación privada frontend↔backend más limitada; menos control de red. | Alternativa para una demo rápida |
| PaaS tradicional | Elastic Beanstalk (plataforma Docker) | Gestiona instancias, balanceo y despliegues. | Administra instancias EC2; menos portable. | No |
| Serverless | AWS Lambda con imagen de contenedor + API Gateway | Pago por invocación, escala a cero. | Arranque en frío alto por cargar numpy/pandas/scikit-learn; el frontend iría aparte (S3 + CloudFront). | No para esta fase |
| PaaS de ML | Amazon SageMaker (endpoint de inferencia) | Diseñado para servir modelos propios; registro de modelos y monitoreo de *drift*. | Mayor costo y complejidad para un solo modelo pequeño. | Evolución futura del backend |
| Kubernetes | Amazon EKS | Máxima flexibilidad y portabilidad. | Costo fijo del plano de control y complejidad operativa. | No justificado a esta escala |
| IA generativa administrada | **Amazon Bedrock** | Modelos fundacionales vía API, sin infraestructura, pago por token. | No ejecuta el Random Forest. | ✅ Para explicaciones |

### 3.2 Estrategia de actualización

- **Rolling update** de ECS con *deployment circuit breaker* y *rollback* automático: ECS levanta
  tareas nuevas, espera a que pasen los *health checks* (`/api/salud` en el backend y `/` en el ALB) y
  solo entonces retira las viejas. Si las nuevas fallan, revierte solo.
- Imágenes **inmutables** etiquetadas con versión y SHA del commit (`1.0.0`, `sha-fe4971b`). Volver a
  una versión anterior es actualizar el servicio con la etiqueta previa.
- Para cambios de alto riesgo (por ejemplo, un modelo nuevo): **blue/green** con AWS CodeDeploy y dos
  *target groups*.

---

## 4. Proceso paso a paso

Todos los comandos se ejecutan desde la raíz del repositorio `faseII`. Las variables definidas en el
paso 1 se reutilizan en los siguientes, así que conviene hacer todo en la misma terminal.

### Paso 1 — Instalar y configurar la AWS CLI

```bash
brew install awscli
aws --version
```

Iniciar sesión (con IAM Identity Center; la CLI abre el navegador para autorizar):

```bash
aws configure sso
aws sts get-caller-identity
```

Variables de trabajo:

```bash
export AWS_REGION=us-east-1
export AWS_PROFILE=<perfil-creado-en-configure-sso>
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export ECR=$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com
export VERSION=1.0.0
```

### Paso 2 — Validar en local antes de subir

```bash
docker compose up -d --build
bash fuentes/prueba_integracion.sh
docker compose down
```

Solo se continúa si la prueba termina con «Todas las pruebas de integración pasaron».
(El `docker build` del backend ya ejecuta las 19 pruebas unitarias.)

### Paso 3 — Habilitar Claude en Amazon Bedrock

1. Consola de AWS → **Amazon Bedrock** → región `us-east-1`.
2. **Model access** → *Modify model access* → marcar **Anthropic – Claude Opus 5.5**.
3. Completar el formulario de caso de uso de Anthropic (solo la primera vez) y enviar.
4. Esperar a que el estado sea **Access granted**.

### Paso 4 — Crear los repositorios en Amazon ECR

```bash
aws ecr create-repository --repository-name faseii-backend  --image-scanning-configuration scanOnPush=true
aws ecr create-repository --repository-name faseii-frontend --image-scanning-configuration scanOnPush=true
```

`scanOnPush` analiza las vulnerabilidades de cada imagen al subirla.

### Paso 5 — Construir y publicar las imágenes

```bash
aws ecr get-login-password | docker login --username AWS --password-stdin $ECR

docker buildx build --platform linux/arm64 \
  -f fuentes/backend/Dockerfile -t $ECR/faseii-backend:$VERSION --push .

docker buildx build --platform linux/arm64 \
  -t $ECR/faseii-frontend:$VERSION --push fuentes/frontend
```

Comprobar:

```bash
aws ecr describe-images --repository-name faseii-backend --query 'imageDetails[].imageTags'
```

### Paso 6 — Red y grupos de seguridad

```bash
export VPC_ID=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
export SUBNETS=$(aws ec2 describe-subnets --filters Name=vpc-id,Values=$VPC_ID --query 'Subnets[].SubnetId' --output text | tr '\t' ',')

export ALB_SG=$(aws ec2 create-security-group --group-name fraude-alb-sg --description "ALB publico" --vpc-id $VPC_ID --query GroupId --output text)
export FRONT_SG=$(aws ec2 create-security-group --group-name fraude-frontend-sg --description "Frontend nginx" --vpc-id $VPC_ID --query GroupId --output text)
export BACK_SG=$(aws ec2 create-security-group --group-name fraude-backend-sg --description "Backend API" --vpc-id $VPC_ID --query GroupId --output text)

# Internet -> ALB (80 y 443)
aws ec2 authorize-security-group-ingress --group-id $ALB_SG --protocol tcp --port 80  --cidr 0.0.0.0/0
aws ec2 authorize-security-group-ingress --group-id $ALB_SG --protocol tcp --port 443 --cidr 0.0.0.0/0
# ALB -> frontend (80)
aws ec2 authorize-security-group-ingress --group-id $FRONT_SG --protocol tcp --port 80 --source-group $ALB_SG
# frontend -> backend (8000)
aws ec2 authorize-security-group-ingress --group-id $BACK_SG --protocol tcp --port 8000 --source-group $FRONT_SG
```

### Paso 7 — Roles de IAM

**Rol de ejecución** (lo usa ECS para descargar imágenes de ECR y escribir logs):

```bash
cat > /tmp/confianza-ecs.json <<'EOF'
{ "Version": "2012-10-17",
  "Statement": [{ "Effect": "Allow",
                  "Principal": { "Service": "ecs-tasks.amazonaws.com" },
                  "Action": "sts:AssumeRole" }] }
EOF

aws iam create-role --role-name fraudeEcsExecutionRole \
  --assume-role-policy-document file:///tmp/confianza-ecs.json
aws iam attach-role-policy --role-name fraudeEcsExecutionRole \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
```

**Rol de la tarea del backend** (permisos que usa el código de la API; aquí, invocar Bedrock):

```bash
aws iam create-role --role-name fraudeBackendTaskRole \
  --assume-role-policy-document file:///tmp/confianza-ecs.json

cat > /tmp/politica-bedrock.json <<'EOF'
{ "Version": "2012-10-17",
  "Statement": [{ "Effect": "Allow",
                  "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                  "Resource": "*" }] }
EOF

aws iam put-role-policy --role-name fraudeBackendTaskRole \
  --policy-name invocar-bedrock --policy-document file:///tmp/politica-bedrock.json
```

> Restringir `Resource` al ARN del modelo o *inference profile* en producción, y confirmar en la
> documentación de Bedrock las acciones exactas que exige el endpoint de Messages (Mantle) en la
> región elegida.

### Paso 8 — Clúster ECS con Service Connect y logs

```bash
aws ecs create-cluster --cluster-name fraude-cluster \
  --service-connect-defaults namespace=fraude.local

aws logs create-log-group --log-group-name /ecs/fraude
aws logs put-retention-policy --log-group-name /ecs/fraude --retention-in-days 14
```

`--service-connect-defaults` crea el *namespace* `fraude.local` en AWS Cloud Map.

### Paso 9 — Definiciones de tarea

`aws/backend-task.json`:

```json
{
  "family": "fraude-backend",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024",
  "runtimePlatform": { "cpuArchitecture": "ARM64", "operatingSystemFamily": "LINUX" },
  "executionRoleArn": "arn:aws:iam::<ACCOUNT_ID>:role/fraudeEcsExecutionRole",
  "taskRoleArn": "arn:aws:iam::<ACCOUNT_ID>:role/fraudeBackendTaskRole",
  "containerDefinitions": [{
    "name": "backend",
    "image": "<ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/faseii-backend:1.0.0",
    "essential": true,
    "portMappings": [{ "name": "backend", "containerPort": 8000, "protocol": "tcp", "appProtocol": "http" }],
    "environment": [
      { "name": "AWS_REGION", "value": "us-east-1" },
      { "name": "BEDROCK_MODELO", "value": "anthropic.claude-opus-5-5" }
    ],
    "healthCheck": {
      "command": ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/api/salud')\""],
      "interval": 15, "timeout": 5, "retries": 3, "startPeriod": 20
    },
    "logConfiguration": {
      "logDriver": "awslogs",
      "options": { "awslogs-group": "/ecs/fraude", "awslogs-region": "us-east-1", "awslogs-stream-prefix": "backend" }
    }
  }]
}
```

`aws/frontend-task.json`:

```json
{
  "family": "fraude-frontend",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "256",
  "memory": "512",
  "runtimePlatform": { "cpuArchitecture": "ARM64", "operatingSystemFamily": "LINUX" },
  "executionRoleArn": "arn:aws:iam::<ACCOUNT_ID>:role/fraudeEcsExecutionRole",
  "containerDefinitions": [{
    "name": "frontend",
    "image": "<ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/faseii-frontend:1.0.0",
    "essential": true,
    "portMappings": [{ "name": "frontend", "containerPort": 80, "protocol": "tcp", "appProtocol": "http" }],
    "logConfiguration": {
      "logDriver": "awslogs",
      "options": { "awslogs-group": "/ecs/fraude", "awslogs-region": "us-east-1", "awslogs-stream-prefix": "frontend" }
    }
  }]
}
```

Reemplazar `<ACCOUNT_ID>` y registrar:

```bash
mkdir -p aws   # guardar ahí los dos JSON anteriores
sed -i '' "s/<ACCOUNT_ID>/$ACCOUNT_ID/g" aws/backend-task.json aws/frontend-task.json
aws ecs register-task-definition --cli-input-json file://aws/backend-task.json
aws ecs register-task-definition --cli-input-json file://aws/frontend-task.json
```

### Paso 10 — Application Load Balancer

```bash
export ALB_ARN=$(aws elbv2 create-load-balancer --name fraude-alb \
  --subnets $(echo $SUBNETS | tr ',' ' ') --security-groups $ALB_SG \
  --query 'LoadBalancers[0].LoadBalancerArn' --output text)

export TG_ARN=$(aws elbv2 create-target-group --name fraude-frontend-tg \
  --protocol HTTP --port 80 --vpc-id $VPC_ID --target-type ip \
  --health-check-path / --query 'TargetGroups[0].TargetGroupArn' --output text)

aws elbv2 create-listener --load-balancer-arn $ALB_ARN --protocol HTTP --port 80 \
  --default-actions Type=forward,TargetGroupArn=$TG_ARN
```

> **HTTPS:** con un dominio propio, emitir un certificado en **AWS Certificate Manager** y crear un
> *listener* 443 con ese certificado; el 80 se configura para redirigir a 443.

### Paso 11 — Crear los servicios ECS

Primero el backend (servidor de Service Connect con nombre `backend`):

```bash
aws ecs create-service --cluster fraude-cluster --service-name fraude-backend \
  --task-definition fraude-backend --desired-count 1 --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$BACK_SG],assignPublicIp=ENABLED}" \
  --deployment-configuration "deploymentCircuitBreaker={enable=true,rollback=true},minimumHealthyPercent=100,maximumPercent=200" \
  --service-connect-configuration '{"enabled":true,"namespace":"fraude.local",
    "services":[{"portName":"backend","discoveryName":"backend",
                 "clientAliases":[{"port":8000,"dnsName":"backend"}]}]}'

aws ecs wait services-stable --cluster fraude-cluster --services fraude-backend
```

Después el frontend (cliente de Service Connect, detrás del ALB):

```bash
aws ecs create-service --cluster fraude-cluster --service-name fraude-frontend \
  --task-definition fraude-frontend --desired-count 1 --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$FRONT_SG],assignPublicIp=ENABLED}" \
  --load-balancers "targetGroupArn=$TG_ARN,containerName=frontend,containerPort=80" \
  --deployment-configuration "deploymentCircuitBreaker={enable=true,rollback=true}" \
  --service-connect-configuration '{"enabled":true,"namespace":"fraude.local"}'

aws ecs wait services-stable --cluster fraude-cluster --services fraude-frontend
```

> `assignPublicIp=ENABLED` es necesario en subredes públicas sin NAT para que las tareas descarguen
> la imagen de ECR. El grupo de seguridad sigue bloqueando todo acceso entrante directo.

### Paso 12 — Verificar el despliegue

```bash
export URL=http://$(aws elbv2 describe-load-balancers --load-balancer-arns $ALB_ARN \
  --query 'LoadBalancers[0].DNSName' --output text)

curl -s $URL/api/salud
bash fuentes/prueba_integracion.sh $URL
```

La **misma prueba de integración** usada en local acepta la URL como argumento, de modo que valida
el camino completo en AWS: navegador → ALB → nginx → Service Connect → API → modelo.

Revisión adicional:

- Abrir `$URL` en el navegador, cargar un ejemplo de fraude y evaluarlo.
- Logs: `aws logs tail /ecs/fraude --follow`
- Estado de los servicios: consola de **ECS → fraude-cluster** (tareas `RUNNING` y `HEALTHY`).

### Paso 13 — Integrar Amazon Bedrock (explicación de la predicción)

Se añade al backend un endpoint `POST /api/explicar` que primero obtiene la predicción del Random
Forest y después pide a Claude en Bedrock una explicación para el analista.

1. Añadir el SDK de Anthropic con el extra de Bedrock (instala `boto3`/`botocore`, necesarios para
   firmar las peticiones con las credenciales de AWS) a `fuentes/backend/requirements.txt`:

   ```
   anthropic[bedrock]==2.3.0
   ```

2. Añadir a `fuentes/backend/app/main.py`:

   ```python
   import json

   import anthropic
   from anthropic import AnthropicBedrockMantle

   # Usa las credenciales del rol de IAM de la tarea; no hay llaves en la imagen.
   bedrock = AnthropicBedrockMantle(aws_region=os.getenv("AWS_REGION", "us-east-1"))
   MODELO_BEDROCK = os.getenv("BEDROCK_MODELO", "anthropic.claude-opus-5-5")

   INSTRUCCIONES = (
       "Eres analista de prevención de fraude en banca digital. Recibes una transacción con "
       "variables estandarizadas (media 0, desviación 1) y la probabilidad de fraude calculada por "
       "un Random Forest. Explica en español, en un máximo de 4 viñetas, qué variables parecen "
       "influir más en el riesgo y qué debe verificar el analista. No cambies la decisión del modelo."
   )


   class Explicacion(BaseModel):
       prediccion: Prediccion
       explicacion: str


   @app.post("/api/explicar", response_model=Explicacion)
   def explicar(transaccion: Transaccion):
       resultado = predecir(transaccion)
       contenido = json.dumps(
           {"transaccion": transaccion.model_dump(), "prediccion": resultado.model_dump()},
           ensure_ascii=False,
       )
       try:
           respuesta = bedrock.messages.create(
               model=MODELO_BEDROCK,
               max_tokens=1024,
               output_config={"effort": "low"},
               system=INSTRUCCIONES,
               messages=[{"role": "user", "content": contenido}],
           )
       except anthropic.APIStatusError as error:
           raise HTTPException(status_code=502, detail=f"Bedrock respondió {error.status_code}")
       except anthropic.APIConnectionError:
           raise HTTPException(status_code=503, detail="No se pudo conectar con Bedrock")

       if respuesta.stop_reason == "refusal":
           texto = "El modelo de lenguaje no generó una explicación para esta transacción."
       else:
           texto = next((b.text for b in respuesta.content if b.type == "text"), "")
       return Explicacion(prediccion=resultado, explicacion=texto)
   ```

   Decisiones:
   - **`AnthropicBedrockMantle`** es el cliente del SDK oficial de Anthropic para el endpoint de
     Messages de Bedrock; los IDs de modelo llevan el prefijo `anthropic.`.
   - **`effort: "low"`**: la tarea es una explicación corta, así que se prioriza la latencia. Subirlo
     a `"medium"` si las explicaciones resultan demasiado superficiales.
   - Si Bedrock falla, `/api/predecir` **sigue funcionando**: la explicación es un complemento, no
     una dependencia del flujo de decisión.

   > **Verificación realizada:** el código anterior se probó agregado a la API en local con
   > `anthropic[bedrock]==2.3.0`. El endpoint `/api/explicar` se registra, calcula la predicción y
   > llega hasta Bedrock; con credenciales de AWS inválidas responde `502 {"detail":"Bedrock
   > respondió 401"}` mientras `/api/predecir` sigue respondiendo `200`. La respuesta real de Claude
   > requiere una cuenta de AWS con acceso al modelo (paso 3).

3. Probar en local con credenciales de AWS (perfil SSO del paso 1):

   ```bash
   cd fuentes/backend
   AWS_PROFILE=$AWS_PROFILE AWS_REGION=us-east-1 ../../.venv/bin/uvicorn app.main:app --reload
   ```

   ```bash
   curl -s -X POST localhost:8000/api/explicar -H 'Content-Type: application/json' \
     -d "$(curl -s 'localhost:8000/api/ejemplo?tipo=fraude')"
   ```

4. Publicar la versión nueva (pasos 5 y 14) con `VERSION=1.1.0`.

### Paso 14 — Actualizar la aplicación (nuevas versiones)

```bash
export VERSION=1.1.0
docker buildx build --platform linux/arm64 -f fuentes/backend/Dockerfile \
  -t $ECR/faseii-backend:$VERSION --push .

sed -i '' "s/faseii-backend:[0-9.]*/faseii-backend:$VERSION/" aws/backend-task.json
aws ecs register-task-definition --cli-input-json file://aws/backend-task.json
aws ecs update-service --cluster fraude-cluster --service fraude-backend --task-definition fraude-backend
aws ecs wait services-stable --cluster fraude-cluster --services fraude-backend
```

ECS hace el *rolling update*; si las tareas nuevas no pasan el *health check*, el *circuit breaker*
vuelve a la revisión anterior.

**Autoescalado del backend** (por CPU, entre 1 y 4 tareas):

```bash
aws application-autoscaling register-scalable-target --service-namespace ecs \
  --resource-id service/fraude-cluster/fraude-backend \
  --scalable-dimension ecs:service:DesiredCount --min-capacity 1 --max-capacity 4

aws application-autoscaling put-scaling-policy --service-namespace ecs \
  --resource-id service/fraude-cluster/fraude-backend \
  --scalable-dimension ecs:service:DesiredCount --policy-name cpu-60 \
  --policy-type TargetTrackingScaling \
  --target-tracking-scaling-policy-configuration \
  '{"TargetValue":60,"PredefinedMetricSpecification":{"PredefinedMetricType":"ECSServiceAverageCPUUtilization"}}'
```

**CI/CD con GitHub Actions.** Automatiza los pasos 2, 5 y 14 en cada *push* a `main`. Se autentica
en AWS con **OIDC** (rol de IAM que confía en `token.actions.githubusercontent.com` limitado al
repositorio `AL07183522/faseII`), sin guardar llaves en GitHub:

```yaml
# .github/workflows/despliegue-aws.yml
name: Desplegar en AWS
on:
  push:
    branches: [main]
permissions:
  id-token: write
  contents: read
jobs:
  desplegar:
    runs-on: ubuntu-24.04-arm
    env:
      AWS_REGION: us-east-1
      VERSION: sha-${{ github.sha }}
    steps:
      - uses: actions/checkout@v4
      - name: Pruebas (unitarias en el build + integración)
        run: |
          docker compose up -d --build
          bash fuentes/prueba_integracion.sh
          docker compose down
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::<ACCOUNT_ID>:role/github-actions-faseii
          aws-region: us-east-1
      - id: ecr
        uses: aws-actions/amazon-ecr-login@v2
      - name: Publicar imágenes
        run: |
          docker build -f fuentes/backend/Dockerfile -t ${{ steps.ecr.outputs.registry }}/faseii-backend:$VERSION .
          docker build -t ${{ steps.ecr.outputs.registry }}/faseii-frontend:$VERSION fuentes/frontend
          docker push ${{ steps.ecr.outputs.registry }}/faseii-backend:$VERSION
          docker push ${{ steps.ecr.outputs.registry }}/faseii-frontend:$VERSION
      # Siguiente paso: aws-actions/amazon-ecs-render-task-definition y
      # aws-actions/amazon-ecs-deploy-task-definition para cada servicio.
```

### Paso 15 — Eliminar los recursos (evitar costos)

En orden inverso a la creación:

```bash
aws ecs update-service --cluster fraude-cluster --service fraude-frontend --desired-count 0
aws ecs update-service --cluster fraude-cluster --service fraude-backend  --desired-count 0
aws ecs delete-service --cluster fraude-cluster --service fraude-frontend --force
aws ecs delete-service --cluster fraude-cluster --service fraude-backend  --force
aws elbv2 delete-load-balancer --load-balancer-arn $ALB_ARN
aws elbv2 delete-target-group --target-group-arn $TG_ARN
aws ecs delete-cluster --cluster fraude-cluster
aws ecr delete-repository --repository-name faseii-backend  --force
aws ecr delete-repository --repository-name faseii-frontend --force
aws logs delete-log-group --log-group-name /ecs/fraude
```

Después, en la consola: borrar el *namespace* `fraude.local` en **Cloud Map**, los tres grupos de
seguridad y los roles `fraudeEcsExecutionRole` y `fraudeBackendTaskRole`. El acceso a modelos de
Bedrock no genera costo si no se invoca.

---

## 5. Seguridad y operación en producción

| Tema | Práctica |
|---|---|
| Exposición | Solo el ALB es público; backend sin acceso directo desde Internet. HTTPS con ACM. |
| Identidades | Roles de IAM por tarea (sin llaves en imágenes ni variables). OIDC para GitHub Actions. |
| Imágenes | Escaneo en ECR (`scanOnPush`), usuario no root en el backend, etiquetas inmutables. |
| Protección | **AWS WAF** en el ALB (límite de peticiones, reglas administradas). Autenticación de la API (Amazon Cognito o API key) antes de abrirla a otros sistemas. |
| Datos | Según AWS, el contenido enviado a Bedrock no se usa para entrenar los modelos fundacionales; aun así, enviar solo variables estandarizadas, nunca datos personales del cliente. |
| Observabilidad | CloudWatch Logs y métricas de ECS/ALB; alarmas por 5xx, latencia p95 y tareas no sanas; *Container Insights*. Monitorear la distribución de `probabilidad_fraude` para detectar *drift*. |
| Modelo | Versionar el `.joblib` (S3 o SageMaker Model Registry) y reentrenar periódicamente; la tasa real de fraude y los costos FN/FP pueden cambiar. |

---

## 6. Herramientas de documentación utilizadas

| Herramienta | Uso en el proyecto |
|---|---|
| **Claude Code** (asistente de IA de Anthropic) | Se usó en este proyecto para generar y revisar código, pruebas y documentación: `README.md`, este manual y el `CHANGELOG.md` (documentación técnica, pruebas y registro de cambios). Además ejecutó las pruebas y capturó las imágenes del frontend y de Swagger que aparecen en la documentación. |
| **GitHub Copilot** | Alternativa integrada en VS Code y GitHub. Usos recomendados: `/doc` en Copilot Chat para generar *docstrings* de funciones (`evaluar`, `buscar_umbral_optimo`, endpoints de la API); `/explain` para documentar fragmentos complejos (Dockerfile multi-etapa, `nginx.conf`); generación de mensajes de *commit* y resúmenes automáticos de *pull requests*. |
| **Amazon Q Developer** | Asistente de IA de AWS para VS Code/JetBrains. Su agente `/doc` genera o actualiza READMEs a partir del código, y conoce los servicios de AWS, por lo que sirve para revisar comandos de la CLI y políticas de IAM de este manual. |
| **FastAPI + OpenAPI (Swagger UI)** | Documentación de la API **generada automáticamente desde el código** en `/docs` y `/openapi.json`. Siempre está sincronizada con los endpoints reales. El esquema OpenAPI también se puede importar en Postman o usarse como *action group* de un agente de Amazon Bedrock. |
| **Markdown en GitHub** | Toda la documentación vive en el repositorio junto al código, versionada con Git y revisable en *pull requests*. |
| **CHANGELOG** | Registro de cambios por versión siguiendo la convención *Keep a Changelog*. |
| **Docstrings y comentarios** | Cada módulo (`modelo_random_forest.py`, `main.py`, `test_api.py`, `prueba_integracion.sh`) explica su propósito y cómo ejecutarlo. |

**Criterio aplicado con las herramientas de IA:** todo contenido generado con asistentes (Claude Code,
Copilot o Amazon Q) se trata como un borrador. Se valida ejecutándolo (pruebas unitarias, prueba de
integración, verificación en el navegador) y las cifras de la documentación se toman de ejecuciones
reales, no de estimaciones del asistente.

---

## 7. Lista de verificación

- [ ] AWS CLI instalada y sesión iniciada (`aws sts get-caller-identity`).
- [ ] Acceso a Claude Opus 5.5 concedido en Bedrock.
- [ ] Pruebas locales en verde (`prueba_integracion.sh`).
- [ ] Imágenes `faseii-backend` y `faseii-frontend` en ECR (ARM64) sin vulnerabilidades críticas.
- [ ] Servicios `fraude-backend` y `fraude-frontend` estables en ECS.
- [ ] `prueba_integracion.sh $URL` en verde contra el ALB.
- [ ] `/api/explicar` devuelve una explicación desde Bedrock.
- [ ] Alarmas de CloudWatch y presupuesto de AWS configurados.
- [ ] Recursos eliminados al terminar la práctica (paso 15).
