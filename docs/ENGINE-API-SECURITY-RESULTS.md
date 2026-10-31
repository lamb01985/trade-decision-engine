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

## Verified protected deployment

Preview URL: https://trade-decision-engine-j1a2wdy02-sarah-lambertsons-projects.vercel.app
Deployment: dpl_HSTYGmeGz3tkznBS81AsHojUFcJz.
Source implementation commit: 2a6187d8f8f1b7985b7762c2a7dd39b5a33ef4ab.
Subsequent branch changes document results and disable automatic deployments
for the two review branches; they do not change deployed application code.

Vercel Authentication returned HTTP302 anonymously. Behind that boundary, 12
hosted checks passed: minimal anonymous health, authenticated preview identity,
empty HEAD, anonymous notification/research/scanner denial, read-write denial,
admin live-service unavailable, disabled analysis, denied OpenAPI and wrong
audience rejection. Every tested application response was no-store. Generated
preview-only authentication tokens remained server-side/in memory and were not
written to repository or CLI arguments. No Massive credential was provisioned.
Temporary URL-scoped Vercel test access was revoked after verification.
Python Production target remained dpl_7ExkVRtBBaVPQe4P37kEgduYyyRU at6200826;
project protection policy remained all_except_custom_domains.

TradeHub PR https://github.com/lamb01985/trade-hub-1/pull/6 contains only the
verified 88fb367 patch, five files, against unchanged main74fa880. It was open
and mergeable when checked; no merge occurred. Production remains88fb367 and
the credential endpoint returns its exact410 retirement response. Its immutable
URL is Vercel protected; the public production alias is not login protected.

The Python review base is a checkpoint of2e16d4e plus only the branch-specific
Git deployment guard. The PR intentionally does not target main. Root
vercel.json disables Git-triggered deployments for those review branches only;
manual security Preview packaging uses config/vercel.security-preview.json.
No production branch/configuration is changed. A separately reviewed production
port/rollout is still required to protect the existing public engine service.
