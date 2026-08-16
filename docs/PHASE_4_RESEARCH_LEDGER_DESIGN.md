# Phase 4A — Event / Outcome Ledger and Feature Availability Audit

## Scope and verdict

This phase is audit and design only. It does not change persistence, trading
logic, setup definitions, scoring, thresholds, notifications, infrastructure,
or runtime behavior.

The current system does **not** record every engine-defined qualifying setup
occurrence as an immutable statistical sample. Stage 2 and Phase 3 persistence
is an operational current-state store: it keeps the latest selected decision,
state changes, coarse price-excursion fields, scanner state, and notification
deduplication. It is valuable production state, but it is not yet a complete
point-in-time research ledger.

## Current-state audit

### PostgreSQL and Alembic schema

Alembic revision `20260815_01` creates the Stage 2 schema and revision
`20260815_02` adds `scanner_heartbeats`.

| Table | Current capture | Research limitation |
|---|---|---|
| `signals` | One row per unique signal fingerprint; latest selected `StandardDecision`, levels, parameters, versions, and latest observation timestamps. | The row is updated on later observations. It is not an immutable observation and does not retain every poll or every transition's complete feature state. |
| `signal_state_transitions` | Append-like rows for accepted changes in `setup_state`, including from/to state, transition/observation time, price, quality, action, reasons, and versions. | No full decision JSON, levels, score factors, confirmations, market context, freshness, or missing-field manifest. Uniqueness does not define a research occurrence. |
| `signal_outcomes` | One mutable row per signal with MFE/MAE price excursion, target/invalidation booleans and first timestamps, last evidence time, calculation version. | Not append-only; no R normalization, terminal classification, evaluation horizon, ambiguity policy, end-of-session value, or immutable evidence provenance. |
| `scanner_state` | Latest alert-policy state for scope/policy/symbol, candidate fingerprint, signal link, transition/alert/observation timestamps, price and score. | Current state only. It can be cleared or overwritten and represents alert policy, not every engine occurrence. Keying permits only one current row per symbol/policy. |
| `scanner_heartbeats` | Current lease plus last cycle start/completion/success, counts, status and error code. | Operational heartbeat only; no per-cycle or per-symbol history. |
| `notifications` | Durable alert eligibility/claim/delivery deduplication and history. | Captures notification events, not the full population of setups. |
| `notification_settings` | Current destination preferences. | Operational configuration, not research evidence. |

There is no `decision_intelligence` table. The Phase 3D contract is derived at
runtime from the latest `StandardDecision`, levels, freshness, and session. Its
confirmation components and recommended action are not persisted as an
immutable transition snapshot.

### What causes a signal write today

`AnalysisService.analyze_symbol` runs the single production engine. A decision
is persisted only when its selected `setup_family` is not `NONE`.

- The hosted scanner passes `persist_actionable_only=True`, so only current,
  fresh regular-session evaluations can advance signal persistence.
- Request-driven analysis currently uses the same store without that hosted
  guard and can update operational state when called.
- The detector may produce several `CandidateEvidence` objects, but
  `build_standard_decision` sorts them and emits only the highest lifecycle
  candidate. Non-selected simultaneous candidates are not persisted.
- An equal-ranked long/short conflict is converted to no setup, so neither
  candidate enters persistence.
- Alert filtering subsequently removes low quality, no-trade, too-far,
  illiquid, incomplete-structure, poor-R/R, and too-late candidates. Alert and
  notification tables therefore cannot be used as the setup denominator.
- Closed/stale/unavailable hosted evaluations intentionally create no durable
  trading transition. Their data-quality condition is visible in the response
  and heartbeat, but no immutable per-symbol observation records it.

Therefore the current database cannot answer “how many qualifying setup
occurrences did the engine see?” independently of user traffic, candidate
selection, alert policy, and current-row mutation.

### Current identity semantics

`signal_fingerprint` hashes:

```text
symbol, setup_family, direction, trigger rounded to 4 decimals,
session_date, engine_version, setup_rules_version
```

Lifecycle state is deliberately excluded. This usefully collapses repeated
polls of the same stable thesis, but it is not sufficient as a research episode
identity:

- a small trigger remap can split one continuing thesis into two signals;
- two genuine same-family/same-direction occurrences at the same rounded level
  on one session can collapse into one signal;
- `parameter_version` is absent from identity;
- the fingerprint does not contain an episode-start/anchor bar;
- only the selected candidate receives an identity;
- later observations overwrite the `signals` snapshot.

Do not change this operational fingerprint casually. Phase 4 should introduce a
separate versioned `setup_instance_id` while retaining the existing fingerprint
for compatibility and traceability.

### Current outcome behavior

The operational outcome updater is monotonic: excursion maxima and hit flags can
only increase, hit timestamps keep the earliest value, and writes are row
locked. It examines bars strictly later than `decision.timestamp`. In normal
live evaluation, however, the decision timestamp is the latest completed bar,
so the current bar list ordinarily contains no later evidence. Later decisions
sharing the fingerprint also carry a later decision timestamp and market price.
Consequently this path does not provide a guaranteed, fixed-reference,
continuously evaluated outcome for every READY occurrence.

The current hit logic can record both a target and invalidation on the same
minute bar, but it does not classify that case as `AMBIGUOUS`. MFE/MAE are price
distances, not R multiples, and the mutable signal's latest market price may not
be the original READY reference.

## Unique setup occurrence contract

### Research identity

Add a distinct `setup_instance_id` generated by identity schema
`setup-instance-v1` from the first chronologically visible detector-positive
completed bar of an episode:

```text
SHA-256(
  identity_schema_version,
  symbol,
  trading_session_date,
  setup_family,
  direction,
  family_anchor_type,
  normalized_family_anchor,
  episode_started_at,
  engine_version,
  setup_rules_version,
  parameter_version
)
```

`episode_started_at` is the market timestamp of the first completed bar on
which that candidate satisfies the engine's existing minimum recognizable
state. It is not scanner wall-clock time. `normalized_family_anchor` uses the
engine-owned structural source and its normal price precision, not an arbitrary
later price. The database atomically attaches repeated observations to the
active episode; a replay processed in the same chronological order produces the
same ID.

The ledger must capture every candidate the engine defines as qualifying for a
research occurrence, before alert-policy filters and before selection of the
single UI decision. The exact qualifying boundary must be explicitly frozen in
an `occurrence_policy_version`; Phase 4B must not silently equate “alertable”
with “qualifying.”

### Episode continuity and termination

Repeated polling remains the same instance while the same family, direction,
and engine-owned anchor is continuously active. A meaningful state snapshot is
added only when lifecycle, action, material mapped plan, confirmation pattern,
or data-quality state changes; identical polls update neither sample count nor
the original snapshot.

An instance terminates at the first applicable existing semantic boundary:

1. engine `INVALIDATED`;
2. engine `TOO_LATE` / Phase 3D `EXPIRED`;
3. final target, when outcome evaluation defines it as terminal;
4. family-specific predefined expiration;
5. regular-session close for current intraday families.

A disappearance that the current engine maps to `INVALIDATED` remains
`INVALIDATED`; the ledger must not relabel it. `DATA_UNAVAILABLE` records an
observation-quality event but does not by itself terminate the setup. On data
recovery, the active instance continues unless a visible engine-defined terminal
condition occurred.

After termination, a later detector-positive completed bar begins a new episode,
even if family, direction, and rounded trigger match an earlier episode.

### Family anchors

| Family | Stable anchor and recurrence rule |
|---|---|
| Opening-range breakout/breakdown | Opening-range high/low plus session date. One continuous episode per direction/anchor; a later episode is allowed only after terminal state and a new detector-positive bar. Always expires at session close. |
| Breakout/breakdown retest | Mapped resistance/support level with retained level-source identity. A remap alone does not end an active thesis unless the engine emits a terminal state; after termination, a new qualifying retest starts a new episode. |
| VWAP reclaim/rejection | Session VWAP role plus first qualifying sequence timestamp. Multiple episodes per day are valid only when separated by terminal state. The drifting numeric VWAP must not itself generate new IDs. |
| Failed breakout/breakdown | Failed structural level plus the first break/failure sequence timestamp. Another failure at the same level is new only after termination. |
| Trend pullback | Pullback/resumption sequence start timestamp and relevant EMA/structural role. Multiple completed pullback episodes may occur in one session. |
| Momentum continuation | Pause sequence start timestamp and its break direction. A later independent pause is a new instance. |
| Compression break | Compression-window start/end timestamps and range bounds. A new non-overlapping compression window after terminal state is a new instance. |

