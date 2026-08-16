# Phase 4C — Conservative Outcome Evaluator

## Status, scope, and authority

Phase 4C is a downstream research measurement component. It reads the first
immutable `READY` observation for each setup instance and appends a versioned
`setup_outcome_assessments` row. It does not update setup observations, call or
change the production detector, alter trading rules, or enter scanner, alert,
notification, broker, or deployment paths.

Versions:

```text
outcome_contract_version       outcome-v1
calculation_version            minute-boundary-r-v1
evaluation_horizon_id          intraday-rth-calendar-v1
mfe_mae_policy_version         ready-through-terminal-r-v1
```

## Frozen reference and classification

The evaluator copies the READY observation's decision reference, engine
trigger, invalidation, targets, direction, and initial risk. The trigger is the
theoretical entry; no fill, latency, spread, slippage, or broker execution is
inferred. A READY observation with an incomplete or non-positive-risk frozen
plan remains explicitly not evaluable and receives no assessment.

One-minute bars strictly after the READY decision bar are evaluated through the
accepted regular-session close, including early closes. First-boundary results
are `TARGET_FIRST`, `INVALIDATION_FIRST`, `NEITHER`, or `AMBIGUOUS`:

- different-minute crossings are ordered chronologically;
- target and invalidation in one minute are `AMBIGUOUS`;
- missing or duplicate minutes that could change ordering are `AMBIGUOUS`,
  never `NEITHER`;
- a complete horizon with neither boundary is `NEITHER`;
- a READY observation on the final session minute has a complete zero-minute
  future horizon and is conservatively `NEITHER`.

## R metrics, provenance, and immutability

Long favorable R is `(price - entry) / 1R`; short favorable R is
`(entry - price) / 1R`. MFE and MAE are symmetric non-negative magnitudes from
the frozen entry through the first final-target/invalidation boundary or the
session horizon. The evidence manifest records completeness, missing/duplicate
minutes, boundary bars, and a deterministic evidence hash.

Outcome evidence preserves `source_mode` and `replay_run_id` from the READY
observation and records the accepted replay contract, session-calendar, replay
cache, outcome-contract, calculation, and MFE/MAE policy versions. Historical
bars must come from the accepted Massive adjusted-minute paginated and
completeness-validated path.

Assessment identity is deterministic and database uniqueness covers the READY
observation, outcome contract, horizon, and evidence cutoff. Repeated
evaluation is idempotent. Corrections require a newly versioned or explicitly
superseding appended assessment; original observations and assessments are
never rewritten.
