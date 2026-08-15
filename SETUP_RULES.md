# Deterministic setup rules

This system follows `LEVELS → STRUCTURE → SETUP → CONFIRMATION → PLAN`. Grades are heuristic descriptions of present structure, not probabilities or claims of expectancy. Touching a level and indicator alignment are never confirmation.

## Existing V2 logic retained

- Trend was bullish only when price was above VWAP, EMA9 was above EMA21, and price was above both EMAs; bearish was the inverse; otherwise neutral.
- Levels were HOD/LOD, prior H/L/C, premarket H/L, opening range H/L, VWAP, EMA9/21, 5m/15m pivot swings, and detected gap boundaries. Nearby values were merged and confluence was the number of independent labels.
- The legacy setup path required aligned 5m/15m trend and VWAP direction. A break of opening-range/prior-session structure plus RVOL 1.2 marked confirmation. Its numerical score awarded points for 5m/15m trend, VWAP, EMAs, RVOL, level proximity, trigger, higher-timeframe agreement, and R/R.
- The legacy plan chose the nearest opposite-side level for invalidation. Earlier V2 targets sometimes used fixed 1R/2R projections. The new standardized decision uses mapped structural targets only; legacy fields remain for compatibility.
- The level playbook required a close across a level and a subsequent hold/retest or failed reclaim. The scanner then filtered by liquidity, score, trigger proximity, R/R, and chase state.

Previously implicit thresholds included 0.08% level merging, 0.15% retest proximity, 0.20% gap detection, 0.30% EMA pullback proximity, RVOL 1.2, scanner score 75, trigger proximity 0.75%, minimum R/R 1.5, and chase distance 0.75%. They are now centralized.

## Setup states

- `NO_SETUP`: prerequisites do not identify one unambiguous family.
- `FORMING`: structural prerequisites exist, but price is not yet at a valid trigger.
- `WATCHING`: price is near the trigger; no qualifying close has occurred.
- `TRIGGERED`: a completed 5m close crossed the trigger, but acceptance/retest or volume evidence is missing.
- `CONFIRMED`: the setup-specific completed-bar sequence and RVOL requirement occurred.
- `INVALIDATED`: a tracked setup lost its mapped invalidation or no longer has its prerequisites.
- `TOO_LATE`: price moved too far beyond the trigger or structural R/R deteriorated.

State history is held per symbol by the analysis service (`prior_state` and `state_changed_at`). Research replay passes the prior chronological decision into the same function.

## Setup library

All comparisons use completed 5m bars. `buffer`, `retest tolerance`, and proximity are ATR multiples.

### Opening range breakout / breakdown

Prerequisite: aligned 5m/15m trend in the trade direction and mapped 30-minute opening-range boundary.

```text
WATCHING when close is near OR boundary
TRIGGERED when 5m close crosses boundary + breakout buffer
CONFIRMED when prior close is beyond boundary, next bar retests/holds,
          and RVOL >= threshold
INVALIDATED when mapped invalidation breaks
TOO_LATE when extension > chase ATR or R/R < minimum
```

### Breakout + retest / breakdown + retest

Prerequisite: aligned 5m/15m structure and nearest mapped resistance/support. Trigger is the mapped level. Confirmation requires a prior close beyond it followed by a bar that tests within tolerance, closes on the breakout side, and meets RVOL. A print through the level is not enough.

### VWAP reclaim / rejection

- Reclaim: bar sequence starts below VWAP, closes above the buffered VWAP, then retests and holds above it with RVOL.
- Rejection: price approaches VWAP from below, fails to close above it, then closes below the rejection bar's low with RVOL.
- VWAP is the trigger/invalidation boundary. Targets must still be mapped structure.

### Failed breakout / failed breakdown

- Failed breakout: close above mapped resistance, close back below, then test/fail beneath it. Direction is short.
- Failed breakdown: close below mapped support, reclaim above, then hold the reclaim. Direction is long.
- Without RVOL the sequence remains `TRIGGERED`, not `CONFIRMED`.

### Trend pullback / momentum continuation

Prerequisite: aligned 5m/15m trend. A trend pullback must approach EMA9/structure, hold, then close through the prior bar in the trend direction. Momentum continuation requires a pause bar no wider than 0.75 ATR followed by a buffered close through that pause. Both require RVOL for confirmation; alignment by itself is not a trigger.

### Compression break

Prerequisite: the high-low range of the configured lookback is no greater than the configured ATR multiple. A close outside the compression is only a trigger. Confirmation requires the next bar to accept/retest outside the boundary plus RVOL.

## Invalidation and targets

Invalidation is the nearest mapped structural level on the opposite side of the trigger. Target 1 and target 2 are the next two mapped levels in the trade direction. If any are absent, the standardized decision reports insufficient structure and cannot receive an actionable quality grade. No synthetic price projections are used by the standardized decision.

## Chase protection

`TOO_LATE / DO_NOT_CHASE` applies when price extends more than the configured ATR multiple past its trigger or target-1 structural R/R falls below the minimum. The response says to wait for a retest, next level, or new structure.

## Quality

- `A`: score meets the A threshold, structure is complete, and state is confirmed or exceptionally close (`WATCHING`).
- `B`: complete recognizable setup meeting the B score threshold but missing meaningful evidence.
- `C`: weak/mixed heuristic candidate; scanner suppresses it.
- `NO_TRADE`: no setup, invalidated, too late, ambiguous, or structurally incomplete.

The score remains an internal ranking aid. It is not a percentage probability.

## Central parameters

All values live in `app/parameters.py` and are shared by live/research logic:

| Parameter | Default | Meaning |
|---|---:|---|
| opening_range_minutes | 30 | Opening range after 09:30 ET |
| structure_lookback_bars | 60 | Reserved bounded structure history |
| swing_window_bars | 2 | Bars required on each side of a swing |
| maximum_swings_per_timeframe | 8 | Prevents level clutter |
| level_merge_atr_fraction | 0.10 ATR | Volatility-aware zone merge |
| level_merge_minimum_pct | 0.08% | Minimum merge tolerance |
| breakout_buffer_atr | 0.05 ATR | Close required beyond a trigger |
| retest_tolerance_atr | 0.15 ATR | Valid retest distance |
| vwap_tolerance_atr | 0.12 ATR | VWAP interaction tolerance |
| volume_confirmation_rvol | 1.20 | Confirmation RVOL floor |
| compression_lookback_bars | 6 | Compression sample |
| compression_range_atr | 1.25 ATR | Maximum compression width |
| chase_threshold_atr | 1.0 ATR | Maximum post-trigger extension |
| trigger_proximity_atr | 0.75 ATR | Watching distance |
| minimum_structural_risk_reward | 1.50 | Minimum target-1 R/R |
| opposing_level_buffer_atr | 0.35 ATR | Reserved opposing-structure clearance |
| atr_period | 14 | 5m ATR sample |
| gap_detection_pct | 0.20% | Session-open gap boundary threshold |
| quality_a_score / quality_b_score | 85 / 75 | Heuristic grade boundaries |
| score_rvol_baseline | 0.70 | Legacy score baseline |
| score_level_proximity_pct | 0.35% | Legacy score proximity |
| trend_pullback_tolerance_atr | 0.25 ATR | EMA/structure pullback tolerance |
| momentum_pause_range_atr | 0.75 ATR | Maximum continuation pause-bar width |
| level_testing_atr / level_approaching_atr | 0.15 / 0.50 ATR | Playbook proximity states |

These parameters are not optimized and must not be presented as validated edge.