Some sequence-start and source identifiers are not retained by current
`CandidateEvidence`; Phase 4B must expose them as persistence evidence without
altering detection outcomes.

## Proposed pragmatic schema

The minimum design uses three research tables. Existing operational tables stay
in place.

### `setup_instances`

Immutable header for one occurrence.

- Primary key: `setup_instance_id` (64-character deterministic digest or UUIDv5
  derived from it).
- Important columns: `identity_schema_version`, `occurrence_policy_version`,
  `symbol`, `session_date`, `setup_family`, `direction`, `family_anchor_type`,
  `family_anchor_json`, `episode_started_at`, `first_observed_at`,
  `source_signal_fingerprint`, `engine_version`, `setup_rules_version`,
  `parameter_version`, `parameter_snapshot_hash`, `created_at`.
- Unique constraint on the complete canonical identity tuple and separately on
  `setup_instance_id`.
- Indexes: `(session_date, setup_family)`, `(symbol, episode_started_at)`, and
  `(engine_version, setup_rules_version, parameter_version)`.
- No mutable current-state or outcome columns.

### `setup_observations`

Append-only point-in-time facts at meaningful transitions.

- Primary key: `observation_id` (UUID).
- Foreign key: `setup_instance_id -> setup_instances`.
- Deduplication: unique `observation_fingerprint`, calculated from instance,
  market timestamp, lifecycle/action, plan hash, confirmation hash,
  data-quality state, and snapshot contract version.
- Time: `observed_at` (application receipt), `market_timestamp`,
  `decision_bar_timestamp`, `session`, `minutes_since_regular_open`,
  `day_of_week`.
- Decision: `setup_state`, `recommended_action`, `quality`, `score`, optional
  `rank_in_scan`, `universe_size`, `qualifying_candidate_count`.
- Plan: `decision_reference_price`, `trigger`, decision-zone bounds,
  `invalidation`, `target_1`, `target_2`, `initial_risk`, `initial_rr`, nearest
  support/resistance.
- Structured JSONB: `score_factors`, `levels_snapshot`,
  `confirmation_components`, `missing_confirmation`, `market_context`,
  `data_quality`, and `feature_manifest`.
- Versioning: engine, setup rules, parameter/config, parameter snapshot/hash,
  observation-contract version, and feature-definition version.
- Indexes: `(setup_instance_id, market_timestamp)`,
  `(setup_family, setup_state, market_timestamp)`, `(recommended_action,
  market_timestamp)`, and GIN only on JSONB fields proven necessary by actual
  analytics.
- Enforce append-only behavior through repository permissions: runtime ledger
  role receives `INSERT, SELECT`, not `UPDATE, DELETE`.

Capture an observation when any of these changes: lifecycle state, Phase 3D
recommended action, terminal/data-unavailable state, confirmation-state vector,
trigger/invalidation/targets, or version tuple. Do not append identical polling
observations. A separate optional scan-cycle fact may record coverage later; it
must not masquerade as an occurrence.

`MANAGE` is retained in the contract but must not be emitted until an engine
position lifecycle exists. Phase 4 does not infer it from READY.

### `setup_outcome_assessments`

Separate append-only future-evidence assessments. The observation rows are never
updated with outcomes.

- Primary key: `outcome_assessment_id` (UUID).
- Foreign keys: `setup_instance_id` and the exact READY
  `reference_observation_id` when READY occurred.
- Identity/version: `outcome_contract_version`, `calculation_version`,
  `evaluation_horizon_id`, `evidence_granularity`, `evidence_start_at`,
  `evidence_cutoff_at`, and `computed_at`.
