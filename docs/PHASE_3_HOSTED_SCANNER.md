# Phase 3A — Hosted Background Scanner

## Status and boundaries

Phase 3A implements an opt-in ECS-suitable scanner lifecycle and durable scanner heartbeat. It does not deploy to AWS and defaults to both scanner and delivery disabled. The existing production `AnalysisService`, `evaluate_production`, `StandardDecision`, `AlertEngine`, persistence adapters, and freshness guards remain the only decision path. No setup, threshold, score, confirmation, trigger, invalidation, target, risk/reward, ranking, options/futures, or backtest rule changed.

## Existing architecture audit

Before Phase 3A, market-wide scanning ran only when an HTTP client called `POST /api/market-scanner` or `/api/v1/scanner`. `GET /api/scanner` is a separate request-driven scan limited to 30 supplied symbols. There was no startup task, periodic loop, shutdown lifecycle, scanner heartbeat, or cross-process cycle lease.

`AnalysisService.market_scan` is the scanner entrypoint. It always bypasses the analysis cache, normalizes/deduplicates the requested `UniverseSelection`, caps it with `SCANNER_MAX_SYMBOLS` (250 by default), and processes sequential batches of `SCANNER_BATCH_SIZE` (8 by default). Symbols within one batch execute concurrently. One symbol exception is logged and accumulated in `failed_symbols`; it does not crash the loop. Any partial failure conservatively makes the aggregate data status `ERROR` and prevents alert state transitions for that cycle.

The request universe came from request fields: watchlist, bundled Nasdaq-100 baseline, bundled liquidity-oriented S&P 500 baseline, and additional symbols. The new hosted universe uses equivalent explicit environment configuration. Its safe default is the existing 13-symbol `DEFAULT_WATCHLIST`; index universes remain opt-in.

Each symbol uses `AnalysisService.analyze_symbol`, which fetches completed minute bars plus a snapshot, falls back safely to the completed aggregate when the snapshot fails, runs the unchanged production engine, hydrates the prior `StandardDecision` from `SignalStore`, and writes the resulting decision/outcome evidence through the same store. The hosted call sets `persist_actionable_only`, so stale, unavailable, premarket, after-hours, weekend, and closed observations remain observable but cannot advance durable production decision transitions.

`AlertEngine` maps existing qualifying candidates to `RADAR` or `TRIGGERED`; disappearance of a previously active candidate produces `INVALIDATED` or `TOO_LATE`. Its policy fingerprint covers all alert-policy values. State/candidate fingerprint, transition/alert time, price, score, and cooldown information are stored in `scanner_state`. PostgreSQL stale-write guards and unique signal/transition/notification constraints remain authoritative across restarts.

Closed-market request scans retain their existing read-only preparation ranking and create no alerts. Premarket/after-hours/stale data now explicitly cannot advance hosted alert state. The current scanner does not define `TARGET_HIT` or `EXPIRED` as alert lifecycle states. Target and invalidation evidence is tracked in `signal_outcomes`; setup progression uses existing `StandardDecision` states. Phase 3A does not invent or relabel those semantics.

Notification delivery previously occurred inline after the HTTP market scan. `NotificationService` already required LIVE/REGULAR data and used durable unique event keys. Phase 3A adds an explicit global delivery gate: with `NOTIFICATIONS_ENABLED=false`, qualifying events are inserted once with status `ELIGIBLE`, but no Pushover provider is constructed and no external request occurs. An `ELIGIBLE` row can later be atomically promoted to `CLAIMED`; concurrent workers still have one database winner.

## Hosted scanner lifecycle

`HostedBackgroundScanner` is created during FastAPI lifespan only when `HOSTED_SCANNER_ENABLED=true`. Startup requires hosted mode, Massive configuration, and durable database stores. It runs one immediate cycle and then waits for a session-aware interval. FastAPI shutdown sets the stop event, cancels/awaits the task, closes the provider afterward, and therefore participates in SIGTERM/task-replacement shutdown.

There are two overlap barriers:

1. An `asyncio.Lock` prevents overlapping cycles inside one process.
2. A row-locked, expiring lease in `scanner_heartbeats` prevents two ECS tasks from scanning the same `hosted` scope concurrently. Task replacement can take over after lease expiry. Completion is accepted only from the current lease owner.

The cycle catches aggregate exceptions without terminating FastAPI. `market_scan` already isolates individual symbols and does not create transitions after a partial provider failure. There is no automatic retry burst: the scanner returns to its configured session interval. Logs contain only aggregate counts, safe status, elapsed time, and exception type—not exception messages, URLs, credentials, or per-symbol INFO chatter.

