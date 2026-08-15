import logging
from datetime import date, datetime, timezone

import httpx

from app.market_data.base import MarketDataError, MarketDataProvider, StockObservation
from app.models import Bar

logger = logging.getLogger(__name__)


class MassiveMarketDataProvider(MarketDataProvider):
    """Massive equity and futures REST data adapter; no strategy logic lives here."""

    def __init__(self, api_key: str, base_url: str = "https://api.massive.com") -> None:
        self._api_key = api_key
        self._client = httpx.AsyncClient(base_url=base_url, timeout=20.0)

    async def _get_payload(self, path: str, params: dict[str, str | int | bool]) -> dict:
        request_params = {**params, "apiKey": self._api_key}
        try:
            response = await self._client.get(path, params=request_params)
            logger.debug("Massive response path=%s status_code=%s", path, response.status_code)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # httpx exception text includes the full request URL. Since the
            # provider credential is a query parameter, never log the
            # exception object or traceback here.
            logger.warning("Massive request failed for %s (%s)", path, type(exc).__name__)
            raise MarketDataError("Massive market-data request failed") from exc
        if payload.get("status") in {"ERROR", "NOT_AUTHORIZED"}:
            raise MarketDataError(payload.get("error") or payload.get("message") or "Massive API error")
        return payload

    async def get_minute_bars(self, symbol: str, start: date, end: date) -> list[Bar]:
        path = f"/v2/aggs/ticker/{symbol}/range/1/minute/{start.isoformat()}/{end.isoformat()}"
        payload = await self._get_payload(path, {"adjusted": "true", "sort": "asc", "limit": 50000})
        results = payload.get("results", [])
        if not results:
            raise MarketDataError(f"No market data returned for {symbol}")
        return [
            Bar(
                timestamp=datetime.fromtimestamp(item["t"] / 1000, tz=timezone.utc),
                open=item["o"], high=item["h"], low=item["l"], close=item["c"], volume=item["v"],
            )
            for item in results
        ]

    async def resolve_active_futures_contract(self, product_code: str, as_of: date) -> dict:
        """Resolve a dated active contract; a root such as NQ is not an aggregate ticker."""
        product = product_code.strip().upper()
        payload = await self._get_payload("/futures/v1/contracts", {
            "product_code": product,
            "active": "true",
            "date": as_of.isoformat(),
            "type": "single",
            "sort": "days_to_maturity.asc",
            "limit": 1000,
        })
        contracts = [item for item in payload.get("results", []) if item.get("active") and item.get("ticker")]
        if not contracts:
            raise MarketDataError(f"No active futures contract returned for {product} on {as_of.isoformat()}")
        return min(contracts, key=lambda item: (item.get("days_to_maturity", 10**9), item["ticker"]))

    async def get_futures_minute_bars(self, contract_ticker: str, start: date, end: date) -> list[Bar]:
        """Fetch normalized futures minute bars without applying an equity session model."""
        contract = contract_ticker.strip().upper()
        payload = await self._get_payload(f"/futures/v1/aggs/{contract}", {
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
        return max(observations, key=lambda item: item.provider_timestamp) if observations else None

    async def get_active_option_contracts(self, underlying_symbol: str, as_of: date) -> list[dict]:
        """Return listed, unexpired option contract metadata for expiration discovery."""
        symbol = underlying_symbol.strip().upper()
        payload = await self._get_payload("/v3/reference/options/contracts", {
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
        payload = await self._get_payload(f"/v3/snapshot/options/{symbol}", {
            "expiration_date": expiration_date,
            "strike_price.gte": strike_low,
            "strike_price.lte": strike_high,
            "limit": 250,
            "sort": "strike_price",
            "order": "asc",
        })
        return payload.get("results", [])

    async def close(self) -> None:
        await self._client.aclose()
