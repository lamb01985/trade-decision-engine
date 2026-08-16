# Phase 4B — Historical Replay Engine

## Status and scope

Phase 4B implements chronological, research-only replay through the unchanged
production evaluation pipeline. It does not implement outcome evaluation,
strategy optimization, trading, alerts, notifications, WebSockets, context
features, or deployment. Phase 4A.3 production acceptance is recorded in
`PHASE_4_RESEARCH_LEDGER_DESIGN.md`.

## Architecture

`Massive adjusted one-minute aggregates -> local range cache -> chronological
completed-bar visibility boundary -> evaluate_production -> detect_candidates
denominator -> existing research-ledger identity/observation/coverage adapters
-> isolated SQLite replay database`

`app.historical_replay` preloads provider data for efficiency, but the only
object passed to `evaluate_production` is a newly constructed prefix whose
timestamps are at or before the current cutoff. The engine call is the same
`app.engines.pipeline.evaluate_production` call used by live analysis. No replay
copy of detector, score, ranking, trigger, invalidation, target, or confirmation
logic exists.

The default production universe remains the source of symbol selection.
The CLI deliberately requires explicit symbols for bounded validation:

```text
python -m app.historical_replay --symbol NVDA --start 2026-08-10 \
  --end 2026-08-12 --output data/replay/nvda.sqlite3
```

Only SQLite output filenames are accepted. The output parent is local and
created on demand. The replay path has no signal store, scanner-state store,
notification service, broker, AWS, or deployment call. It cannot write to the
production PostgreSQL database through its public configuration.

## Chronology and bar visibility

Massive aggregate timestamps are treated as interval starts. A one-minute bar
stamped `10:04:00 ET` is visible at `10:05:00 ET`. The current production
detector evaluates on five-minute boundaries using the visible one-minute bar
ending at minute `:04`, `:09`, and so on.

- **1m:** visible only after the complete minute. No snapshot/in-progress bar is
  used by replay.
- **5m:** composed only from five completed one-minute bars. A `10:00` bucket is
  detector-visible after the `10:04` source bar completes at `10:05`.
- **15m:** composed from completed visible one-minute bars. At five-minute
  decision checkpoints, the shared production analyzer may expose a partial
  resampled 15m value exactly as live production currently does; replay does
  not improve or reinterpret this behavior. No source minute later than the
  decision cutoff is included.
- **Higher intraday:** the current engine has no timeframe above 15m; replay
  fabricates none.
- **Daily/prior session:** prior-day high, low, and close use only earlier
  regular sessions. The current session's eventual daily high, low, close, or
  volume is never supplied as a daily fact.
- **Premarket:** 04:00–09:29:59 ET bars are carried in the visible prefix only
  for the production analyzer's premarket high/low context. They do not create
  occurrences under `occurrence-v1`.
- **Regular session:** evaluation checkpoints run from completed regular bars
  in 09:30–15:59:59 ET. Missing internal five-minute checkpoints become
  `NOT_OBSERVED`/`DATA_UNAVAILABLE`, not negative setup evidence.
- **Regular close:** remaining intraday instances close at 16:00 ET as
  `SESSION_ENDED`. State resets before the next session's first evaluation.

The current engine's 15m partial-bucket behavior is a documented parity
constraint and a known limitation. Changing it would change live trading
semantics and is outside this phase.

## Point-in-time session reconstruction

At each cutoff, `analyze_symbol` receives the complete historical lookback plus
only the current-session prefix. HOD/LOD, session VWAP, volume, EMA, ATR,
relative volume, opening range, and mapped levels are therefore derived only
from visible bars. The opening range uses the existing first-30-minute rule.
Prior-session OHLC comes from completed earlier sessions. Premarket high/low
uses only premarket bars already completed by the cutoff.

The replay does not call a function with a completed current day while asking
it to make an earlier decision. Tests instrument the shared production call and
assert every input timestamp is at or before its cutoff.

## Data source and caching

The source is the existing Massive REST adapter's adjusted one-minute equity
aggregates, including OHLC and volume. The adapter currently does not retain
Massive transaction count or provider aggregate VWAP; production computes
session VWAP from visible OHLCV, so replay does the same for parity. Prior-day,
premarket, opening-range, and current-session values are reconstructed from the
minute stream. No daily endpoint or WebSocket is required.

`HistoricalBarCache` stores one local JSON range per symbol/date range. A warm
repeat makes zero provider requests. Raw history is not copied to PostgreSQL.