## Durable state and operational status

Alembic revision `20260815_02` adds `scanner_heartbeats`. One row per scanner scope stores:

- enabled/running state and expiring owner lease;
- last scan started/completed/successful timestamps;
- last analyzed symbol, error, and transition counts;
- last aggregate data status and safe error code;
- update timestamp.

Per-symbol setup/cooldown state remains in `scanner_state`; signals, ordered decision transitions, monotonic outcomes, and eligible notification events use their existing Stage 2 tables. Raw Massive bars are not added to PostgreSQL.

`GET /api/scanner/status` and `/api/v1/scanner/status` return scanner enabled/running state, timestamps, counts, aggregate data status, stale flag, universe size, and notification gate. They never return owner IDs, leases, database configuration, or secrets. Readiness includes `scanner_ready`; when enabled it becomes false if the task has died, the heartbeat is stale during expected premarket/regular hours, or database heartbeat access fails. Outside expected scanning hours, an old completion timestamp alone does not make readiness false.

## Configuration

| Variable | Default | Purpose |
|---|---:|---|
| `HOSTED_SCANNER_ENABLED` | `false` | Explicitly starts the lifespan scanner; requires hosted mode and durable DB. |
| `NOTIFICATIONS_ENABLED` | `false` | Constructs Pushover delivery only when true and credentials exist. |
| `SCANNER_REFRESH_SECONDS` | `300` | Regular-session REST cadence. |
| `SCANNER_PREMARKET_INTERVAL_SECONDS` | `600` | Premarket observation cadence; no production transitions outside REGULAR. |
| `SCANNER_CLOSED_INTERVAL_SECONDS` | `1800` | After-hours/closed/weekend cadence. |
| `SCANNER_STALE_AFTER_SECONDS` | `900` | Expected-hours heartbeat threshold. |
| `SCANNER_CYCLE_LEASE_SECONDS` | `900` | Distributed overlap/takeover lease. |
| `SCANNER_BATCH_SIZE` | `8` | Concurrent symbols per sequential provider batch. |
| `SCANNER_MAX_SYMBOLS` | `250` | Hard universe cap. |
| `HOSTED_SCANNER_WATCHLIST` | existing 13-symbol list | Hosted base watchlist. |
| `HOSTED_SCANNER_ADDITIONAL_SYMBOLS` | empty | Extra comma-separated symbols. |
| `HOSTED_SCANNER_INCLUDE_NASDAQ_100` | `false` | Adds bundled Nasdaq baseline. |
| `HOSTED_SCANNER_INCLUDE_SP500` | `false` | Adds bundled liquid S&P baseline. |

Local defaults remain unchanged: local mode, SQLite support, no infinite scanner, and no Pushover sends.

## Validation and rollout plan

Local PostgreSQL validation commands:

```bash
DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/alembic upgrade head
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest -q
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m tests.phase3a_hosted_probe
.venv/bin/python -m compileall -q app tests
git diff --check
```

Future AWS rollout, not performed in Phase 3A:

1. Build and test an immutable image.
2. Run the existing one-off migration task to revision `20260815_02`; verify table, privileges, TLS, and readiness revision.
3. Deploy with `HOSTED_SCANNER_ENABLED=true` and `NOTIFICATIONS_ENABLED=false`, initially using only the 13-symbol watchlist.
4. Observe at least one complete regular-session cycle: heartbeat, provider quota/rate limits, duration, errors, transitions, DB connections, and eligible events.
5. Replace the ECS task and prove the new task restores heartbeat/per-symbol state and does not duplicate transitions or eligibility rows.
6. Expand the universe only after measured REST capacity supports it.

## Exact Phase 3B Pushover activation plan

1. Keep Phase 3A running with delivery false until scanner cadence and transition quality are accepted.
2. Review `ELIGIBLE` rows and notification preferences without sending them; decide explicitly whether pre-activation eligible events expire or are drained.
3. Add a bounded notification worker that atomically promotes `ELIGIBLE` to `CLAIMED`, uses the existing lease/dedup constraints, marks `SENT`/`FAILED`, and defines recovery for ambiguous external sends. Do not place delivery semantics in the trading engine.
4. Test concurrent claims, restart recovery, Pushover timeouts/rate limits, secret-safe logs, and the chosen old-eligibility policy in local PostgreSQL.
5. In a market-closed deployment, confirm Pushover secrets, keep one ECS task, set `NOTIFICATIONS_ENABLED=true`, and send one explicit operator test notification.
6. During the next regular session, enable only the lowest-risk preference/destination scope, observe one genuine transition, verify exactly one delivery and one `SENT` row, then expand alert types deliberately.
7. Roll back immediately by setting `NOTIFICATIONS_ENABLED=false`; scanner persistence and eligibility recording continue unchanged.