- Classification: `primary_outcome` in `TARGET_FIRST`,
  `INVALIDATION_FIRST`, `NEITHER`, `AMBIGUOUS`; `terminal_reason`; target and
  invalidation hit timestamps; ambiguity reason.
- Fixed reference: `ready_reference_price`, `theoretical_entry_price`,
  `entry_reference_type`, `initial_invalidation`, `initial_risk_1r`, and target
  prices/R multiples copied from the READY observation for auditability.
- Metrics: `mfe_r`, `mae_r`, `terminal_r`, `end_of_session_r`,
  `target_1_r`, `target_2_r`, time-to-target/invalidation seconds, expiration
  status, and last evidence timestamp.
- Evidence: compact `evidence_json` containing the first crossing bar IDs/times,
  relevant OHLC bounds, and missing-data intervals—not the full Massive history.
- Unique constraint on `(reference_observation_id, outcome_contract_version,
  evaluation_horizon_id, evidence_cutoff_at)`.
- Corrections append a new assessment with `supersedes_assessment_id`; old rows
  remain queryable. A canonical analytics view may select the latest valid
  version without deleting history.

## Immutable point-in-time snapshot contract

The snapshot is built inside the production evaluation path from values already
visible at `decision_bar_timestamp`. It receives no later bars. Serialization
must occur before outcome evaluation and must include an explicit manifest for
present, missing, unavailable, and not-applicable features.

### Feature dictionary

“Available now” means available at evaluation time in current Python objects or
deterministically derivable from the same visible bars. It does not mean it is
already persisted.

