# Massive REST/SDK and streaming architecture audit

**Status:** architecture recommendation only; no implementation or provider-behavior change
**Audit date:** 2026-08-15
**Decision:** adopt a **hybrid** approach eventually: retain the existing async REST adapter initially, use the official Massive Python client for a controlled stock WebSocket ingestion proof, and migrate REST methods only where parity and async execution are proven. Do not perform a wholesale SDK rewrite.

## Executive answers

1. **Should the app adopt the official client?** Yes, incrementally, primarily for WebSocket transport. It should not replace the current REST adapter merely because it is official.
2. **If yes, for what?** First for a stock minute-aggregate WebSocket proof, then possibly trades/quotes where a measured use case exists. Its typed messages, authentication, subscription reconciliation, reconnect loop, and resubscription remove useful transport code. Evaluate selected REST calls separately behind the existing provider boundary.
3. **What should stay REST?** Historical minute bars, startup/bootstrap state, previous-session context and levels, reference/options contract discovery, bounded option-chain snapshots, futures contract resolution and historical aggregates, recovery/backfill, and periodic reconciliation.
4. **What should stream?** Initially completed stock minute aggregates (`AM`) for the selected universe. Add trades (`T`) and quotes (`Q`) only if they demonstrably improve freshness or trigger detection; do not feed every tick into the deterministic engine.
5. **Would event-driven scanning materially improve the app?** Yes for a broad universe. It can replace repeated two-request-per-symbol refreshes with incremental state updates and selective reevaluation, reducing normal-path REST traffic and decision latency from minutes toward one completed-bar interval. It will not make computation free, and it needs coalescing, backpressure, recovery, and reconciliation.
6. **Is ECS/Fargate suitable?** Yes. A continuously running Fargate task is appropriate for an outbound persistent connection, provided ownership is singleton, task replacement is expected, shutdown is graceful, readiness includes stream health, and reconnect/backfill is designed. The provider's default one-connection-per-asset-class allowance is a scaling constraint.
7. **Best live frontend architecture?** Browser to FastAPI WebSocket or SSE, with FastAPI reading application-owned normalized/current state. Never connect the browser to Massive. Prefer SSE for one-way updates unless bidirectional client messaging is required; use FastAPI WebSocket when subscription changes or richer two-way behavior justify it.
8. **Exact migration phases:** P0 harden/measure current behavior; P1 introduce provider-neutral event/state contracts; P2 prove REST SDK parity without production cutover; P3 optionally migrate only beneficial REST methods; P4 shadow a singleton WebSocket ingestion path; P5 make selected scanning event-driven with REST reconciliation; P6 expose app-owned live updates to React; P7 consider separating ingestion from API only when scale or availability requires it.
9. **What not to change yet:** provider behavior, the working `httpx` REST path, scanner cadence, trading rules, database schema, AWS resources, frontend transport, or Massive subscription. Do not stream the full universe or raw ticks into Postgres.

## Scope and invariants

This review is read-only with respect to runtime code, dependencies, market-data behavior, trading logic, and AWS. The architecture preserves these invariants:

- The deterministic engine receives normalized bars/state and never imports Massive SDK types or knows whether input came from REST, WebSocket, or replay.
- No setup, threshold, score, confirmation, trigger, invalidation, target, risk/reward, ranking, options/futures, or backtest rule changes.
- Postgres remains durable application state. Massive remains the source for raw market data; Postgres is not a tick archive.
- Provider time, receive time, normalization time, and evaluation time remain distinct.
- Live permission remains gated by the existing freshness/session policy, not merely by an open socket.

## 1. Current market-data implementation

### Provider and authentication

`app/market_data/massive.py` defines one `MassiveMarketDataProvider`. It owns a long-lived `httpx.AsyncClient` with a configurable base URL (default `https://api.massive.com`) and a 20-second timeout. Every request adds `apiKey=<MASSIVE_API_KEY>` as a query parameter. The provider closes the client during FastAPI lifespan shutdown.

The adapter redacts the API key and credential-shaped values from bounded error excerpts. Application startup logs only key presence/length and a sanitized provider origin, and `httpx` logging is reduced to warning because request URLs contain the query credential. This is carefully implemented, but bearer-header authentication would reduce the risk of secrets appearing in URLs and intermediary logs.

### REST routes currently used

