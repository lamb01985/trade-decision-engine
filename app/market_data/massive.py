import asyncio
import contextvars
import os
import time
import uuid
import logging
import re
from datetime import date, datetime, timezone
from urllib.parse import parse_qsl, urlsplit, urlunsplit, quote

import httpx

from app.market_data.base import AggregateBatch, MarketDataError, MarketDataProvider, StockObservation
from app.models import Bar

logger = logging.getLogger(__name__)
_live_context = contextvars.ContextVar("market_data_live_context", default=None)
_CREDENTIAL_PATTERN = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|authorization)(\s*[=:]\s*)([^\s&,;]+)"
)


def _safe_response_excerpt(response: httpx.Response, api_key: str, limit: int = 300) -> str:
    """Return bounded provider detail with credential-like values removed."""
    # Redact before truncation: a boundary must not disclose a secret prefix.
    excerpt = response.text
    if api_key:
        import json
        for secret in (api_key, quote(api_key, safe=''), json.dumps(api_key)[1:-1]):
            excerpt = excerpt.replace(secret, '[REDACTED]')
    excerpt = _CREDENTIAL_PATTERN.sub(r"\1\2[REDACTED]", excerpt)
    return excerpt[:limit].replace("\r", " ").replace("\n", " ")



def log_configuration_metadata(api_key: str | None, base_url: str) -> None:
    """Log startup configuration presence without URL credentials or secret values."""
    parsed = urlsplit(base_url)
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname + (f":{parsed.port}" if parsed.port is not None else "")
    safe_base_url = urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    logger.info("MASSIVE_API_KEY present: %s", str(bool(api_key)).lower())
    logger.info("MASSIVE_API_KEY length: %d", len(api_key or ""))
    logger.info("MASSIVE_BASE_URL: %s", safe_base_url)


