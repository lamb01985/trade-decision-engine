# AWS Stage 1B: ECS Express Mode preparation and deployment runbook

This runbook is preparatory. Do not run mutating commands until the hands-on deployment step. It deploys one existing FastAPI service from a private ECR image to ECS Express Mode/Fargate. It does not add PostgreSQL, a permanent scanner, or a separate frontend.

## Current container contract

- Image entrypoint: `uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log`
- Container port: `8000/tcp`
- Load-balancer health path: `/api/health` (lightweight liveness; `/api/readiness` reports whether market data is configured)
- Static dashboard: packaged read-only under `/app/app/static`
- Runtime data: `/app/data/signals.sqlite3` and `/app/data/notifications.sqlite3` in local storage mode
- Logging: Python/Uvicorn writes to stdout/stderr. No local log files are required.
- Runtime user: unprivileged `app`

**Stage 1B container storage is temporary across task replacement.** SQLite data in `/app/data` can disappear whenever Fargate replaces, reschedules, or redeploys the task. With more than one task, each task has independent state. Stage 2 will move durable state to PostgreSQL.

## A. Prerequisites

On the Mac, install AWS CLI v2 and Docker Desktop (or a compatible Docker Engine), then verify:

```bash
aws --version
aws sts get-caller-identity
docker --version
docker buildx version
docker info
```

The AWS identity needs narrowly scoped permission for ECR, ECS Express Mode, IAM role creation/attachment and `iam:PassRole`, Secrets Manager, CloudWatch Logs, and the networking/load-balancing resources Express Mode manages. Prefer a deployment role rather than permanent administrator credentials.

## B. Choose the region

Do not embed a region in application code. Set it for the current shell:

```bash
export AWS_REGION="YOUR_CHOSEN_REGION"
export AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
export ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
export ECR_REPOSITORY="trade-decision-engine"
export IMAGE_TAG="stage-1b"
```

Confirm that `AWS_REGION` is non-empty before any mutating command.

## C. Create the private ECR repository

Future mutation—do not run during repository preparation:

```bash
aws ecr create-repository \
  --repository-name "${ECR_REPOSITORY}" \
  --image-tag-mutability IMMUTABLE \
  --image-scanning-configuration scanOnPush=true \
  --region "${AWS_REGION}"
```

## D. Authenticate Docker to ECR

```bash
aws ecr get-login-password --region "${AWS_REGION}" | \
  docker login --username AWS --password-stdin "${ECR_REGISTRY}"
```

ECR authorization tokens expire; authenticate again if a later push reports an authorization error.

## E. Build and tag a Linux image for Fargate

The development Mac is Apple Silicon. Do not push an implicit native image and configure an x86 task. The simplest baseline is an explicit Linux AMD64 image and an ECS `X86_64` task:

```bash
docker buildx build \
  --platform linux/amd64 \
  --tag "trade-decision-engine:${IMAGE_TAG}" \
  --load .

docker image inspect "trade-decision-engine:${IMAGE_TAG}" \
  --format '{{.Os}}/{{.Architecture}}'

docker tag "trade-decision-engine:${IMAGE_TAG}" \
  "${ECR_REGISTRY}/${ECR_REPOSITORY}:${IMAGE_TAG}"
```

Fargate also supports Linux ARM64, but the task architecture and image must both be `ARM64`. Do not mix architectures. A multi-platform ECR manifest is an optional later improvement, not required for Stage 1B.

## F. Push the image

Future mutation:

```bash
docker push "${ECR_REGISTRY}/${ECR_REPOSITORY}:${IMAGE_TAG}"
aws ecr describe-images \
  --repository-name "${ECR_REPOSITORY}" \
  --image-ids imageTag="${IMAGE_TAG}" \
  --region "${AWS_REGION}"
```

Use the returned image digest when selecting the image in ECS where practical.

## G. Create Secrets Manager secrets

Create three separate secrets. Avoid command-line literals and shell history; these examples read each value without echoing it:

```bash
read -s "SECRET_VALUE?MASSIVE_API_KEY: "
aws secretsmanager create-secret --name trade-decision-engine/MASSIVE_API_KEY \
  --secret-string "${SECRET_VALUE}" --region "${AWS_REGION}"
unset SECRET_VALUE

read -s "SECRET_VALUE?PUSHOVER_USER_KEY: "
aws secretsmanager create-secret --name trade-decision-engine/PUSHOVER_USER_KEY \
  --secret-string "${SECRET_VALUE}" --region "${AWS_REGION}"
unset SECRET_VALUE

read -s "SECRET_VALUE?PUSHOVER_APP_TOKEN: "
aws secretsmanager create-secret --name trade-decision-engine/PUSHOVER_APP_TOKEN \
  --secret-string "${SECRET_VALUE}" --region "${AWS_REGION}"
unset SECRET_VALUE
```

