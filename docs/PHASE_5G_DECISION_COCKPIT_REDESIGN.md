# Phase 5G — Decision Cockpit Redesign

## Redesign principles

The React preview now follows the trader's decision sequence: decision, trade structure, open conditions and invalidation, evidence/opportunity, then diagnostics. The interface remains a read-only presentation of backend state. It does not calculate eligibility, confirmations, targets, invalidation, scanner rank, evidence, or room to run.

## Old → new information hierarchy

The old Overview began with a large state hero followed by a grid of similarly weighted cards. The new Overview is a command-center split: price context and backend overlays occupy the main workspace, while a compact decision stack answers what to do, what the trade is, what must happen next, and where the thesis fails. Evidence is deliberately last and compact.

## Major layout decisions

- Desktop (including 1440p) uses a wide chart with a decision sidebar in the first viewport.
- The chart uses completed backend candles and restrained default overlays only.
- The decision state is prominent but no longer consumes a hero-sized block.
- Trade structure is a single scan line: zone, invalidation, T1, T2, and structural R:R.
- Conditions merge duplicate backend-authored `waiting_for` and `missing_confirmation` strings for presentation only.
- The explicit invalidation panel remains visually distinct and does not weaken any safeguard.

## Removed duplication

The setup name and presentation state each have one authoritative location in the decision header. The separate backend-state card, repeated waiting/context card, and large global safety disclaimer were removed. Market freshness appears once above the cockpit. The invalidation price remains in both the compact trade-plan sequence and the dedicated failure boundary because those serve different scanning and safety needs; explanatory text is not repeated.

## Evidence placement

Overview exposes only contract-backed evidence status and marks sample, excursion, target-rate, and room-to-run values unavailable until a compatible analysis/evidence join exists. The Evidence route leads with the question “Does this setup historically deserve attention?”, supports the Phase 4F metric shape, and places distributions and provenance in expandable detail. Disabled cohort controls communicate the intended interaction without pretending the API supports it.

## Chart placement

The five-minute chart is now part of Overview rather than being buried behind several card screens. Current price and backend-default overlays load automatically. Optional VWAP, EMA, structural, and other overlays remain user-controlled and backend-authored. The full Chart route retains timeframe and extended-hours controls.

## Radar and Watchlist

Scanner results are compact expandable rows. They surface symbol, direction, setup, lifecycle, decision trigger, R:R, evidence, room-to-run, and freshness. Backend order is preserved for every mode; the browser no longer re-sorts “Best setups.” Proximity is explicitly unavailable because the current scanner contract does not supply an authoritative zone-distance field. Watchlist uses the same compact primitive but is framed as monitoring rather than a duplicate ranked-card experience.

## Levels

The active trade plan is separated from deeper structure. Additional backend levels are grouped by their supplied type labels and ordered by backend-supplied distance when present. No support/resistance or proximity calculation is created in the browser.

## Responsive strategy

Wide desktop uses chart-left and decision-right. Laptop/tablet stacks the decision first and chart second. Mobile preserves that priority, reduces the plan to a two-column strip, keeps invalidation visible, collapses scanner columns into expandable detail, and retains 44px navigation targets. Mobile is a monitoring view rather than a compressed copy of every desktop column.

## Options and Futures

Options remains an honest placeholder for volume, OI, IV, GEX, walls, gamma flip, strike concentration, and dealer interpretation. It performs no browser calculations. Futures retains the existing data-only extension state and no setup logic.

## Deferred backend contracts

- Analysis-scoped evidence join keyed by setup family, direction, cohort/slice, and evidence version.
- Scanner-provided decision-zone bounds and authoritative distance/proximity value.
- Scanner freshness/status fields with a consistent timestamp requirement.
- Scanner evidence sample size/quality and room-to-run context beyond free-form statuses.
- Evidence cohort/slice discovery and query parameters, outcome buckets including neutral, realized-R distribution buckets, and explicit units/format metadata for rates and excursions.
- Categorized level roles or stable level-type identifiers to replace presentation grouping of labels.
- Production options-extension API and any future futures decision-support contract.

## Known limitations

Evidence cannot yet be joined to the selected Overview symbol through the current frontend contract. Scanner proximity is unavailable. Some level categories depend on backend label text because stable categories are absent. The fixture intentionally contains no fabricated evidence and fixture mode remains opt-in and visibly labeled. No deployment, production replacement, or legacy cockpit change is part of this phase.