| Candidate feature | Status | Current source / limitation |
|---|---|---|
| Symbol, family, direction | AVAILABLE NOW | `StandardDecision`; only the selected family is currently persisted. |
| Market timestamp / decision bar | AVAILABLE NOW | Decision and `SymbolAnalysis`; decision bar is not in current signal columns. |
| Observed/analysis timestamp | AVAILABLE NOW | `SymbolAnalysis.analysis_timestamp`; not persisted per setup. |
| Session, day of week, minutes since open | DERIVABLE WITHOUT LOOKAHEAD | Session classifier and market timestamp. Persist derived value plus timezone/definition version. |
| Engine/rules/parameter versions | AVAILABLE NOW | Constants on every `StandardDecision`; parameter snapshot is persisted operationally. Versions are manual constants. |
| Lifecycle and legacy action | AVAILABLE NOW | `setup_state`, `action_now`. |
| Phase 3D recommended action | AVAILABLE NOW | Deterministically derived at runtime; not persisted. |
| Quality/grade | AVAILABLE NOW | Decision quality; explicitly heuristic. |
| Score and score-factor breakdown | AVAILABLE NOW | `SymbolAnalysis.setup_score`; absent from `signals.decision_json`, except scanner state's latest integer score for alert candidates. |
| Rank in scan / candidate population | AVAILABLE NOW | Scanner can determine it, but it is not persisted and needs a deterministic tie/rank definition. |
| Current/decision reference price | AVAILABLE NOW | Decision uses completed-bar price; snapshot price may be fresher and must remain separately labeled. |
| Trigger / point decision zone | AVAILABLE NOW | Current engine has one trigger, not a true range. Do not fabricate zone width. |
| Invalidation, targets, initial R/R | AVAILABLE NOW | Persisted in current signal. Missing structure remains null. |
| Relevant levels and confluence | AVAILABLE NOW | `PriceLevel` and current levels snapshot; source timeframe labels are partly inferred from types. |
| Confirmation PASS/PENDING/UNAVAILABLE | AVAILABLE NOW, PARTIAL EVIDENCE | Phase 3D derives states. Some numeric/bar evidence was discarded and is honestly `UNAVAILABLE`. |
| 1m/5m/15m trend | AVAILABLE NOW | Runtime timeframe analyses. Current signal stores only 5m/15m. No daily/weekly trend exists. |
| VWAP value, relationship and distance | AVAILABLE NOW | Runtime on 1m/5m/15m; current signal stores only a 5m relationship string. |
| EMA9/EMA21 values, alignment, relationships | AVAILABLE NOW | Runtime timeframe analyses; not persisted in current signal. |
| ATR / volatility | AVAILABLE NOW | 5m ATR is persisted. Broader realized/implied regime is not. |
| Relative volume | AVAILABLE NOW | Per-frame current-bar ratio, based on up to 20 earlier sampled bars; not persisted in signal. This is not a multi-day seasonally adjusted RVOL. |
| HOD/LOD, prior-day OHLC, premarket H/L | AVAILABLE NOW | `InstrumentAnalysis`; selected values appear in levels but not as a complete named snapshot. |
| Opening-range high/low/context | AVAILABLE NOW | Runtime and levels. “Context” beyond bounds must be explicitly defined before use. |
| Current-session change % | AVAILABLE NOW | Close versus prior close. It is not gap %. |
| Gap % | DERIVABLE WITHOUT LOOKAHEAD | Session open versus prior close from visible bars. Not currently a named output. |
| Average dollar volume | AVAILABLE NOW | Five-session mean in runtime; not persisted in signal. |
| Market index context | REQUIRES NEW DATA | No synchronized index context is attached to hosted per-symbol scanner decisions. QQQ/SPY/IWM request analysis has limited optional context; it is not a market-wide ledger feature. |
| NQ/futures confirmation | REQUIRES NEW DATA | Current decision path explicitly reports unavailable; futures probe is separate. |
| Sector identity/context | REQUIRES NEW DATA | No sector taxonomy or synchronized sector series in the decision pipeline. |
| Breadth | REQUIRES NEW DATA | No breadth feed or definition exists. |
| Options/GEX context | NOT CURRENTLY JUSTIFIED | Options snapshots are separate UI intelligence and no GEX feature is integrated or point-in-time synchronized. Do not attach it retroactively. |
| Market regime label | REQUIRES DEFINITION/NEW DATA | May later be derived from point-in-time index/volatility inputs, but no frozen definition exists now. |
| Provider timestamp, source, freshness, data state | AVAILABLE NOW | Runtime fields and freshness classifier; not persisted per setup. |
| Missing/unavailable/degraded fields | AVAILABLE NOW | Can be manifested from current model/null/error state; currently not retained per transition. |
| Raw Massive minute OHLCV | AVAILABLE FROM PROVIDER, NOT A LEDGER FEATURE | Engine receives adjusted one-minute OHLCV. Do not duplicate raw history in PostgreSQL; store evidence references/bounds needed for audit. |
| Massive quote/trade details | PARTIALLY AVAILABLE NOW | Adapter retains only normalized price, timestamp, and source. Bid/ask/spread/size are discarded. New capture is required if later justified. |

## Lifecycle capture and Phase 3D semantics

Persist the exact current mapping, without adding trading semantics:

| Engine state / condition | Ledger action |
|---|---|
| `FORMING`, `WATCHING` | `MONITOR` |
| `TRIGGERED` | `WAIT_FOR_CONFIRMATION` |
| `CONFIRMED` with fresh regular data | `READY` |
| `CONFIRMED` outside fresh regular data | `MONITOR` |
| `INVALIDATED` | `INVALIDATED` |
| `TOO_LATE` | `EXPIRED` |
| stale/error/unavailable data | `DATA_UNAVAILABLE` observation; not READY and not automatically terminal |
| `MANAGE` | Reserved and unavailable; no position/fill state exists |

`NO_SETUP/IGNORE` is not a setup occurrence, but coverage accounting must retain
that a symbol was successfully evaluated and produced no qualifying candidate.
Otherwise provider failures and missing symbols can create survivor bias. Phase
4B may add a compact append-only scan-cycle coverage table if heartbeat data is
insufficient; it should not create fake setup instances.

## Outcome contract and R normalization

### Fixed READY reference

The primary outcome population is setup instances that reach a persisted READY
observation. Freeze the following at the first READY transition:

- `ready_reference_price`: the completed decision-bar close/current engine
  reference at READY;
- `theoretical_entry_price`: the engine trigger if current deterministic rules
  define trigger as theoretical entry; otherwise null;
