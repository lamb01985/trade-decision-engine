# Backend platform boundaries

## Architecture

The normalized provider boundary is `app.market_data.base`. Massive is an adapter that
implements stock, options, and futures capabilities. `evaluate_production` is the one
deterministic pipeline shared by live analysis and historical replay: indicators and
market structure feed levels, setup detection, trade-plan scoring, and the standardized
decision. `AnalysisService` owns request orchestration, its short process cache, scanning,
and signal logging. Options and futures services consume provider capabilities but do not
modify the equity decision. FastAPI and notification services are delivery adapters.

Legacy `SymbolAnalysis`, `ScannerRow`, and `MarketScanResult` remain for the current UI.
`app.api_contracts` isolates future clients from those internal/legacy representations;
new clients should use `/api/v1`.

No frontend or notification code is imported by the deterministic engine. Provider URLs,
credentials, HTML, DOM concepts, UI colors, Pushover, and deployment targets stay outside
the engine.

## Frontend ownership invariant

Frontends may render, format, navigate, visualize backend levels, filter display, and sort
already-ranked results. A frontend must not independently determine setup family,
direction, confirmation, trigger, invalidation, targets, quality, actionable state, or
chase state. Those are backend-owned outputs of the deterministic engine.

## State and serverless audit

| State | Current location | Classification | Consequence |
|---|---|---|---|
| Analysis response cache | `AnalysisService._cache` | Safe for stateless requests | Cold instances refetch; correctness is unchanged. |
| Per-symbol prior decision | `AnalysisService._decisions` | Needs persistent storage | A cold/new instance loses transition history used by the decision lifecycle. |
| Scanner transition state/cooldown | `AlertEngine._states` | Needs persistent storage | Multiple instances can duplicate/miss scanner transitions. |
| Pushover delivery dedup/preferences | SQLite notification store | Needs shared persistent storage | Durable locally, but ephemeral/server-local files are insufficient across serverless instances. |
| Signal evidence/outcomes | SQLite signal store | Needs shared persistent storage | Durable locally; must move to a shared database for hosted multi-instance use. |
| Browser radar polling | Browser timers | Local-only | Stops when the page closes and is unsuitable for hosted scanning. |
| Hosted scheduled scans | Not implemented | Needs background worker/scheduler | Vercel request handlers must not be treated as a scheduler. |
| Historical bar caches/reports | Local filesystem | Local-only | Research jobs need durable object/database storage in hosted environments. |

## Deployment readiness

The API is request-safe, versioned, CORS-configurable, and environment-configured. A
serverless deployment is not yet operationally equivalent to the local app because local
SQLite files, in-process scanner/decision state, browser-driven schedules, and filesystem
research artifacts are not shared or durable. Long market-wide scans may also exceed
serverless request limits. Before hosted scanning, use a shared relational store and a
worker/scheduler with idempotent scan jobs. Do not deploy SQLite on an ephemeral Vercel
filesystem as the source of notification or lifecycle truth.

## Security notes

Secrets are read only from environment variables and are never returned by health or
readiness endpoints. Provider exceptions are sanitized. Massive URLs are not logged because
the API key is a query parameter. CORS is disabled unless explicit origins are configured;
wildcard origins are not enabled by default. Authentication and rate limiting are outside
this pass and are required before exposing expensive scanner endpoints publicly.
