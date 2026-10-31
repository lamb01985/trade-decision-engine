# Phase 3 — Market Intelligence dashboard

Implemented on `feature/market-positioning-phase2`, based on `49d0cd9`.
Existing modified setup rules/engine/version/parameters and untracked `.github/` were preserved. No branch switch, commit, merge, deployment, broker connection or trade occurred.

## Dashboard and API

React route: `/market-intelligence`. Four views use the existing cockpit theme: Market Map, Options Intelligence, Intraday Playbook, Swing Opportunities.

Market Map includes required QQQ/SPY/IWM/SMH placeholders and every stock in the report, with report and individual source timestamps, daily/weekly levels and structure. Options exposes call/put OI by strike and expiration, contract changes, unusual volume and baseline status/observation counts, bounded trade/quote sampling and pagination completeness, expected moves, IV/realized volatility/history quality, and explicit hypothetical gamma scenarios. Playbooks show trigger, confirmation, invalidation, targets and no-trade conditions, including neutral and unavailable values. Data errors and source warnings remain visible. Neither institutional identity nor actual dealer direction is inferred.

- `GET /api/v1/market-intelligence/latest`
- `GET /api/v1/market-intelligence/history?limit=20&offset=0` (limit 1–100)

Both read the Phase 2 SQLite `intelligence_reports` table with `mode=ro`; they never create/migrate a store, invoke Massive, generate a report, or modify outcomes. `MARKET_INTELLIGENCE_DB` selects the store (default `data/market_intelligence.sqlite3`). Latest/history order by generated timestamp with ID as a deterministic tie-break. Phase 2 stores report payloads before assigning their report ID, so the authoritative row ID is attached on retrieval. Missing latest returns 404; missing history is empty. Invalid JSON/schema/version/timestamps or unreadable database returns a generic 503 without leaking paths or source content. Pydantic validates envelope and typed evidence timestamps; extensible nested analytics remain dictionaries. Zod validates the corresponding browser contract.

Freshness is deliberately conservative: an observation cutoff older than 24 elapsed hours is STALE, including weekends. Future cutoffs are explicitly flagged. Per-symbol timestamps also receive age warnings. RECENT means a recent saved snapshot, never a live quote or trading-readiness guarantee. Refresh re-fetches latest/history and preserves an older loaded report with a refresh-failure warning. Latest also polls every minute. No fabricated intelligence fixtures are presented.

## Deployment findings and required steps

This checkout has a FastAPI backend (including a separate legacy static UI) and a separately built Vite frontend. `frontend/.vercel/project.json` links `trade-decision-cockpit-preview`. The Vercel config only provides SPA routing; it does not proxy API requests. `VITE_API_BASE_URL` must point to an HTTPS backend with the matching allowed CORS origin. The Dockerfile builds Python only and does not copy `frontend/dist` or local `data/`.

Repository operational records describe an AWS ECS backend with PostgreSQL for existing application persistence. Phase 2 intelligence is a separate SQLite artifact store; PostgreSQL alone does not supply these reports. Live AWS inspection was attempted read-only but the AWS session has expired. The actual work-computer URL has not been provided, so its current backend and deployed build cannot be conclusively matched. This local implementation is not evidence that either hosted app has changed.

After approval:

1. Identify the work-computer URL/Vercel project and public API base, inspect deployed build SHA from `/health`, and check whether the new report endpoint exists. Inspect Vercel environment metadata without retrieving secret values. Reauthenticate AWS only as needed for read-only inventory.
2. Review all intended source changes independently of pre-existing unstaged work. Build/test an explicit reviewed commit; do not accidentally publish unrelated setup changes.
3. Provision durable report-artifact storage accessible to the report generator and backend. For this implementation, mount a persistent SQLite volume and configure `MARKET_INTELLIGENCE_DB` to that file. Do not rely on ephemeral ECS disk or the unrelated PostgreSQL database. For multiple replicas, implement a shared artifact repository before scaling. Keep generation and SQLite writes on a single owner.
4. Run the existing `python -m app.market_intelligence --help` workflow on the backend/authorized job with its server-only Massive secret. The CLI uses `--database`; make it match `MARKET_INTELLIGENCE_DB`. Review limits/entitlements before authorizing generation. This implementation did not fetch new market data.
5. Deploy the backend image with build identity, durable artifact mount and existing server secrets; preserve scanner/database/notification configuration. Verify health and latest/history schemas, stale/unavailable states, and existing endpoints.
6. Build Vite with `VITE_USE_DEV_FIXTURES=false` and the approved public `VITE_API_BASE_URL`; deploy the frontend separately. Never place Massive/Claude keys in any `VITE_` variable. Verify desktop/mobile navigation and that refresh reaches the intended backend.
7. Retain prior images/frontend deployment for rollback. No database migration is needed by these read-only endpoints.

