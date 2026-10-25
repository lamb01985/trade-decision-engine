# TradeHub Market Positioning — Phase 2

Phase 2 extends the Phase 1 nightly market map. It remains research-only: no broker,
order, execution, or institutional-identity inference is implemented.

## Verified Massive entitlements (live probes on 2026-10-25)

Representative contracts were `O:QQQ261026P00690000` and
`O:SPY261026P00740000`, observed for the October 23 session.

| Data | Result | Coverage / freshness |
|---|---|---|
| Contract and chain snapshots | Entitled | IV, delta/gamma/theta/vega, OI, day volume, quote and trade timestamps returned for QQQ and SPY |
| Historical timestamped quotes | Entitled | Nanosecond SIP timestamps and paginated NBBO results returned for both contracts |
| Individual option trades | Entitled at `/v3/trades/{ticker}` | Nanosecond SIP timestamps, price, size, conditions and pagination returned for both contracts |
| Documented `/options/v3/trades/{ticker}` alias | Not usable | HTTP 404 for both representative contracts; code does not use it |
| Historical option aggregates | Entitled | Adjusted daily OHLCV returned through October 23 for both contracts |
| IV and Greeks | Entitled in snapshots | Values were present for both representative contracts; Massive may omit Greeks for some deep-ITM contracts |
| Open interest | Entitled in snapshots | Prior-session OI was present; it is not same-day trade volume or directional positioning |
| FMV | Not tested/used | Paid proprietary FMV is outside this implementation |

Snapshot quote/trade fields for the representatives were real-time and last updated
near the October 23 options close. Historical quote/trade calls returned continuation
cursors. Phase 2 deliberately samples bounded recent pages and reports pagination as
incomplete when a cursor remains.

The MCP response does not expose account quota headers or an exact contractual rate
limit. Eight concurrent entitlement probes plus two corrected trade-route probes
completed without HTTP 429. This is a bounded observation, not a claimed quota.

## Implemented semantics

- OI is aggregated by strike, expiration and call/put type. Daily snapshots enable
  deltas only against an actually stored prior snapshot.
- Unusual-volume flags require at least five historical daily volume observations.
  New and insufficient-history contracts are labeled and cannot pass the baseline flag.
- Flow classification compares a trade only with the latest quote at or before its SIP
  timestamp and requires a quote no more than one second old. It estimates aggressor
  side; it cannot identify owner, institution, opening, or closing intent.
- Expected move uses the ATM call-plus-put midpoint. IV rank/percentile require 20
  prior stored daily chain observations. Realized volatility uses backward-looking
  log returns.
- Gamma output is scenario analysis. OI and Greeks are observed; dealer/customer signs
  are explicit assumptions. Concentrations are not verified support/resistance.
- Options evidence never overrides stock price invalidation.

## Run

```bash
cd /Users/sarahlambertson/Desktop/trade-decision-engine
source .venv/bin/activate
python -m app.market_intelligence --as-of 2026-10-26
```

Focused and full tests:

```bash
.venv/bin/python -m pytest -q tests/test_options_flow_engine.py tests/test_market_intelligence.py tests/test_massive.py
.venv/bin/python -m pytest -q
```

The command writes JSON and Markdown under `data/market_intelligence_reports/` and
persists reproducibility data in `data/market_intelligence.sqlite3`.
