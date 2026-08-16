# Phase 4A.1 — Research Measurement Contract

## Status, scope, and authority

This document freezes research-measurement semantics before any Phase 4 ledger
implementation. It introduces no runtime behavior, migration, table, setup,
threshold, notification, infrastructure, outcome evaluator, or ML component.

The production detector and Phase 3D decision-intelligence mapping remain the
only trading authority. The future ledger observes their inputs and outputs; it
must not promote a setup, repair missing evidence, or reinterpret a trading
state.

Contract versions defined here:

```text
occurrence_policy_version       occurrence-v1
identity_schema_version         setup-instance-v1
closure_policy_version          closure-v1
coverage_contract_version       scan-coverage-v1
context_contract_version        market-context-v1
regime_contract_version         market-regime-v1
measurement_contract_version    measurement-v1
```

## 1. Qualifying-occurrence rules

### Denominator boundary

A research occurrence begins when `detect_candidates` returns a
`CandidateEvidence` for a completed, chronologically visible bar prefix under
the current engine/rules/parameter version tuple.

This boundary is deliberately:

- before `build_standard_decision` selects and ranks one candidate;
- before equal-ranked opposing-direction conflict suppression;
- before quality, score, liquidity, distance, structural completeness, R/R,
  chase, alert-policy, preference, or notification filters;
- independent of whether the candidate later becomes READY or produces a good
  outcome.

Every candidate returned by the detector belongs in the denominator, including
simultaneous same-symbol candidates. A successful evaluation returning no
candidates is `NO_SETUP` scan coverage, not a setup instance. A failed, stale,
or unavailable evaluation is `DATA_UNAVAILABLE`/coverage failure, never
`NO_SETUP`.

The initial ledger observation preserves the engine state returned at that
instant. It does not normalize all entries to MONITOR. Phase 3D action mapping
remains exact:

| Current engine state or condition | Phase 3D recommended action |
|---|---|
| `FORMING` | `MONITOR` |
| `WATCHING` | `MONITOR` |
| `TRIGGERED` | `WAIT_FOR_CONFIRMATION` |
| `CONFIRMED` with current regular-session data | `READY` |
| `CONFIRMED` outside current regular-session data | `MONITOR` |
| `INVALIDATED` | `INVALIDATED` |
| `TOO_LATE` | `EXPIRED` |
| stale/error/unavailable evidence | `DATA_UNAVAILABLE` |
| `NO_SETUP` | `IGNORE`, coverage only |
| `MANAGE` | unavailable/reserved; no position state exists |

Only fresh regular-session hosted evaluations may create production research
occurrences in `occurrence-v1`. Closed-session rankings are preparation views,
not new occurrences. Historical replay uses the same regular-session rule at
each simulated timestamp.

### Per-family earliest qualifying policy

These rules describe the actual current detector; they do not make the setup
more permissive.