class MassiveMarketDataProvider(MarketDataProvider):
    """Massive equity and futures REST data adapter; no strategy logic lives here."""

    def __init__(self, api_key: str, base_url: str = "https://api.massive.com") -> None:
        self._api_key = api_key
        self._client = httpx.AsyncClient(base_url=base_url, timeout=20.0)

    async def _get_payload(self, path: str, params: dict[str, str | int | bool]) -> dict:
        request_params = {**params, "apiKey": self._api_key}
        context = _live_context.get()
        unified = os.getenv("MARKET_DATA_UNIFIED_ENABLED") == "true"
        lease = None
        budget = None
        if unified or context or os.getenv("MASSIVE_BUDGET_ENFORCED") == "true":
            from app.market_data.budget import configured_budget, BudgetUnavailable
            try:
                budget = configured_budget()
                scope = os.getenv("MASSIVE_ACCOUNT_BUDGET_SCOPE", "")
                role = "new" if context else "legacy"
                if context and time.monotonic() >= context:
                    raise MarketDataError("REFRESH_DEADLINE")
                admission = await budget.admit(scope, str(uuid.uuid4()), role, 10000 if context else 20000)
                if not admission["granted"]:
                    if hasattr(budget, "client"):
                        await budget.client.aclose()
                    raise MarketDataError("DEPENDENCY_UNAVAILABLE" if admission.get("reason") in {"POLICY_MISMATCH", "DUPLICATE_ATTEMPT"} else "BUDGET_EXHAUSTED")
                lease = admission["lease_id"]
            except BudgetUnavailable:
                if budget is not None and hasattr(budget, "client"):
                    await budget.client.aclose()
                raise MarketDataError("DEPENDENCY_UNAVAILABLE") from None
        try:
            timeout = min(10.0, context-time.monotonic()) if context else 20.0
            if timeout <= 0:
                raise MarketDataError("REFRESH_DEADLINE")
            response = await asyncio.wait_for(self._client.get(path, params=request_params, **({"timeout": timeout} if context else {})), timeout=timeout)
            logger.debug("Massive response path=%s status_code=%s", path, response.status_code)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            response = exc.response
            logger.warning(
                "Massive request failed status=%s path=%s body=%s",
                response.status_code,
                response.request.url.path,
                _safe_response_excerpt(response, self._api_key),
            )
            error = MarketDataError("PROVIDER_NOT_ENTITLED" if response.status_code == 403 else "PROVIDER_RATE_LIMIT" if response.status_code == 429 else "UPSTREAM_FAILURE")
            error.status_code = response.status_code
            raise error from None
        except (httpx.HTTPError, ValueError, asyncio.TimeoutError) as exc:
            # httpx exception text includes the full request URL. Since the
            # provider credential is a query parameter, never log the
            # exception object or traceback here.
            logger.warning("Massive request failed for %s (%s)", path, type(exc).__name__)
            raise MarketDataError("Massive market-data request failed") from exc
        finally:
            if lease:
                try:
                    await budget.release(scope, lease)
                except Exception:
                    pass  # Lease expires; never refund an attempted request.
                finally:
                    if hasattr(budget, "client"):
                        await budget.client.aclose()
        if not isinstance(payload, dict):
            raise MarketDataError("UPSTREAM_MALFORMED")
        if payload.get("status") in {"ERROR", "NOT_AUTHORIZED"}:
            raise MarketDataError("PROVIDER_NOT_ENTITLED" if payload.get("status") == "NOT_AUTHORIZED" else "UPSTREAM_FAILURE")
        return payload

    async def get_bounded(self, path, params, deadline=None, paginate=False):
        """Separate bounded adapter; legacy pagination is deliberately unchanged."""
        deadline = deadline or time.monotonic() + 30
        token = _live_context.set(deadline)
        items, pages, seen, cursor = [], 0, set(), False
        error = None
        original_path = path
        original_params = dict(params)
        try:
            for _ in range(10):
                if time.monotonic() >= deadline:
                    error = 'REFRESH_DEADLINE'
                    break
                payload = await self._get_payload(path, params)
                pages += 1
                if paginate:
                    expected_symbol = original_path.split('/ticker/',1)[1].split('/',1)[0]
                    if payload.get('ticker') != expected_symbol:
                        raise MarketDataError('IDENTITY_MISMATCH')
                    requested_adjusted = str(original_params.get('adjusted', 'true')).lower() == 'true'
                    if type(payload.get('adjusted')) is not bool or payload['adjusted'] is not requested_adjusted:
                        raise MarketDataError('ADJUSTMENT_MISMATCH')
                page = payload.get('results', []) if paginate else [payload.get('ticker')]
                if not isinstance(page, list) or any(not isinstance(row, dict) for row in page):
                    raise MarketDataError('UPSTREAM_MALFORMED')
                items.extend(page)
                cursor = bool(payload.get('next_url')) if paginate else False
                if not cursor:
                    if paginate and len(page) >= 50000:
                        error = 'PAGE_LIMIT'
                    break
                next_url = payload['next_url']
                parsed = urlsplit(next_url)
                base = urlsplit(str(self._client.base_url))
                if (parsed.scheme, parsed.netloc, parsed.path) != (base.scheme, base.netloc, original_path) or next_url in seen or parsed.fragment:
                    raise MarketDataError('INVALID_CURSOR')
                seen.add(next_url)
                path = parsed.path
                query = [(key,value) for key,value in parse_qsl(parsed.query) if key.lower() != 'apikey']
                if len({key for key,value in query}) != len(query):
                    raise MarketDataError('INVALID_CURSOR')
                params = dict(query)
                if any(key in params and str(params[key]) != str(value) for key,value in original_params.items()):
                    raise MarketDataError('INVALID_CURSOR')
                params = {**original_params, **params}
            else:
                error = 'PAGE_LIMIT'
        except MarketDataError as exc:
            error = str(exc) if str(exc) in {'PROVIDER_NOT_ENTITLED','BUDGET_EXHAUSTED','DEPENDENCY_UNAVAILABLE','REFRESH_DEADLINE','INVALID_CURSOR','PAGE_LIMIT','IDENTITY_MISMATCH','ADJUSTMENT_MISMATCH'} else 'UPSTREAM_FAILURE'
        finally:
            _live_context.reset(token)
        return dict(items=items, pages_read=pages, pagination_complete=error is None and not cursor,
                    next_cursor_present=cursor, requested_window=None, returned_window=None, error=error)

    async def _get_paginated_payload(self, path: str,
                                     params: dict[str, str | int | bool]) -> dict:
        """Follow provider next_url deterministically and reject ambiguous truncation."""
        results: list[dict] = []
        seen_urls: set[str] = set()
        page_path, page_params = path, dict(params)
        for _ in range(10_000):
            payload = await self._get_payload(page_path, page_params)
            page = payload.get("results", [])
            if not isinstance(page, list):
                raise MarketDataError("Massive aggregate results are malformed")
            results.extend(page)
            next_url = payload.get("next_url")
            if not next_url:
                # The documented cap is 50,000 base aggregates. A full page
                # without a continuation cannot prove that the range ended.
                if len(page) >= 50_000:
                    raise MarketDataError("Massive aggregate range may be truncated at 50000 results")
                return {**payload, "results": results, "resultsCount": len(results)}
            if not isinstance(next_url, str) or next_url in seen_urls:
                raise MarketDataError("Massive aggregate pagination is invalid or cyclic")
            seen_urls.add(next_url)
            parsed = urlsplit(next_url)
            if parsed.hostname and parsed.hostname not in {self._client.base_url.host}:
                raise MarketDataError("Massive aggregate pagination changed provider host")
            page_path = parsed.path
            page_params = {key: value for key, value in parse_qsl(parsed.query)
                           if key.lower() != "apikey"}
        raise MarketDataError("Massive aggregate pagination exceeded the safety page limit")

    async def get_minute_bars(self, symbol: str, start: date, end: date) -> list[Bar]:
        path = f"/v2/aggs/ticker/{symbol}/range/1/minute/{start.isoformat()}/{end.isoformat()}"
        payload = await self._get_paginated_payload(
            path, {"adjusted": "true", "sort": "asc", "limit": 50000})
        results = payload.get("results", [])
        if not results:
            raise MarketDataError(f"No market data returned for {symbol}")
        bars = [
            Bar(
                timestamp=datetime.fromtimestamp(item["t"] / 1000, tz=timezone.utc),
                open=item["o"], high=item["h"], low=item["l"], close=item["c"], volume=item["v"],
            )
            for item in results
        ]
        if any(left.timestamp >= right.timestamp for left, right in zip(bars, bars[1:])):
            raise MarketDataError("Massive aggregate pages are not strictly chronological")
        return bars

    async def get_daily_bars(self, symbol: str, start: date, end: date,
                             adjusted: bool = True) -> AggregateBatch:
        """Return daily bars with adjustment and request provenance preserved."""
        ticker = symbol.strip().upper()
        path = f"/v2/aggs/ticker/{ticker}/range/1/day/{start.isoformat()}/{end.isoformat()}"
        payload = await self._get_paginated_payload(path, {
            "adjusted": str(adjusted).lower(), "sort": "asc", "limit": 50000,
        })
        results = payload.get("results", [])
        bars = tuple(Bar(
            timestamp=datetime.fromtimestamp(item["t"] / 1000, tz=timezone.utc),
            open=item["o"], high=item["h"], low=item["l"], close=item["c"], volume=item["v"],
        ) for item in results)
        if not bars:
            raise MarketDataError(f"No daily market data returned for {ticker}")
        if any(left.timestamp >= right.timestamp for left, right in zip(bars, bars[1:])):
            raise MarketDataError("Massive daily aggregate pages are not strictly chronological")
        returned_adjusted = payload.get("adjusted")
        normalized_adjusted = (returned_adjusted.lower() == "true"
                               if isinstance(returned_adjusted, str) else returned_adjusted)
        if normalized_adjusted is not None and normalized_adjusted is not adjusted:
            raise MarketDataError("Massive daily aggregate adjustment status did not match the request")
        return AggregateBatch(ticker, "1day", adjusted, bars, payload.get("request_id"),
                              datetime.now(timezone.utc), str(payload.get("status", "UNKNOWN")))

    async def resolve_active_futures_contract(self, product_code: str, as_of: date) -> dict:
        """Resolve a dated active contract; a root such as NQ is not an aggregate ticker."""
        product = product_code.strip().upper()
        payload = await self._get_payload("/futures/v1/contracts", {
            "product_code": product,
            "active": "true",
            "date": as_of.isoformat(),
            "type": "single",
            "sort": "ticker.asc",
            "limit": 1000,
        })
        contracts = [item for item in payload.get("results", []) if item.get("active") and item.get("ticker")]
        if not contracts:
            raise MarketDataError(f"No active futures contract returned for {product} on {as_of.isoformat()}")
        return min(contracts, key=lambda item: (item.get("days_to_maturity", 10**9), item["ticker"]))

    async def get_futures_minute_bars(self, contract_ticker: str, start: date, end: date) -> list[Bar]:
        """Fetch normalized futures minute bars without applying an equity session model."""
        contract = contract_ticker.strip().upper()
        payload = await self._get_paginated_payload(f"/futures/v1/aggs/{contract}", {
            "resolution": "1min",
            "window_start.gte": start.isoformat(),
            "window_start.lte": end.isoformat(),
            "limit": 50000,
        })
        results = payload.get("results", [])
        if not results:
            raise MarketDataError(f"No futures market data returned for {contract}")
        return [
            Bar(
                timestamp=datetime.fromtimestamp(item["window_start"] / 1_000_000_000, tz=timezone.utc),
                open=item["open"], high=item["high"], low=item["low"], close=item["close"], volume=item["volume"],
            )
            for item in results
        ]

    async def get_stock_snapshot(self, symbol: str) -> dict:
        """Return a raw underlying snapshot; no technical or setup processing is applied."""
        ticker = symbol.strip().upper()
        payload = await self._get_payload(
            f"/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}", {})
        snapshot = payload.get("ticker")
        if not snapshot:
            raise MarketDataError(f"No stock snapshot returned for {ticker}")
        return snapshot

    @staticmethod
    def _snapshot_timestamp(value: object) -> datetime | None:
        if not isinstance(value, (int, float)) or value <= 0:
            return None
        divisor = 1_000_000_000 if value > 10**16 else 1_000 if value > 10**11 else 1
        return datetime.fromtimestamp(value / divisor, tz=timezone.utc)

    async def get_stock_observation(self, symbol: str) -> StockObservation | None:
        """Normalize the freshest priced observation from Massive's stock snapshot."""
        ticker = symbol.strip().upper()
        snapshot = await self.get_stock_snapshot(ticker)
        trade, quote, minute = (snapshot.get(key) or {} for key in ("lastTrade", "lastQuote", "min"))
        observations: list[StockObservation] = []
        trade_time = self._snapshot_timestamp(trade.get("t"))
        if isinstance(trade.get("p"), (int, float)) and trade_time:
            observations.append(StockObservation(ticker, float(trade["p"]), trade_time, "MASSIVE_LAST_TRADE"))
        quote_time = self._snapshot_timestamp(quote.get("t"))
        bid, ask = quote.get("p"), quote.get("P")
        if isinstance(bid, (int, float)) and isinstance(ask, (int, float)) and bid > 0 and ask >= bid and quote_time:
            observations.append(StockObservation(ticker, (float(bid) + float(ask)) / 2, quote_time, "MASSIVE_QUOTE_MIDPOINT"))
        minute_time = self._snapshot_timestamp(minute.get("t"))
        if isinstance(minute.get("c"), (int, float)) and minute_time:
            observations.append(StockObservation(ticker, float(minute["c"]), minute_time, "MASSIVE_MINUTE_SNAPSHOT"))
        observation = max(observations, key=lambda item: item.provider_timestamp) if observations else None
        logger.info(
            "market_freshness_provider symbol=%s endpoint=stock_snapshot "
            "raw_trade_timestamp=%r raw_quote_timestamp=%r raw_minute_timestamp=%r "
            "normalized_timestamp=%s source=%s",
            ticker, trade.get("t"), quote.get("t"), minute.get("t"),
            observation.provider_timestamp.isoformat() if observation else None,
            observation.source if observation else None,
        )
        return observation

    async def get_active_option_contracts(self, underlying_symbol: str, as_of: date) -> list[dict]:
        """Return listed, unexpired option contract metadata for expiration discovery."""
        symbol = underlying_symbol.strip().upper()
        payload = await self._get_paginated_payload("/v3/reference/options/contracts", {
            "underlying_ticker": symbol,
            "expiration_date.gte": as_of.isoformat(),
            "expired": "false",
            "limit": 1000,
            "sort": "expiration_date",
            "order": "asc",
        })
        return [item for item in payload.get("results", []) if item.get("ticker") and item.get("expiration_date")]

    async def get_options_chain_snapshot(self, underlying_symbol: str, expiration_date: str,
                                         strike_low: float, strike_high: float) -> list[dict]:
        """Return one bounded expiration-chain snapshot with provider-returned options fields."""
        symbol = underlying_symbol.strip().upper()
        payload = await self._get_paginated_payload(f"/v3/snapshot/options/{symbol}", {
            "expiration_date": expiration_date,
            "strike_price.gte": strike_low,
            "strike_price.lte": strike_high,
            "limit": 250,
            "sort": "strike_price",
            "order": "asc",
        })
        return payload.get("results", [])

    async def get_option_daily_bars(self, option_ticker: str, start: date,
                                    end: date) -> AggregateBatch:
        """Return adjusted option bars used only for backward-looking volume baselines."""
        ticker = option_ticker.strip().upper()
        payload = await self._get_paginated_payload(
            f"/v2/aggs/ticker/{ticker}/range/1/day/{start.isoformat()}/{end.isoformat()}",
            {"adjusted": "true", "sort": "asc", "limit": 50000},
        )
        bars = tuple(Bar(
            timestamp=datetime.fromtimestamp(item["t"] / 1000, tz=timezone.utc),
            open=item["o"], high=item["h"], low=item["l"], close=item["c"], volume=item["v"],
        ) for item in payload.get("results", []))
        if any(left.timestamp >= right.timestamp for left, right in zip(bars, bars[1:])):
            raise MarketDataError("Massive option aggregate pages are not strictly chronological")
        return AggregateBatch(ticker, "1day", True, bars, payload.get("request_id"),
                              datetime.now(timezone.utc), str(payload.get("status", "UNKNOWN")))

    async def get_option_trades(self, option_ticker: str, session_date: date,
                                cutoff: datetime,
                                limit: int = 250) -> dict:
        """Return a bounded recent trade sample and disclose whether more pages exist."""
        ticker = option_ticker.strip().upper()
        payload = await self._get_payload(f"/v3/trades/{ticker}", {
            "timestamp.gte": session_date.isoformat(),
            "timestamp.lte": cutoff.isoformat(),
            "sort": "timestamp.desc", "limit": min(max(limit, 1), 5000),
        })
        return {"results": payload.get("results", []), "complete": not bool(payload.get("next_url")),
                "request_id": payload.get("request_id")}

    async def get_option_quotes(self, option_ticker: str, session_date: date,
                                cutoff: datetime,
                                limit: int = 1000) -> dict:
        """Return a bounded recent NBBO sample for point-in-time trade classification."""
        ticker = option_ticker.strip().upper()
        payload = await self._get_payload(f"/v3/quotes/{ticker}", {
            "timestamp.gte": session_date.isoformat(),
            "timestamp.lte": cutoff.isoformat(),
            "sort": "timestamp", "order": "desc", "limit": min(max(limit, 1), 5000),
        })
        return {"results": payload.get("results", []), "complete": not bool(payload.get("next_url")),
                "request_id": payload.get("request_id")}

    async def close(self) -> None:
        await self._client.aclose()
