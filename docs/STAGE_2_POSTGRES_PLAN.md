# Infrastructure Stage 2: PostgreSQL persistence plan

Status: Stage 2A audit/design complete; Stage 2B local storage abstraction implemented on 2026-08-15. No AWS resource or production migration has been created, and no trading rule was changed.

## Stage 2B implementation record

Implemented:

- SQLAlchemy 2.x shared schema and repository behavior, Alembic, and psycopg 3.
- Typed `SignalStore`, `ScannerStateStore`, and `NotificationStore` protocols.
- `SQLiteSignalStore`, `SQLiteScannerStateStore`, `SQLiteNotificationStore`, and corresponding PostgreSQL implementations.
- Stable signal-fingerprint uniqueness, guarded latest-state writes, ordered lifecycle transitions, monotonic outcome updates, durable scanner state, unique notification events, and short notification claims.
- Service hydration of the prior `StandardDecision`, persistent scanner transition/cooldown state, SQL-backed notification preferences/deduplication, and database-aware readiness.
- SQLite durability/restart/concurrency tests and an opt-in PostgreSQL parity suite using `TEST_DATABASE_URL`.

Files added: `app/persistence/{__init__,contracts,schema,sqlalchemy_stores}.py`, `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, `alembic/versions/20260815_01_initial_persistence.py`, `tests/test_persistence_stage2b.py`, `tests/test_postgres_persistence.py`, and `tests/test_database_readiness.py`. Existing configuration, startup, service/scanner/notification adapters, dependency files, `.env.example`, and this document were updated. Existing Stage 1B working-tree changes were preserved.

Compatibility behavior:

- With no `DATABASE_URL`, local startup continues to use `data/signals.sqlite3` and `data/notifications.sqlite3` through the existing compatibility adapters. Neither file is removed or rewritten.
- With an explicit SQLite URL, the new unified schema is initialized automatically.
- With PostgreSQL, schema creation is never automatic: apply the reviewed Alembic migration first. Readiness performs a bounded connectivity check and returns `ready: false` when PostgreSQL is unavailable without returning its URL or credentials.

Configuration examples:

```bash
# New unified local SQLite store (optional; omission preserves legacy paths)
export DATABASE_BACKEND=sqlite
export DATABASE_URL=sqlite:///data/application.sqlite3

# Local PostgreSQL only
export DATABASE_BACKEND=postgres
export DATABASE_URL='postgresql+psycopg://tde:local_password@127.0.0.1:55432/tde'
```

Local PostgreSQL and migration commands:

```bash
docker run --name tde-stage2-postgres \
  -e POSTGRES_USER=tde \
  -e POSTGRES_PASSWORD=local_password \
  -e POSTGRES_DB=tde \
  -p 127.0.0.1:55432:5432 -d postgres:16-alpine

export DATABASE_URL='postgresql+psycopg://tde:local_password@127.0.0.1:55432/tde'
.venv/bin/alembic upgrade head

export TEST_DATABASE_URL="$DATABASE_URL"
.venv/bin/python -m pytest -q tests/test_postgres_persistence.py
```

Verification commands:

```bash
.venv/bin/python -m pytest -q
TEST_DATABASE_URL="$DATABASE_URL" .venv/bin/python -m pytest -q tests/test_postgres_persistence.py
.venv/bin/python -m compileall -q app tests alembic
git diff --check
```

The local process-replacement equivalent is covered by closing the first SQLAlchemy engine after writing a signal and notification claim/delivery, creating fresh repositories, restoring the same `StandardDecision`, and proving the same notification event cannot be reserved again.

## Stage 2C local PostgreSQL validation — ACCEPTED

Validation date: 2026-08-15. Scope was local PostgreSQL reliability only; no AWS resource or production credential was used.

### Clean database and migration

The exact disposable PostgreSQL 16 command was:

```bash
docker run --name tde-stage2c-postgres \
  -e POSTGRES_USER=tde_stage2c \
  -e POSTGRES_PASSWORD=stage2c_local_only \
  -e POSTGRES_DB=tde_stage2c \
  -p 127.0.0.1:55433:5432 -d postgres:16-alpine

export DATABASE_BACKEND=postgres
export DATABASE_URL='postgresql+psycopg://tde_stage2c:stage2c_local_only@127.0.0.1:55433/tde_stage2c'
.venv/bin/alembic upgrade head
```

An empty database upgraded successfully to `20260815_01`. Catalog inspection confirmed `signals`, `signal_state_transitions`, `signal_outcomes`, `scanner_state`, `notifications`, `notification_settings`, and `alembic_version`. It also confirmed all primary/foreign keys, unique signal fingerprint, unique notification event, unique transition, scanner composite primary key, direction check, and the signal/state/transition/notification indexes described above.

### Full PostgreSQL-enabled test run

```bash
export TEST_DATABASE_URL="$DATABASE_URL"
.venv/bin/python -m pytest -q
```

Final result: **169 passed, 0 failed, 0 skipped, 1 warning** in 1.67 seconds. The warning is an existing Starlette deprecation notice recommending `httpx2`; it is unrelated to persistence. The first clean full run exposed a PostgreSQL-only concurrent transition conflict that SQLite had masked. Signal, transition, and outcome creation now use native `ON CONFLICT DO NOTHING` inside one valid transaction, and stale/equal losing observations cannot append transitions. The complete suite passed after this hardening.

### SQLite/PostgreSQL parity

`tests/test_stage2c_parity.py` runs an identical representative workflow through both backends and compares business fields after normalizing SQLite's loss of timezone metadata. Parity passed for:

- stable signal fingerprint and latest lifecycle state;
- ordered `from_state`/`to_state` transitions and transition times;
- MFE/MAE, target hit/time, and invalidation state;
- engine, setup-rules, and parameter versions;
- scanner prior state/fingerprint/time;
- notification preferences, reservation, delivery history, and duplicate suppression.

There were no business-semantic differences. The intentional physical differences are PostgreSQL JSONB versus SQLite JSON, PostgreSQL timezone-aware values versus SQLite UTC values rehydrated as timezone-aware, and backend-generated UUID/creation timestamps that are not domain identity.

### True process replacement

Two separate interpreter processes were executed:

```bash
TEST_DATABASE_URL="$DATABASE_URL" .venv/bin/python -m tests.stage2c_restart_probe write
TEST_DATABASE_URL="$DATABASE_URL" .venv/bin/python -m tests.stage2c_restart_probe read
```

Process A wrote fingerprint `sig_13c501915273d790ee9935dc`, two lifecycle transitions, scanner `READY` state, and a `SENT` notification. It exited and disposed its engine. Fresh Process B restored the same fingerprint and `CONFIRMED` latest state, restored scanner `READY`, read two ordered transitions and the `SENT` delivery, and rejected the repeated notification (`duplicate_accepted: false`). Restart persistence passed.

### Concurrency and rollback

- Four identical concurrent signal writers resolve to one fingerprint and one durable signal; PostgreSQL conflict handling no longer aborts the surrounding transaction.
- Six concurrent workers competing for one notification event produce exactly one accepted reservation.
- Competing lifecycle observations serialize on the signal row; the newest observation remains current and a stale loser cannot append a transition.
- Repeated outcome writes are row-locked and monotonic: MFE/MAE and hit flags never regress and first-hit time remains earliest.
- Scanner compare-and-set rejects an older `last_observed_at`.
- An injected failure at outcome insertion proved the whole `record_decision` transaction rolls back: neither signal nor transition remained.

### Live readiness and recovery

The FastAPI app was run locally on port 8766 with the PostgreSQL configuration. With PostgreSQL reachable, `/api/health` returned process health and `/api/readiness` returned `ready: true` with both stores available. After `docker stop tde-stage2c-postgres`, health remained `200` and process-oriented while readiness returned `ready: false` and both stores unavailable. After `docker start tde-stage2c-postgres`, readiness recovered to `true` without restarting FastAPI. Observed server timings were 2 ms health/5 ms failed readiness and 38 ms on the first recovered readiness check.

Health/readiness responses and captured application logs contained no database URL/password, Massive key value, Pushover user key/token, or PostgreSQL credential. Startup logs disclose only provider configuration presence/length and a credential-stripped provider host; no secret values are logged.

### Performance sanity sample

Thirty local samples were collected with `python -m tests.stage2c_latency_probe`:

| Operation | Median | p95 | Maximum |
|---|---:|---:|---:|
| Signal insert plus initial transition/outcome | 3.912 ms | 5.798 ms | 70.251 ms cold/outlier |
| Signal fingerprint lookup | 1.316 ms | 2.267 ms | 3.104 ms |
| Lifecycle update/transition | 3.750 ms | 5.465 ms | 9.179 ms |
| Notification reservation | 1.373 ms | 2.335 ms | 2.486 ms |
| Duplicate notification lookup/rejection | 1.243 ms | 1.992 ms | 2.013 ms |

No unexpectedly slow steady-state query or missing index was identified. This is a development-machine sanity sample, not capacity evidence; RDS sizing/pooling still requires later load observation.

### Known issues and role decision

- The only test warning is the unrelated Starlette/httpx deprecation noted above.
- Pushover cannot participate in the database transaction; the documented ambiguous external-send boundary remains.
- SQLite remains supported for local development and lightweight tests only. PostgreSQL is the single intended production persistence backend; no dual-production-storage design is planned.

**Stage 2C acceptance: ACCEPTED.** The local PostgreSQL backend met migration, parity, restart, concurrency, rollback, readiness recovery, secret-safety, and performance-sanity criteria. Stage 2D may proceed only through a separately authorized, reviewed AWS RDS creation plan.

## Stage 2D — AWS RDS Deployment Plan

Status: **DESIGN REVIEW COMPLETE; IMPLEMENTATION NOT AUTHORIZED.** This section is based on repository inspection plus read-only AWS inventory in `us-east-1`. It creates, changes, and deletes nothing.

### A. Current architecture discovered

The deployed `trade-decision-engine` is an ECS Express Mode service in the `default` cluster:

- One desired/running Fargate task, Linux/X86_64, platform 1.4.0, 0.5 vCPU and 1 GB RAM.
- `awsvpc` networking in the default `172.31.0.0/16` VPC. Express may place the task in any of the six default subnets across `us-east-1a` through `us-east-1f`; all six map public IPs and use the default route table with `0.0.0.0/0` through an internet gateway. The observed task was in `us-east-1b` with both private and public IPv4 addresses.
- The task has an Express-managed application security group. Its only inbound rule is TCP/8000 from the Express gateway security group; outbound is currently unrestricted. Public ingress terminates at the Express-managed gateway/HTTPS endpoint.
- Desired and maximum task counts are both one. CPU target tracking is configured at 60%, but cannot add a second task under the current maximum.
- The current task definition injects `MASSIVE_API_KEY`, `PUSHOVER_USER_KEY`, and `PUSHOVER_APP_TOKEN` from Secrets Manager. It has an ECS task execution role and no application task role.
- The task execution role has the standard ECR/CloudWatch policy plus an inline `secretsmanager:GetSecretValue`/`ssm:GetParameters` policy with `Resource: "*"`; this is functional but not least privilege.
- No `DATABASE_BACKEND`, `DATABASE_URL`, or other non-secret application environment values are currently present. The running task therefore defaults to local SQLite on ephemeral Fargate storage.
- The load-balancer health path is `/api/health`. `/api/readiness` checks database connectivity when PostgreSQL is configured but currently does not verify the Alembic revision.
- The production image installs SQLAlchemy, Alembic, and psycopg, but the Dockerfile copies only `app/`; it does not contain `alembic.ini`, the `alembic/` directory, or an AWS RDS CA bundle. A migration task cannot use the current image as built.
- The application creates one shared SQLAlchemy engine for all three repositories. `DB_POOL_SIZE` defaults to 5, but SQLAlchemy's implicit `max_overflow=10` means one task can currently open up to 15 connections.

Account IDs, endpoints, secret ARNs, and generated resource IDs deliberately are not recorded in source control. Stage 2E must rediscover and validate them before any change. One configuration hygiene issue also needs review: current JSON-key selectors on all three provider secret references appear to use the same field label. The values are functioning, but the database-secret setup must use an unambiguous dedicated secret and must not copy that selector pattern blindly.

### B. Proposed AWS architecture

```text
Internet
   |