| Provider method | Route | Purpose and caller |
|---|---|---|
| `get_minute_bars` | `GET /v2/aggs/ticker/{symbol}/range/1/minute/{start}/{end}` | Adjusted ascending bars, limit 50,000. Used by `AnalysisService`, backtests, research, CLI, and live verification. |
| `get_stock_snapshot` | `GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}` | Raw latest stock trade/quote/minute/day snapshot. Used directly by options intelligence and indirectly for observations. |
| `get_stock_observation` | same single-ticker snapshot | Selects the newest valid last-trade price, quote midpoint, or minute close using provider timestamps. |
| `get_active_option_contracts` | `GET /v3/reference/options/contracts` | Active, unexpired option metadata, up to 1,000, sorted by expiry. |
| `get_options_chain_snapshot` | `GET /v3/snapshot/options/{underlying}` | One expiration and strike-bounded chain snapshot, up to 250 contracts. |
| `resolve_active_futures_contract` | `GET /futures/v1/contracts` | Chooses the active dated single contract with minimum days to maturity. |
| `get_futures_minute_bars` | `GET /futures/v1/aggs/{contract}` | One-minute futures aggregates with nanosecond timestamps, limit 50,000. |

There are no current REST calls for individual historical trades/quotes, full-market snapshots, grouped daily bars, or previous-day bars.

### Concurrency and batching

- A single symbol analysis concurrently requests ten calendar days of minute bars and one stock snapshot via `asyncio.gather`. It excludes the still-forming minute and uses the snapshot only when it is at least as fresh as the newest completed bar.
- `scan()` launches up to 30 symbol analyses concurrently.
- `market_scan()` serializes whole scans with a process-local lock. It caps the universe at 250, processes sequential batches (default eight symbols), and gathers each batch concurrently. Since every forced symbol analysis makes two provider requests, a full 250-symbol cycle normally makes about 500 REST requests, excluding failures and option/futures calls.
- The hosted scanner defaults to a forced full-universe cycle every 300 seconds during regular trading, 600 seconds premarket, and 1,800 seconds while closed. A local lock and a durable Postgres lease prevent overlapping scan ownership.
- There is no provider-level semaphore, token bucket, request coalescer, bulk snapshot use, or adaptive concurrency based on rate-limit response headers.

### Caching

- `AnalysisService` has a process-local, per-symbol analysis cache with a default 60-second monotonic TTL and per-symbol locks. Interactive reads can use it; scanner paths deliberately force refreshes and therefore bypass it.
- Backtesting has filesystem JSON/gzip historical caches keyed by symbol/date/timeframe. They avoid repeat aggregate requests but are local/rebuildable research artifacts, not production shared state.
- The provider itself has no response cache, ETag/conditional request support, snapshot batch cache, or durable bar cache.

### Retry, errors, and rate limiting

- `response.raise_for_status()` handles all non-2xx responses uniformly. HTTP failures, transport failures, and JSON errors become a sanitized `MarketDataError`; provider payload statuses `ERROR` and `NOT_AUTHORIZED` also fail.
- There is no retry, exponential backoff, jitter, `Retry-After` handling, circuit breaker, or classification of permanent versus transient failures. A 429 fails immediately just like a 4xx or 5xx.
- A failed snapshot is tolerated when the aggregate request succeeds: analysis falls back to the newest completed minute close. A failed aggregate request fails the symbol.
- Any failed symbol makes the market scan's aggregate status `ERROR` and suppresses candidate/alert evaluation for that scan, although successful analyses are retained in the result.
- Batching eight symbols limits bursts heuristically, but it is not rate-limit enforcement. Effective request rate depends on provider latency and universe size.

### Freshness and snapshot behavior

Snapshot normalization accepts second, millisecond, or nanosecond timestamps and selects the latest priced observation among last trade, quote midpoint, and minute snapshot. `classify_data` marks non-closed data live at age <=90 seconds, delayed at <=300 seconds, and stale thereafter. Closed sessions are explicitly `MARKET_CLOSED`. Only live regular-session analysis advances actionable persistence/lifecycle state.

The deterministic evaluation is built from completed minute aggregates, while the displayed current price/freshness may come from a newer snapshot trade or quote. This separation is important and should survive streaming: tick data may update current observation and trigger a decision check, but completed bars remain the deterministic analytical input unless a separately reviewed engine contract says otherwise.

### Current `httpx` dependency

Massive market data and Pushover notifications both use `httpx`; removing it from Massive would not remove the dependency. The current provider benefits from native asyncio, connection pooling, explicit lifecycle, easy request mocking, custom base URLs, and known secret-safe errors. Its missing resilience is application code the SDK could partly replace for REST, but the official REST client is synchronous, which offsets that benefit.

