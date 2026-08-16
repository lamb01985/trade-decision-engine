# Decision Cockpit UX

## Purpose and hierarchy

The frontend is a read-only decision cockpit over the existing FastAPI engine. Every primary screen should answer, in order: what is happening, whether the engine reports a valid setup, what remains incomplete, what would make it actionable, where the thesis is invalid, which targets are mapped, and what the backend recommends now.

The Overview owns the selected-symbol decision plan. Watchlist owns the user's compact universe and its diagnostic table. Best Setups owns ranked candidates. Live Radar owns browser-local scanning and transition visibility. Levels owns the complete structural price map. Options owns contract-fit presentation downstream of the underlying decision. Futures Data is an isolated provider-data probe and never presents a setup.

## Audit of the Phase 3C starting point

The existing interface used a dark institutional palette, compact sans-serif labels, monospace prices, bordered cards, responsive grids, an overflow-safe table wrapper, and a horizontally scrollable narrow-screen tab bar. Overview already contained a prominent decision hero, trade plan, level map, timeframe evidence, and collapsed raw evidence. Levels already provided an overview map, full descending ladder, backend confluence, and level detail. Scanner cards were shared by Watchlist, Best Setups, and Live Radar. Options correctly put the underlying decision ahead of contract evidence, while Futures remained explicitly data-only.

The main weaknesses were cognitive rather than architectural. Some supporting text remained very small; the Overview checklist derived presentation judgments from combinations of fields instead of limiting itself to explicit missing-confirmation state; scanner cards hid current price and invalidation in secondary disclosure; `FORMING` and `WATCHING` were flattened to `WAIT`; stale scanner cards could remain visible after a failed refresh; and phone navigation and controls needed more consistent touch sizing.

## State language

Lifecycle language is displayed from backend `setup_state`, `action_now`, and data-status values. Confirmed triggered states may be presented as READY, but the UI does not promote a state using price position, score, grade, or locally calculated conditions. INVALIDATED, TOO LATE, NO TRADE, stale, delayed, unavailable, and closed-session states remain visibly distinct. IGNORE is a quiet scanner grouping, not a replacement backend lifecycle value.

## Overview and trade plan

The first viewport emphasizes symbol, current or last price, freshness, backend decision state, setup, direction, and next action. The plan then separates mapped trigger/targets from invalidation. “What we need to see” lists only backend-reported `missing_confirmation` items; it does not synthesize checkboxes from timeframe alignment, level presence, or frontend thresholds. Missing values remain unavailable rather than being calculated.

## Levels

Current price is the visual anchor. Above-price and below-price rows retain their backend-provided level types and are described as structural context unless a setup-specific trigger, invalidation, or target maps to the same value. Prior-session, opening-range, VWAP, EMA, support, resistance, target, and invalidation labels only appear when supplied by analysis. Level distance is explanatory placement, never a signal.

## Scanner cards and degraded states

Cards expose symbol, direction, setup, quality, lifecycle status, provider price, trigger, invalidation, and next action before disclosure. Targets, R:R, structure, and confirmation evidence remain secondary. IGNORE / NO TRADE is collapsed and visually quiet. DATA UNAVAILABLE uses an error treatment and clears old ranked results so it cannot be mistaken for NO TRADE.

## Market closed and mobile behavior

Closed-session analysis is preparation only. It uses the latest completed-session labels, says that fresh live analysis must revalidate the plan, and must never display closed data as live authorization. The application continues refreshing across the closed-to-open boundary.

On narrow screens, cards and plan sections stack, controls keep at least a 42-pixel target, the app shell prevents page-level horizontal overflow, tabs remain deliberately horizontally scrollable, and wide evidence tables remain inside their own scroll container. The decision state, price, and next action remain ahead of secondary evidence.

## Data integrity

The browser may escape, format, order, group, and label values returned by the API. It must not calculate stops, targets, setup grades, probabilities, confirmation, lifecycle transitions, market state, or authoritative R:R. Frontend-only price distances are descriptive context and cannot change readiness or action language. Missing authoritative values are shown as unavailable or omitted.

## Future React and live-chart roadmap

A later dedicated frontend phase may introduce a typed API client, schema-generated view models, route-level code splitting, component tests, visual regression snapshots, and persistent URL-addressable views in React/Next. Live charts should arrive only after the API defines a timestamped chart-data contract, session boundaries, freshness behavior, and authoritative overlays. Chart overlays must consume backend levels and decisions verbatim; they must not reconstruct indicators or setup logic in the browser. WebSocket work should include reconnect state, snapshot reconciliation, stale-stream gating, and an explicit distinction between observation updates and decision updates.
