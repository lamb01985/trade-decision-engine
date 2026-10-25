# TradeHub Market Map — 2026-10-26 pre-session sample

Generated from Massive on October 25, 2026. Observation cutoff:
`2026-10-23T04:00:00+00:00`, representing the October 23 ET daily window.
October 26 outcomes are correctly `PENDING`.

- Regime: `MIXED` across SPY, QQQ, IWM, and SMH ETF proxies.
- Swing: SMH, MU, and CRWV screened bearish; the other five symbols were neutral.
- Intraday carry-forward map: SPY, NVDA, MU, and CRWV had aligned bearish
  5m/15m/1h structure in the completed October 23 session; the other symbols were neutral.
- QQQ 2026-10-26 chain: 226 contracts observed in the bounded window; 63 met
  OI ≥ 100 and spread ≤ 10%.
- SPY 2026-10-26 chain: 226 contracts observed; 55 met that same liquidity screen.
- No data errors were returned.

The full generated JSON and Markdown are runtime artifacts under
`data/market_intelligence_reports/` and can be reproduced with:

```bash
.venv/bin/python -m app.market_intelligence --as-of 2026-10-26
```

No unusual/institutional activity is inferred. No trade execution is implemented.