## 2. Official Massive Python client review

The official repository describes the package as the REST and WebSocket client, supports Python 3.9+, is MIT-licensed, and shows 573 commits and active 2026 maintenance at audit time. It states a compatibility-oriented API release policy while explicitly reserving the right to change type annotations and add model fields. This is mature enough to evaluate seriously, not stable enough to allow SDK models to cross the app's provider boundary. [Official client repository](https://github.com/massive-com/client-python)

### Relevant REST functionality

The SDK exposes typed methods/models for stock aggregates, trades, quotes, snapshots, previous close, open/close, and broad reference data. `list_aggs` maps to the app's current equity aggregate route and can paginate. Snapshot support includes single/full-market and v3 universal snapshots (up to 250 selected tickers per documented SDK method); options-chain snapshots are supported. The current README examples cover aggregates, last/list trades, and last/list quotes. [Official REST examples and pagination](https://github.com/massive-com/client-python#rest-api-client), [official stocks REST catalog](https://massive.com/docs/rest/stocks), [official SDK snapshot implementation](https://github.com/massive-com/client-python/blob/master/massive/rest/snapshot.py)

Important limitations for this app:

- The REST client is synchronous and uses `urllib3.PoolManager`; the repository contains no async REST client. Calling it directly inside FastAPI/scanner coroutines would block the event loop. Safe integration would require a bounded thread pool/`asyncio.to_thread`, a separate worker, or retention of `httpx`.
- It uses bearer authorization headers rather than query credentials, which is operationally preferable.
- It automatically retries configured statuses including 413, 429, 499, 500, 502, 503, and 504 with exponential backoff factor 0.1. Default retry count must be pinned/verified for the selected release, and application metrics still need to distinguish retries from successes. [Official REST base implementation](https://github.com/massive-com/client-python/blob/master/massive/rest/base.py)
- Automatic pagination reduces custom cursor code, but it can also turn one logical call into unbounded API work unless callers explicitly cap/disable pagination.
- Equity endpoints have clear parity. Current options methods likely have SDK parity. The newer futures v1 routes must be verified method-by-method against the exact pinned SDK release; raw REST may remain appropriate where coverage or models lag.

### WebSocket functionality

Massive documents real-time and 15-minute-delayed stock sockets, explicit authentication, subscription messages, batched event arrays, and stock streams for trades, quotes, and aggregates. The quickstart notes that one concurrent WebSocket connection per asset class is allowed by default and that slow consumers may be disconnected. [Official WebSocket quickstart](https://massive.com/docs/websocket/quickstart?assetClass=stocks), [official stock trade schema](https://massive.com/docs/websocket/stocks/trades)

The Python SDK's `WebSocketClient`:

- has an async `connect(processor)` API and a synchronous `run(handler)` wrapper;
- parses messages into SDK models (or can deliver raw payloads);
- tracks desired subscriptions and reconciles subscribe/unsubscribe changes;
- authenticates on every connection and restores subscriptions after an error reconnect;
- uses the underlying `websockets` async reconnect iterator and defaults to a maximum of five error reconnects;
- allows connection keyword arguments, so heartbeat/ping, queue, and size behavior can be configured through the underlying library;
- closes explicitly with an async method.

It does **not** solve application recovery. It has no durable cursor, no replay guarantee, no gap backfill, no deduplication, no out-of-order policy, no stream-health state in Postgres, and no provider-outage circuit breaker. Authentication failures and clean closes require deliberate supervisor behavior. Its in-process desired-subscription set is lost at container restart. The app must own those responsibilities. [Official WebSocket client implementation](https://github.com/massive-com/client-python/blob/master/massive/websocket/__init__.py)

### Does the SDK meaningfully reduce custom code?

For WebSockets, yes: it removes protocol authentication, message parsing/model dispatch, basic reconnect, and subscription reconciliation. For the existing REST adapter, only partly: typed endpoint methods, pagination, bearer auth, pooling, and retries replace code, but sync-to-async bridging, mapping to domain models, error translation, metrics, freshness, caching, and recovery remain application responsibilities. Since the current REST adapter is small and well-tested, the net reduction is modest.

## 3. Custom client versus official client

| Dimension | Current custom `httpx` client | Official Python client | Assessment |
|---|---|---|---|
| Reliability | Simple, known path; no retry/backoff | REST retries selected transient statuses; WS reconnect/resubscribe | SDK improves transport mechanics, but app-level recovery is still required. |
| Maintainability | Six explicit routes, transparent mapping, locally owned | Provider-maintained endpoint/model surface | SDK helps with API evolution but introduces upstream release/model changes. Keep an anti-corruption adapter. |
| Latency | Async REST and concurrent batches | REST is synchronous; WS is async | REST SDK can regress latency/event-loop health unless offloaded. WS materially improves update latency. |
| Typing | Raw dictionaries at provider boundary; domain `Bar`/`StockObservation` output | Typed REST and WS models, with upstream annotations explicitly changeable | Useful inside adapter only; normalize immediately into app-owned frozen models. |
| Async support | Native async REST | Async WS, sync REST | Hybrid is the natural fit. |
| Retries | None | REST status retries/backoff; WS reconnect attempts | SDK is better baseline, but retry budget, jitter, `Retry-After`, and observability need validation. |
| Reconnect | None/not applicable | Basic reconnect and resubscribe | Valuable, but insufficient for gaps, clean-close supervision, and outages. |
| Rate limits | Batch-size heuristic; 429 immediately fails | 429 is retryable in REST base | Neither provides application-wide quotas/admission control; subscription entitlements still govern WS. |
| Testing | Easy `httpx`/method monkeypatching; small surface | SDK models, thread boundary for REST, socket lifecycle for WS | REST migration increases test layers; WS SDK reduces protocol mocks but requires deterministic event/recovery harnesses. |
| Dependency risk | Already pinned `httpx`; no market SDK | Adds `massive`, `urllib3`, `websockets`, `certifi` and provider release coupling | Pin exact version and use compatibility tests; `httpx` remains needed for notifications. |
| Migration effort | None | Adapters, model mapping, thread strategy, parity tests, ops | Low-to-moderate for WS proof; moderate for REST; high for event-driven scanner. |

**Decision:** hybrid. Keep the present REST path until a parity experiment proves a concrete benefit. Adopt the official SDK for the first WebSocket implementation, behind app-owned interfaces. This is not a recommendation to install or change anything now.

## 4. Provider-independent event architecture

```text
Massive REST ───────┐
Massive WebSocket ──┼─> provider adapters -> normalized MarketEvent envelope
Historical replay ─┘                           |
                                                v
                                      per-symbol reducer/state
                                      (watermarks + bar buffer)
                                                |
                                 reevaluation policy / coalescing
                                                |
                                                v
                                  existing deterministic pipeline
                                                |
                          Postgres decisions, transitions, heartbeats
                                                |
                         alerts + FastAPI query/stream projection -> React
```

The central boundary should be an app-owned event envelope, not an SDK class. Conceptually it contains `event_id` (when available or derived), `provider`, `asset_class`, `event_type`, `symbol`, `provider_timestamp`, `sequence` (when available), `received_at`, normalized payload, and schema version. A bar event additionally states timeframe, window start/end, and whether it is provisional or closed.

Three adapters must be able to produce the same semantic events:

- WebSocket adapter: normalizes SDK events immediately.
- REST bootstrap/backfill adapter: converts fetched bars/snapshots into the same reducer inputs.
- Replay adapter: reads recorded normalized events/bars and advances a supplied clock deterministically.

The reducer owns per-symbol market state and watermarks. The reevaluation policy decides whether the existing `evaluate_production(symbol, completed_bars, prior)` should run. The engine remains a pure consumer of ordered completed bars and prior decision state. Transport, reconnect, SDK types, rate limits, and subscription commands stay outside it.

## 5. REST and WebSocket responsibilities

| Data/work | Recommended transport | Reason |
|---|---|---|
| Ten-day/historical minute bars | REST | Bounded history and pagination are request/response work; streaming cannot reconstruct startup history reliably. |
| Startup state and container restart | REST + Postgres | Load durable app state from Postgres, then fetch provider history through a watermark before enabling live decisions. |
| Prior-day/session levels | REST or derived from REST history | Stable context, computed once/reconciled periodically; no value in tick streaming. |
| Recovery/backfill/reconciliation | REST | WebSocket delivery is not a replay log. |
| Reference option contracts | REST | Slowly changing metadata and bounded user-driven queries. |
| Option-chain snapshots | REST initially | Current workflow is on-demand and expiration/strike bounded. Streaming all contracts would add volume and entitlement complexity without a demonstrated decision benefit. |
| Futures contract resolution/history | REST | Contract metadata and bootstrap aggregates are not continuous trigger inputs. Evaluate futures WS independently later. |
| Completed stock minute aggregates | WebSocket first | Best match to the engine's current input and gives one natural reevaluation point per symbol/minute. |
| Live trades | Optional WebSocket | Useful for displayed price, freshness, and threshold-crossing wakeups, but coalesce; do not evaluate on every trade. |
| Live quotes | Optional WebSocket | Useful for midpoint/spread/display and trigger validation; high volume and not currently an engine input. |
| Snapshot | REST fallback/reconciliation | Useful when stream is absent/stale and for periodic truth checks; bulk/universal snapshot may reduce per-symbol requests if entitlements and semantics match. |

Start with `AM.<selected symbols>`, not `T.*`/`Q.*`. A completed minute event aligns with current deterministic semantics and sharply limits event volume. If Massive's minute aggregate messages update during the minute, the ingestion layer must explicitly distinguish provisional updates from the final closed window and only append one canonical completed bar after the end watermark/reconciliation policy.

## 6. Event-driven scanner design

### Flow

1. Ingestion validates and normalizes an event, records receive/provider lag, and submits it to a bounded queue.
2. A symbol-partitioned reducer deduplicates and orders it, updates the latest observation/quote and/or minute bar buffer, and advances per-stream watermarks.
3. A cheap reevaluation predicate checks whether a completed bar arrived, freshness changed, a relevant price threshold crossed, session changed, or recovery replaced a prior bar.
4. A per-symbol debounce/coalescer permits at most one queued evaluation for the newest state. It prevents trade/quote bursts from building an obsolete work backlog.
5. The existing deterministic pipeline evaluates an immutable ordered snapshot. Existing transition deduplication and persistence decide whether an alert is warranted.
6. Periodic REST reconciliation and a low-frequency full-universe sweep remain as a safety net during migration.

### Expected benefit

The present 250-symbol forced cycle makes roughly 500 REST requests every five regular-session minutes, approximately 39,000 requests over a 6.5-hour session before retries or user requests. Streaming minute aggregates replaces most normal-path refresh calls with one connection and data only as it changes. A symbol can be evaluated shortly after its completed minute rather than waiting up to the poll interval. Selective threshold triggers can reduce latency further without evaluating every tick.

The benefit is material for API usage and freshness at broad-universe scale. CPU savings depend on the predicate: evaluating all 250 symbols once per minute is more compute than the current five-minute poll, so the design must reuse rolling state, coalesce, and reevaluate only when a completed bar or relevant state change warrants it. The goal is event-directed work, not simply higher-frequency full recomputation.

## 7. Failure and recovery

### Required state machine

Use explicit ingestion states such as `BOOTSTRAPPING`, `LIVE`, `DEGRADED`, `RECONNECTING`, `BACKFILLING`, and `STALE`. An open TCP socket is not proof of usable data. Readiness for live scanning should require authenticated subscription, expected subscription coverage, acceptable event age during an active session, completed bootstrap/backfill, and functioning Postgres.

### Failure policies

| Failure | Safe behavior |
|---|---|
| Disconnect | Immediately mark stream degraded; stop authorizing new live transitions from stream state; keep API available with explicit stale/degraded status. Reconnect with bounded exponential backoff and jitter. |
| Reconnect | Reauthenticate/resubscribe, but remain non-live until gap assessment and REST backfill complete through a safe watermark. Reset retry budget only after a sustained healthy interval. |
| Missed messages | Assume every disconnect can lose data. Fetch aggregates from the last durable completed-bar watermark with overlap, normalize, deduplicate, then merge buffered post-reconnect events. |
| Stale stream without disconnect | Track last message globally and per subscribed active symbol plus provider/receive lag. Use heartbeat/ping and expected-session activity; alarm and enter `STALE` when thresholds are exceeded. Do not mistake an illiquid symbol for global outage. |
| Provider outage | Circuit-break REST recovery attempts, back off, preserve last known state with timestamps, suppress new actionable transitions, and expose provider-degraded status. Resume only after reconciliation. |
| Duplicate event | Prefer provider ID/sequence where defined; otherwise use a deterministic key such as provider + event type + symbol + window/timestamp + canonical payload hash. Reduction must be idempotent. |
| Out-of-order event | Maintain per-symbol/event-type watermark and a small reorder window. Accept corrections according to an explicit policy; never append bars blindly. A late correction triggers at most one reevaluation and is auditable. |
| Container restart | New task acquires singleton ingestion lease, loads durable decision/scanner/watermark state, REST-bootstraps bars, connects/subscribes, backfills the handoff interval, then becomes live. No local queue is assumed durable. |
| Backfill overlap | Request an intentional overlap before the last watermark, upsert/dedupe normalized bars, compare with buffered stream events, and favor a documented canonical source/version. |

Persist application-level watermarks/checkpoints and current derived scanner state, not all trades and quotes. Persisting every event to Postgres would create unnecessary write amplification. If exact replay becomes a requirement later, introduce a purpose-built durable log/object-storage capture with retention rather than turning core relational tables into a tick store.

Recovery testing must cover disconnect between authentication and subscription, disconnect mid-batch, duplicate reconnect delivery, corrections, clock skew, empty market periods, REST 429/5xx during backfill, Postgres outage, and overlapping ECS revisions. A replay fixture should prove identical decisions for equivalent REST, stream, and replay inputs.

## 8. AWS/ECS/Fargate implications

Fargate is suitable because this is a continuously running outbound network workload. The task ENI needs stable DNS resolution and HTTPS/WSS egress to Massive. Public-subnet/public-IP or private-subnet/NAT placement can both work; the existing architecture already requires Massive/Pushover egress. Security groups are stateful, so no inbound rule is needed for the outbound provider socket. Ensure DNS/NAT/egress failures and connection resets are observable.

Operational constraints:

- **Singleton ownership:** Massive documents one concurrent connection per asset class by default. Current desired/max count one aligns with that, but rolling deployment may briefly run old and new tasks. Use a Postgres lease/fencing token for stream ownership; the new task should not connect until it owns the lease, or obtain a subscription plan that explicitly permits overlap.
- **Replacement is normal:** deployments, health failures, scaling, and infrastructure maintenance replace tasks. ECS sends `SIGTERM` and later `SIGKILL`; the app must stop accepting events, checkpoint, close the socket, and release/expire ownership. AWS cautions against aggressively short deregistration delays for streaming connections. [ECS task lifecycle](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-lifecycle-explanation.html), [ECS connection draining](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/load-balancer-connection-draining.html)
- **Startup/readiness:** a task may serve read-only/stale API responses while bootstrapping, but it must not report streaming/scanner readiness until lease, Postgres, authentication, subscriptions, freshness, and backfill are healthy. Configure enough health-check grace time for recovery. ECS rolling updates can start replacement tasks before stopping old tasks, so fencing—not assumptions about deployment order—prevents dual ingestion. [ECS service updates](https://docs.aws.amazon.com/AmazonECS/latest/APIReference/API_UpdateService.html)
- **Scaling:** do not scale WebSocket consumers horizontally by cloning identical subscriptions. Shard symbols only with explicit connection entitlements and durable shard ownership. API/frontend fan-out can scale separately from a singleton ingestion worker once needed.
- **Resource sizing:** full-universe trades/quotes can create high network, parsing, queue, and memory load. Begin with selected minute aggregates; bound the SDK/socket receive queue and internal queues; measure dropped/coalesced events and event-loop lag.
- **Metrics:** emit connection/auth/subscription state; reconnect attempts; connected duration; expected vs active subscriptions; messages/events by type; provider-to-receive and receive-to-process lag; last-event age; queue depth/high-water mark; coalesced/dropped/duplicate/out-of-order counts; evaluations triggered/skipped; REST backfill calls/rows/duration/429/5xx; recovery duration; lease owner/fencing conflicts; and frontend client/fan-out counts.
- **Logs/alarms:** structured logs must never include API keys/auth frames. Alarm on stale active-session stream, reconnect storms, authentication/subscription failure, growing queues, recovery timeout, no healthy owner, REST rate limits, and database failure. Container Insights supports Fargate metrics/log correlation and alarms. [CloudWatch Container Insights](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/ContainerInsights.html)

The provider plan must be checked before any proof: real-time versus delayed entitlement, stock WebSocket access, permitted feeds, symbol/full-market subscriptions, maximum connections, exchange coverage, and redistribution/display rights. These commercial constraints can change and are architecture inputs, not implementation details.

## 9. Live data to React

| Option | Security | Latency | Reliability/complexity | Verdict |
|---|---|---|---|---|
| A. Browser connects to Massive | Exposes provider credential or requires unsafe token brokerage; bypasses server authorization | Low | Every browser consumes provider connections/subscriptions; inconsistent app state | Reject. The Massive key must never enter browser code, storage, URLs, logs, or dev tools. |
| B. Browser polls FastAPI | Strong key protection | Bounded by poll interval | Simplest and currently understood; wasteful at high frequency | Keep as fallback/bootstrap, not primary future live path. |
| C. FastAPI WebSocket/SSE | Strong key protection; app auth/authorization applies | Low | Moderate; reconnect/resume/backpressure owned by app | Recommended initial live architecture. |
| D. Broker/distribution service | Strong if private and app-controlled | Low | Best multi-consumer decoupling, highest operational cost | Defer until one-process fan-out or availability requirements demand it. |

Recommended flow:

```text
Massive WS -> server ingestion/reducer -> app-owned current projection
                                      -> FastAPI SSE/WS -> authenticated React
REST/Postgres -> bootstrap snapshot ---^
```

On browser connect, FastAPI sends a versioned current snapshot, then small application events (`symbol_state_changed`, `scanner_status_changed`, `alert_transition`) rather than raw Massive ticks. Include an app sequence/version so a reconnecting browser can detect a gap and refetch the snapshot. Heartbeats and bounded per-client queues prevent a slow tab from blocking ingestion. Do not make browser presence control provider subscriptions needed by the hosted scanner.

Prefer **SSE** when traffic is server-to-browser only: it is simple over HTTPS, browser-native, and reconnect-friendly. Choose **FastAPI WebSocket** if the frontend needs dynamic per-connection subscription commands or bidirectional low-latency interaction. Retain periodic REST bootstrap/recovery either way. The public stream must expose derived/app-approved fields, never the provider key, provider auth/status frames, or unrestricted raw licensed data.

With one ECS task, an in-process broadcaster is sufficient for a first proof. Before horizontal API scaling, separate ingestion ownership from fan-out using a broker (for example Redis-compatible pub/sub/streams, NATS, or managed AWS messaging chosen after requirements testing); Postgres `LISTEN/NOTIFY` may suit low-volume invalidation signals but should not be assumed to be a durable market-event bus.

## 10. Phased migration plan and gates

### P0 — freeze, baseline, and resilience specification

Keep the current REST client and scanner behavior. Measure request counts, latency, failures/429s, scan duration, data age, cache hits, and per-symbol analysis cost. Record exact Massive plan/entitlements. Specify retry budgets and semantic parity fixtures. **Gate:** trustworthy baseline and no trading-rule change.

### P1 — provider-neutral contracts

Add app-owned normalized event, clock, bar-store/view, and ingestion-health contracts in a future change. Keep all existing provider protocols compatible. Add replay-based conformance tests proving the deterministic pipeline takes ordered normalized bars, not transport objects. **Gate:** identical decisions for the current REST fixtures and no SDK import outside adapters.

### P2 — official REST SDK parity spike (non-production/shadow)

Pin one SDK version in an isolated future branch/environment. Compare output and failure semantics for equity aggregates, single/universal snapshots, option references/chains, and futures routes. Benchmark bounded thread offloading against native async `httpx`; test pagination limits, timestamps, missing bars, adjusted data, errors, retries, cancellation, logging redaction, and custom base URL behavior. **Gate:** exact domain parity and no event-loop blocking. If benefits are not material, keep `httpx`.

### P3 — selectively adopt REST SDK, if justified

Migrate one low-risk method behind the adapter with a feature flag and dual-read comparison. Never dual-read in a way that doubles production rate-limit risk. Equity aggregates are a possible candidate only after performance proof; bulk/universal snapshot may provide a larger API reduction than a mechanical single-route port. Leave unsupported/new futures routes on raw REST. **Gate:** production metrics at least as good, rollback switch tested.

### P4 — singleton WebSocket shadow ingestion

Use the official SDK for stock minute aggregates on a small watchlist. Normalize into app events, maintain memory state/watermarks, implement metrics, heartbeat, lease/fencing, reconnect, and REST gap backfill. Do not drive decisions, alerts, persistence transitions, or frontend. Compare each closed streaming bar with REST. **Gate:** multi-session parity, restart/reconnect chaos tests, no gaps or dual owners, acceptable resource use.

### P5 — event-driven scanner canary

For a small symbol subset, allow canonical completed-minute events to invoke the unchanged deterministic pipeline. Keep low-frequency full REST reconciliation and the existing scanner as control/fallback. Coalesce and enforce per-symbol serialization. Compare decisions and transitions before expanding. Add optional trade/quote threshold wakeups only after evidence. **Gate:** identical decisions, lower normal REST usage, faster age/decision metrics, proven degradation behavior.

### P6 — frontend live projection

Expose a versioned FastAPI SSE endpoint initially (or WebSocket if bidirectional requirements are real). Send bootstrap snapshot plus derived state changes, with auth, heartbeats, bounded queues, reconnect gap detection, and REST fallback. **Gate:** key-absence/security review, load test, slow-client test, reconnect test, and licensed-data review.

### P7 — separate ingestion/fan-out only when needed

If API scaling, availability, or consumer count exceeds one-task in-process capabilities, split singleton/sharded ingestion workers from stateless FastAPI tasks and add a selected event distribution system. Preserve Postgres as durable decision state and REST recovery. **Gate:** explicit scale/SLO justification and provider connection entitlement.

## 11. Risks and controls

| Risk | Control |
|---|---|
| SDK REST blocks asyncio | Do not call it directly in coroutines; benchmark bounded offload or retain async `httpx`. |
| SDK/API model drift | Pin version, keep SDK types inside adapter, contract-test timestamps/fields, review releases before upgrade. |
| Retry amplification/rate-limit storm | Global budgets, jitter, `Retry-After`, circuit breaker, metrics, and bounded pagination/concurrency. |
| Stream mistaken for durable log | Always reconnect with overlap and REST backfill; persist watermarks/state, not assumptions. |
| Dual consumers during ECS rollout | Postgres lease plus fencing and provider entitlement-aware deployment sequence. |
| Tick flood/backpressure | Start with minute aggregates; bounded queues, coalescing, per-symbol serialization, drop policy for superseded observations. |
| Different decisions by transport | Canonical normalized models, closed-bar semantics, deterministic replay/parity tests. |
| Out-of-order/correction changes history | Watermarks, reorder window, idempotent upserts, explicit correction/reevaluation audit. |
| Frontend leaks key/licensed raw data | Server-only provider connection; project app-owned state; auth and redistribution review. |
| Postgres write amplification | Persist decisions/transitions/checkpoints; keep hot market state in memory and raw ticks out of core tables. |
| One socket becomes availability bottleneck | Fast recovery plus REST fallback first; later active/passive ownership or entitled sharding, not uncontrolled duplicate connections. |

## Expected benefits

- Lower normal-path REST usage for broad live scans and fewer provider burst failures.
- Completed-bar-driven evaluation with substantially lower data-to-decision delay than five-minute polling.
- Provider transport independence shared by live, REST recovery, and historical replay.
- Officially maintained WebSocket protocol parsing, subscriptions, and basic reconnect behavior.
- Clear stale/degraded states and deterministic recovery rather than silent continuation after gaps.
- Secure server-side fan-out to React without exposing the Massive key.
- A migration path with reversible gates and no change to trading logic.

## What not to change yet

- Do not replace `MassiveMarketDataProvider` or remove `httpx` now.
- Do not install the SDK until a separately approved parity spike; `httpx` remains required by notifications anyway.
- Do not change market-data route parameters, adjustment, sort, limits, freshness thresholds, completed-bar filtering, snapshot precedence, scanner cadence/batch size, or cache semantics as part of an SDK evaluation.
- Do not change any trading decision, alert, ranking, option/futures, persistence, or backtest rule.
- Do not connect React directly to Massive or expose the key through any client-delivered credential.
- Do not subscribe to all trades/quotes, store raw ticks in Postgres, or assume WebSocket completeness.
- Do not scale the current combined ECS task beyond one while it owns singleton scanner/stream work without fencing and connection-entitlement design.
- Do not create AWS resources or alter the deployment until shadow ingestion and recovery have passed explicit gates.

## Official sources

- [Massive official Python client and examples](https://github.com/massive-com/client-python)
- [Official Python WebSocket implementation](https://github.com/massive-com/client-python/blob/master/massive/websocket/__init__.py)
- [Official Python REST base/retry implementation](https://github.com/massive-com/client-python/blob/master/massive/rest/base.py)
- [Massive WebSocket quickstart](https://massive.com/docs/websocket/quickstart?assetClass=stocks)
- [Massive Stocks REST API catalog](https://massive.com/docs/rest/stocks)
- [Massive stock trades WebSocket schema](https://massive.com/docs/websocket/stocks/trades)
- [AWS ECS task lifecycle](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-lifecycle-explanation.html)
- [AWS ECS connection draining](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/load-balancer-connection-draining.html)
- [AWS CloudWatch Container Insights](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/ContainerInsights.html)