| Setup family | Earliest detector output included | Actual current qualifying boundary |
|---|---|---|
| `OPENING_RANGE_BREAKOUT` | `FORMING` | Sufficient completed 5m history and ATR exist, opening-range high exists, and 5m/15m trends are both bullish. `_break_candidate` then returns FORMING, WATCHING, TRIGGERED, or CONFIRMED from proximity/cross/acceptance and relative volume. |
| `OPENING_RANGE_BREAKDOWN` | `FORMING` | Same, using opening-range low and aligned bearish 5m/15m trends. |
| `BREAKOUT_RETEST` | `FORMING` | Sufficient history/ATR, bullish 5m/15m alignment, and a currently mapped resistance at or above the latest completed 5m close. The current `_break_candidate` state is retained. |
| `BREAKDOWN_RETEST` | `FORMING` | Sufficient history/ATR, bearish alignment, and a currently mapped support at or below the latest completed 5m close. |
| `VWAP_RECLAIM` | `TRIGGERED` | A completed sequence either crosses from below and closes above session VWAP plus the existing ATR buffer, or completes the current reclaim/hold/retest sequence. It is CONFIRMED only when that sequence and current relative-volume rule pass. Mere proximity to VWAP does not produce this candidate today. |
| `VWAP_REJECTION` | `TRIGGERED` | The current three-bar rejection sequence from below VWAP is present. Relative volume distinguishes TRIGGERED from CONFIRMED. |
| `FAILED_BREAKOUT` | `TRIGGERED` | The current break-above, close-back-below, retest, and failure-to-reclaim sequence exists at a mapped non-current-price level. Relative volume distinguishes TRIGGERED from CONFIRMED. |
| `FAILED_BREAKDOWN` | `TRIGGERED` | The mirror break-below, reclaim, retest, and hold sequence exists at a mapped level. |
| `TREND_PULLBACK` | `TRIGGERED` | Aligned 5m/15m trend, the existing pullback-to-EMA9 tolerance test, and the current resumption close through the prior completed 5m bar are present. Relative volume distinguishes TRIGGERED from CONFIRMED. |
| `MOMENTUM_CONTINUATION` | `TRIGGERED` | Aligned trend, the current pause-range test, and completed close beyond the pause bar plus buffer are present. Relative volume distinguishes TRIGGERED from CONFIRMED. |
| `COMPRESSION_BREAK` | `TRIGGERED` | The current fixed lookback compression range passes and the two-bar break/retest/acceptance sequence completes outside its bound. Relative volume distinguishes TRIGGERED from CONFIRMED. |

Consequences:

- Opening-range and mapped break/retest families can enter the research ledger
  before a trigger because the detector actually emits FORMING/WATCHING.
- The other current families do not emit a pre-trigger recognizable candidate;
  their full denominator begins at TRIGGERED or CONFIRMED unless a future rules
  version changes the detector.
- Phase 4 must not fabricate earlier MONITOR samples for families whose detector
  currently exposes none.

## 2. Family anchors

An anchor is the engine-owned object or completed-bar sequence that makes two
polls the same thesis. It is not the current market price and is never selected
using future outcome information.

All price values in an identity payload use a canonical decimal representation
defined by `setup-instance-v1` (four decimal places, matching current signal
identity compatibility). Timestamps are UTC ISO-8601 market timestamps. Lists
of level source types are sorted and deduplicated.

| Setup family | `family_anchor_type` | Canonical `family_anchor` payload |
|---|---|---|
| Opening-range breakout | `OPENING_RANGE_HIGH` | Session date, `OPENING_RANGE_HIGH`, canonical opening-range-high price, opening-range cutoff definition/version. |
| Opening-range breakdown | `OPENING_RANGE_LOW` | Session date, `OPENING_RANGE_LOW`, canonical opening-range-low price, cutoff definition/version. |
| Breakout retest | `MAPPED_RESISTANCE` | Canonical trigger price plus the matched `PriceLevel.types` that produced that mapped resistance. |
| Breakdown retest | `MAPPED_SUPPORT` | Canonical trigger price plus matched level source types. |
| VWAP reclaim | `SESSION_VWAP_RECLAIM` | Session date, role `SESSION_VWAP`, and first qualifying sequence's prior2/previous/latest completed 5m timestamps. Numeric VWAP at first observation is evidence, not the durable anchor identity. |
| VWAP rejection | `SESSION_VWAP_REJECTION` | Session date, role `SESSION_VWAP`, and the three completed 5m timestamps forming the first qualifying rejection sequence. |
| Failed breakout | `FAILED_STRUCTURAL_LEVEL` | Canonical failed level, matched level source types, and first qualifying prior2/previous/latest 5m sequence timestamps. |
| Failed breakdown | `RECLAIMED_STRUCTURAL_LEVEL` | Canonical reclaimed level, source types, and first qualifying sequence timestamps. |
| Trend pullback | `PULLBACK_RESUMPTION_BAR` | Direction, EMA9 role, pullback bar timestamp (`prior2`), resumption reference bar timestamp (`previous`), and canonical trigger (`previous.high` for long, `previous.low` for short). |
| Momentum continuation | `MOMENTUM_PAUSE_BAR` | Direction, pause bar timestamp (`previous`), canonical pause high/low, and breakout buffer definition/version. |
| Compression break | `COMPRESSION_WINDOW` | Direction, completed compression-window start/end timestamps, canonical range high/low, and acceptance/retest bar timestamps. |