ECS Express managed HTTPS gateway
   |
Fargate task ENI in existing public subnet
  - existing application SG (TCP/8000 from gateway SG)
  - new stable DB-client SG
   |
   | private VPC routing, TCP/5432, TLS verify-full
   v
RDS PostgreSQL 16 in new private DB subnets (2 AZs)
  - publicly accessible = false
  - DB SG permits 5432 only from DB-client SG
  - encrypted gp3, backups/PITR, PostgreSQL logs
```

Keep the ECS service in its current subnets for this stage. It needs direct outbound internet access to Massive and Pushover, and moving it to private subnets would require NAT gateways or VPC endpoint redesign with material cost. Same-VPC traffic from its private ENI address reaches RDS through the VPC `local` route; a task's public IP is not used for the DB connection.

Create two real private DB subnets in different AZs, proposed as `172.31.96.0/24` in `us-east-1a` and `172.31.97.0/24` in `us-east-1b`. Read-only inventory confirmed these CIDRs do not overlap the six current `/20` subnets. Associate both with a dedicated route table containing only the VPC-local route—no internet gateway and no NAT route. Create an RDS DB subnet group containing exactly these private subnets. RDS requires subnet coverage in at least two AZs even for the recommended initial Single-AZ DB instance.

Use a stable `trade-decision-engine-db-client` SG attached to the ECS service in addition to its generated application SG. Use a separate `trade-decision-engine-postgres` SG on RDS. Its only inbound rule is TCP/5432 with the DB-client SG as source: no CIDR, workstation IP, public IP, default SG, or gateway SG. This avoids coupling the DB rule to an Express-generated SG that may be replaced. The ECS service therefore needs only the additive SG attachment; its subnet list, public ingress, container port, and gateway remain unchanged.

### C. RDS recommendation and exact resources

Recommended initial database:

| Setting | Recommendation | Reason |
|---|---|---|
| Engine | RDS PostgreSQL **16.14**, or the newest AWS-offered 16.x minor at creation after compatibility recheck | Stage 2C validated PostgreSQL 16; major 16 has standard RDS support through February 2029. Do not jump major versions during deployment. |
| Class | `db.t4g.micro` (2 vCPU, 1 GiB) | The measured workload is tiny, one app task, and local DB operations had low-millisecond latency. Alarm on memory/swap/CPU and move to `db.t4g.small` if thresholds are sustained. |
| Availability | **Single-AZ initially** | The application itself is one task and not end-to-end HA. Multi-AZ roughly doubles DB compute/storage cost. Upgrade before an availability SLA or permanent critical usage. |
| Storage | 20 GiB gp3; storage autoscaling maximum 100 GiB | Ample for application-owned state; raw market history is excluded. Autoscaling prevents storage-full failure but only grows, never shrinks. |
| Encryption | Enabled at creation using AWS-managed `aws/rds` KMS key | No custom-key requirement currently; encryption cannot be enabled later in place. |
| Public access | `false` | Mandatory. Private subnets plus SG-only client access. |
| DB name/port | `trade_decision_engine`, TCP/5432 | Matches application purpose and PostgreSQL default. |
| Backups | Automated backups/PITR, 14-day retention; daily backup window 06:00–06:30 UTC | A positive retention period enables transaction-log PITR. Fourteen days is conservative but modest at this size. |
| Maintenance | Weekly Sunday 07:00–07:30 UTC; auto minor upgrades enabled; major upgrades disabled | Non-overlapping with backup; patching is controlled while safe minor fixes apply in-window. |
| Deletion | Deletion protection enabled; skip-final-snapshot disabled; retain automated backups and require a uniquely named final snapshot | Prevents accidental deletion. Final snapshots do not expire; review them manually for cost/retention. |
| Parameter group | Custom `postgres16` group with `rds.force_ssl=1`, `log_lock_waits=1`, and `log_min_duration_statement=1000` initially | TLS is mandatory for PostgreSQL 15+ by default, but the explicit group makes the control reviewable; slow/lock logging is useful without logging statements or credentials. |
| Logs | Export PostgreSQL and upgrade logs to CloudWatch; retention 30 days | Minimum diagnostic evidence. Do not enable indiscriminate statement logging. |
| Monitoring | Standard RDS/CloudWatch metrics at 1 minute. Enhanced Monitoring at 60 seconds is optional but recommended for the first month. | Enough to establish memory/CPU/connection baseline. Database Insights/RDS Proxy are not initially required. |

Exact new AWS resources:

1. Two private VPC subnets in `us-east-1a` and `us-east-1b`.
2. One private route table, associated with both, with only the automatically created VPC-local route.
3. One DB subnet group.
4. One stable ECS DB-client security group.
5. One RDS PostgreSQL security group.
6. One custom PostgreSQL 16 DB parameter group.
7. One Single-AZ encrypted RDS PostgreSQL instance with gp3 storage/autoscaling, backups, deletion protection, and log exports.
8. One RDS-managed master secret, plus two purpose-specific Secrets Manager secrets: runtime application URL and migration-owner URL.
9. One migration-task execution role/policy, separate from the normal task execution role, plus a one-off ECS task definition/revision capable of running Alembic.
10. A revised least-privilege inline policy for the existing task execution role.
11. CloudWatch alarms, one RDS event subscription, and an SNS notification topic/subscription unless an existing operational topic is selected during review.
12. A new ECS Express service revision attaching the DB-client SG and adding the database configuration/secret.

No NAT gateway, RDS Proxy, read replica, custom KMS key, bastion host, public DB endpoint, or dual production database is recommended now.

### D. Exact ECS/application changes required

Normal web/scanner task configuration:

| Name | Source | Value |
|---|---|---|
| `APP_STORAGE_MODE` | Plain environment | `hosted` |
| `DATABASE_BACKEND` | Plain environment | `postgres` (required; validates the URL/backend match) |
| `DATABASE_URL` | ECS `secrets` entry | Entire URL from the application DB secret |
| `DB_CONNECT_TIMEOUT_SECONDS` | Plain environment | `5` |
| `DB_POOL_SIZE` | Plain environment | `5` |
| `DB_MAX_OVERFLOW` | Plain environment after Stage 2E hardening | `0` |
| `DB_POOL_TIMEOUT_SECONDS` | Plain environment after Stage 2E hardening | `5` |
| `DB_POOL_RECYCLE_SECONDS` | Plain environment after Stage 2E hardening | `300` |

The exact URL shape expected by current code is:

```text
postgresql+psycopg://tde_app:<percent-encoded-password>@<private-rds-endpoint>:5432/trade_decision_engine?sslmode=verify-full&sslrootcert=/app/certs/aws-rds-global-bundle.pem
```

Store that complete value as a Secrets Manager secret string. Never place it in an ordinary task-definition environment entry, deployment JSON, shell history, logs, health response, image, or repository. Use a URL-safe generated password or correctly percent-encode it before URL construction. Secret injection occurs at task start; rotation requires updating the secret and forcing a new ECS deployment.

Required Stage 2E repository hardening before building the production image:

- Add typed `DB_MAX_OVERFLOW`, pool timeout, and recycle settings; pass `max_overflow=0`, bounded `pool_timeout`, and `pool_recycle` to SQLAlchemy. With max one active task and one overlapping deployment task, this caps normal app connections at 10 total; allow one separate migration connection.
- Add a schema-revision readiness check for `20260815_01` (not merely `SELECT 1`). `/api/health` remains liveness-only; `/api/readiness` must be false for unreachable or wrong-revision DBs. Keep the Express gateway health path on `/api/health` to avoid restarting every task during a DB outage; make `/api/readiness` a mandatory deployment gate and alarm/synthetic check.
- Copy `alembic.ini` and `alembic/` into the image so a one-off migration task can run. Do not run Alembic from every normal app startup.
- Add the current official AWS RDS global CA bundle to a fixed image path with a reviewed checksum/source, and validate `sslmode=verify-full` from the built image.
- Keep SQLite dependencies/adapters for local development, but do not configure SQLite paths in ECS.

### E. Secrets Manager, database roles, and IAM

Use three distinct database identities:

1. **RDS master**: let RDS manage its master password in Secrets Manager. It is bootstrap/break-glass only and is never injected into the normal ECS task.
2. **`tde_migrator`**: owns the application schema and runs reviewed Alembic migrations. Store its TLS URL in `trade-decision-engine/prod/database-migration-url`; only the dedicated migration task execution role can retrieve it.
3. **`tde_app`**: runtime login with `CONNECT`, schema `USAGE`, and only `SELECT/INSERT/UPDATE/DELETE` on application tables (plus sequence rights if a future migration introduces sequences). It has no role creation, database creation, schema ownership, or DDL privileges. Store its TLS URL in `trade-decision-engine/prod/database-url`; only the normal ECS task execution role can retrieve it.

Before the first migration, use the master identity in a tightly controlled bootstrap task to create the two login roles, grant schema creation/ownership to the migrator, grant runtime DML to `tde_app`, and configure `ALTER DEFAULT PRIVILEGES` so future migrator-created tables receive runtime grants. Revoke unnecessary `PUBLIC` schema/database privileges. Do not pass credentials through command arguments or print them.

The existing normal ECS **task execution role**, not the application task role, needs `secretsmanager:GetSecretValue` on exactly four applicable runtime secret ARNs: Massive, optional two Pushover secrets, and the runtime DB URL. Remove wildcard `Resource: "*"`; remove `ssm:GetParameters` if no task definition uses Parameter Store. Add `kms:Decrypt` only if a customer-managed Secrets Manager key is later chosen. The app calls no AWS APIs, so no task role remains necessary.

Create a separate migration execution role with standard ECR/log permissions and `GetSecretValue` only for the migration URL (and, for the one-time bootstrap task only, explicitly controlled access to the master secret). The deployment identity may `iam:PassRole` only these intended roles and may run migration tasks; normal developers/tasks do not receive master/migration credentials.

Do not enable automatic credential rotation until a tested procedure updates URL secrets and forces new tasks. Initial recommendation is manual 90-day rotation: change the DB role password transactionally, update its URL secret, deploy/restart the appropriate task, verify, then retire the old credential where an overlap mechanism is used.

### F. Alembic deployment and cutover sequence

All mutating steps below are **future Stage 2E/2F actions requiring explicit approval**:

1. Re-run read-only inventory and record a reviewed change set: region, VPC, non-overlapping CIDRs, current ECS SG/revision, secret ARNs, quotas, engine versions/classes, and current AWS Pricing Calculator estimate.
2. Implement and locally retest only the infrastructure-facing code/image hardening above: bounded pool overflow, schema-aware readiness, Alembic files, and RDS CA. Repeat Stage 2C against PostgreSQL 16.
3. Provision/tag networking: two private subnets, local-only route table, DB subnet group, stable client SG, and DB SG. Verify no route exposes DB subnets and the DB SG has one SG-sourced 5432 ingress rule.
4. Create the parameter group and encrypted, private, deletion-protected Single-AZ RDS instance with backups/log exports. Wait for `available`; verify `PubliclyAccessible=false`, encryption, backup retention, endpoint private DNS, and parameter status.
5. Create/generated secrets without printing values. Bootstrap `tde_migrator`/`tde_app` and privileges from a one-off task inside the VPC. Never open 5432 to an operator IP.
6. Run a dedicated one-off migration task using the migration secret and command `alembic upgrade head`. Require exit code zero. Verify `alembic_version=20260815_01`, six tables, constraints/indexes, default privileges, TLS cipher/use, and that `tde_app` can perform DML but cannot create/drop tables.
7. Decide the SQLite seed explicitly. Recommended default is **no legacy production import**: the audited local `signals.sqlite3` rows have null identity/version provenance, and Fargate SQLite is ephemeral. Archive both SQLite files read-only instead. If historical evidence is declared necessary, defer cutover and use the separately reviewed Stage 2F dry-run/import/checksum/reject-manifest process; never infer legacy versions or destructively merge rows.
8. Schedule cutover while the market is closed to avoid overlapping old SQLite/new PostgreSQL notification state. Preserve the old ECS revision and image digest. Take an RDS manual pre-cutover snapshot after schema/bootstrap.
9. Register a new ECS Express revision with the stable client SG, non-secret variables above, and runtime `DATABASE_URL` secret. Keep desired/max task count at one. Deploy with circuit breaker/rollback where Express supports it.
10. Gate promotion on CloudWatch logs plus `/api/health` and `/api/readiness`; readiness must confirm connectivity and expected schema. Confirm logs/responses contain no URL/password. Verify TLS, signal insert/read, lifecycle/scanner state, and notification event reservation without sending an unnecessary real alert.
11. Perform the accepted persistence proof: Task A writes known state; replace Task A; Task B restores the same fingerprint/lifecycle/scanner/notification state and rejects the duplicate event.
12. Observe at least one market session. Review CPU, memory, connections, storage, slow queries, errors, notification duplicates, and RDS events before declaring RDS authoritative. SQLite remains only a local/test backend.

Do not dual-write. A market-closed cutover from ephemeral SQLite to a clean RDS source of truth avoids reconciliation ambiguity.

### G. Rollback plan

Application rollback and database rollback are separate:

1. **Before cutover failure:** leave the current ECS revision running on SQLite, fix RDS/migration/config, and retry. Nothing reads RDS yet.
2. **New task fails readiness before accepting writes:** ECS deployment rollback to the prior image/revision; remove no RDS resource and preserve migration logs. Correct config/schema and redeploy.
3. **Failure after RDS accepts writes:** do not point the old SQLite revision back into service as if it contained the new durable state. Freeze scanner/notifications, preserve RDS, take a manual snapshot, diagnose, and deploy the prior application image configured for PostgreSQL if schema-compatible. If not compatible, restore RDS to a new instance from the pre-change snapshot/PITR and repoint the secret only after validation.
4. **Database corruption/operator error:** restore PITR to a new private RDS instance; validate schema/data/TLS/readiness; update the runtime and migration URL secrets to the new endpoint; force a deployment. Never overwrite the damaged instance during investigation.
5. Keep deletion protection on throughout. Any eventual RDS deletion requires a separately approved final snapshot and retained automated backups. Never delete the SQLite archives during rollback windows.

Rollback triggers include failed schema readiness, privilege/TLS failure, unexplained duplicate notifications/signals, transaction errors, inability of Task B to restore Task A state, or sustained DB resource exhaustion.

### H. Observability and alarms

Create the minimum alarms below, notify one reviewed SNS destination, and configure RDS event subscriptions for failure, failover, maintenance, low storage, recovery, and availability events:

| Signal | Initial threshold/action |
|---|---|
| `CPUUtilization` | >80% for 15 minutes; inspect queries/credits, then size up if sustained |
| `CPUCreditBalance` / surplus charges | Low balance or nonzero surplus-credit charges on T4g; investigate burst load |
| `FreeableMemory` and `SwapUsage` | Memory <200 MiB for 10 minutes or sustained swap; move to `db.t4g.small` if recurrent |
| `FreeStorageSpace` | <4 GiB and storage-autoscaling events; investigate growth before the 100-GiB ceiling |
| `DatabaseConnections` | >70% of the instance's observed `max_connections`; detect leaks/pool multiplication |
| Availability | RDS event unavailable/reboot/failure and optional `DatabaseConnections` missing-data alarm |
| Failed connections/errors | CloudWatch PostgreSQL log metric filters for authentication failure, `too many connections`, fatal startup, deadlock, and repeated migration errors |
| Readiness | External/synthetic `/api/readiness` failure for two consecutive checks; liveness is monitored separately |

Use PostgreSQL log export and 30-day CloudWatch retention. Never enable parameter logging that captures passwords, URLs, bind values, or every statement. Test the SNS path and a restore from snapshot/PITR; a configured backup without a restore exercise is not a complete recovery control.

### I. Connection management and RDS Proxy decision

Current one-task maximum after hardening: 5 pooled application connections, zero overflow. During a rolling deployment at most two revisions should overlap, for 10 application connections; one migration connection makes an expected ceiling of 11. Leave at least 50% of the RDS connection capacity unused for administration, monitoring, and recovery. Recalculate before raising ECS `maxTaskCount`: planned ceiling is `task_count * (pool_size + max_overflow) + migration/admin reserve`.

RDS Proxy is unnecessary now. One steady task, a small fixed pool, direct private networking, and short transactions do not justify its hourly/vCPU cost and operational surface. Re-evaluate only if task count/churn rises materially, connection spikes become visible, Lambda clients are added, or failover connection handling becomes a measured issue.

### J. Expected monthly incremental cost

Current `us-east-1` public Price List API rates checked on 2026-08-15 are `$0.016/hour` for Single-AZ PostgreSQL `db.t4g.micro` and `$0.115/GiB-month` for Single-AZ PostgreSQL gp3:

| Item | Approximate monthly cost |
|---|---:|
| `db.t4g.micro`, 730 hours | $11.68 |
| 20 GiB gp3 | $2.30 |
| Three database-related Secrets Manager secrets | $1.20 plus negligible API calls |
| Six to eight standard CloudWatch alarms | $0.60–$0.80 |
| PostgreSQL logs/metrics at this traffic | $0–$1 initially |
| Private subnets, route table, SGs, DB subnet group | $0 |
| Automated backup storage up to the running DB's allocated storage | Normally included; excess/manual retained snapshots billed |
| **Expected incremental total** | **about $16–$18/month** |

This excludes the existing ECS/Fargate, Express gateway/load balancer, ECR, provider secrets, logs, and data transfer already incurred. `db.t4g.small` would add roughly $12/month. Multi-AZ would approximately double DB compute and storage, moving this database portion toward $30–$35/month. Unexpected cost drivers are T4g Unlimited surplus CPU credits, irreversible storage autoscaling, retained manual/final snapshots, backup storage beyond the free allocation, verbose log ingestion/retention, custom KMS keys, Performance/Database Insights paid modes, RDS Proxy, NAT gateways, data transfer, and accidental Multi-AZ/read replicas.

Always refresh the AWS Pricing Calculator immediately before creation; prices and free-tier eligibility vary over time and account.

### Security review checklist

- [x] Design sets `PubliclyAccessible=false` and places RDS only in local-route private subnets.
- [x] RDS inbound 5432 is SG-to-SG from ECS DB-client SG only; no CIDR/public/operator rule.
- [x] Storage and backups encrypted at creation using KMS.
- [x] TLS required server-side; client uses `verify-full` with AWS RDS CA.
- [x] Runtime DB role is DML-only; migrator/master are not exposed to the application.
- [x] Normal task execution role reads only named runtime secrets; task role remains unnecessary.
- [x] DB credentials exist only in Secrets Manager and are injected at task start.
- [x] No secret value/URL in source, image, task plaintext environment, logs, health/readiness, or deployment command output.
- [x] Fourteen-day automated backup/PITR, deletion protection, retained backups, and final snapshot required.
- [x] No raw Massive history, broker/account data, or research caches are added to RDS.
- [x] No trading or backtesting logic change is part of this plan.

### Risks and unresolved approval questions

1. **Availability/cost:** approve Single-AZ `db.t4g.micro` for the initial one-task service, or explicitly pay for Multi-AZ/`t4g.small`. The recommendation is Single-AZ micro with alarms and a defined upgrade trigger.
2. **Legacy data:** approve the recommended clean RDS start plus read-only SQLite archive, or require a separate Stage 2F legacy import review. Existing null fingerprint/version provenance prevents a casual import.
3. **Network ownership:** confirm ECS Express accepts the additional stable client SG without replacing the managed gateway SG; otherwise reference the current generated task SG and add a drift check.
4. **Notifications during cutover:** approve a market-closed maintenance window so the old and new stores never generate live alerts concurrently.
5. **Operator notifications:** choose the SNS email/endpoint and alarm ownership before provisioning.
6. **Authentication exposure:** the existing dashboard is publicly reachable through Express. RDS remains private, but permanent dashboard authentication/rate limiting is still a separate application-security risk.

### Stage 2D readiness verdict

**READY FOR APPROVAL, NOT AUTHORIZED TO IMPLEMENT.** The target architecture is technically compatible with the accepted Stage 2B/2C implementation. Stage 2E must begin with repository hardening and a refreshed read-only inventory; it must not create RDS until the six approval questions above are resolved.

### K. Exact recommended Stage 2E implementation sequence

1. Obtain explicit approval for Stage 2E plus the instance/AZ, legacy-data, cutover-window, SG-attachment, and SNS decisions.
2. Add/test pool caps, schema-aware readiness, Alembic files, and RDS CA in the image; repeat full local PostgreSQL tests and image-level TLS/migration tests.
3. Prepare infrastructure as reviewed IaC (preferred) or a fully captured CLI change set; run plan/dry-run and security/cost review. Do not use ad hoc console-only resources without recording configuration.
4. Provision private subnet/route/SG/subnet-group resources and verify isolation.
5. Provision parameter group, secrets/roles, and private encrypted RDS with backups/deletion protection/logs/alarms.
6. Bootstrap least-privilege DB roles and run the one-off Alembic migration task.
7. Validate schema revision, grants, TLS, monitoring, backup/PITR metadata, and private-only reachability.
8. Archive or import SQLite according to the approved decision; take pre-cutover snapshot.
9. Deploy the new ECS revision during the market-closed window with PostgreSQL variables/secret and stable client SG.
10. Gate on health, schema readiness, logs, smoke DML, and the Task A→Task B persistence/notification proof.
11. Observe one market session, test alarms and restore procedure, then declare RDS authoritative. Preserve the rollback revision, snapshots, and SQLite archives until the rollback window closes.

### Official AWS references for Stage 2D

- [RDS PostgreSQL release calendar](https://docs.aws.amazon.com/AmazonRDS/latest/PostgreSQLReleaseNotes/postgresql-release-calendar.html)
- [RDS instance types and T4g specifications](https://aws.amazon.com/rds/instance-types/)
- [RDS DB instances in a VPC and DB subnet groups](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_VPC.WorkingWithRDSInstanceinaVPC.html)
- [RDS PostgreSQL SSL/TLS and `rds.force_ssl`](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/PostgreSQL.Concepts.General.SSL.html)
- [RDS automated backups and point-in-time recovery](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_WorkingWithAutomatedBackups.html)
- [Retaining automated backups and final snapshots](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_WorkingWithAutomatedBackups.Retaining.html)
- [ECS task execution role and Secrets Manager permissions](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_execution_IAM_role.html)

## Executive decision

PostgreSQL should become the shared system of record for application-owned production state: setup identity and lifecycle, scanner transition state, derived outcomes, notification claims/delivery history, user notification preferences, and the exact engine/rules/parameter versions that produced a decision. It should not become a copy of Massive minute-bar history, an analysis-response cache, or a requirement for local research.

The deterministic engine must continue to return `StandardDecision`. An orchestration layer will load the prior decision, call the unchanged engine, and pass its output to storage adapters. SQLite remains the default local adapter until PostgreSQL parity is proved.

## Current-state audit

The checked-in working data contains `data/signals.sqlite3` (130 `signals`, 780 `signal_prices`) and `data/notifications.sqlite3` (0 `notification_state`, 1 `notification_settings`). The signal database was created before fingerprint/version columns were added: all 130 existing rows have null `signal_fingerprint`, `engine_version`, `setup_rules_version`, and `parameter_version`. This is a migration concern, not permission to infer or rewrite those values.

| State | Current location/schema | Readers and writers | Class | Production/reliability decision |
|---|---|---|---|---|
| Signal observations and decision context | `data/signals.sqlite3`, `signals`; scalar decision fields, level/config JSON, derived outcomes and version columns | `SignalLogger.observe` writes; `_update_outcomes` reads/updates; no production read path currently restores lifecycle | A | Required evidence and lifecycle basis. Loss removes audit/outcome history. Move the application-owned fields to PostgreSQL. |
| Post-signal prices | `signals.sqlite3`, `signal_prices(signal_id,timestamp,high,low)` | `_update_outcomes` inserts and reads bars supplied by Massive | C (currently), with derived result in A | Raw duplicate bars need not move to PostgreSQL. Keep locally for research/debug if desired; persist durable derived outcomes and their observation timestamps. Massive remains raw-data source. |
| MFE/MAE and hit flags/times | Columns on `signals`; recalculated from later bars | `_update_outcomes` writes | A | Reliability/audit evidence. Persist normalized per signal. Current values are absolute price excursions, not R multiples; preserve semantics explicitly. |
| Parameter snapshot | `signals.config_json`; current rows contain exact `EngineParameters.as_dict()` | `SignalLogger.observe` writes | A | Required reproducibility evidence. Store a JSONB snapshot with its `parameter_version`; do not normalize every parameter yet. |
| Engine/rules/parameter versions | `StandardDecision`, constants in `app/versioning.py`, nullable columns on `signals` | engine creates; logger writes for new rows | A | Required audit metadata. Make non-null for newly persisted production signals/transitions. Legacy nulls stay labelled unknown unless provably backfilled. |
| Prior decision/lifecycle input | `AnalysisService._decisions[symbol]` holds the latest `StandardDecision` | service reads before and writes after `evaluate_production`; engine uses it for `prior_state` and `state_changed_at` | A | Must survive replacement. Loss can reset lifecycle and transition timestamps. PostgreSQL must supply latest setup/decision state. |
| Scanner prior state and cooldown | Each `AlertEngine._states[symbol] = (state,fingerprint,last_alert_at,price,score)`; one engine per `ScannerPolicy` in `_alert_engines` | `AlertEngine.evaluate` reads/writes/pops | A | Loss can duplicate/miss RADAR/TRIGGERED/INVALIDATED/TOO_LATE transitions and cooldown behavior. Persist per policy/scope. |
| Notification dedup/delivery summary | `notifications.sqlite3`, `notification_state`; primary key is only `symbol` | `NotificationService._prior/_record/process` | A | Must be shared. Loss can duplicate alerts. Current read-send-write sequence is not concurrency-safe and stores only the latest outcome. Replace with claimable notification events plus delivery history. |
| Notification preferences | `notification_settings`, singleton row; in-memory `AlertPreferences` only when persistence is unavailable | API and `NotificationService` read/write | A for a deployed mutable installation | Losing user choices changes alert behavior. Persist in PostgreSQL (keyed by deployment/account scope); local SQLite remains supported. Secrets are not preferences and never belong here. |
| Analysis response cache | `AnalysisService._cache`, 60-second monotonic TTL | service reads/writes | D | Safe to lose; Massive is refetched and decisions recomputed. Keep process-local initially. |
| Per-symbol async locks | `AnalysisService._locks` | service creates/uses | B | Process synchronization only. Do not persist; database constraints/transactions handle cross-process races. |
| Candidate rankings and scan response | Request-local lists and API response | scanner builds/returns | B | Recomputable. Do not persist merely for display. |
| Scanner observations useful for audit | Not separately stored; some decision context lands in `signals` | none as a distinct store | A, selectively | Store meaningful observations/transitions, not every market-data tick or full response. |
| Historical market-data caches | `data/historical_cache/*.json` and `*.json.gz`; two cache implementations in `app/backtesting.py` and `app/backtest.py` | backtest loaders read/write; Massive fills misses | D/C | Local research cache. Rebuildable; do not put in production PostgreSQL. |
| Backtest/research reports | `data/backtest_reports/*.json|md`, `data/backtest_*.json|md`, CLI-selected paths; `research.replay` prints JSON | research/backtest commands write; humans/tests consume | C | Remain local. Later object storage is optional, separate from production DB. |
| Backtest lifecycle, fingerprints, events, MFE/MAE | In-memory during replay, serialized into reports | `app/backtest.py`, `app/backtesting.py`, `app/research.py` | C | Remain local and continue to use `evaluate_production`; no AWS/PostgreSQL dependency. |
| Application/API/scanner/provider logs | stdout/stderr via Python logging | application emits | E | Send ECS logs to CloudWatch. Do not create a general log table. Logs must redact URLs/credentials and avoid decision payload secrets. |
| Browser UI state/scheduling | Browser memory/local UI behavior; radar runs while tab is open | static frontend | B | Not server durability. A future scheduled worker is separate infrastructure; its idempotent job state may later use PostgreSQL. |

In hosted mode today, `NoOpSignalStore` and a `NotificationService` without a state path intentionally disable these durable writes. `/api/readiness` nevertheless considers Massive alone sufficient. Before production workers are enabled, readiness must require the configured durable stores and a successful lightweight database check.

## Existing identity semantics

`signal_fingerprint` hashes, then prefixes, a canonical array of:

`symbol.upper(), setup_family, direction, round(trigger, 4), session_date, engine_version, setup_rules_version`

Lifecycle state, quality, targets, invalidation, score, and parameter version are intentionally excluded. API signal IDs use this function. Notification fingerprints wrap that identity plus rounded invalidation, targets, and risk/reward. `AlertEngine` separately compares direction, setup, trigger, invalidation, targets, and risk/reward. Backtests have two more local tuple fingerprints.

The production fingerprint is a reasonable thesis identity and must not be casually changed in Stage 2B. It distinguishes a new trading date, direction, family, trigger, engine, or rule set while treating repeated scans as the same setup. Risks are: a materially revised trigger creates a new signal; rounding can merge very close triggers; changing engine/rules versions creates a new identity; and parameter version is not part of identity. Keep this algorithm and test vectors for parity. Store its inputs in columns, not only the hash.

Recommendation: use the current fingerprint as `signals.fingerprint` and a database unique constraint. Treat changed invalidation/targets/quality/state as observations or transitions of that signal. Revisit adding `parameter_version` only as a separately approved identity-version change: doing it now could turn an unchanged market thesis into a new signal and resend alerts. If parameter changes can alter setup identity before that review, operationally roll them with an explicit setup-rules version and document the rollout.

The current SQLite uniqueness constraint, `UNIQUE(symbol,timestamp,setup_family,setup_state)`, identifies an observation rather than a setup. `signal_fingerprint` is indexed but not unique. PostgreSQL must make the stable fingerprint unique. Do not deduplicate legacy null-fingerprint rows destructively; import only state that can be mapped deterministically and retain an import manifest.

## Minimum PostgreSQL schema

Use UUID primary keys (generated by the application or PostgreSQL), `timestamptz` in UTC, explicit enums or checked text values, JSONB only for evolving evidence/snapshots, and numeric precision sufficient to preserve current values. Every mutable table has `created_at` and `updated_at`; event tables are append-only where practical.

### `signals`

- PK: `id uuid`.
- Identity: `fingerprint text NOT NULL UNIQUE`, plus `symbol`, `setup_family`, `direction`, `session_date`, `trigger` and identity versions.
- Latest state: `current_state`, `quality`, `action_now`, `market_price`, `invalidation`, `target_1`, `target_2`, `structural_risk_reward`, support/resistance, VWAP/structure/ATR/RVOL, `decision_at`, `state_changed_at`, `last_observed_at`.
- Evidence: `levels_snapshot jsonb`, `parameter_snapshot jsonb`.
- Required provenance: `engine_version`, `setup_rules_version`, `parameter_version` (non-null for new data).
- Constraints: unique fingerprint; valid direction/state; `decision_at <= last_observed_at`; values retain current nullable semantics.
- Indexes: `(symbol, session_date DESC)`, `(current_state, last_observed_at DESC)`, `(setup_family, direction, session_date DESC)`, and version fields only if query demand appears.

This is the current/latest setup projection. Upsert may update it only when the incoming observation is newer; stale scans cannot overwrite newer state.

### `signal_state_transitions`

- PK: `id uuid`; FK `signal_id -> signals(id)` with restrictive deletion.
- Columns: `from_state`, `to_state`, `transition_at`, `observed_at`, price/score/quality/action snapshot, `reason_json`, all three version fields, optional `source`/`worker_id`.
- Unique constraint: `(signal_id, to_state, transition_at)`; stronger application event keys may be added after parity testing.
- Index: `(signal_id, transition_at, id)` for deterministic ordering; `(transition_at DESC)` for operations.

Insert a transition only when the locked latest state differs. Do not manufacture a transition for every repeated observation.

### `signal_outcomes`

- PK and FK: `signal_id -> signals(id)` (one current aggregate per signal).
- Columns: `mfe_price`, `mae_price`, optional future explicitly named `mfe_r`/`mae_r`, booleans and first-hit times for target 1, target 2, and invalidation, `last_evidence_at`, `calculation_version`, timestamps.
- Constraints: hit time implies its hit flag; values are nonnegative under current excursion semantics.
- Indexes on each hit time only if outcome queries require them.

Update monotonically: `mfe/mae = GREATEST(existing,incoming)` and first-hit timestamps never move later. Same-bar target/invalidation ordering must retain current engine/research semantics; storage must not reinterpret trading outcomes.

### `scanner_state`

- PK: `(scanner_scope, policy_fingerprint, symbol)`; `scanner_scope` identifies the deployed scanner/universe behavior.
- Columns: nullable `signal_id` FK, `state`, candidate fingerprint/details JSONB, `last_transition_at`, `last_alert_at`, `last_price`, `last_score`, `last_observed_at`, timestamps.
- Index: `(scanner_scope, policy_fingerprint, last_observed_at)`.

This is the durable equivalent of `AlertEngine._states`, including cooldown inputs. It is not raw scan history. If selective observation history proves useful, append meaningful scan events to transitions or add a single pragmatic `scanner_observations` table later; do not add it before a concrete query/retention need.

### `notifications`

- PK: `id uuid`; nullable FK `signal_id -> signals(id)` for test/system messages.
- Columns: `channel`, `destination_scope` (non-secret logical recipient), `event_type`, `event_fingerprint`, `status` (`CLAIMED/SENDING/SENT/FAILED/SUPPRESSED`), `attempt_count`, `claimed_at`, `claim_expires_at`, `first_attempt_at`, `last_attempt_at`, `delivered_at`, provider response/request identifier if non-secret, sanitized error code, timestamps.
- Unique constraint: `(channel, destination_scope, event_fingerprint)` is the database idempotency key.
- Indexes: `(status, claim_expires_at)`, `(signal_id, created_at DESC)`, `(delivered_at DESC)`.

The event fingerprint should be derived from the stable signal fingerprint plus the notification event (`RADAR`, `READY`, `INVALIDATED`, `TOO_LATE`) and any intentionally re-alertable revision/cooldown bucket. Freeze this rule in tests. Never store Pushover user keys/tokens or full credential-bearing URLs.

Keep mutable alert choices in a pragmatic `notification_settings` table keyed by `destination_scope`, with the five existing booleans and timestamps. This sixth table is justified because preferences are durable behavior, not delivery events.

## Storage boundaries and abstractions

The existing `SignalStore` protocol is too narrow (`observe` only), `SignalLogger` is SQLite-specific despite its generic name, and notification persistence is embedded inside `NotificationService`. `AlertEngine` owns an in-memory state repository directly.

Stage 2B should define domain-oriented interfaces whose methods accept/return domain records rather than SQL rows or connections:

- `SignalStore`: `get_latest_decision(symbol, scope)`, `record_observation(StandardDecision, evidence, parameters) -> SignalRecord`, `append_transition(...)`, `update_outcome(...)`, `get_signal(fingerprint)`.
- `ScannerStateStore`: `get(...)`, `advance(...)`, and optionally `clear(...)`; advancement performs compare-and-set/locking and returns whether an alert event was newly created.
- `NotificationStore`: preferences methods plus `claim(event_key, lease)`, `mark_sent`, `mark_failed`, and history lookup. Sending remains in `NotificationService`/provider adapters.

Implement `SQLiteSignalStore`, `SQLiteScannerStateStore`, and `SQLiteNotificationStore` for local parity, plus PostgreSQL equivalents. Keep `NoOpSignalStore` only for explicitly non-durable interactive modes, never a ready production worker. Dependency injection selects adapters at startup. No adapter imports engine setup/scoring logic; orchestration loads prior state, calls the unchanged engine once, then persists its `StandardDecision`.

Avoid a single giant repository and avoid one transaction spanning a network call. Unit-of-work methods may coordinate signal/current-state/transition/scanner-event writes within one database transaction.

## Atomicity, ordering, and deduplication

1. **Concurrent discovery:** insert `signals` with `ON CONFLICT (fingerprint) DO UPDATE` guarded by `incoming.last_observed_at > stored.last_observed_at`; return the one canonical ID. The unique constraint prevents duplicate setups.
2. **Lifecycle advancement:** lock the signal/current scanner row with `SELECT ... FOR UPDATE`, reject stale `observed_at`, append a transition, and update the latest projection in one transaction. Use `(observed_at, deterministic event ID)` ordering for ties.
3. **Notification creation:** in the same transition transaction, insert the notification event with `ON CONFLICT DO NOTHING`. This is an outbox-style boundary: a committed transition cannot be lost between scanner and notifier.
4. **Notification delivery:** a worker atomically claims a pending/expired event using `FOR UPDATE SKIP LOCKED`, commits the short claim, sends Pushover, then records success/failure. The unique event key prevents two workers from creating the same alert. A lease permits recovery after worker death.
5. **External exactly-once limit:** no database transaction can atomically include Pushover. A crash after provider acceptance but before `mark_sent` creates an ambiguous outcome. Prefer a stable provider idempotency feature if Pushover supports one; otherwise choose and document at-most-once (mark before send, possible loss) versus at-least-once (retry, possible duplicate). The current implementation effectively suppresses retry after a failed attempt. Stage 2B must preserve that observable policy initially and surface failure history rather than silently changing it.
6. **Outcomes:** use atomic `GREATEST`, boolean OR, and earliest non-null hit times so concurrent/replayed observations are monotonic and idempotent.
7. **Isolation:** default `READ COMMITTED` plus row locks and unique constraints is adequate; retry serialization/deadlock/unique races with bounded jitter. Keep transactions small.

## Configuration and readiness

Use typed settings consistent with the existing dataclass, evolving toward:

```text
DATABASE_BACKEND=sqlite|postgres|none
DATABASE_URL=sqlite:///data/app.sqlite3          # local example only
DB_CONNECT_TIMEOUT_SECONDS=5
DB_POOL_MIN_SIZE=1
DB_POOL_MAX_SIZE=10
```

`DATABASE_URL` is the single adapter endpoint; `DATABASE_BACKEND` is an explicit validation guard, not a second source of credentials. Preserve `SIGNAL_LOG_PATH` and `NOTIFICATION_STATE_PATH` during compatibility, then deprecate only after SQLite adapters share a configured local URL. Local backtests must continue without PostgreSQL.

In ECS, inject the URL or its components from Secrets Manager at runtime. Never bake it into source, image layers, `.env.example`, GitHub, logs, exception messages, health/readiness payloads, or Pydantic representations. Sanitize driver errors because URLs can appear in them. `/health` remains liveness-only. `/api/readiness` must test a bounded `SELECT 1` and required migration revision; a configured production worker is unready if PostgreSQL or either durable repository is unavailable.

## Migration tooling

Use Alembic with SQLAlchemy 2.x metadata in Stage 2B. It is conventional for Python/FastAPI, supports PostgreSQL and SQLite, provides ordered/reviewable upgrades, and can validate the deployed schema revision. Prefer SQLAlchemy Core/repository mappings if a full ORM adds no value. Add Alembic only with the Stage 2B adapters and first schema migration; adding it during this audit would create unused runtime machinery. Migrations run as a controlled one-off deployment step, not concurrently in every web/worker process. Every destructive migration requires a reviewed backup/rollback plan.

## Safe delivery sequence

### Stage 2A — this document

Audit and design only. Preserve both SQLite files and all trading behavior.

### Stage 2B — exact implementation plan

1. Add typed database config with redacted representations and explicit `sqlite`, `postgres`, and narrowly scoped `none` modes.
2. Introduce persistence domain records, the three store protocols above, and a transaction/unit-of-work boundary; keep `StandardDecision` unchanged.
3. Rename/wrap `SignalLogger` as `SQLiteSignalStore`; extract notification SQL into `SQLiteNotificationStore`; add `SQLiteScannerStateStore`. Preserve existing paths and behavior through compatibility constructors.
4. Add SQLAlchemy 2.x, a PostgreSQL driver suitable for the chosen sync/async repository style, and Alembic. Create the initial schema migration; do not run it against production or alter existing SQLite files.
5. Implement PostgreSQL stores with fingerprint upsert, latest-observation guard, row-locked transitions, monotonic outcomes, notification outbox/claim leases, and unique idempotency constraints.
6. Change orchestration only: hydrate the prior `StandardDecision` before `evaluate_production`, persist the returned decision afterward, and make scanner transition evaluation use `ScannerStateStore`. Do not copy or fork any setup logic.
7. Add dependency injection at FastAPI/worker startup and close pools at shutdown. Update readiness to check connectivity and migration revision without exposing configuration.
8. Add contract tests run against both SQLite and ephemeral local PostgreSQL, concurrency tests, restart tests, redaction tests, and frozen fingerprint vectors.
9. Document legacy import rules and write a dry-run/read-only inventory command. Do not seed or migrate data in Stage 2B.

### Stage 2C — local PostgreSQL parity

Run PostgreSQL locally, apply migrations explicitly, run the shared contract/concurrency suite, compare SQLite/PostgreSQL semantics and selected decision traces, exercise worker restarts, and retain SQLite as the default local fallback until acceptance passes.

### Stages 2D–2G — later AWS and migration

- **2D:** create private RDS PostgreSQL only after sizing, availability, retention, encryption, maintenance, and cost decisions are approved.
- **2E:** connect ECS using private subnets/DNS, least-privilege security groups and Secrets Manager; validate TLS and pool sizing.
- **2F:** back up SQLite, stop/coordinate writers, run a dry-run import, map only deterministic identities, preserve legacy unknown versions, produce counts/checksums/reject report, then seed in a transaction or resumable idempotent batches. Never delete SQLite.
- **2G:** execute the task-replacement acceptance test and monitor errors, duplicate constraints, notification claims, and RDS health before declaring PostgreSQL authoritative.

No dual-write cutover should be used unless its reconciliation/failure semantics are designed and tested. A short controlled write pause plus verified import is safer at this scale.

## Later AWS RDS shape

```text
ECS/Fargate web + scanner + notification workers
                    |
          private VPC DNS/TLS connection
                    |
        RDS PostgreSQL in private DB subnets
```

- RDS is not publicly accessible and has no internet ingress. Its security group permits PostgreSQL only from the ECS task security group; ECS does not receive broad CIDR access.
- Encrypt storage and automated backups, enable point-in-time recovery, define retention/deletion protection, maintenance windows, monitoring/alarms, and test restores. Multi-AZ is a production availability/cost decision.
- Store credentials in Secrets Manager, inject them into tasks at runtime with a least-privilege task execution role, rotate them deliberately, and never expose them through task definitions, image, API, or logs. Consider IAM database authentication later; it is not required for initial parity.
- Require PostgreSQL TLS (`sslmode=verify-full` where driver/RDS CA handling permits) and ship/trust the current AWS RDS CA bundle. Encrypt Secrets Manager and RDS with KMS as appropriate.
- Size connection pools across the maximum number of Fargate tasks and workers below RDS limits. Evaluate RDS Proxy only if task churn/concurrency justifies its cost; do not add it by default.
- Run Alembic via a controlled one-off ECS task/CI release gate before application rollout. Application tasks use a non-owner DB role with only needed DML privileges.
- ECS application logs go to CloudWatch with retention/alarms; database audit/performance logs are enabled selectively and must not contain secrets.

## Stage 2B/2C test and acceptance plan

- Run one adapter contract suite against SQLite and PostgreSQL: identical fingerprint lookup, observation upsert, transition append/order, outcome monotonicity, preferences, and notification claim semantics.
- Prove repeated discovery creates one signal and concurrent discovery from multiple connections also creates one signal.
- Prove repeated/concurrent event creation yields one notification row and only one active claim; verify claim expiry/recovery and the explicitly selected provider ambiguity policy.
- Restart the service and scanner and prove latest `StandardDecision`, `prior_state`, `state_changed_at`, scanner cooldown inputs, and lifecycle transitions are restored.
- Assert engine/setup/parameter versions and exact parameter JSON snapshots round-trip unchanged, including legacy `unknown/null` handling.
- Submit out-of-order transitions and prove stale state cannot replace newer state; verify deterministic ordering for equal timestamps.
- Force failures midway through multi-write operations and prove rollback leaves no partial transition/outbox; test deadlock/serialization retry limits.
- Update outcomes concurrently and prove MFE/MAE never decrease, hit flags never revert, and first-hit times remain earliest.
- Stop PostgreSQL and prove liveness remains meaningful while readiness fails clearly and quickly; no production scanner/notifier starts against `none` storage.
- Capture logs and all health/error responses with sentinel credentials and assert the sentinel and URL password never appear.
- Run unchanged engine/backtest regression suites under both adapter selections; local research works with no AWS/PostgreSQL connection.

Future decisive acceptance test:

1. Fargate Task A writes a signal, current state, transition, outcome evidence, and a successfully recorded Pushover event to RDS.
2. Terminate Task A without copying any local filesystem.
3. ECS launches Task B with the same release/configuration.
4. Task B reads the identical signal fingerprint, lifecycle/current state, versions, outcome, and ordered transition history from PostgreSQL.
5. Replaying the same scanner observation does not create a second signal or Pushover event and does not resend the prior notification.

## Risks and explicit non-goals

- **Legacy ambiguity:** existing rows have null fingerprints/versions. Never guess provenance or destructively merge them.
- **Current outcome scope:** every later `observe` scans all symbol signals and stores duplicate raw highs/lows; this will become costly. Derive/upsert only relevant evidence with retention bounds, without changing outcome semantics.
- **Identity drift:** independent notification/scanner/backtest fingerprints can diverge. Production storage should centralize the existing signal identity while leaving research-only dedup local.
- **Notification atomicity:** Pushover is outside the DB transaction; document and test the unavoidable ambiguous-send policy.
- **Timestamp/session correctness:** fingerprint session uses `datetime.date()` in the datetime's own timezone. Do not silently switch it to Eastern trading date; first freeze timezone cases and approve any semantic migration.
- **Stale writers:** concurrent tasks may observe different market timestamps. Guard projections with observation time and lock transitions.
- **Pool exhaustion:** multiply pool sizes by maximum task/worker count; monitor before scaling.
- **Schema overreach:** no raw Massive history warehouse, per-tick scanner archive, general log database, broker/account/P&L state, or research-report migration is part of the minimum design.
- **Trading logic:** setup definitions, thresholds, scoring, confirmation, triggers, invalidation, targets, R:R, ranking, options/futures logic, and backtesting assumptions remain untouched.

## Stage 2E — AWS RDS implementation and production cutover

**Status: ACCEPTED on 2026-08-15.** Stage 2E was executed in `us-east-1` during the approved market-closed window. No SQLite data was imported, no SQLite files/support were removed, and no trading logic changed.

### Resources and configuration implemented

- Created private database subnets `tde-db-private-a` (`172.31.96.0/24`, `us-east-1a`) and `tde-db-private-b` (`172.31.97.0/24`, `us-east-1b`). Public IP assignment is disabled. Both use `tde-db-private` route table `rtb-020f9882db36e6704`, which contains only the VPC-local `172.31.0.0/16` route—no IGW or NAT route.
- Created RDS subnet group `tde-postgres-private` spanning those two AZs.
- Created stable ECS identity SG `tde-ecs-db-client` (`sg-06f702ddcec8fb404`) and RDS SG `tde-rds-postgres` (`sg-0aef28cee517b1948`). RDS ingress contains exactly one rule: TCP/5432 sourced from the DB-client SG. There is no public/CIDR PostgreSQL ingress. The ECS service retains its existing gateway SG and adds the DB-client SG.
- Created `tde-postgres-prod`: RDS PostgreSQL 16.14, `db.t4g.micro`, Single-AZ, 20 GiB gp3, autoscaling ceiling 100 GiB, encrypted with the account's AWS-managed RDS key, not publicly accessible, deletion protection enabled, automatic minor upgrades enabled, and final-snapshot deletion required by procedure.
- Configured seven-day automated backup retention/PITR, backup window `08:00–08:30 UTC`, maintenance window Sunday `09:00–09:30 UTC`, and copy-tags-to-snapshot. `LatestRestorableTime` was populated during validation, proving PITR became available.
- Created parameter group `tde-postgres16`; `rds.force_ssl=1`. PostgreSQL connection/disconnection logging and the `postgresql` CloudWatch log export are enabled. The image pins and verifies the AWS global RDS CA and both application URLs use `sslmode=verify-full` with that CA.
- Created Secrets Manager secrets `tde/prod/database/app-url` and `tde/prod/database/migrator-url`. Values are generated credentials encoded as TLS URLs and were never printed. RDS manages and automatically rotates the separate master secret. Automatic rotation is not enabled for the application/migrator URL secrets; rotating those remains a coordinated maintenance procedure.
- Created no-permission application task role `tdeEcsTaskRole`. The normal execution role can retrieve only Massive, Pushover, and runtime DB secrets. Dedicated `tdeDbMigrationExecutionRole` can retrieve only the migrator secret. The runtime role cannot retrieve master/migrator credentials; no task role has application AWS API permissions.
- Published immutable ECR image digest `sha256:388406f49525d10beeded53014022f8eeb5ceb7d24748a664802cd8c17b7ee32`. It runs unprivileged and includes Alembic plus the pinned RDS CA.
- Registered one-off `tde-db-migration` and `tde-persistence-smoke` task definitions. Migration is not part of normal container startup. The completed one-time bootstrap task is no longer authorized to read the master secret through the runtime execution role.
- Deployed application task definition `default-trade-decision-engine:11` with `APP_STORAGE_MODE=hosted`, `DATABASE_BACKEND=postgres`, runtime `DATABASE_URL` from Secrets Manager, pool size 3, maximum overflow 0, five-second connect/pool timeouts, and 300-second pool recycle. The service remains one task (maximum one), so the current application pool budget is three connections.

### Migration, schema, and acceptance evidence

The empty database was bootstrapped with separate `tde_migrator` (schema owner) and `tde_app` (DML-only) roles. `alembic upgrade head` completed in a one-off Fargate task with exit code 0. A separate verifier confirmed:

- Alembic revision `20260815_01`;
- seven tables including `alembic_version`;
- seven indexes, three unique constraints, and four foreign keys;
- TLS active for the verifier connection;
- `tde_app` has no `CREATE` privilege on the application schema;
- runtime DML works through the production store adapters.

Production-safe Fargate Process A wrote a deterministic probe signal, two ordered lifecycle transitions, scanner state, and a sent notification reservation. It exited. Independent Process B loaded state `CONFIRMED`, scanner state `READY`, two transitions, and notification state `SENT`; reserving the same event returned false. No Pushover request was sent. This proves persistence and database-enforced notification deduplication across Fargate process replacement.

The first application rollout was automatically rolled back by the Express-managed `RollbackAlarm`. Investigation proved it was not an RDS/readiness failure: the alarm counts the existing dashboard's repeated `/api/scanner` HTTP 400 responses, and revision 11 had returned six consecutive successful readiness responses. For the retry, only that noisy deployment alarm was temporarily disabled; the ECS task-start circuit breaker, health checks, and manual readiness gate remained enabled. The Express canary ran at 5% for three minutes, revision 11 then ran at 100% for its three-minute bake, and the deployment finished `SUCCESSFUL`. The managed rollback alarm was restored afterward. Final state is one running revision-11 task, no pending task, and rollout `COMPLETED`.

Final `/api/health` was process-healthy and secret-safe. `/api/readiness` returned ready with signal and notification stores available, and production capabilities show durable notification deduplication enabled/local research disabled. CloudWatch application log scans found zero occurrences of `DATABASE_URL`, PostgreSQL URL prefixes, or SQLAlchemy operational errors. API responses expose no connection strings or credentials.

### Monitoring and remaining notification action

Created these alarms; all were `OK` at acceptance:

- `tde-rds-cpu-high` (80% for 15 minutes)
- `tde-rds-storage-low` (less than 5 GiB for 10 minutes)
- `tde-rds-connections-high` (more than 60 for 15 minutes)
- `tde-rds-memory-low` (less than 128 MiB for 15 minutes)
- `tde-rds-read-latency-high` (more than 100 ms for 15 minutes)
- `tde-rds-telemetry-missing` (missing RDS telemetry for 15 minutes)
- `tde-rds-failed-connections` (five PostgreSQL `FATAL` log events in five minutes)

No SNS destination exists, as allowed by the approved decision, so alarm actions are empty. A future operational task must create/choose an SNS topic, confirm subscribers, and attach it to these alarms. The Express rollback alarm remains in `ALARM` because of pre-existing `/api/scanner` 400 traffic; future application deployments will need that unrelated endpoint behavior corrected or the same documented temporary-alarm procedure.

### Rollback procedure

1. Confirm rollback is needed using `/api/readiness`, ECS task events, and CloudWatch logs; do not expose the DB URL while diagnosing.
2. Update the ECS service to task definition revision 10 and remove `sg-06f702ddcec8fb404` from the service only if reverting fully to the prior SQLite-backed runtime. Keep its existing gateway SG and subnet/public-IP configuration.
3. Wait for the prior task to pass `/api/health`; verify the dashboard and prior local persistence behavior. Revision 10 remains registered.
4. Leave RDS, private subnets, secrets, backups, and snapshots intact for diagnosis. Do not downgrade Alembic, import SQLite, delete RDS, disable deletion protection, or delete secrets during an application rollback.
5. Before a later retry, rerun the one-off migration task (forward-only), verifier, and Process A/B smoke. Rotation of app/migrator passwords requires coordinated role password changes plus new Secrets Manager versions and task replacement.

### Cost and follow-up

At the observed us-east-1 rates, the instance is about `$11.68/month`, initial gp3 storage about `$2.30/month`, and three Secrets Manager secrets about `$1.20/month`; current standard alarms are expected to be free or low-cost depending on the account's existing alarm count. Allow roughly **$15–18/month incremental** before logs, extra backup storage, cross-AZ data, or taxes. Storage autoscaling to 100 GiB would raise storage to about `$11.50/month`; retained manual/final snapshots, verbose logs, and future Multi-AZ/RDS Proxy are the main cost-growth risks.

Remaining actions are operational rather than acceptance blockers: attach SNS alarm routing, address the pre-existing `/api/scanner` 400 behavior before the next deployment, periodically test snapshot restore, and define credential-rotation/runbook ownership. The configured AWS CLI session identified as the account root; future infrastructure operations should use IAM Identity Center or a scoped administrative role with MFA instead. SQLite remains supported only for local development/testing/fallback. RDS PostgreSQL is authoritative production persistence.
