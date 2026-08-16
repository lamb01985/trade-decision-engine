# Phase 4D — Evidence Analytics

## Status, scope, and authority

This document is the authoritative Phase 4D specification. Phase 4D is an
isolated research/analytics layer over immutable Phase 4A/4B ledger facts and
accepted Phase 4C outcome assessments. It cannot call or modify the production
detector, setup library, scoring, thresholds, confirmations, ranking, plans,
scanner state, notifications, brokerage, or execution.

Contract identifiers:

```text
analytics_schema_version       evidence-artifact-v1
analytics_contract_version     evidence-analytics-v1
slice_contract_version         evidence-slices-v1
sample_policy_version          evidence-sample-size-v1
realized_r_policy_version      phase4c-terminal-r-v1
```

## Statistical unit and accepted input

The statistical unit is one unique `setup_instance_id`. Only the first
immutable `CANDIDATE` observation whose `recommended_action` is `READY` is an
eligible reference. Repeated observations never add samples. An instance may
contribute at most once to an exact outcome horizon and compatibility cohort.

The input selector follows `supersedes_assessment_id` chains and selects only a
unique, non-superseded leaf. Dangling references, cycles, forks, mismatched
instance/reference identities, duplicate instance/horizon cohort membership, or
missing provenance invalidate the affected aggregate. Invalid denominator
integrity produces `valid=false` and no performance statistics.

An accepted input retains identity, occurrence, measurement, engine, setup
rules, parameter, observation, feature, context, regime, outcome, calculation,
horizon, evidence-granularity, source-mode, replay-run, replay-contract,
session-calendar, and replay-cache versions. No future feature is joined to the
READY snapshot.

## Cohorts and compatibility

The required cohort dimensions are `setup_family`, `direction`, `symbol`,
`evaluation_horizon_id`, `setup_rules_version`, `parameter_version`,
`outcome_contract_version`, and `source_mode`. Callers may request any
combination. Empty dimensions produce an overall result only within an exact
compatibility partition.

The compatibility partition is always enforced and includes:

- identity, occurrence, and measurement contracts;
- engine, setup-rules, parameter, observation, and feature versions;
- outcome contract, calculation, horizon, and evidence granularity;
- source mode and materially relevant replay contract/calendar/cache versions.

No compatibility mapping exists in v1. Therefore versions are conservatively
separated. `LIVE` and `REPLAY` never pool by default. A request for one aggregate
that spans multiple compatibility partitions is rejected; a collection request
returns separate aggregates. Replay run IDs remain provenance but do not split
a compatible replay cohort because independent deterministic runs may represent
the same evidence contract.

## Slice definitions

All slices use immutable READY fields. Missing values are `UNKNOWN` or
`UNAVAILABLE`; they are never inferred.

- `time_of_day`: `OPENING` for minute 0–59, `MIDDAY` for 60–239,
  `LATE_SESSION` for 240 or later, and `UNKNOWN` when absent.
- `relative_volume`: reads a point-in-time relative-volume value from the
  READY market-context snapshot. `<0.8` is `LOW`, `0.8–<1.2` is `NORMAL`,
  `1.2–<2.0` is `ELEVATED`, `>=2.0` is `HIGH`, otherwise `UNAVAILABLE`.
- `volatility_atr`: reads point-in-time 5m ATR percentage when present. `<0.5`
  is `LOW`, `0.5–<1.0` is `NORMAL`, `1.0–<2.0` is `ELEVATED`, `>=2.0` is
  `HIGH`, otherwise `UNAVAILABLE`.
- `trend_alignment`: preserves the immutable 5m/15m trend pair as
  `5M:<value>|15M:<value>`; absent inputs are `UNAVAILABLE`.
- `market_regime`: preserves the immutable regime label; absent context is
  `UNAVAILABLE`.
- `symbol`, `setup_family`, and `direction` preserve their canonical ledger
  values.

The numeric boundaries are definitions, not tuned predictors, and are frozen
by `evidence-slices-v1`.

## Denominators, uncertainty, and realized R

Every aggregate reports `n_total`, `n_evaluable`, `n_incomplete`,
`n_ambiguous`, and exclusion reasons. `n_total` counts unique eligible setup
instances. `n_evaluable` is the realized-R denominator. These categories are
explicit and are never silently discarded. Completeness and ambiguity are
independent evidence properties, so an assessment with a consequential gap may
be counted in both `n_incomplete` and `n_ambiguous`; it is excluded exactly once
from realized-R statistics and its exclusion reason remains visible.

Phase 4C stores the accepted theoretical outcome value in `terminal_r`.
`phase4c-terminal-r-v1` consumes that field unchanged as `realized_r` only when
the selected assessment:

1. is non-ambiguous;
2. has complete minute evidence;
3. has a finite `terminal_r`; and
4. has valid identity, provenance, and denominator integrity.

