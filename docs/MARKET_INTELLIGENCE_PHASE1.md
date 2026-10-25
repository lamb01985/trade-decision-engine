# TradeHub Market Intelligence Phase 1

Phase 1 adds a read-only nightly market map without changing the existing scanner,
setup rules, broker boundaries, or frontend. It stores provider source batches and
reproducible reports in SQLite, and writes separate intraday and swing playbooks.

## Run locally

Configure the existing `MASSIVE_API_KEY` in `.env`, then run:

```bash
.venv/bin/python -m app.market_intelligence --as-of 2026-10-26
```

Defaults:

- Universe: `QQQ,SPY,IWM,SMH,AMD,NVDA,MU,CRWV`
- Database: `data/market_intelligence.sqlite3`
- Reports: `data/market_intelligence_reports/`
- Both adjusted and unadjusted daily source batches are retained.
- QQQ and SPY options use the nearest returned expiration and a ±15% strike window.

Use `--symbols` to change the universe, `--database` or `--output-dir` to change
destinations, and `--skip-options` when options should not be queried.

## Scheduling

Run once after the provider has finalized the regular session, for example at
18:15 America/New_York on trading days. A local cron entry is:

```cron
CRON_TZ=America/New_York
15 18 * * 1-5 cd /path/to/trade-decision-engine && .venv/bin/python -m app.market_intelligence >> data/market_intelligence.log 2>&1
```

For the existing AWS environment, prefer an EventBridge Scheduler invocation of
the existing ECS task definition with the module command above. That is an
operations change and is intentionally not deployed by this phase.

## Point-in-time and reproducibility contract

- Feature calculations receive only bars at or before the report cutoff.
- The report records provider request IDs when returned, retrieval timestamps,
  adjustment status, source bounds, row counts, payload checksums, engine/config
  versions, an input checksum, and a report checksum.
- Previous-day and previous-week values use completed preceding periods.
- Outcomes are stored separately. On a later run, the first available later daily
  bar evaluates unresolved hypotheses; it never feeds the original signal.
- A same-day daily-OHLC stop/target collision is conservatively recorded as
  `AMBIGUOUS_STOP_FIRST` because intraday ordering is unknowable.
- Static current-index membership remains unsuitable for historical universe
  backtests. A dated membership master is still required before broad historical
  cross-sectional claims.

## Verified Massive entitlements (2026-10-25)

| Data family | Status | Verification |
|---|---|---|
| Stock daily aggregates and snapshots | Available | Requested adjusted/unadjusted bars and AMD snapshot |
| Options reference, chain, Greeks/IV/OI | Available | Live QQQ, SPY and AMD calls |
| Options historical aggregates | Available | AMD option daily bars |
| Index aggregates | Not entitled | Massive returned `NOT_ENTITLED` for `I:SPX` |
| Futures contract reference/session aggregates | Available | ES reference and ESZ6 bars |
| Futures snapshot/trades/quotes | Not entitled | Massive returned `NOT_ENTITLED` |

ETF regime proxies are therefore used in Phase 1; they are not labeled as cash
indices. Futures are not part of the nightly map.

## Known limitations

- Catalyst/event data is not integrated, so reports explicitly make no catalyst claim.
- Option open interest is treated as prior-day data. Strike concentration is an
  observed OI concentration, not dealer positioning or institutional intent.
- The first-pass options screen is liquidity-only; it is not a volatility forecast
  or strategy recommendation.
- Intraday playbooks describe the most recently completed session. Before acting on
  a new session, refresh current intraday observations and require the stated
  confirmation.
- Relative strength is 20-session price return minus SPY return, not a factor model.
- No market-calendar dependency was added; scheduling must use a trading-day-aware
  operator or tolerate an unchanged cutoff on holidays.