The current `CandidateEvidence` exposes family, direction, state, trigger,
reasoning, and missing evidence, but it does not expose every timestamp or level
source listed above. A future implementation may pass through those facts from
the bars/levels already used by the detector. That is instrumentation, not a
change to detection. Until the provenance is exposed, production ledger capture
for that family is not complete enough to claim `setup-instance-v1` identity.

## 3. Setup-instance identity algorithm

### Canonical identity

For the first qualifying candidate in chronological order:

```text
identity_payload = canonical_json({
  "identity_schema_version": "setup-instance-v1",
  "symbol": upper(symbol),
  "session_date": exchange_session_date,
  "setup_family": setup_family,
  "direction": direction,
  "family_anchor_type": family_anchor_type,
  "family_anchor": canonical_family_anchor,
  "first_qualifying_bar_at": completed_decision_bar_timestamp,
  "engine_version": engine_version,
  "setup_rules_version": setup_rules_version,
  "parameter_version": parameter_version
})

setup_instance_id = "set_" + sha256(identity_payload)
```

The full digest is stored; a display prefix may be shortened, but database
identity may not rely on a truncated value. Canonical JSON has sorted keys,
UTF-8 encoding, fixed decimal strings, explicit nulls, and UTC timestamps.

### Continuation rules

An observation attaches to an active instance when all are true:

1. symbol, session, family, direction, and version tuple match;
2. the canonical anchor key matches, or the family explicitly defines a
   role-stable moving value (session VWAP) whose sequence anchor matches;
3. the instance has not reached a terminal closure;
4. no confirmed-absence closure has completed.

Repeated scanner polls—even from different ECS tasks—attach to the same active
instance. An append-only observation is emitted only for a meaningful change in
lifecycle, recommended action, confirmation vector, plan/anchor evidence,
data-quality state, or versioned snapshot contract. Identical polls produce no
new observation and no new sample.

For VWAP episodes, normal numeric VWAP drift does not create a new instance;
each observation stores its point-in-time VWAP. Opening-range bounds are fixed
after the current opening-range cutoff. A revision before the cutoff cannot
create a regular-session occurrence because these candidates require the
regular-session visible prefix and their first identity captures the then
visible bound.

### New-instance rules

A new instance begins when a candidate qualifies and any of these is true:

- there is no active matching instance;
- the prior matching instance is terminal or closed after confirmed absence;
- family or direction differs;
- a structural anchor key or sequence anchor materially differs;
- engine, rules, parameter, occurrence-policy, or identity version differs;
- the exchange session date differs.

If a structural anchor changes while an earlier instance is active, the new
anchor begins a new instance immediately. The old instance enters absence
reconciliation; it is not rewritten to the new anchor. This permits genuinely
simultaneous theses and avoids outcome-based merging.

After explicit invalidation or expiration, a later qualifying reformation is a
new instance even at the same level. Its later
`first_qualifying_bar_at` makes the ID distinct. Historical replay processed in
ascending completed-bar order reproduces the same active-instance transitions
and IDs.

## 4. Disappearance and closure policy

Disappearance from one detector evaluation is not a trading loss.

### Closure reasons