Phase 4D never recalculates R from entry, target, invalidation, excursion, fill,
or price fields. Incomplete and non-evaluable outcomes have no realized R.
Ambiguous outcomes remain separately counted and have no realized R. They are
never wins, losses, neutral samples, or zero-R samples.

Among evaluable values: positive R is a win, negative R is a loss, and exactly
zero R is neutral/other.

## Statistics and sample labels

Each valid aggregate reports counts, win/loss/neutral counts and rates,
positive/negative-R rates, mean, median, cumulative R, population standard
deviation, minimum, and maximum over `n_evaluable`. For two or more evaluable
values it also reports standard error of the mean and the normal 95% confidence
interval `mean ± 1.96 * standard_error`. Undefined metrics are `null`; they are
not fabricated.

The visible quantity label uses `n_evaluable`:

- `<20`: `INSUFFICIENT`
- `20–49`: `PRELIMINARY`
- `50–99`: `MODERATE_SAMPLE`
- `>=100`: `SUBSTANTIAL_SAMPLE`

Labels describe quantity only and never establish edge or production fitness.

## Deterministic artifact contract

The canonical artifact contains schema/contract identifiers, exact source
versions, cohort and slice definitions, denominator accounting, statistics,
sample label, sorted sample identities, and aggregate results. Object keys are
serialized in sorted order with compact separators; arrays use explicit stable
sort keys. Non-finite numbers are rejected.

`generation_input_fingerprint` hashes canonical semantic accepted inputs.
`artifact_fingerprint` hashes the complete semantic artifact excluding only the
fingerprint field itself. Wall-clock generation time, database row order, and
random identifiers are excluded. Identical accepted inputs and requests must
produce byte-identical semantic JSON and identical fingerprints.

Artifacts are returned through a read-only research API and may be written to a
caller-selected local research path. Phase 4D adds no write path to production
tables. Persisted artifacts, when requested, are immutable content-addressed
JSON files; a mismatched existing file is an error, not an overwrite.

## Known limitations

- Phase 4C does not expose broker fills or user P/L; `terminal_r` is theoretical
  engine evidence, not account performance.
- The v1 confidence interval is the transparent normal mean interval and is not
  a profitability claim, multiple-testing correction, or robust small-sample
  model.
- Most synchronized market/regime inputs remain unavailable in current ledger
  snapshots and therefore appear explicitly as `UNAVAILABLE`.
- No compatibility mappings exist in v1.
- Analytics do not optimize parameters, promote setups, or alter production.

## Acceptance boundary

Acceptance requires the Phase 4C checkpoint and conservative outcome behavior
to remain intact; deterministic denominator integrity; conservative incomplete
and ambiguous treatment; compatibility separation; reproducible artifacts;
focused, replay/ledger, full default, and PostgreSQL suites; compileall; and
`git diff --check`. Any genuine correctness failure blocks acceptance rather
than weakening this contract.

## Implementation and acceptance record — 2026-08-16

Phase 4D is implemented as a pure evidence builder over the existing
`ResearchLedger` read interface, a read-only `/api/v1/research/evidence`
endpoint, and an optional immutable content-addressed local artifact writer. No
schema migration or production-state write path was added.

The bounded four-instance mechanics sample used the accepted SQLite research
ledger and Phase 4C evaluator. It produced one target-first, one
invalidation-first, two ambiguous outcomes (one also incomplete), family and
direction cohorts, and point-in-time time-of-day/RVOL slices. LONG denominator
accounting was `n_total=3`, `n_evaluable=1`, `n_incomplete=1`,
`n_ambiguous=2`; SHORT was `1/1/0/0`. Both labels were `INSUFFICIENT`, as
required. Two independently rebuilt inputs produced identical fingerprints:

```text
generation_input_fingerprint  62d72e403433119482b703d1cae224bf780934192e9a16efc3f1551882079566
artifact_fingerprint          832b696c7e36287ccc8cef5d7a48efb5a89d9cba877b3d260d08826a9afa3e82
```

This is a mechanics validation, not evidence of strategy edge, and no rule was
tuned from it.

Final validation:

- focused Phase 4D + Phase 4C/replay/ledger: 46 passed;
- full default suite: 246 passed, 11 optional PostgreSQL tests skipped;
- PostgreSQL-enabled full suite: 257 passed;
- bounded deterministic sample: passed twice with identical semantic output;
- `compileall`: passed;
- `git diff --check`: passed.

A 1,000-instance in-memory aggregation with three cohort keys and two slice
keys completed in approximately 121 ms on the development machine. This is a
sanity measurement, not a hosted capacity guarantee; the v1 repository reader
performs per-instance observation/outcome queries and should be replaced with a
bounded joined read if production research volume makes that material.

**Final verdict: PHASE 4D ACCEPTED.** The Phase 4C prerequisite remains intact,
denominator and compatibility failures invalidate evidence instead of
weakening it, and production trading/notification behavior is unchanged.
Phase 4E is safe to begin from a reviewed Git checkpoint of this implementation.
