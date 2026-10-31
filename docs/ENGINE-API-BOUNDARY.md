# Engine API boundary

This security-only increment continues security/edgeiq-alpha-api-boundary at
2e16d4e. It does not activate market data, financial research or production.
The existing untracked EDGEIQ-ALPHA-SECURITY-PLAN.md is preserved separately.

## Contract

The full FastAPI application denies requests before routing, parsing bodies,
reading artifacts or contacting providers unless an exact approved route and
permission match. No browser receives a service token. Node operator login is
unchanged. Full-app docs/OpenAPI are disabled. Unknown routes and new methods
require an explicit policy addition, including GET routes.

Only GET/HEAD of /health, /api/health and /api/v1/health is anonymously public:
the response is exactly {"status":"ok"}, private/no-store, with no capability,
configuration or build details. A supplied invalid credential is rejected and
cannot downgrade to anonymous health. This proves process liveness only.

ENGINE_API_TOKEN grants approved legacy GET/HEAD reads, including research,
scanner status, charts and existing analysis. ENGINE_ADMIN_API_TOKEN with a
different audience grants these reads and the exact notification settings PUT,
notification test POST and scanner POST routes. Scanner POST can send alerts,
so it is privileged. Analysis GET can update internal caches/research records;
read permission means no notification/settings administration, not no disk writes.
No broker execution is added. Unknown writes are denied even to administrators.

Both tokens must be distinct, printable ASCII, at least 32 characters. Read
configuration is required for legacy access; admin configuration is optional and
missing admin access fails closed. Tokens also cannot equal the existing
MARKET_DATA_SERVICE_TOKEN. Comparisons use constant time; duplicate credential
or audience headers, encoded/noncanonical paths and query-token attempts do not
authenticate. Audiences are edgeiq-engine-v1 and edgeiq-engine-admin-v1.

Exact GET Thesis and Unified resource routes retain their own token/audience
checks. There is no prefix exemption. Legacy tokens cannot cross that boundary.
Standalone existing market-data and Thesis fixture applications keep their
current restrictions. Authenticated responses and failures are private/no-store.

Background scanning or notification startup requires valid read/admin security
configuration before providers or stores initialize. API protection does not
authorize background work. The isolated security preview mounts the same full
route handlers and middleware, but does not run the production lifespan, load
provider clients, start scanners, send notifications, or initialize real stores.
Sensitive authorized routes return explicit unavailable responses there. It
verifies the API boundary, not live financial functionality.

## Implementation and acceptance

Use an outer ASGI middleware with explicit route patterns/method sets. Keep
existing handler response contracts for authorized clients. Use synthetic tokens
in tests and explicit authenticated clients for existing API tests. Enumerate
all current routes, prove anonymous rejection before provider/notification/store
effects, verify read/admin and service audiences, invalid/missing/colliding
configuration, health downgrade, path/header aliases, and future routes.

Deploy only a Vercel Preview artifact with no production alias change. Verify
Vercel Authentication before upload. A preview-only entrypoint and configuration
exclude .env, data, virtual environments and private artifacts. Provision only
new preview authentication tokens server-side, never Massive credentials. These
tokens must not appear in commands, logs, repository or browser responses.
Verify anonymous Vercel login, application denials behind the platform boundary,
and correct authenticated health. If protected access cannot be tested without
changing production policy, report that limitation and do not weaken protection.

The existing public Python production and old public cockpit deployments remain
unchanged. Audit their public GET exposure without invoking analysis, sending
notifications or mutating settings. Production protection needs a separate
approved rollout; a protected preview does not contain older public services.

Run full Python tests with isolated PostgreSQL plus focused security regressions.
Run existing React tests/build because backend authentication contracts affect
its clients, even though UI source is unchanged. Preserve old branch/worktree
changes and review the exact staged files before commits/pushes. GitHub main
push triggers a production workflow, so never push or merge main in this work.

## Binding implementation decisions after independent review

This section overrides ambiguous wording above. Authentication headers are
Authorization: Bearer <token> and X-TradeHub-Audience: <audience>. Missing or
malformed/duplicate credentials return 401 AUTHENTICATION_REQUIRED. Wrong
audience and forbidden method/path return 403 ACCESS_DENIED. Invalid legacy
configuration returns 503 AUTHENTICATION_UNCONFIGURED. Responses contain only
{"error":{"code":<code>}} and private/no-store. Query strings never authenticate;
otherwise valid header credentials remain subject to handler query validation.

The fixed read registry is GET/HEAD of /, the three health aliases,
/api/readiness, /api/v1/readiness, /api/scanner/status, /api/v1/scanner/status,
/api/analysis, /api/notifications/settings, /api/v1/notifications/status,
/api/futures-probe/nq, /api/scanner, /api/v1/research/evidence,
/api/v1/research/excursions, /api/v1/market-intelligence/latest and /history;
plus /api/options-plan/{symbol}, /api/v1/options-plan/{symbol},
/api/options-shortlist/{symbol}, /api/options-intelligence/{symbol},
/api/analyze/{symbol}, /api/v1/analysis/{symbol}, /api/v1/chart/{symbol}.
Symbol grammar is [A-Za-z][A-Za-z0-9.-]{0,14}. Existing static files under
/static/ allow only ASCII letters, digits, slash, dot, underscore and hyphen;
filesystem resolution remains StaticFiles' responsibility. Empty segments,
backslashes, percent encodings and dot/dot-dot segments are rejected first.
All OPTIONS requests and trailing-slash aliases are denied; same-origin server
relays need no cross-origin browser access. No GET automatically joins this list.