| Reason | Trigger | Research meaning |
|---|---|---|
| `INVALIDATED` | The current engine explicitly emits `INVALIDATED` for the matching selected instance. | Preserve the engine terminal state. Do not infer it for an unselected candidate. |
| `EXPIRED` | The current engine emits `TOO_LATE`, mapped by Phase 3D to `EXPIRED`, for the matching instance. | Preserve existing semantics. |
| `CONDITION_NO_LONGER_PRESENT` | Candidate is absent on two distinct, consecutive, successfully evaluated completed 5m decision bars under fresh regular-session data. | Research-only episode closure, explicitly not invalidation or a negative outcome. |
| `SESSION_ENDED` | Exchange regular-session close is reached before another terminal reason. | Intraday measurement boundary. Early-close calendar must be versioned. |
| `DATA_UNAVAILABLE` | Active instance cannot be evaluated because evidence is stale, unavailable, partial, or errored. | Append a quality-state observation once per change; freeze instance and absence counter. Not terminal. |
| `SCANNER_DATA_GAP` | Expected cycle/symbol coverage is missing or a cycle is skipped/failed. | Coverage fact only; freeze reconciliation. Not terminal. |

### Deterministic grace and reconciliation

`closure-v1` uses two **distinct completed 5m bar timestamps**, not two scanner
polls. A repeated poll of the same bar cannot advance absence. Both absence
observations must come from successful, fresh regular-session evaluation of the
symbol. Any provider error, stale input, scanner outage, missing symbol, partial
cycle, or version change interrupts the consecutive sequence and leaves the
instance open/frozen.

If the same anchor reappears before the second qualifying absence bar, it is
continuation and the pending absence is cleared. If a different anchor qualifies,
it creates a new instance while the old one continues its own reconciliation.

At regular-session close, remaining nonterminal instances close as
`SESSION_ENDED`; a late scanner cycle is not required to manufacture that fact.
The exchange calendar and close time are part of the closure-policy version.
No closure reason is itself the primary READY outcome; outcome evaluation uses
its separate chronological evidence contract.

## 5. Scan-coverage contract

Heartbeat state cannot prove per-cycle/per-symbol observation. The future ledger
needs compact append-only coverage metadata without storing raw market history.

### Cycle record

One `scan_cycle` fact per acquired cycle:

- `cycle_id`: UUID generated when the durable scanner lease is acquired;
- scanner scope and non-secret task/process run ID;
- scheduled/started/completed timestamps;
- cadence/session policy version;
- `coverage_status`: `COMPLETE`, `PARTIAL`, `FAILED`,
  `SKIPPED_OVERLAP`, or `CANCELLED`;
- exchange session and market-data aggregate status;
- expected-universe count and canonical universe hash;
- attempted/succeeded/failed/not-attempted counts;
- earliest/latest provider timestamps and maximum data age;
- scanner readiness at start/end;
- engine, rules, parameter, scanner-config, universe, occurrence-policy, and
  measurement-contract versions;
- safe aggregate error codes and elapsed time.

`universe_hash` is SHA-256 over the sorted symbol list and a versioned universe
definition. Counts alone are not enough to prove which symbols were expected.

### Per-symbol coverage record

One compact child fact for every expected symbol:

- `cycle_id`, `symbol` composite uniqueness;
- `attempt_status`: `SUCCESS`, `PROVIDER_ERROR`, `DATA_STALE`,
  `DATA_UNAVAILABLE`, `ENGINE_ERROR`, `NOT_ATTEMPTED`, or `CANCELLED`;
- market/decision bar/provider timestamps, freshness seconds, data/session state;
- candidate count before selection and candidate instance IDs/fingerprints;
- result classification: `QUALIFYING_CANDIDATES`, `NO_SETUP`, or
  `NOT_OBSERVED`;
- bounded safe error code, never provider exception text or URLs.

Only `SUCCESS + fresh regular data + candidate_count=0` proves `NO_SETUP` for
that symbol/bar. Any other condition is an observation gap. Coverage records
may share a context snapshot ID. They do not contain Massive OHLCV arrays.

