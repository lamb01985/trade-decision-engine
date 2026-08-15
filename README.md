# Trade Decision Engine

See [REALTIME_DATA.md](REALTIME_DATA.md) for the stock-data path, freshness safety contract, and provider-backed Monday verification procedure.

## Historical baseline backtest

The backtest is a **non-optimizing, event-driven replay**. It invokes the same production analysis and `StandardDecision` path used by the scanner at each completed five-minute checkpoint; it does not define a separate setup library. Future minute bars are used only after a confirmed decision has been recorded to model execution and subsequent outcomes.

The initial workflow keeps the final holdout unavailable by default. It supports only one-minute source bars and the current regular session because changing either would change the production engine’s inputs rather than test the rules as currently defined. Historical data is cached under `data/historical_cache/`, while JSON and Markdown results are written under `data/backtest_reports/`.

With `MASSIVE_API_KEY` configured locally, run a small baseline before any larger experiment. The date ranges below are deliberately non-overlapping; the final holdout is fetched only when `--include-final-holdout` is supplied.

```bash
python -m app.backtest_cli \
  --symbols QQQ,SPY,IWM,NVDA,AMD,META \
  --dev-start 2025-01-02 --dev-end 2025-01-17 \
  --validation-start 2025-01-21 --validation-end 2025-01-31 \
  --holdout-start 2025-02-03 --holdout-end 2025-02-07 \
  --slippage-bps 0 --fee-bps-per-side 0
```

The runner uses a next-bar-open entry, optionally applies adverse slippage and per-side fees, records outcomes in R, treats same-bar stop/target collisions conservatively by default, and emits five representative event histories. Use `--same-bar-policy AMBIGUOUS` when you prefer to exclude unorderable OHLC-bar collisions from measured results. Do not enable `--include-final-holdout` until strategy definitions, parameters, and execution assumptions are frozen.


## Local live radar

The refreshed dashboard adds a local monitoring workflow without adding broker execution or automatic orders. Start the app as usual, open `http://127.0.0.1:8000`, then select **Live radar**. While that browser tab remains open, the radar scans the selected universe every 60 seconds by default and records only meaningful `RADAR`, `TRIGGERED`, and `INVALIDATED` transitions. The interval can be changed to two or five minutes from the page.

Use **Enable browser alerts** to opt into local browser notifications. Alerts include the setup transition, trigger, invalidation, and structural risk/reward context; they remain decision-support notifications, not instructions to place a trade. The radar always displays `LIVE`, `DELAYED`, `STALE`, or `UNAVAILABLE` data status. Do not treat delayed or stale data as actionable.

The **Watchlist** now defaults to **Actionable A/B**, which hides `NO_TRADE`, `TOO_LATE`, `INVALIDATED`, and incomplete candidates. Use **Confirmed only** to show only completed `LONG_TRIGGERED` or `SHORT_TRIGGERED` setup states, or select **All rows** when you need diagnostic context. The quality/score header remains sortable for either descending or ascending rank order.

The local radar stops when the browser tab is closed or the local server is stopped. It is intentionally the first validation step before any future hosted, background notification service.

## Pushover phone notifications

Pushover delivery is an optional layer downstream of the existing scanner state machine. It does not create signals or keep scanning after the application server stops. Credentials remain in your local `.env`; they are never returned by the settings API or displayed in the browser.

1. Install Pushover on your phone and sign in.
2. In Pushover, create/register an application for the Trade Decision Engine.
3. Copy your Pushover User Key.
4. Copy the new application's API Token/Key.
5. Copy `.env.example` to `.env` if needed, then set `PUSHOVER_USER_KEY` and `PUSHOVER_APP_TOKEN` there.
6. Restart the application.
7. Open **Live radar**, expand **Pushover**, and choose the alert transitions you want.
8. Select **Send test notification**.

The defaults notify for A-quality forming setups near their existing scanner trigger, READY/CONFIRMED transitions, and subsequent INVALIDATED or TOO_LATE transitions. B and C setups are suppressed by default. Repeated scans of the same unchanged state do not resend the notification, and the durable deduplication state is stored locally in `data/notifications.sqlite3`.

`READY` delivery requires the scanner's existing `LIVE` regular-session data status. Delayed, stale, unavailable, closed-market, premarket, and after-hours data do not produce an actionable phone alert. Pushover delivery errors are logged but never interrupt scanner operation.

To add a link to each push, set `PUBLIC_APP_BASE_URL` to a phone-accessible base URL. Do not set it to `localhost` or `127.0.0.1`: those addresses refer to the phone itself when tapped. This project does not host or expose the app, so without an independently configured reachable URL the notification is intentionally sent without a link.


## Data-only NQ futures probe

The **Futures data** tab is a compatibility check, not a futures strategy. It resolves the active Massive `NQ` contract through the Futures Contracts API, then loads and normalizes raw one-minute futures bars through the Futures Aggregates API. The tab intentionally does **not** invoke the setup engine, session filter, decision path, trade plan, target mapping, scanner, alerting, or execution code.

