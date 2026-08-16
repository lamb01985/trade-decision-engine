# Phase 3D Decision Intelligence Contract

## Authority boundary

The production setup engine remains the sole authority for setup family, lifecycle, trigger,
invalidation, targets, risk/reward, ranking, and confirmation. `decision_intelligence` is a
deterministic runtime projection of the existing `StandardDecision` and mapped levels. It does
not alter setup detection or thresholds. Browser code formats this object and must not promote a
state from price proximity, quality, score, or a touch of the trigger.

The existing fields remain for compatibility. New clients should use the normalized contract.
No breaking API change or database migration is introduced.

## Normalized lifecycle and action

| Existing lifecycle | Recommended action | Meaning |
|---|---|---|
| `NO_SETUP` | `IGNORE` | Valid data produced no qualifying setup. |
| `FORMING` | `MONITOR` | Recognizable structure is still forming. |
| `WATCHING` | `MONITOR` | Watch the mapped setup; required evidence is incomplete. |
| `TRIGGERED` | `WAIT_FOR_CONFIRMATION` | Trigger behavior occurred, but confirmation is incomplete. |
| `CONFIRMED` | `READY` | All existing setup-specific rules passed on current regular-session data. |
| `INVALIDATED` | `INVALIDATED` | The mapped invalidation was breached. |
| `TOO_LATE` | `EXPIRED` | Extension, R/R, or opposing structure made the entry invalid. |

`CONFIRMED` is downgraded to `MONITOR` outside a current regular session. Stale or errored data
always produces `DATA_UNAVAILABLE`. `MANAGE` is reserved but cannot be emitted because the engine
does not track positions or fills.

## Confirmation components

Each component contains `id`, trader-facing `label`, `state` (`PASS`, `PENDING`, or
`UNAVAILABLE`), `required`, and optional structured evidence. `PASS` is emitted only when the
existing decision is `CONFIRMED`. An unmet condition explicitly retained by the setup detector is
`PENDING`. Evidence not retained by the current engine is `UNAVAILABLE`; it is never guessed.

Setup-family component identities preserve the current detectors:

- Opening-range breakout/breakdown and mapped breakout/breakdown retest: 5m acceptance/retest,
  plus relative volume.
- VWAP reclaim: reclaim hold/retest plus relative volume.
- VWAP rejection: rejection/lower close plus relative volume.
- Failed breakout/breakdown: failed reclaim or held reclaim plus relative volume.
- Trend pullback: aligned pullback hold/resumption plus relative volume.
- Momentum continuation: aligned pause break plus relative volume.
- Compression break: acceptance outside the compressed range plus relative volume.

The current detector sometimes retains only the final missing-condition strings, not every
numeric/bar fact used during detection. Those facts are marked unavailable rather than rebuilt
from browser-visible data.

## Next-state requirements

`next_state_requirements` is built only from the setup-family components and existing
`missing_confirmation` values. Each entry contains `id`, `label`, state, and target lifecycle.
The contract does not synthesize price commentary or generic confirmation prose.

## Decision zone

The current engine persists a single deterministic trigger, not an entry range. Therefore the
contract represents it honestly as `lower_bound == upper_bound`, role `SETUP_TRIGGER`, with its
setup-family source and active flag. A wider range will require future engine-owned range evidence;
Phase 3D does not fabricate one.

## Invalidation and targets

Invalidation contains type, price/zone fields, condition, timeframe, and a deterministic label.
The condition is directional and uses the existing mapped invalidation price. Targets and
structural R/R are copied unchanged from the existing decision.

## Level metadata

Every mapped level receives categories, setup-specific status, confluence count, source context,
and freshness context. Trigger, invalidation, and targets are assigned only by exact equality with
the existing decision values. `currently_interacting` is true only for the exact setup trigger in
an existing watching/triggered/confirmed lifecycle; proximity alone never assigns setup relevance.

## NO_TRADE and DATA_UNAVAILABLE

`IGNORE` means valid evidence was evaluated and no setup qualified. `DATA_UNAVAILABLE` means the
engine cannot issue a decision from current valid evidence. These values have distinct labels,
frontend states, and tests. Degraded data can never produce `READY`.

## Auditability and persistence

Existing persistence already stores lifecycle, rules/parameter versions, trigger, invalidation,
targets, R/R, reasons, missing confirmation, state transitions, and level snapshots. These are the
durable audit facts. The normalized contract is derived from them at runtime; persisting it would
duplicate versioned data and risk divergence, so no schema change is needed.

A READY decision can be traced to its setup family, `CONFIRMED` lifecycle, structured PASS
components, mapped levels, rule/parameter versions, and current regular-session freshness.

## Known gaps

- `MANAGE` requires position/fill lifecycle data that does not exist.
- True zone bounds do not exist; current triggers are point levels.
- Some component-level numeric evidence was not retained by the original detector and is exposed
  as unavailable.
- Structural level source timeframes are limited to deterministic labels already present on each
  level; no proximity-based relevance is inferred.
