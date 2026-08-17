# Phase 4E — Excursion, Opportunity, and Path Analytics

## Status, objective, and authority

This is the authoritative Phase 4E measurement contract. Phase 4E is a
deterministic research-only layer over the immutable Phase 4D denominator and
accepted Phase 4C outcomes. It measures what price did after READY. It neither
predicts edge nor calls, changes, or writes production detection, setup,
threshold, score, confirmation, trigger, invalidation, target, rank, scanner,
notification, brokerage, or execution state.

Contract identifiers are `excursion-artifact-v1`,
`excursion-measurement-v1`, `minute-ohlc-path-v1`,
`ready-opportunity-context-v2`, and `excursion-aggregation-v1`. Changing a
measurement contract changes semantic fingerprints.

## Reference event, identity, and provenance

The statistical unit is one unique `setup_instance_id`. The reference event is
the chronologically first immutable `CANDIDATE` observation whose
`recommended_action` is `READY`. Later READY/MONITOR rows cannot add samples.
The reference price is the frozen engine trigger, consistent with Phase 4C's
theoretical entry. The reference timestamp is `decision_bar_timestamp`.

Every measurement preserves the instance and observation IDs; engine,
setup-rule, parameter, observation, feature, context, and regime versions;
source mode and replay run; replay, calendar, cache, and market-data versions.
LIVE and REPLAY are separate compatibility partitions. Versions are not mapped
or silently pooled.

## Forward path and horizons

The builder accepts explicit point-in-time historical minute OHLCV evidence;
it performs no provider fetch. Bars strictly after READY are sorted and
deduplicated. Standard horizons are 5, 15, 30, 60, 120 minutes and end of
regular session. Additional positive-minute horizons are additive. A minute
horizon stops at the versioned regular-session close and is marked session
truncated when the requested duration cannot be observed. Overnight bars are
excluded. End-of-session uses all eligible regular-session minutes before the
close. Missing and duplicate expected minutes remain explicit.

## MFE, MAE, and R

For LONG, favorable movement is `bar.high - reference` and adverse movement is
`reference - bar.low`. For SHORT they are `reference - bar.low` and
`bar.high - reference`. Both MFE and MAE are reported as non-negative
magnitudes in price, percent of reference price, and R. Time to extrema uses
the first minute attaining the maximum.

The only R denominator is the frozen structural risk
`abs(trigger - invalidation)`. Missing, zero, non-finite, or ambiguous inputs
produce null R values plus an explicit reason; no denominator is reconstructed.

## Timing, thresholds, and ambiguity

Thresholds are +0.25R, +0.5R, +1R, +1.5R, +2R, and +3R. First-hit time is
elapsed seconds from the READY decision bar. A minute whose OHLC touches both a
favorable threshold and invalidation has unknown ordering: reached and time
are null and the measurement is marked ambiguous. No intrabar sequence is
fabricated. Directly observable extrema remain usable even when a different
path metric is ambiguous; ambiguity is metric-specific and always counted.

MFE before invalidation uses only bars strictly before the first invalidation
minute, excluding that ambiguous bar. MAE before meaningful favorable movement
uses +0.5R as the v1 definition and bars strictly before its first minute. If
adverse and meaningful favorable movement share that minute, the ordering is
flagged. End return uses the final observed close in the horizon; incomplete
coverage is never reclassified as failure. End-of-session return is the return
of the end-of-session horizon when complete.

## Frozen room-to-run context

Opportunity fields come only from the READY snapshot: directional distance to
nearest relevant support/resistance, HOD/LOD, prior-day high/low, opening-range
high/low, VWAP, structural target, ATR-normalized structural space, frozen
structural risk/reward, relative volume, ATR, trend, time of day, setup family,
direction, and available regime. Every field has explicit availability. The
`structural_space_atr` slices are `<0.5`, `0.5–<1`, `1–<2`, and `>=2 ATR`.
These are descriptive cohorts, not a score or decision rule.

## Aggregation and sample handling

Compatibility-safe cohorts include setup family, direction, symbol, horizon,
Phase 4C outcome state, rules/parameter versions, and source mode. Existing
time, RVOL, ATR, trend, regime, symbol, family, and direction slices are reused.
Aggregates expose total, evaluable, incomplete, and ambiguous counts; sample
IDs; mean, median, population standard deviation, min/max, and p10/p25/p50/p75/p90
for excursion and timing metrics; threshold hit rates and hit-time
distributions; and MAE-before-success among complete setups reaching +1R.
Conditioning on Phase 4C outcome is descriptive only.

Quantity labels use `n_evaluable`: `<20 INSUFFICIENT`, `20–49 PRELIMINARY`,
`50–99 MODERATE_SAMPLE`, and `>=100 SUBSTANTIAL_SAMPLE`. `n` is never hidden.

## Missing data, determinism, persistence, and API

Missing reference price, invalid risk, insufficient history, missing/duplicate
bars, session truncation, unavailable context, and incompatibility remain null
or explicit; they are never zero. Ambiguous observations remain reported.
Percentiles use deterministic linear interpolation over sorted values.

Canonical JSON sorts keys and semantic sample order. SHA-256 fingerprints
exclude clocks, random IDs, paths, and row order. Identical inputs build
byte-identical artifacts. Optional persistence is immutable,
content-addressed `phase4e-<fingerprint>.json`. The read-only
`/api/v1/research/excursions` endpoint requires an explicitly installed
research bar source; it provides no production write path and does not fetch
implicitly.

## Known limitations

- Minute OHLC cannot resolve intrabar chronology.
- The ledger does not store raw bars, so builds require a separately
  completeness-validated, versioned minute source.
- Current READY snapshots may leave structural fields unavailable.
- Exchange early-close correctness inherits the accepted versioned calendar.
- No compatibility mappings, statistical significance tests, causal claims,
  optimization, or production recommendations exist in v1.

## Acceptance validation — 2026-08-16

Mechanics validation covers both directions, multiple families and horizons,
missing/ambiguous evidence, thresholds, conditioning, opportunity slices, and
deterministic rebuilds. The bounded existing real replay used the accepted
versioned SPY/NVDA cache and a disposable copy of its replay ledger. Of 193
ledger setup instances, five had a first READY reference; the Phase 4C pass
evaluated two and conservatively left three not evaluable. For the
end-of-session Phase 4E horizon all five paths were complete, one had an
intrabar ambiguity, and two had valid structural R. Their MFE-R values were
4.64R and 14.35R (median 9.49R); MAE-R values were 2.37R and 6.08R (median
4.22R). These unusually small-denominator values are mechanics evidence only,
not strategy evidence. After excluding missing-risk and ambiguous ordering,
only one threshold sample remained; it reached 0.5R/1R/1.5R/2R/3R at 60
seconds. No unambiguous successful-setup heat sample was available. Family,
direction, and structural-space cohorts were emitted, including 0.5–1 ATR and
1–2 ATR space buckets. The repeat fingerprint was
`1ca3e2536553c15f0af076053ca668d50152c2ca8719e9764fafe44c62f7a6a9`.

The final real-data build produced 30 horizon measurements in about 0.260 seconds
(approximately 115 measurements/second). The principal scaling limitation is
repeated per-instance ledger reads plus repeated per-horizon path filtering;
large research runs should add a bounded joined ledger read and pre-index bars
by symbol/session before considering other optimization.