## 6. Continuous outcome-data requirements

### Current Massive REST sufficiency

The current adapter retrieves adjusted one-minute OHLCV aggregates with UTC
timestamps. For fixed READY references and an intraday horizon, complete
one-minute bars are sufficient to calculate conservatively:

- whether a target or invalidation boundary was crossed on different bars;
- minute-resolution time to first target/invalidation;
- MFE_R from bar highs/lows;
- MAE_R from bar lows/highs;
- end-of-session and terminal-bar R;
- `NEITHER` when complete coverage proves neither boundary occurred.

One-minute OHLC cannot determine event order when the same bar crosses both a
target and invalidation. Such a result is `AMBIGUOUS`. Candle open/close,
direction, distance, or a favorable heuristic may not resolve it.

Trade-level data is necessary only if a later outcome-contract version chooses
to resolve same-minute chronology or needs sub-minute event times. Even trades
do not prove an executable fill without latency and order assumptions. Quote
data is unnecessary for the theoretical engine outcome; it becomes necessary
for later bid/ask spread, executable-price, or broker-execution analysis.

### Simplest trustworthy architecture

For Phase 4, use a separate bounded REST outcome-assessment job after the
predefined horizon (normally session close):

1. select immutable READY observations lacking an assessment for the current
   outcome-contract version;
2. request Massive one-minute aggregates only from READY reference time through
   the fixed horizon;
3. validate chronological ordering, expected session-minute coverage,
   duplicates, timestamp bounds, and provider status;
4. compute conservative boundary order and R metrics;
5. append one immutable assessment with the first crossing bar metadata,
   coverage manifest/hash, and ambiguity reason;
6. retain no full duplicate raw history in PostgreSQL.

A gap before either possible boundary yields `AMBIGUOUS` when it can affect
ordering; it must not become `NEITHER`. A gap after a conclusively earlier
terminal event does not undo that ordering but remains in the evidence manifest.
REST retries must be bounded and idempotent. WebSockets are not required for
the initial conservative contract.

Trade-level re-evaluation, if later justified, appends a new versioned outcome
assessment and never overwrites the minute-based result.

## 7. Synchronized market-context contract

### General point-in-time rule

Every setup observation references one immutable `market_context_snapshot` built
for its `decision_bar_timestamp`. Context features use only completed source
bars whose interval end is at or before that timestamp. Each instrument includes
its own provider timestamp, decision cutoff, freshness, source, and availability
reason. No latest-value join is allowed after the fact.

Build one context snapshot per distinct decision-bar cutoff/cycle and share it
across setup observations. If context collection fails, persist the setup with
explicit unavailable context; never remove it from the denominator.

### Core index context

For SPY, QQQ, and IWM, prospectively capture from the same Massive one-minute
aggregate path:

- price and return from prior regular-session close;
- return from current regular-session open;
- 1m/5m/15m VWAP relationship, EMA9/EMA21 relationship, and current engine
  trend classification;
- session VWAP distance, session high/low position, opening-range position;
- ATR and current relative-volume definition;
- alignment label based on available index directions;
- timestamps, freshness, and missing fields.

The existing engine can derive most of these fields, but the hosted per-symbol
scanner does not currently attach synchronized index context. This is new
prospective measurement data, not retroactive reconstruction.

### Futures context

NQ is recorded as `UNAVAILABLE` until the active contract, roll policy, session
normalization, synchronization cutoff, and provider continuity are validated.
The existing futures probe is not sufficient evidence for an automatic ledger
feature. Once validated, NQ context may include contract ticker, roll-policy
version, 5m/15m direction, VWAP relationship, return, timestamp, and alignment
with QQQ. It remains context and cannot alter production actionability without a
separate rules release.

### Sector context

Use a reviewed, effective-dated, versioned symbol-to-sector-ETF mapping. For a
mapped symbol, capture the ETF using the same completed-bar cutoff:

- ETF identifier and mapping version;
- 5m/15m direction and VWAP/EMA relationship;
- return from prior close and regular-session open;
- relative strength versus SPY over the same point-in-time interval;
- alignment with the setup direction;
- timestamps/freshness/unavailable reason.

Unmapped symbols remain `UNAVAILABLE`; they are not assigned a sector from name
similarity. Relative-strength thresholds beyond the raw return difference are
TBD pending prospective calibration.

### Optional future context

- Breadth requires a defined universe, timestamped advancer/decliner inputs, and
  point-in-time source. It is not core `market-context-v1`.
- VIX9D/VIX3M term structure requires validated provider history and synchronized
  contracts. It is optional in a later context version.
- Options/GEX remains experimental and outside the core ledger. No GEX field is
  populated from current options UI data, and no absence of GEX may filter a
  setup.

## 8. VIX feature contract

VIX is contextual evidence, never an automatic bullish/bearish signal. The
current repository has no validated VIX adapter or entitlement path, so every
VIX feature is `UNAVAILABLE` until its symbol/endpoint, timestamp semantics,
history continuity, and provider access are validated prospectively.

When available, `vix-context-v1` records:

| Feature | Deterministic point-in-time definition |
|---|---|
| Current level | Latest completed VIX observation at or before the setup cutoff, with source timestamp and age. |
| Change from prior close | Current level minus the prior completed VIX session close; both values retained. |
| Intraday percent change | `(current - first eligible regular-session value) / first value`, using only values available by cutoff. |
| Short-term slope/trend | Completed 5m VIX EMA9/EMA21 values and raw difference. RISING/FALLING/FLAT deadband is TBD and must be calibrated prospectively, frozen, and versioned. |
| Moving-average relationship | Current level versus prior-completed-session SMA20 and SMA50. Do not include future/current session close in the daily averages. |
| Recent-history percentile | Empirical rank of current level among the preceding 252 completed daily closes, excluding the current incomplete day. Record history count; unavailable when required history quality is not met. |
| Equity/VIX relationship | Interpretable pair of core-index composite direction and VIX direction: `EQUITY_UP_VIX_DOWN`, `EQUITY_DOWN_VIX_UP`, `BOTH_UP`, `BOTH_DOWN`, `MIXED`, or `UNAVAILABLE`. |
| Volatility regime input | Raw level, percentile, daily relationship, and intraday direction. Classification thresholds are owned by `market-regime-v1`, not the setup engine. |

The initial history window is a definition, not a tuned predictor. Minimum
history requirements and category boundaries remain TBD until source coverage is
audited. Missing VIX never converts a setup to NO_SETUP or blocks ledger entry.

## 9. Initial market-regime design

The regime is a versioned, interpretable context label with separate dimensions,
not an opaque score and not a trading gate.

### Trend dimension

Use only synchronized completed-bar SPY/QQQ/IWM context:

- `TRENDING_UP`: at least two available indexes are bullish on both existing 5m
  and 15m trend classifications and none of the three is fully bearish on both;
- `TRENDING_DOWN`: mirror condition;
- `RANGE`: neither directional condition is met;
- `UNAVAILABLE`: fewer than two indexes have fresh synchronized context.

This reuses current transparent trend classifications. It does not introduce a
numeric market score.

### Volatility-level dimension

Labels are `LOW_VOL`, `NORMAL_VOL`, `ELEVATED_VOL`, `STRESSED_VOL`, or
`UNAVAILABLE`. Inputs are VIX current level and prior-history percentile.
Numeric boundaries are **TBD**; they require prospective source validation and a
predeclared calibration sample. Boundaries must be frozen before outcome
analysis and versioned. They may not be selected to improve setup expectancy.

### Volatility-change dimension

Labels are `EXPANDING`, `CONTRACTING`, `STABLE`, or `UNAVAILABLE`, based on VIX
intraday change and short-term slope. Deadbands are **TBD** under the same
prospective calibration rule.

