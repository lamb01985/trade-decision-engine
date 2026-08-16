# Phase 5E — React Preview Readiness

## Scope and prerequisite result

Phase 5E started from clean commit `1aeb096e034cf7a6f04bc4d621375d9eabb22e30`, exactly equal to freshly fetched `origin/main`. Phase 5C, Phase 5D, the focused frontend suite, and the chart snapshot/API suite were present and passing after disposable worktree dependencies were repaired. The Phase 4F worktrees remain separate under `/private/tmp`; no research-population files or processes were changed. No backend, production route, trading logic, or legacy static asset is modified by this phase.

## Legacy / React parity matrix

| Surface or behavior | Legacy | React | Result / disposition |
|---|---:|---:|---|
| Overview decision hierarchy | Yes | Yes | Equivalent core hierarchy; React keeps symbol, family, direction, raw state, last price, zone, invalidation, T1/T2, R:R, confirmation, failure conditions, and freshness. |
| Setup/decision detail from scanner | Yes | Yes | Added explicit keyboard-accessible “Open decision detail” action. |
| Watchlist scanner | Yes | Yes | Core backend-ranked results present. Editing, persistence, advanced sortable table, and filters are intentionally deferred. |
| Best setups | Yes | Yes | Backend candidates and ranking present; legacy ready/watch/ignore buckets are deferred pending a parity-safe response mapping. |
| Live radar | Yes | Yes | Current backend scanner results present. Browser/Pushover alerts, local scan scheduling, and transition feed remain legacy-only. |
| Chart | No dedicated legacy tab | Yes | Better in React: Phase 5D completed-bar chart, volume, overlay controls, timeframe and extended-hours requests, explicit stale/closed state. |
| Levels | Yes | Yes | Core backend level ladder present. Interactive level detail, confluence summary, and if/then playbooks remain legacy-only. |
| Options | Yes | Placeholder | Intentionally deferred: React has no production options-extension contract. It shows unavailable rather than calculating or fabricating GEX/options intelligence. Legacy remains authoritative. |
| Futures data | Yes | Placeholder | Intentionally deferred: React preserves the isolated data-only extension point but does not call the legacy probe contract. |
| Research / evidence | No dedicated legacy route | Yes | Better in React when backend aggregate exists; otherwise every evidence field is explicitly unavailable. |
| Advanced raw metrics/rationale | Yes | Partial | React retains backend-supported decision context but does not reproduce the full diagnostic panel yet. |
| Market freshness / closed state | Yes | Yes | Equivalent safety intent; React explicitly says when presentation is not live. |

Nothing was removed from the production cockpit. Deferred behaviors remain available there until their stable React/API contracts are reviewed.

## Preserved product patterns

The cockpit remains read-only and decision-first. Risk is never hidden for visual simplicity. Setup cards retain the decision zone, invalidation, both targets, structural R:R, confirmation requirements, failure conditions, backend state, and timestamps/evidence availability. Backend ranking and trade decisions remain authoritative. No win probability, confidence percentage, expected return, GEX interpretation, options conclusion, or room-to-run conclusion is created in the browser.

## State presentation mapping

The raw backend state is always displayed separately. Presentation mapping changes labels only:

| Raw backend state | Actionable | Presentation |
|---|---:|---|
| `CONFIRMED`, `TRIGGERED`, `READY` | Yes | `READY` |
| `CONFIRMED`, `TRIGGERED`, `READY` | No | `WAIT` |
| `WAIT`, `FORMING` | Either | `WAIT` |
| `INVALID` | Either | `INVALID` |
| `PASS` | Either | `PASS` |
| `MONITOR`, unknown, absent | Either | `MONITOR` |

The non-actionable guard prevents a closed/stale response from appearing ready. This mapping does not mutate or reinterpret the backend field.

## UX, error, and data-state changes