- `entry_reference_type`: `ENGINE_TRIGGER`, `READY_DECISION_PRICE`, or
  `UNDEFINED`;
- initial invalidation and targets;
- `1R = abs(theoretical_entry_price - initial_invalidation)` when both exist and
  risk is positive.

Do not assume a fill at the trigger or READY price. No spread, slippage, latency,
or broker execution is represented. If theoretical entry or invalidation is
undefined, R metrics remain unavailable with an explicit reason.

For long setups, favorable R is `(price - entry_reference) / 1R`; for short,
`(entry_reference - price) / 1R`. MFE_R is the maximum favorable excursion and
MAE_R the maximum adverse magnitude after the READY decision reference and
within the fixed horizon. Target R values use their fixed prices. Terminal and
end-of-session R use the last eligible completed-bar price at the specified
boundary, not an assumed executable fill.

### First-event classification

- `TARGET_FIRST`: earliest target boundary precedes invalidation.
- `INVALIDATION_FIRST`: invalidation precedes the first target boundary.
- `NEITHER`: neither occurs before the fixed horizon.
- `AMBIGUOUS`: available granularity contains both boundaries in the same bar or
  has a data gap that prevents ordering.

With one-minute OHLC, a bar whose high and low cross favorable and adverse
boundaries cannot establish intrabar order. Mark `AMBIGUOUS`; never infer order
from candle direction and never choose the favorable result. Later finer data
may produce a new versioned assessment, not mutate the original.

## Evaluation horizons

Freeze horizon policy in `evaluation_horizon_id` before outcomes are inspected.
For all current intraday setup families, use the first of:

1. invalidation;
2. final target;
3. existing engine expiration / too-late state when defined as terminal;
4. regular-session close on the setup's session date.

Opening-range, breakout/retest, VWAP, failed-break, trend-pullback, momentum, and
compression setups are all currently intraday and receive no overnight carry.
There is no justified family-specific overnight exception today. If a later
rules version defines one, it requires a new horizon ID and must not rewrite old
instances.

Outcome tracking for pre-READY MONITOR/WAIT states may be added as separately
labeled gate-analysis horizons, anchored to their observation price. Those rows
must never be mixed with the primary READY outcome without an explicit cohort.

## Anti-bias and append-only guarantees

1. Evaluate snapshots from the exact visible-bar prefix used by the production
   engine; assert `feature_source_timestamp <= decision_bar_timestamp`.
2. Serialize the observation before any outcome evaluator receives future bars.
3. Give the runtime ledger role no update/delete privilege on research tables.
4. Use database uniqueness on deterministic instance and observation
   fingerprints; repeated polls are not independent samples.
5. Capture all engine-qualifying candidates before alert filters, including
   low-grade and incomplete-structure candidates within the frozen occurrence
   policy.
6. Retain scan coverage, symbol failures, and data-unavailable intervals so
   missing observations cannot be silently excluded.
7. Never delete ugly, invalidated, expired, neither, or ambiguous samples.
8. Never reconstruct a missing point-in-time feature from later data. Mark it
   `UNAVAILABLE`; future capture begins prospectively.
9. Use only completed bars and explicit provider timestamps. The current
   resampling completion rule must be covered by boundary tests.
10. Resolve same-bar target/invalidation as `AMBIGUOUS`.
11. Pin engine, rules, parameters, occurrence policy, feature definition,
    snapshot contract, horizon, and outcome calculation versions on every row.
12. Analytics must group or filter explicit version tuples; never pool rule
    versions implicitly.
13. Corrections are new rows with supersession metadata, never destructive
    rewrites.

## Versioning strategy

Current code provides `ENGINE_VERSION=2.0.0`,
`SETUP_RULES_VERSION=1.0.0`, and `PARAMETER_VERSION=1.0.0`, plus the full
parameter snapshot. This is a useful foundation but not sufficient by itself:
the identifiers are manually maintained and there is no immutable ruleset hash,
occurrence-policy version, feature-definition version, or outcome-contract
version.

Phase 4B should persist:

- semantic labels such as `rules_v1`, `rules_v2` for research grouping;
- existing engine/rules/parameter strings for compatibility;
- canonical SHA-256 hashes of the parameter snapshot and a reviewed ruleset
  manifest;
- `identity_schema_version`, `occurrence_policy_version`,
  `observation_contract_version`, `feature_definition_version`,
  `evaluation_horizon_id`, and `outcome_contract_version`.

CI should fail when reviewed rule/parameter manifests change without a version
bump. Old rows retain their original labels and hashes forever.

## Analytics and future ML readiness

The proposed instance/observation/outcome join supports occurrence and READY
counts/rates, target-before-invalidation, ambiguous/expiration rates, expectancy
in R, average/median R, profit factor, MFE/MAE, and times to target/invalidation.
Typed columns plus versioned JSON features support slices by family, symbol,
direction, time/day, confirmation pattern, grade, score band, R/R band, trend,
volatility, relative volume, and rule versions. Sector, breadth, GEX, and broader
regime slices remain unavailable until prospectively captured.

This contract would support later supervised targets such as target-first
probability, expected MFE_R/MAE_R, and ranking quality, but it is **not ML-ready
today** because the complete denominator and immutable feature snapshots do not
exist.

Primary leakage risks for future work are:

- using the latest mutable signal row instead of the READY snapshot;
- including target/invalidation flags, later lifecycle states, outcome-derived
  rank, or future data freshness as input features;
- computing session HOD/LOD or volume with bars after the observation;
- retroactively joining end-of-day sector/index/regime values;
- using a confirmation component derived from the final state rather than its
  state at the observation;
- selecting only alerts, READY setups, traded setups, surviving symbols, or
  current rule winners;
- random row-level train/test splits that place observations from the same
  setup instance or adjacent market period in both sets.

Future evaluation must split by setup instance and preferably chronological
session blocks, fit transformations on training data only, and keep a final
untouched out-of-time set. No ML implementation belongs in Phase 4A or 4B.

## Engine versus user execution

The research ledger represents engine observations and theoretical deterministic
references only. A later broker integration should use separate tables keyed by
broker account/order/execution identifiers with optional
`setup_instance_id`/READY-observation links. Actual fills, fees, slippage,
partial fills, position management, and user overrides must never overwrite
engine observations or theoretical outcomes.

This separation permits:

```text
engine expectancy from fixed deterministic references
versus
actual user execution from broker fills and realized P&L
```

No Schwab or broker integration is proposed now.

## Storage estimate

At the current 13-symbol universe and five-minute regular cadence, the scanner
performs at most about 1,014 symbol evaluations per full regular session. The
ledger does not store identical polling rows. Assuming 2–6 qualifying episodes
per symbol/day, 2–3 meaningful observations per episode, and an 8–20 KB
compressed logical snapshot:

- roughly 26–78 setup instances/day;
- roughly 65–234 observation rows/day;
- approximately 0.5–4.7 MB/day before indexes and PostgreSQL overhead;
- approximately 0.1–1.2 GB/year of payload, plausibly 0.3–2.5 GB/year including
  indexes/overhead.

Persisting every poll instead would create about 1,014 snapshots/day and several
GB/year; it adds correlated duplicates and is not recommended. A 500-symbol
universe is roughly 38.5 times current volume, approximately 12–95 GB/year under
the assumptions above. PostgreSQL remains appropriate. Consider monthly
partitioning or cold export only after measured volume/query plans justify it;
do not add another datastore now.

Raw Massive history remains with Massive or the existing replay-data workflow.
The ledger stores application-derived point-in-time evidence and compact outcome
provenance, not a duplicate market-data warehouse.

## Phase 4B implementation plan

1. Freeze and review `occurrence-policy-v1`, family anchor evidence, episode
   termination rules, and the precise definition of an engine-qualifying
   candidate. This semantic boundary is the main required decision before code.
2. Add persistence-neutral dataclasses/contracts for `SetupInstance`,
   `SetupObservation`, and `OutcomeAssessment`; keep them downstream of the
   unchanged detector and `StandardDecision`.