## Identity, provenance, and isolation

Replay uses the existing `setup-instance-v1` implementation and its family
anchors. The replay run ID is SHA-256 over replay contract, universe, requested
dates, versions, parameter snapshot, and canonical source bars. Repeating the
same input produces the same run ID and setup-instance IDs.

Every replay observation carries `source_mode=REPLAY` and `replay_run_id` in
both `data_quality` and `feature_manifest`. Live calls default to
`source_mode=LIVE`. Replay cycles use `scanner_scope=historical-replay` and the
same deterministic run ID. Replay databases are physically separate from live
production data. Observation UUIDs and wall-clock database creation times are
irrelevant runtime metadata; deterministic validation compares the complete
business-semantic stream.

## Coverage and gaps

Every expected symbol receives one coverage row at every materialized replay
checkpoint:

- successful evaluation plus candidates: `QUALIFYING_CANDIDATES`;
- successful evaluation plus no detector candidates: `NO_SETUP`;
- missing checkpoint/history or evaluation failure: `NOT_OBSERVED`, with
  `DATA_UNAVAILABLE` and a bounded error code.

Five-minute boundaries between a session's first and last observed checkpoint
are materialized even when every symbol is missing that bar. This prevents a
shared provider hole from disappearing from the denominator. A partial run is
reported as `PARTIAL`; a gap never advances absence reconciliation. Entire
sessions absent from every requested symbol cannot currently be distinguished
from an exchange holiday without a versioned exchange calendar and are a known
limitation.

## Replay-run metadata

The JSON run report retains run ID, source mode/provider, requested universe
and dates, engine/rules/parameter and research-contract versions, completion
status, sessions/cycles/source bars, symbol evaluations, instances,
observations, transitions, all coverage categories, gaps, requests/cache hits,
duration, and throughput. Cycle rows retain run ID and version metadata inside
the isolated ledger. Failed fetches exit without claiming a complete run;
partial chronological evaluation is labeled `PARTIAL`.

## Determinism and live parity

Two independent SQLite replays over identical cached input produced the same
run ID, setup-instance IDs, family/direction/session identities, market
timestamps, lifecycle states, recommended actions, event/closure types, and
semantic fingerprints. The comparison contained 607 observation rows and was
identical.

A parity test evaluates the exact same visible bars, prior decision, and
parameters through the shared pipeline twice (replay call and live-equivalent
call). Candidate evidence and `StandardDecision` business fields are equal.
No replay-only decision path exists.

## Initial real-data validation and performance

Validation used NVDA from 2026-08-10 through 2026-08-12, selected as a normal
bounded mechanical interval rather than from outcomes:

- sessions: 3;
- chronological five-minute cycles / symbol evaluations: 234 / 234;
- cached source bars including lookback: 8,445;
- setup instances: 251;
- observations: 607 (191 candidate, 165 absence-pending, 251 closure);
- non-candidate lifecycle/closure events: 416;
- qualifying / no-setup / not-observed coverage: 126 / 108 / 0;
- gaps/errors: 0;
- candidate states: 34 FORMING, 64 WATCHING, 82 TRIGGERED, 11 CONFIRMED;
- warm-cache requests/hits: 0 / 1;
- first measured replay: 7.99 seconds, 29.29 cycles/second and 1,057 cached
  source bars/second;
- deterministic repeat: 8.83 seconds, 26.51 cycles/second and identical 607-row
  business stream.

The ledger wrote 1,326 logical facts for this run: 251 instances, 607
observations, 234 cycles, and 234 symbol-coverage rows. At the measured
single-symbol rate, a 13-symbol universe projects to approximately 35–38
seconds per session, 13 minutes for 21 sessions, and 2.6 hours for 252 sessions,
before cold-provider latency. Cold request volume is one aggregate range request
per symbol when the date range fits Massive's current result limit; the present
adapter does not yet paginate longer ranges.

The instance count is intentionally the complete pre-selection detector
denominator, including simultaneous families and recurrence after two-bar
absence closure. It must not be compared to alert count. Its volume warrants
continued identity-distribution review on additional mechanics-validation
samples, but no outcome-based merging or strategy change is justified here.

## Tests and safety

Focused replay/ledger tests cover no-lookahead prefixes, five-minute completion,
point-in-time HOD/LOD/VWAP/opening range/prior day, session reset and close,
deterministic identity/streams, live parity, source provenance, provider gaps,
NO_SETUP versus NOT_OBSERVED, and SQLite-only output. Existing research-ledger,
notification, scanner, production persistence, and engine tests provide the
remaining isolation and unchanged-logic regression coverage.