Breadth/alignment may become a fourth independent dimension later. It must not
be backfilled from end-of-day breadth. The stored regime consists of the
individual labels, inputs, thresholds/version, and availability—not one blended
number.

## 10. Immutability and anti-lookahead invariants

1. Setup instances, observations, coverage facts, context snapshots, and outcome
   assessments are append-only. Corrections append a superseding version.
2. Future outcomes have no write path to historical observation/context rows.
3. Every feature carries a source timestamp no later than its observation
   cutoff; completed interval end, not interval start, controls eligibility.
4. Detector input is exactly the visible chronological bar prefix used by the
   production engine.
5. Every detector-returned candidate is persisted before selection or alert
   filters; ugly and untraded samples remain in the denominator.
6. Repeated polls of the same active anchor do not create new instances.
7. Scanner/data gaps and unavailable context are explicit; they never become
   NO_SETUP, invalidation, expiration, or NEITHER.
8. Absence grace advances only on distinct successful completed 5m bar
   evaluations.
9. Same-minute target/invalidation order remains `AMBIGUOUS` unless a later
   independently versioned finer-granularity assessment proves chronology.
10. No historical feature is reconstructed from later bars, end-of-day labels,
    revised membership, or future regime data. Unknown remains `UNAVAILABLE`.
11. Engine, rules, parameters, identity, occurrence, closure, feature, context,
    regime, horizon, and outcome versions are immutable research boundaries.
12. Version cohorts are never pooled silently.
13. No outcome may cause occurrence inclusion, anchor merging, closure relabeling,
    feature replacement, or favorable ambiguity resolution.

Database roles should enforce `INSERT, SELECT` without `UPDATE, DELETE` on the
future research ledger. Administrative corrections require a new row and an
audited `supersedes_id`.

## 11. Experimental and production setup metadata

Maintain an effective-dated, versioned setup catalog independent of observed
outcomes:

- `setup_family` and `setup_catalog_version`;
- `research_status`: `PRODUCTION`, `EXPERIMENTAL`, or `REJECTED`;
- engine/rules version applicability;
- effective start/end timestamps;
- `actionable_allowed` boolean;
- approval reference, approver, approval timestamp, and rationale;
- originating experiment/research version where applicable.

Each setup instance copies the catalog status effective when it first qualifies.
Current engine-released setup families are `PRODUCTION` for their existing rules
versions; this metadata does not change their logic.

An `EXPERIMENTAL` detector may eventually emit research observations but must be
isolated from `StandardDecision` actionability, production ranking,
notifications, and user permission. `REJECTED` preserves historical observations
and prevents future production use; it does not delete samples.

Promotion requires a new explicit catalog/rules release after all of:

- sufficient predeclared sample size;
- positive expectancy under the frozen outcome contract;
- robustness across symbols, periods, and reasonable sensitivity checks;
- walk-forward and untouched out-of-sample validation;
- bias/leakage review;
- human approval and documented release rationale.

Research observation alone can never promote a setup automatically.

## 12. Required contract tests

### Occurrence and identity

1. Golden tests for every family prove the first qualifying state listed above.
2. All simultaneous detector candidates are included before selection/conflict
   suppression and alert filters.
3. Repeated polls of the same bar and later polls of the same active anchor
   produce one instance.
4. Process restart and chronological replay reproduce the identical instance ID.
5. Same level after terminal closure produces a new ID from the later qualifying
   timestamp.
6. Different structural anchors can coexist and never overwrite each other.
7. VWAP numeric drift stays on the same sequence-anchored instance.
8. Version change always creates a distinct research boundary.

### Closure and coverage

9. One successful absence bar does not close an instance; two distinct
   consecutive successful 5m absence bars close it as
   `CONDITION_NO_LONGER_PRESENT`.
