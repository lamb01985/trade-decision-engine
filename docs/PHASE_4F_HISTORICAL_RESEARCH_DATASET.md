# Phase 4F — Historical Evidence Population and Research Dataset

## Objective and frozen research subject

Phase 4F constructs reproducible descriptive evidence for the frozen production
engine accepted at source checkpoint `3c869f7301ca44ceceb305fcb4ee53152e0d517a`.
It does not optimize, tune, rank strategies, or change trading logic. Every run
fingerprints the engine, setup rules, parameters, identity, observation,
features, outcomes, Phase 4D evidence, Phase 4E excursion/path/opportunity,
replay, provider/cache, and exchange-calendar contracts. Later production
changes do not change what an existing dataset describes.

The authoritative initial universe is the production scanner's ordered default:
`QQQ, SPY, IWM, SMH, NVDA, AMD, AMZN, META, AAPL, MSFT, AVGO, TSLA, GOOGL`.
The exact resolved universe is immutable in each manifest.

## Historical-window policy

The target population window is the longest provider-complete interval up to
approximately twelve months that the accepted paginated Massive minute source,
calendar, cache, point-in-time feature reconstruction, and operational budget
can support. A shorter period must be explicit: project symbol-session volume,
runtime, and storage from a bounded pilot and record the constraint. Dates are
selected chronologically, never from observed setup performance.

The required pre-flight pilot is three symbols over five sessions. It must run
replay, immutable ledger capture, Phase 4C outcomes, Phase 4D-compatible
evidence, and all Phase 4E measurements. Any correctness, pagination,
chronology, identity, persistence, or determinism failure blocks expansion.

## Orchestration, run identity, and isolation

`app.research_dataset` processes deterministic `symbol + session + compatibility`
units. A SHA-256 run ID covers ordered symbols, sessions, every compatibility
dimension, and `phase4f-symbol-session-v1`. Concurrency is bounded (one by
default and never more than eight). The provider command uses only the accepted
paginated Massive cache. Each unit writes a disposable isolated SQLite ledger;
it cannot access production persistence, notifications, brokerage, or execution.

Run and inspect a provider-backed research job with:

```text
python -m app.research_dataset run --run-dir data/research/<run> \
  --symbols QQQ,SPY,NVDA --start YYYY-MM-DD --end YYYY-MM-DD \
  --source-commit <accepted-sha>
python -m app.research_dataset status --run-dir data/research/<run>
```

The status surface reports run state, completed symbols and units, the current
unit, elapsed time, a mechanical estimate when calculable, failures,
incompleteness, setups, outcomes, and excursions. It is local research tooling.

## Resumability and failure policy

Only a `COMPLETE` unit with the exact deterministic compatibility key is reused.
Incomplete, failed, unavailable, corrupt, or incompatible work cannot
masquerade as complete. Completed unit files are immutable; collisions fail
closed. Restart reconstructs aggregate outputs from compatible units and
continues granularly.

Failures retain class, retryability, and exclusion reason. Provider transient
failures may be retried by an explicitly bounded future runner. Permanent
unavailability and deterministic replay, calendar, outcome, excursion,
persistence, or compatibility failures are not retried indefinitely.

## Manifest, coverage, and dataset schema

`manifest.json` canonically records run ID, contracts, source SHA, universe,
requested range and sessions, actual symbol-session coverage, all version
dimensions, requested/completed/unavailable/incomplete/failed counts,
exclusions, setup/READY/outcome/excursion totals, and semantic fingerprints.
Wall-clock metrics never enter semantic fingerprints.

Coverage includes setup instances, READY instances, MONITOR observations,
NO_SETUP and NOT_OBSERVED cycles, provider-unavailable units, gaps, evaluable,
incomplete and ambiguous outcomes, evaluable/incomplete excursions, and
unavailable structural fields. Missing requested work remains visible.

`dataset.json` preserves one end-of-session statistical record per unique first
READY setup instance. It retains identity, family, direction, symbol, frozen
reference state and risk, point-in-time slices/opportunity context, Phase 4C
outcome and ambiguity metadata, full Phase 4E MFE/MAE/R/timing/threshold/path
measurements, structural-room availability, source/replay/research IDs, and all
compatibility versions. Provenance is deliberately not flattened away.

`baseline.json` is descriptive only: counts by family, direction, symbol and
time bucket; outcome denominators; terminal-R, MFE, MAE, normalized excursion,
timing, threshold, and structural-room distributions. It performs no feature
combination search, ranking, inference, score production, or rule proposal.

## Chronological partitions and holdout

`chronological-60-20-20-v1` assigns whole ordered sessions by chronology only:
60% development, 20% validation, and 20% holdout. Fewer than five sessions is
reported as insufficient. The holdout metadata states that it remains untouched
until an explicit future final-evaluation contract authorizes use. Partition
selection never examines outcomes.

## Quality gates and determinism

Acceptance verifies: point-in-time feature timestamps; unique first-READY
identity and denominators; strictly forward same-session evidence; complete
compatibility partitions; REPLAY/LIVE isolation; versioned session closes;
pagination/truncation safeguards; independent semantic rebuild equality; and
explicit requested-versus-observed coverage. Duplicate statistical units or
incompatible pooling block acceptance.

Canonical JSON and SHA-256 cover semantic content only. Equivalent independent
builds must match setup units, outcomes, excursions, coverage, dataset bytes,
and semantic fingerprints. Elapsed time and throughput may differ.

## Performance and known limitations

The runner records replay, outcome, excursion, and end-to-end time per unit.
Pilot throughput projects the larger run and identifies its dominant component.
Infrastructure optimization may batch reads or index bars but cannot change
semantics.

- Provider entitlement and retained historical depth must be established by a
  bounded pre-flight; the repository cannot infer them from an API key.
- The accepted calendar is versioned but is not a complete exchange-issued
  calendar library; its accepted holiday/early-close scope remains inherited.
- Minute OHLC cannot resolve intraminute ordering.
- Some frozen READY snapshots do not contain every structural context field.
- A complete twelve-month population is a separately authorized operational
  run after projected cost and duration are reviewed.
