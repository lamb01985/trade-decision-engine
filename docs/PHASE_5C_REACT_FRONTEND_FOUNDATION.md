# Phase 5C React Frontend Foundation

## Scope and safety boundary

This phase adds a frontend-only React foundation beside the current cockpit. It does not change setup logic, thresholds, scoring, confirmations, targets, invalidations, scanner behavior, research generation, notifications, AWS, or deployment. The existing static UI remains the FastAPI root until reviewed parity and an explicit cutover.

## Current frontend architecture

FastAPI serves `app/static/index.html` at `/` and mounts `app/static/` at `/static`. The UI is a single-page, framework-free application:

- `index.html` owns all page structure and tab panels.
- `app.js` owns mutable browser state, fetch calls, rendering templates, navigation, sorting, local storage, radar polling, and browser notifications.
- `styles.css`, `playbook.css`, and `readability.css` provide the dark institutional palette, compact labels, price typography, state treatments, responsive grids, and accessibility/readability refinements.
- `tests/test_ui_navigation.py` provides source-level regression coverage for navigation, backend-authored decision semantics, degraded states, levels, options, futures, responsiveness, and visual tokens.
- FastAPI exposes both legacy endpoints used by `app.js` and newer typed `/api/v1` contracts in `app/api_contracts.py`.

### Existing screen inventory

| Product surface | Current implementation | Important behavior to preserve |
| --- | --- | --- |
| Overview / setup detail | `#dashboard` | Decision hero first; backend conditions; trade plan; invalidation; targets; concise rationale; advanced evidence collapsed |
| Watchlist | `#watchlist` | Browser-persisted symbols; quality filters; ranked cards plus advanced table |
| Best setups | `#best` | Qualified/forming/ignore buckets; closed-session wording |
| Live radar | `#market` | Local polling; transition feed; explicit freshness; no execution |
| Levels | `#levels` | Current-price anchor; descending ladder; backend-authored metadata and confluence |
| Options | `#options` | Underlying decision vetoes contract selection; interpretation before raw chain |
| Futures data | `#futures` | Isolated NQ data probe; never presented as a setup |
| Chart | Not present | New dedicated React route required |
| Research / Evidence | Backend endpoints exist; no dedicated UI | New typed React surface required |

### Existing API usage

The legacy client calls `/api/analyze/{symbol}`, `/api/scanner`, `/api/market-scanner`, `/api/options-plan/{symbol}`, `/api/options-intelligence/{symbol}`, `/api/futures-probe/nq`, and notification settings endpoints. Stable frontend contracts also exist at `/api/v1/analysis/{symbol}`, `/api/v1/scanner`, `/api/v1/options-plan/{symbol}`, `/api/v1/research/evidence`, and `/api/v1/research/excursions`.

## React architecture

The new application lives in `frontend/` and uses React, TypeScript, Vite, React Router, CSS modules/global tokens, TanStack Query for server-state caching, and TradingView Lightweight Charts for the dedicated chart surface. Domain types, runtime parsers, HTTP transport, query hooks, fixtures, and components are separated.

The production default is live HTTP. Development fixtures are opt-in through `VITE_USE_DEV_FIXTURES=true`, carry `source: "fixture"`, and trigger a persistent, visible development-data banner. Evidence placeholders never display invented numeric values.

### Chart library decision

`lightweight-charts` is selected because it is purpose-built for performant browser financial charts, supports candlesticks, volume, price lines, and incremental series updates, and keeps all decision semantics in our API/view-model layer. Version 5 is distributed under Apache-2.0; its license and required TradingView attribution must be retained and reviewed before production release. It is not a data provider, does not supply indicators, and does not own setup logic. Backend chart bars and overlay contracts are still required before live chart data can be shown.

## Migration plan

1. Keep `app/static` and the current `/` route untouched.
2. Develop and test the React app independently with Vite.
3. Validate route, content, degraded-state, keyboard, responsive, and visual parity against the static cockpit.
4. Add backend-owned chart snapshot/stream contracts; map them in the centralized client without calculating indicators or decisions in the browser.
5. Build the React bundle and serve it at a review-only path or separate preview host.
6. Cut over `/` only after reviewed parity, while retaining a rollback path to the static client.

## Required future backend contracts

### Chart snapshot and stream

The chart requires timestamped OHLCV bars plus explicit freshness/session metadata and backend-authored overlays: current price, decision-zone bounds, invalidation, targets, VWAP, selected EMA values, structural levels, and setup markers. A future WebSocket/SSE stream should carry sequence/version identifiers, snapshot reconciliation metadata, heartbeat/freshness fields, and distinct event types for market observations versus decision changes.

### Evidence

Frontend types cover sample size and quality, outcome distribution, realized R, MFE/MAE in price and R units, threshold reach rates for 0.5R through 3R, time to threshold, structural room, room-to-run context, and cohort/slice provenance. Current research endpoints expose only some of this through flexible aggregate dictionaries, so the UI must label missing fields unavailable and must not infer them.

### Options extensions

Typed extension points cover volume, open interest, IV, GEX, gamma flip, call wall, put wall, strike concentration, and dealer-positioning interpretation. These values remain unavailable unless a backend response explicitly supplies them. No GEX or dealer-position calculation belongs in the browser.

## Hosting and latency preparation

The Vite build is static and portable to Vercel, S3/CloudFront, or another web host. `VITE_API_BASE_URL` selects a standard HTTP backend; no hosting-vendor business logic is used. TanStack Query provides cache boundaries and incremental refetching. The chart adapter exposes append/update seams for a future WebSocket/SSE transport. Backend timestamps are preserved and rendered independently from browser receipt time.