10. Duplicate polls of one bar do not satisfy the two-bar grace.
11. Stale data, provider error, skipped overlap, task outage, cancellation, and
    partial scan freeze absence reconciliation.
12. Explicit matching INVALIDATED/TOO_LATE closes immediately with exact Phase
    3D semantics.
13. Session/early close produces `SESSION_ENDED` without a fabricated loss.
14. Coverage reconciliation proves every expected symbol is SUCCESS/NO_SETUP,
    qualifying, or explicitly not observed; cycle counts equal child facts.
15. Universe hash is stable under input ordering and changes on membership or
    universe-version changes.

### Point-in-time context

16. Reject any feature whose source interval ends after the observation cutoff.
17. Context collection failure persists the setup with explicit unavailable
    fields.
18. Shared context snapshot IDs are identical only for the same cutoff/version.
19. VIX percentile uses only prior completed daily closes; current/future close
    contamination fails the test.
20. Sector mappings are effective-dated and unmapped symbols remain unavailable.
21. Regime golden tests exercise UP, DOWN, RANGE, and UNAVAILABLE without an
    opaque score.

### Outcomes and immutability

22. Different-bar target/invalidation ordering classifies chronologically.
23. Same-bar crossing classifies AMBIGUOUS in both directions.
24. Missing bars that could change ordering never classify NEITHER.
25. MFE_R/MAE_R use the frozen READY reference and initial risk, not later plan
    revisions or assumed fills.
26. Outcome writes cannot update observations; runtime DB role cannot update or
    delete research facts.
27. Corrections append a superseding record and retain the original.
28. Denominator counts do not change when outcomes, grades, alertability, or
    user trades are filtered.
29. Property tests assert every feature source timestamp is `<=` cutoff and all
    event streams are monotonic by market timestamp.
30. Experimental/rejected setups cannot enter actionable ranking or notification
    paths; promotion requires a new approved catalog/rules version.

## 13. Remaining unresolved questions

The measurement semantics are frozen, but these source/implementation details
must be resolved before claiming complete production capture:

1. Expose detector anchor provenance—especially level source types and sequence
   timestamps—without changing detector decisions.
2. Validate the Massive VIX symbol/endpoint, entitlement, timestamp/session
   semantics, and sufficient point-in-time history. Until then VIX is
   unavailable.
3. Validate NQ active-contract roll and session policy before enabling futures
   context.
4. Approve an effective-dated symbol-to-sector-ETF mapping and ownership process.
5. Predeclare VIX regime percentile/level thresholds, slope deadbands, minimum
   history, and their calibration sample; all remain TBD now.
6. Decide whether coverage uses two normalized tables or a cycle row plus
   compact JSON for small universes. Semantics must remain identical.
7. Confirm Massive one-minute aggregate completeness and adjustment behavior in
   the bounded outcome window before enabling outcome assessment.
8. Decide retention/export policy only after measured volume; PostgreSQL remains
   the recommended initial store.

## 14. Ledger implementation safety recommendation

Phase 4 ledger foundation implementation is **safe to begin only after explicit
approval of this measurement contract**, with these boundaries:

- safe now: additive setup-instance, append-only observation, scan-coverage,
  context-snapshot contracts/adapters and their deterministic tests;
- prerequisite for family capture: required anchor provenance must be exposed
  from existing detector inputs without changing decisions;
- not yet safe to claim available: VIX, NQ, sector, breadth, term structure, or
  GEX features until their sources are validated; store them as unavailable;
- not yet authorized: outcome evaluator implementation, migrations, deployment,
  notification changes, experimental setups, strategy optimization, or ML.

With those constraints, the duplicate-sample, denominator, identity,
disappearance, scan-gap, context-timing, versioning, and lookahead blockers from
Phase 4A have deterministic and testable resolutions. Outcome-source and new
context-source validation can proceed as separately versioned work without
weakening the core ledger contract.
