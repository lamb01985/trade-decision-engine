# Phase 5D — Chart Snapshot Contract

## Endpoint

`GET /api/v1/chart/{symbol}` is read-only. Query parameters are:

- `timeframe`: `1m`, `5m`, `15m`, `1h`, or `1d` (default `5m`).
- `lookback_sessions`: 1–60 (default 5). Used when an explicit range is absent.
- `start` and `end`: inclusive ISO dates; both are required together and the range is capped at 120 days.
- `extended_hours`: `false` for the 09:30–16:00 ET regular session (default), or `true` for 04:00–20:00 ET.

Invalid symbols or query values return an explicit 4xx response. Provider failures remain distinguishable from a 404 no-data response.

## Schema and provenance

The typed response contains `schema_version`, `symbol`, `timeframe`, `extended_hours`, ordered `candles`, `overlays`, `market_status`, `market_session`, `provider_timestamp`, `generated_timestamp`, `data_through_timestamp`, `data_age_seconds`, `stale`, `source`, and `forming_bar_included`.

Each candle has an RFC 3339 `timestamp` plus numeric `open`, `high`, `low`, `close`, and `volume`. `source` identifies the existing Massive aggregates path. Candle and overlay shapes are intentionally stable units that a later WebSocket/SSE message can reuse for append/update events.

## Timeframe and completion semantics

Massive one-minute aggregates remain the canonical input and retain the accepted pagination and chronological validation behavior. Intraday bars are aligned to the selected equity session in Eastern Time. A bucket is returned only after its deterministic end time has passed. The currently forming minute or aggregate bucket is omitted; `forming_bar_included` is therefore always `false` in v1.

Daily bars are emitted only after the applicable regular or extended session has completed. Early closes use the existing market-calendar semantics. Missing provider minutes are not filled, interpolated, or fabricated; aggregation only combines provider-returned observations.

## Market hours and freshness

When the market is closed, the latest completed historical bars still return with `market_session=CLOSED`, `market_status=MARKET_CLOSED_LAST_SESSION`, `stale=true`, and an explicit `data_through_timestamp`. Historical data is never labeled live. During open sessions, the existing freshness classifier maps the latest completed-bar timestamp to `LIVE_CURRENT`, `LIVE_STALE`, or `DATA_ERROR`.

## Overlays

The endpoint never runs the decision engine merely to draw a chart. The latest completed candle supplies a default-visible current/last-completed-price line. If a current analysis already exists in the service cache, that line is replaced by the analysis current price and the response also includes its decision/entry trigger, invalidation, targets 1 and 2, session VWAP, EMA 9, EMA 21, important structural levels, and setup marker where present. If no cached analysis exists, trade-plan and indicator overlays are absent rather than inferred.

Current price and trade-plan levels are default-visible. VWAP, EMAs, structure, and markers are opt-in. All values originate in backend state; the browser only validates and renders them.

## Caching and performance

Chart reads use the existing Massive provider and a 15-second in-process cache keyed by symbol and bounded date range. Resampling is linear in returned minute bars and performs no strategy replay, persistence write, notification, or population-job work. API latency continues to use the existing request-duration logging middleware. Representative transformation benchmarks should use the normal 5-session `5m` response and the maximum 60-session `1d` response.

## Realtime extension path

A future stream can reuse `ChartCandle` and `ChartOverlay` payloads with an operation such as `append`, `replace-forming`, or `overlay-update`, while a snapshot remains the recovery/bootstrap source. Streaming is intentionally not implemented in Phase 5D.

## Limitations

- v1 supports US equity session semantics only.
- Overlay history is not reconstructed; overlays describe cached current analysis state.
- No forming bar is returned, even as a separately labeled provisional object.
- The cache is process-local and is not a cross-instance CDN or Redis cache.