The admin write registry is exactly PUT /api/notifications/settings, POST
/api/notifications/test, POST /api/market-scanner, POST /api/v1/scanner.
Admin credentials may read too. Both scanner POST handlers remain unchanged.
No other writes are permitted. In particular admin cannot authorize arbitrary
new POST paths. Thesis delegation is only GET /api/v1/thesis/company with
audience thesis-v1. Unified delegation is only GET
/api/v1/market-data/{capabilities,snapshot,bars,options,intelligence}, audience
market-data-v1. These branches validate their service credential before routing,
then retain existing handler checks. They never inherit legacy authorization.

Collision of any nonempty read/admin/service tokens disables every sensitive
branch with 503, including delegated service routes. Invalid optional admin or
service token format also disables all sensitive access. Missing service token
denies service routes; missing read denies legacy routes; missing admin denies
admin writes. All token validation/comparison uses bytes after ASCII validation.
Anonymous minimal health remains available during bad configuration; any
Authorization or audience header opts into authentication, so an invalid one
never becomes anonymous. Authenticated normal health preserves its existing
capability/build contract. HEAD always omits response content.

Create app/security.py, app/security_preview.py, tests/auth_client.py,
tests/test_engine_security.py and config/vercel.security-preview.json. Modify
app/main.py to install outer admission, disable docs and check startup before
any stores/provider constructors. If background scanning OR notification
delivery is enabled, startup requires valid read/admin configuration AND
ENGINE_BACKGROUND_SERVICES_ENABLED=true; otherwise fail startup. Neither a read
token nor API authentication alone starts these services. Normal disabled
background behavior and approved handler contracts remain unchanged.

The new security_preview app copies the existing full app's route objects into
a separate FastAPI instance with no production lifespan. Its own admission
middleware runs normal authorization first, then intercepts every authorized
non-health request with 503 PREVIEW_LIVE_SERVICES_DISABLED. Its health response
is minimal for anonymous callers and reports SECURITY_PREVIEW to authenticated
legacy callers. No handler, lazy service, artifact read or notification runs.
This provides security evidence only; it is not a live-data Alpha preview.
Existing market_data/preview_app.py and thesis/dev_app.py are unchanged.

Legacy full-app API test modules opt into a synthetic-token fixture and use an
authenticated TestClient subclass that sends real request headers; middleware
is never patched out. Standalone router tests keep their existing clients and
service headers. New negative tests use raw TestClient with no default headers.
Startup tests explicitly authorize background work with synthetic configuration.
No test installs real credentials or calls real providers.

Preview packaging stages only app/, requirements.txt, an api/index.py importing
app.security_preview.app, and the preview config under a private temporary
directory. No .env, data, frontend, workflow, virtualenv or .git is uploaded.
Deploy manually to the existing trade-decision-engine project as Preview after
verifying its all_except_custom_domains policy and unchanged production target.
Supply newly generated preview-only engine read/admin tokens through an API
request held in memory (not CLI arguments). No Massive key is added/updated.
Vercel URL-scoped temporary access may be used for security tests and revoked
afterward; no project-wide bypass or production protection change is permitted.
Record authenticated results and verify production remains 6200826.

Phase order: reviewed policy; middleware and route/client regressions; full
isolated database tests and unchanged frontend build; narrow commit on existing
security/edgeiq-alpha-api-boundary; protected manual preview and exposure report.
Do not push this Python branch until the existing Vercel Git-trigger behavior is
verified to deploy only protected Preview. If access or packaging blocks safe
deployment, deliver the tested branch and exact blocker instead.

Test migration is exactly test_background_scanner.py, test_chart_api.py,
test_database_readiness.py, test_evidence_analytics.py,
test_excursion_analytics.py, test_options_phase1_integration.py,
test_platform_hardening.py and test_serverless_storage.py. Each imports
EngineTestClient as TestClient and the module-local autouse engine_security
fixture from tests/auth_client.py. That fixture installs synthetic environment
configuration before the test body/lifespan. Security configuration is read at
request/startup time, never captured at module import. No global TestClient
patch is used. Negative and standalone test clients remain untouched.

Authenticated security-preview health is exactly HTTP200
{"status":"ok","mode":"SECURITY_PREVIEW","live_services":false}.
Anonymous health remains {"status":"ok"}. HEAD sends no body. Only valid legacy
read/admin audiences authorize detailed preview health; service audiences fail.
For missing/invalid legacy configuration with backgrounds disabled, normal
lifespan initializes no provider/store/notification and serves only minimal
health plus fail-closed errors. Enabled backgrounds instead abort startup unless
all security and explicit background permission requirements pass first.