Start the local application, open **Futures data**, and select **Load active NQ contract**. A successful response reports the dated active contract ticker, exchange metadata, raw-bar count, and the latest provider-returned OHLCV bar. If Massive returns a permissions error, verify that your existing API key includes a Futures plan; do not paste the key into chat or source files.

This probe does not make the current equity setup rules valid for futures. Futures sessions span the evening-to-next-day trading date, so any future attempt to run setup logic on NQ requires a separate, explicit session-semantics review and historical validation.


## Options intelligence

The **Options** tab remains a read-only, provider-backed evidence workspace. Its raw-chain view is now intentionally an **advanced detail**, beneath the primary Options Plan. It can load an underlying snapshot, active contract metadata for expiry discovery, and a bounded option-chain snapshot. It displays provider-returned contract details, bid/ask-derived midpoint and spread, last trade, volume, prior-day open interest, implied volatility, and Greeks where present.

The interface does **not** classify buy/sell intent, label activity as unusual flow, create a new setup, alter any equity setup state, or expose execution. Open interest is prior-day data; missing quote, trade, or Greek fields remain visibly unavailable rather than inferred. Explicit REST refreshes are used; the API key remains server-side.

For a read-only provider-data audit, open **Options**, expand **Advanced details / raw option-chain evidence**, then select **Load raw chain**. The chain status reports `REAL_TIME` only if the provider returns real-time quote frames across the displayed contracts; otherwise it reports `AVAILABLE`. If the request fails, verify your existing Massive entitlement without sharing the key in chat.


## Phase 1 Options Plan

The **Options** tab now begins with one read-only **Options Plan**. It first loads the existing frozen `StandardDecision` for the underlying and does not alter setup rules, parameters, confirmation gates, session semantics, targets, invalidation, quality grade, or `action_now`. It then decides whether an option contract may be evaluated as a downstream expression of that existing decision.

The underlying veto is hard. `NO_TRADE`, `NO_SETUP`, `INVALIDATED`, `TOO_LATE`, `DO_NOT_CHASE`, non-A/B quality, a missing direction, or a plan missing any of trigger, invalidation, T1, or T2 prevents any provider option-chain request. The result is a prominent **PASS — UNDERLYING SETUP TOO WEAK**, **TOO LATE — DO NOT CHASE**, or **NOT ENOUGH DATA** state. A contract can never rescue an ineligible underlying setup.

For an eligible A/B long or short setup, the contract type is derived from the frozen decision: `LONG` evaluates calls; `SHORT` evaluates puts. A waiting setup can produce **WAIT FOR UNDERLYING TRIGGER** with a contract-quality review, but it does not authorize an entry. Only the pre-existing `LONG_TRIGGERED` or `SHORT_TRIGGERED` action can produce the plan label **GOOD TRADE — CONFIRMED**. The application remains read-only with no broker or order capability.

### Visible Phase 1 methodology

| Judgment | Phase 1 method | What it does not claim |
|---|---|---|
| Setup Quality | The existing frozen `StandardDecision.quality`; no options score substitutes for it. | Probability of winning. |
| Contract Quality | The contract must match the underlying direction, have a usable bid/ask-derived spread, a returned delta in the selected absolute-delta range, returned OI at or above the selected minimum, a displayed spread inside the selected cap, and a `GOOD` or `PLENTY` time assessment. Among passing contracts, `EXCELLENT` requires OI at least 5× the selected minimum, positive returned volume, spread at or below 30% of the selected cap, and delta within 0.08 of the selected delta-band midpoint. `GOOD` requires OI at least 2× the selected minimum and spread at or below 60% of the cap. Other passing contracts are `ACCEPTABLE`. | Flow intent, market prediction, or a guaranteed trade outcome. |
| Time | `DANGEROUS` at 0–1 DTE, `TIGHT` below the selected working-horizon minimum, `GOOD` within the selected horizon, and `PLENTY` above it. | A forecast of how long the underlying move will take. |
| Price / IV | Always **INSUFFICIENT DATA** in Phase 1. | That an option is cheap, fair, expensive, underpriced, or overvalued. |
| Scenario risk/reward | Not implemented in Phase 1. | Estimated option values or guaranteed option returns at invalidation, T1, or T2. |

Volume is used only when returned: it can support stronger liquidity quality, but an absent volume field does not fabricate a value or automatically reject an otherwise observable contract. Missing bid/ask, delta, or OI prevents a contract-quality recommendation because the displayed Phase 1 criteria cannot be verified. Advanced settings and the raw provider chain remain collapsed below the primary plan for auditability.

To use it, start the application, open **Options**, enter an equity or ETF underlying, and select **Build options plan**. Verify the returned `REAL_TIME`/`AVAILABLE` status and provider fields with your own Massive entitlement. The code deliberately treats the actual returned response—not an account-plan label—as the authority for field availability. Never paste the API key into chat or source files.