## Claude key investigation

No Claude/Anthropic integration, key-setting UI, Claude SDK dependency, server credential configuration, or authentication/session layer was found in this checkout or the sibling Trade Decision Engine source worktrees. The React app's only localStorage use is `tde-watchlist`; it contains symbols. No sessionStorage or cookie credential handling was found. The backend models/configuration contain no Claude credential storage. The local Vite configuration contains no Claude integration. These findings apply to the inspected source, not an unidentified remote deployment.

We therefore cannot attribute the deployed key to localStorage, sessionStorage, cookies, a database, or Vercel environment variables yet. Device-specific recognition is consistent with browser-local state or different login/deployment sessions, but is not proof of any storage mechanism. Do not copy the work browser's key or export browser storage. Obtain the exact URL and corresponding source first; inspect storage key names and boolean presence only, cookie names/flags only, request destinations/header names only, database schema only, and Vercel environment variable names/scopes only. Never capture values, HAR files containing credentials, response bodies with credentials, or raw headers.

**Claude API access is not required for these reports.** Phase 2 calls Massive and computes reports deterministically in Python. Phase 3 only reads saved reports. No Claude dependency or paid Claude calls were added.

A credential proxy is not presently compatible as an authenticated feature: the inspected app has no user identity/session contract to authenticate it, and the reported deployed Claude feature is absent. Adding an anonymous proxy or browser-held shared backend secret would introduce a billable exposure. Accordingly no speculative Claude endpoint or credential migration was added.

For the actual Claude-enabled application, implement: identity-provider login with a server session (Secure, HttpOnly cookie, appropriate SameSite/CSRF protection); backend authorization on every Claude request; provider key stored in a backend secret manager/server environment only, never returned to the browser or a public build variable; a constrained report-summary endpoint with validated inputs, model/token/time limits and sanitized errors; atomic shared per-user rate limits and global daily spend/concurrency quotas, returning 429 when exceeded; redacted metadata-only audit logging. Keep the endpoint disabled until identity, secret injection and distributed limit storage are configured and verified. Test unauthorized/forbidden requests, CSRF, quota exhaustion, cross-user isolation and secret-free responses before deployment. Do not change the existing deployed credential without approval.

References: [Claude authentication](https://platform.claude.com/docs/en/manage-claude/authentication), [OWASP REST security](https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html).

## Validation and preview

Backend suite: 337 passed, 11 skipped (environment-dependent tests), one existing Starlette/httpx deprecation warning. Frontend: 45 tests passed. Production Vite/TypeScript build passed with a bundle-size advisory. `git diff --check` passed. The configured lint command cannot run because the repository has no ESLint 9 flat config; no lint result is claimed.

Local preview: `http://127.0.0.1:4173/market-intelligence`, proxied to `127.0.0.1:8765`. Preview backend runs with lifespan disabled and scanner/notifications disabled; it supports saved-report inspection without starting background/provider services. Existing report API was HTTP-checked locally. Browser visual inspection was not performed.

Exact files added/modified by Phase 3:

- `app/intelligence_api.py` (new)
- `app/main.py`
- `frontend/src/App.tsx`
- `frontend/src/api/client.ts`
- `frontend/src/pages/MarketIntelligence.tsx` (new)
- `frontend/src/pages/MarketIntelligence.css` (new)
- `frontend/src/test/market-intelligence.test.tsx` (new)
- `tests/test_intelligence_api.py` (new)
- `docs/MARKET_INTELLIGENCE_PHASE3.md` (new)