Pushover is optional; omit its two secrets if notifications will remain disabled. Record secret ARNs, never values:

```bash
aws secretsmanager list-secrets \
  --filters Key=name,Values=trade-decision-engine/ \
  --query 'SecretList[].{Name:Name,ARN:ARN}' --region "${AWS_REGION}"
```

Inject them through the Express primary container's `secrets` entries (`name` and `valueFrom` ARN), not ordinary plaintext environment entries. Updating a secret does not update a running task; force a new service deployment afterward.

## H. Create or verify ECS IAM roles

Use the ECS Express Mode console's **Create new role** flow for the initial deployment, or pre-create equivalent least-privilege roles:

- Task execution role trusted by `ecs-tasks.amazonaws.com`, with `AmazonECSTaskExecutionRolePolicy` plus `secretsmanager:GetSecretValue` restricted to the three secret ARNs. It permits ECR pulls, CloudWatch log delivery, and startup secret retrieval.
- Express infrastructure role trusted by `ecs.amazonaws.com`, with AWS managed policy `AmazonECSInfrastructureRoleforExpressGatewayServices`.
- No application task role is currently required because the app itself does not call AWS APIs.

Verify role metadata without changing it:

```bash
aws iam get-role --role-name ecsTaskExecutionRole
aws iam list-attached-role-policies --role-name ecsTaskExecutionRole
aws iam get-role --role-name ecsInfrastructureRoleForExpressServices
aws iam list-attached-role-policies --role-name ecsInfrastructureRoleForExpressServices
```

The deploying identity needs `iam:PassRole` only for these roles and their intended ECS services.

## I–L. Create the ECS Express Mode service

Use the ECS console for the first deployment because it makes generated networking, role selection, secret injection, and resource costs visible before confirmation:

1. ECS → Express Mode → Create service.
2. Name: `trade-decision-engine`.
3. Select the private ECR image by digest.
4. Container port: `8000`.
5. Health check path: `/api/health`.
6. Architecture: Linux `X86_64` for the `linux/amd64` image.
7. Start with `0.5 vCPU`, `1 GB` memory, minimum `1` task, maximum `1` task.
8. Select the execution and infrastructure roles from section H.
9. Add non-secret environment configuration from the table below.
10. Add Secrets Manager ARNs using secret injection, mapped to their environment variable names.
11. Review every generated resource and estimated cost, then create only during the authorized deployment step.

The equivalent CLI operation is `aws ecs create-express-gateway-service`; inspect the exact input supported by the installed AWS CLI before using it:

```bash
aws ecs create-express-gateway-service --generate-cli-skeleton yaml-input > /tmp/ecs-express-input.yaml
```

Populate the primary container image, `containerPort: 8000`, environment, `secrets`, roles, CPU/memory, `healthCheckPath: /api/health`, and scaling target in that temporary file. Then review it before the future mutation:

```bash
aws ecs create-express-gateway-service \
  --cli-input-yaml file:///tmp/ecs-express-input.yaml \
  --region "${AWS_REGION}" \
  --monitor-resources DEPLOYMENT
```

Do not commit generated task/service input containing account-specific ARNs or configuration.

## Environment classification

