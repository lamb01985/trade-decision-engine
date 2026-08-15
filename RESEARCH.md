# Research and historical replay

The research module replays Massive minute bars chronologically through the same `build_standard_decision` function used live. It is an evidence collection foundation, not proof that any setup is profitable.

## Preventing lookahead

At each completed 5m checkpoint, the live engine receives only bars whose timestamp is at or before that checkpoint. Levels, VWAP, ATR, trends, setup state, and score are recomputed from that visible slice. Future bars are passed only after a confirmed occurrence has been recorded, and only to measure outcomes. A same-bar target/invalidation collision is conservatively counted as invalidated because OHLC bars cannot reveal intrabar ordering.

## Recorded occurrence fields

Symbol, timestamp, development/validation period, setup family, direction, trigger, invalidation, structural targets, MFE in R, MAE in R, outcome in R, time to target, and time to invalidation.

Aggregates are sample size, win rate, average win, average loss, expectancy, median R, average MFE, and average MAE. Expectancy is:

```text
(win_rate × average_win) - (loss_rate × average_loss)
```

Open/unfinished outcomes remain 0R; reports must distinguish these from wins and losses when interpreting results.

## Development and validation

The command requires non-overlapping development and validation date ranges. Parameter ideas belong only in development. Freeze parameters before examining validation results. Positive development results alone do not justify deployment or an A+ label.

## Run one test manually

With `MASSIVE_API_KEY` configured:

```bash
python -m app.research NVDA \
  --dev-start 2025-01-02 --dev-end 2025-03-31 \
  --val-start 2025-04-01 --val-end 2025-06-30
```

The command prints JSON with occurrences plus separate development and validation aggregates.

## Signal log

Live meaningful observations are stored in `data/signals.sqlite3`. The `signals` table records the full decision context, RVOL, ATR, mapped levels, and the exact parameter snapshot. `signal_prices` stores subsequent observed highs/lows. Later observations update MFE, MAE, target hits, invalidation hits, and hit timestamps. It records market evidence only—not user trades, account P/L, or recovery goals.

## Future A+

There is deliberately no live A+ grade. A future research-backed classifier may consider A+ only after a sufficiently large sample, positive development expectancy, positive out-of-sample expectancy, acceptable MAE, adequate structural R/R, and a current match to the validated setup. Even then, neither A+ nor historical win rate would guarantee an individual outcome.