Validation results:

- focused replay + ledger: 17 passed;
- complete default suite: 210 passed, 11 optional database tests skipped;
- complete PostgreSQL-enabled suite: 221 passed;
- `compileall`: passed;
- `git diff --check`: passed.

## Known limitations and safe next step

- Exchange holidays and early closes are inferred only from returned bars; a
  versioned exchange calendar is required before broad historical runs.
- The Massive aggregate adapter has a fixed 50,000-row request limit and no
  pagination, so long ranges must remain bounded or gain validated pagination.
- Cache files have no TTL/correction manifest. Run identity hashes actual bars,
  so corrected data creates a distinct run ID, but provider revision metadata
  is unavailable.
- The engine currently derives VWAP from typical-price OHLCV and exposes a
  partial current 15m resample at five-minute checkpoints. Replay preserves
  these live semantics.
- Replay speed is sequential and correctness-first.
- No outcome assessment has been implemented or written.

The safe next step is a reviewed Git checkpoint for Phase 4B mechanics, followed
by a small additional multi-symbol/early-close-calendar validation. Phase 4C
outcome evaluation should not begin until that review accepts the current
identity distribution and the exchange-calendar/pagination limits are either
bounded operationally or resolved.

## Phase 4B.1/4B.2 acceptance audit and correction — 2026-08-16

The acceptance audit found that opening-range research anchors incorrectly used
the numeric price of a deduplicated `PriceLevel`. Level deduplication can merge
the raw opening-range boundary with moving VWAP, EMA, HOD/LOD, swing, or current
price levels and average the result. Production deliberately continues to use
that merged trigger. Research identity now uses the immutable raw
`InstrumentAnalysis.opening_range_high` or `opening_range_low` while retaining
the merged trigger unchanged. `historical-replay-v2` versions this corrected
identity stream.

The identical NVDA 2026-08-10 through 2026-08-12 input changed from 251
instances/607 observations to 224 instances/555 observations. Qualifying and
NO_SETUP coverage remained exactly 126 and 108. Each opening-range family now
has exactly one distinct anchor per session: breakdown instance counts are
29/23/1 by session and the 2026-08-12 breakout count is 12. Repeated active
polls reuse the instance; explicit expiration or two-bar disappearance can
still produce a later episode under the frozen recurrence contract.

Session reconstruction now uses `us-equities-session-calendar-v1`. It encodes
the recurring 13:00 ET closes before Independence Day, after Thanksgiving, and
on eligible Christmas Eves, consistent with the published
[NYSE holiday calendar](https://www.nyse.com/markets/hours-calendars). The strict default materializes every expected
five-minute boundary from 09:34 through the session's final `:59` source bar;
missing tail data therefore makes the run partial. Unscheduled exchange closes
not represented by v1 safely produce NOT_OBSERVED rather than a false complete
run.

The real SPY/NVDA 2025 Thanksgiving sample produced 78 normal checkpoints on
November 26, no session on the November 27 holiday, and 42 checkpoints on the
November 28 early close. Both symbols retained premarket context, selected
November 26 as the prior session, and closed remaining instances at 13:00 ET.
There were 240 successful symbol evaluations: 128 qualifying, 112 NO_SETUP,
zero NOT_OBSERVED, and zero gaps. Two independent runs had identical instance,
observation, lifecycle, coverage, and session-boundary streams.

Massive's [stock aggregate documentation](https://massive.com/docs/rest/stocks/aggregates/custom-bars)
defines a maximum `limit` of 50,000 base aggregates and returns
`next_url` for continuation. The adapter now follows continuation URLs with a
10,000-page safety bound, removes any embedded credential before applying its
configured key, rejects foreign hosts/cycles/malformed pages, verifies strictly
chronological output, and rejects a terminal 50,000-result page as possibly
truncated. The earlier 8,445-bar NVDA sample was not affected. Without
pagination, the conservative maximum for a liquid symbol with 04:00–20:00
minute bars would be about 52 full trading sessions; multi-month/year requests
could previously have been incomplete.

Final Phase 4B.2 focused validation passed 35 tests. The final PostgreSQL suite,
compile, and diff checks are recorded in the completion report. Trading logic,
notifications, outcomes, AWS, and production deployment remain unchanged.