- Replaced backend-oriented headings with concise decision language while retaining backend provenance.
- Added setup-card targets and a direct scanner-to-detail action.
- Added a visible note whenever presentation and backend labels differ.
- Standardized safe network, service, malformed-response, loading, unavailable, stale, and market-closed copy. Raw backend exceptions/details are not rendered.
- Fixture mode remains opt-in, defaults off, and has a persistent “NOT LIVE / NOT FOR TRADING” banner.
- Empty evidence/options/futures states remain explicit; no mock values enter live mode.

## Chart review

The Phase 5D contract supplies completed OHLCV bars only. Candles and volume use color plus shape/position; current price and invalidation receive stronger lines, decision zones display both bounds, and T1/T2/structural overlays remain distinct backend-colored lines. VWAP/EMA/structure controls are generated only for returned overlays. The default remains limited to backend `default_visible` overlays. Timeframes `1m`, `5m`, `15m`, `1h`, and `1d`, plus extended-hours selection, are sent to the backend and included in the query cache key.

Desktop visual inspection at 1440×1100 confirmed readable candles, volume, current price, controls, timestamps, and market-closed warning. The fixture contains only two candles and is deliberately marked non-live; it is not market evidence. Live Massive review was unavailable because this isolated worktree has no `MASSIVE_API_KEY`. Legitimate historical Phase 5D transformation/API fixtures cover completed bars, closed sessions, missing cached analysis, bounded ranges, overlays, and unavailable responses. Provider-dependent high-price/lower-price/ETF live visual comparisons remain a preview review item.

## Responsive and accessibility review

Visual captures were reviewed at 1440×1100, 768×1024, and 390×844. Desktop and tablet retain the complete trade structure. Mobile stacks freshness, headers, setup grids, and chart settings; fixes prevent the symbol action from overflowing. Primary navigation deliberately scrolls horizontally rather than hiding routes. Chart height is reduced on mobile without removing trade overlays.

The React shell uses semantic headings, navigation landmarks, labels, status/alert roles, a skip link, visible keyboard focus, 44px interactive targets, an accessible chart description, and text labels in addition to state colors. `R:R`, `VWAP`, `MFE`, and `MAE` use concise native explanations where displayed. RVOL and GEX are not currently displayed in React; no unsupported educational or analytical copy was added.

## Bundle measurement

Baseline: one initial JS bundle, **525.05 kB** minified / **163.31 kB gzip**.

After route-level lazy loading:

- initial JS: **354.57 kB** / **108.49 kB gzip**
- Chart lazy chunk: **171.05 kB** / **55.33 kB gzip**
- Evidence lazy chunk: **2.19 kB** / **0.83 kB gzip**
- Options lazy chunk: **1.20 kB** / **0.64 kB gzip**

This reduces initial minified JS by 170.48 kB (32.5%). A single calm loading panel is used during route fetches.

## Preview configuration

`VITE_API_BASE_URL` selects the separately deployed preview backend. Empty means same-origin (and Vite proxies local `/api` requests). `VITE_USE_DEV_FIXTURES` defaults false. Only variables prefixed `VITE_` are browser-visible; no Massive, AWS, RDS, database, or other credentials are referenced client-side. `frontend/vercel.json` supplies only an SPA fallback rewrite; it contains no business logic and the normal Vite build remains portable to AWS/static hosting.

A preview deployment must configure `VITE_API_BASE_URL` to an HTTPS backend that permits the preview origin. Fixture mode must remain false. The Massive key and all server credentials belong only on that backend.

## Known limitations and cutover philosophy

- Live Massive visual sampling could not be completed in the isolated keyless worktree.
- React Options and Futures are honest unavailable placeholders, not parity implementations.
- Watchlist editing/persistence, advanced tables, alert scheduling/notifications, level-detail playbooks, and full legacy diagnostics remain intentionally legacy-only.
- The chart decision zone is represented by clear upper/lower boundary lines; a filled band can be evaluated later if supported without visual clutter.
- This is responsive web/PWA groundwork, not native mobile UX.

Preview is additive. The legacy FastAPI root stays production until a separately authorized human preview review and cutover decision. Rollback means removing/redirecting the separate preview deployment; no production route rollback is necessary. Phase 5E does not deploy, merge, redirect `/`, or change FastAPI static serving.
