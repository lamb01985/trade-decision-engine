# Engine boundary implementation

Security work continues from 2e16d4e on security/edgeiq-alpha-api-boundary.
The existing untracked EDGEIQ-ALPHA-SECURITY-PLAN.md remains untouched and has a
private preservation copy under ~/TradeHub-preservation/security-hardening-20261031.

Implemented fixed method/path admission, separate read/admin bearer audiences,
collision/malformed configuration rejection, minimal anonymous health, private
errors, disabled full-app docs, and explicit startup authorization. Scanner
POST requires admin because it can send notifications. No browser token input,
Massive credential change, strategy change or live execution was added.

Existing full-app contract tests now send explicit synthetic authenticated
headers. Standalone Thesis/Unified fixtures retain their own credentials.
Independent EGM design and code reviews are recorded in ENGINE-API-BOUNDARY-REVIEW.md.

The security-only preview shares the full route registry and authorization but
never starts the production lifespan or executes live handlers. Authorized
sensitive requests return PREVIEW_LIVE_SERVICES_DISABLED, not fabricated data.
It is not a live-data or Company Intelligence acceptance environment.

Local validation before final commit: 31 focused security regressions passed;
full Python suite passed 468 tests, none skipped, with isolated PostgreSQL.
The unchanged engine React frontend passed 45 tests and its production build.
TradeHub's exact containment branch passed all 80 tests again. Existing
warnings: Starlette/httpx deprecation and large
frontend chunk. No new dependency was installed.

Production remains unchanged in this workstream. The current public Python
production /api/notifications/settings still responds without authentication.
Three historical cockpit Preview URLs (90rncy7on, fhqw89usb, r4uyg33id) are public;
their /api/health path is an HTML SPA fallback, not proof of a working backend.
Do not treat their UI status as authenticated live data. Older immutable Python
project deployments redirect to Vercel login, but its public production alias
does not. These require a separately approved access-control/production rollout.

This branch includes preserved pre-existing Thesis/market work in its ancestry.
A focused security PR must target a checkpoint of 2e16d4e, not main, so it does
not present unrelated work as an approved production merge. Existing Node
operator authentication remains unchanged; direct browser-to-engine legacy
clients require a server relay with the new credentials before activation.