3. Extend candidate evidence output with sequence/anchor timestamps and level
   source IDs already used by detection. Do not change candidate outcomes,
   ranking, or thresholds.
4. Add the three append-only PostgreSQL tables through a reviewed additive
   Alembic migration. Preserve SQLite adapters for local/research tests.
5. Add an `EventLedger` adapter invoked after production evaluation and before
   alert filtering. Atomically claim/reuse active instances and deduplicate
   meaningful observations.
6. Persist the full point-in-time snapshot and feature manifest, including all
   missing/unavailable values. Add compact scan-cycle coverage if necessary to
   prove the denominator.
7. Implement a separate chronological outcome evaluator using fixed READY
   references, predefined horizons, and conservative ambiguity handling. It may
   consume historical bars but cannot call or mutate the trading engine.
8. Backfill no features that were not actually retained point-in-time. Existing
   operational signals may be imported only into a clearly labeled
   `LEGACY_INCOMPLETE` cohort excluded from primary validation.
9. Test deterministic identity across polling/restarts/replay, same-level
   recurrence after termination, simultaneous candidates, append-only
   permissions, no-lookahead timestamp invariants, ambiguous bars, data gaps,
   version separation, and denominator reconciliation.
10. Run local PostgreSQL parity and historical replay validation before any
    hosted rollout. Notifications remain disabled and no infrastructure or ML
    work is part of this plan.

## Blockers and decisions required before implementation

- Freeze the exact qualifying-occurrence boundary: all detector candidates from
  `detect_candidates`, or a reviewed subset of recognizable states. Using only
  the selected `StandardDecision` would not meet the “every occurrence” goal.
- Define and expose family sequence-start/anchor provenance without changing
  detector behavior.
- Decide whether a detector disappearance continues to rely exclusively on the
  current `INVALIDATED` mapping or needs a separately labeled research-only
  closure reason. It must not silently change Phase 3D semantics.
- Define an auditable data source for continuous post-READY outcome evaluation;
  the live signal writer does not currently guarantee it.
- Decide whether scan-cycle coverage needs its own append-only table to reconcile
  expected versus successfully evaluated symbols.

None of these blockers requires changing trading logic in Phase 4A.

## Phase 4A.2 implementation status — 2026-08-16

The measurement foundation is implemented locally at Alembic revision
`20260816_03`. The migration is additive and creates `setup_instances`,
`setup_observations`, `setup_outcome_assessments`, `research_scan_cycles`, and
`research_scan_symbol_coverage`. It neither imports nor rewrites the existing
operational signal history.

The hosted durable scan path now persists every `CandidateEvidence` returned by
the unchanged detector before `StandardDecision` selection, alert policy,
ranking, or notification filtering. Family anchors and the first qualifying
completed five-minute bar are detector provenance fields only; they do not
participate in production setup selection. Repeated semantically identical
polls reuse the deterministic instance and do not add observations. A state,
data-quality, reappearance, or closure change appends a new row.

Coverage records make `NO_SETUP` a successful symbol evaluation with no
qualifying candidate. `NOT_OBSERVED` is used for missing, stale, failed, or
unattempted coverage. `DATA_UNAVAILABLE` is an append-only quality event on an
active current-session instance and freezes disappearance reconciliation. Two
distinct successful completed-bar absences close an episode as
`CONDITION_NO_LONGER_PRESENT`; one absence remains `ABSENCE_PENDING`.

Outcome tables and the append-only `OutcomeAssessment` interface support
`TARGET_FIRST`, `INVALIDATION_FIRST`, `NEITHER`, and `AMBIGUOUS`. No outcome
evaluator is active in this phase. Continuous chronological evidence and the
approved ambiguity policy remain prerequisites for enabling assessment writes
in production.

Validation completed against SQLite and a clean local PostgreSQL 16 database:
the full migration chain, deterministic identity, concurrent poll
deduplication, restart durability, coverage distinctions, disappearance grace,
append-only outcomes, version retention, and timestamp no-lookahead checks all
pass. AWS deployment, notification changes, frontend changes, new market-data
sources, and trading-rule changes remain outside Phase 4A.2.