| Variable | Class | Required in ECS | Stage 1B value/purpose |
|---|---|---:|---|
| `MASSIVE_API_KEY` | Secret | Yes for analysis | Secrets Manager injection |
| `PUSHOVER_USER_KEY` | Secret | No | Secrets Manager injection |
| `PUSHOVER_APP_TOKEN` | Secret | No | Secrets Manager injection |
| `APP_STORAGE_MODE` | Non-secret | Yes | `local`; enables current SQLite behavior, with temporary task storage |
| `PUBLIC_APP_BASE_URL` | Non-secret | After URL exists | Exact Express HTTPS application URL, no trailing slash required |
| `CORS_ALLOWED_ORIGINS` | Non-secret | Usually no | Leave empty for the same-origin packaged frontend; never `*` |
| `MASSIVE_BASE_URL` | Non-secret | No | Keep default unless provider configuration requires it |
| `LOG_LEVEL` | Non-secret | No | `INFO` default; do not use debug in deployment |
| `SIGNAL_LOG_PATH` | Non-secret | No | Default `data/signals.sqlite3` |
| `NOTIFICATION_STATE_PATH` | Non-secret | No | Default `data/notifications.sqlite3` |
| `SCANNER_REFRESH_SECONDS` | Non-secret | No | Existing default `300` |
| `SCANNER_BATCH_SIZE` | Non-secret | No | Existing default `8` |
| `SCANNER_MAX_SYMBOLS` | Non-secret | No | Existing default `250` |
| `SCANNER_MIN_PRICE` | Non-secret | No | Frozen existing default; do not change for infrastructure |
| `SCANNER_MIN_AVERAGE_DOLLAR_VOLUME` | Non-secret | No | Frozen existing default; do not change for infrastructure |
| `SCANNER_MIN_SCORE` | Non-secret | No | Frozen existing default; do not change for infrastructure |
| `PUSHOVER_API_URL` | Non-secret | No | Keep default |
| `PUSHOVER_PRIORITY_RADAR` | Non-secret | No | Existing default |
| `PUSHOVER_PRIORITY_READY` | Non-secret | No | Existing default |
| `PUSHOVER_PRIORITY_INVALIDATED` | Non-secret | No | Existing default |
| `PUSHOVER_PRIORITY_TOO_LATE` | Non-secret | No | Existing default |

`AWS_REGION`, account ID, ECR registry, repository, and image tag are deployment-shell inputs; they are not application environment variables.

## M–T. Deploy, verify, and finish configuration

After explicit authorization to deploy:

1. Wait until the Express service is `ACTIVE` and its target is healthy.
2. Obtain the generated URL; it will resemble `https://<service-name>.ecs.<region>.on.aws/`. Do not invent it in advance.
3. Test liveness and readiness:

   ```bash
   export APP_URL="THE_RETURNED_HTTPS_URL"
   curl --fail --show-error "${APP_URL}/api/health"
   curl --fail --show-error "${APP_URL}/api/readiness"
   ```

4. Open `${APP_URL}/`, verify CSS/JavaScript load, and analyze a ticker.
5. Test Overview, Watchlist/manual scan, Levels, Options, and Futures as applicable to provider entitlements.
6. Open the HTTPS URL from the iPhone over cellular data.
7. Inspect the Express service's CloudWatch log group. Confirm startup/request logs appear and no secrets are printed.
8. Set `PUBLIC_APP_BASE_URL` to the exact returned HTTPS URL and redeploy so Pushover deep links use it.
9. Keep `CORS_ALLOWED_ORIGINS` empty for this same-origin application. If a separate React/Vercel frontend is introduced later, set it to that exact HTTPS origin (or a comma-separated allowlist), never `*`.

## Cost and security boundaries

Express Mode can create or manage an ECS service/cluster, Fargate tasks, an internet-facing Application Load Balancer, target group, listeners/HTTPS certificate and hostname, security groups, auto-scaling configuration, CloudWatch logs/monitoring, and service-linked roles. ECR image storage, Secrets Manager secrets, Fargate compute, load balancer capacity/time, CloudWatch ingestion/storage, and data transfer may all incur charges. The load balancer can be a meaningful part of a tiny application's cost even with one small task.

For this personal app, use one service, `0.5 vCPU`, `1 GB`, and min/max `1/1` initially. This sacrifices multi-AZ task redundancy to control cost and avoids unsafe multi-task SQLite semantics. Reassess from observed CPU/memory and request latency; do not add services or autoscaling preemptively.

Minimum posture: secrets stay outside Git/image/task plaintext configuration; Express serves HTTPS; debug mode remains off; client errors stay sanitized; health endpoints expose capability booleans but no credentials; IAM roles are least privilege; and logs contain no provider URLs with keys. The Express URL is internet-facing. **Do not leave a personal trading dashboard permanently public without an authentication/access-control layer.** Authentication is explicitly outside Stage 1B, so treat this as a temporary controlled deployment and plan that protection before permanent use.

## References

- [AWS: create an ECS Express Mode service](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/express-service-getting-started.html)
- [AWS: resources created by Express Mode](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/express-service-work.html)
- [AWS: ECR CLI image workflow](https://docs.aws.amazon.com/AmazonECR/latest/userguide/getting-started-cli.html)
- [AWS: Fargate CPU, memory, and architectures](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/fargate-tasks-services.html)
- [AWS: inject Secrets Manager values into ECS](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/secrets-envvar-secrets-manager.html)