## Phase 3B — Combined production rollout validation

Phase 3B deployed the combined Phase 3A scanner and Phase 3C cockpit during the
market-closed window on 2026-08-15. Trading rules were not changed and Pushover
delivery remained disabled throughout.

### Production artifact and configuration

- ECR image: `trade-decision-engine@sha256:1fa45e313ec49c2fbd91c85ea22bbb3efb17ff9752317156ab6d18e6434f9ef0`
- ECS task definition: `default-trade-decision-engine:12`
- ECS service: `default/trade-decision-engine`, desired count `1`
- Alembic revision: `20260815_02`
- Scanner: enabled, 13-symbol explicit watchlist, batch size 8, maximum 13
- Cadence: 300 seconds regular, 600 seconds premarket, 1800 seconds closed
- Notifications: explicitly disabled
- Index universes and Massive WebSockets: disabled
- PostgreSQL pool: size 3, overflow 0

The task revision retained the existing Secrets Manager references, RDS client
security group, task/execution roles, port, logging, and health configuration.
No credential was added as plaintext configuration.

### Migration and schema verification

The existing production revision was `20260815_01`. The one-off migration task
ran `alembic upgrade head` transactionally and exited 0. Revision
`20260815_02` added only `scanner_heartbeats`, keyed by `scanner_scope`, with
the enabled/running lease state, scan timestamps, aggregate counters, status,
and audit timestamps required by the hosted scanner. Counts before and after
migration remained: 119 signals, 120 transitions, 119 outcomes, one scanner
state row, one notification, and one notification-settings row. A one-off task
using the application credential verified effective select/insert/update/delete
access and the expected primary-key/not-null constraints without exposing the
credential.

### Automatic cycles and restart persistence

The initial task autonomously completed a 13-symbol cycle at 03:19:39 UTC in
3.065 seconds. A controlled task replacement autonomously completed another at
03:31:11 UTC in 3.497 seconds. Both evaluated 13 of 13 symbols successfully,
reported `MARKET_CLOSED`, and created zero transitions with zero errors. No
browser scanner request was used to produce either cycle.

An independent RDS read after replacement showed the heartbeat advanced to the
second task while all durable application counts remained unchanged. The sole
historical notification remained `SENT`; no eligible, claimed, duplicate, or
delivery row was created. CloudWatch contained no Pushover request/delivery log,
database error, credential-bearing URL, or secret assignment for either task.

Production status reported scanner enabled, not running between cycles, last
symbol count 13, error count 0, transition count 0, non-stale, and notifications
disabled. `/api/health` and `/api/readiness` were healthy and readiness reported
PostgreSQL and scanner ready.

### Frontend and deployment safety

The deployed Overview rendered the combined cockpit correctly using the backend
market-closed and no-trade response. Overview, Best Setups, Watchlist, Levels,
Live Radar, and unavailable/degraded/no-trade states are covered by the combined
frontend test suite. Live Radar was not opened in production because Phase 3B
required autonomy proof without a browser-triggered scan.

The ECS Express managed rollback alarm originally combined normal target 4xx
responses with 5xx responses. For both the initial rollout and controlled
replacement, its expression was changed to target 5xx percentage only while
retaining the 1% threshold, 2-of-3 evaluation, circuit breaker, rollback, and
alarm-enabled deployment protection. ECS Express regenerates this managed alarm
when an update starts, so the expression had to be corrected immediately after
each update. Durable ownership of that alarm policy remains an operations item.

### Rollback and remaining operations

Rollback is to update the service to task definition revision 11, leaving
Alembic revision `20260815_02` in place because it is additive and backward
compatible. Do not downgrade or delete scanner state. Keep desired count one and
verify health/readiness after the same canary and bake process.

Remaining maintenance items are SNS alarm routing, snapshot-restore rehearsal,
credential-rotation ownership, AWS administrative-access hardening, and moving
the rollback-alarm policy to durable infrastructure ownership. None blocks the
notification-disabled hosted scanner rollout.
