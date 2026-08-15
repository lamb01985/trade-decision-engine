from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from app.models import Bar


@dataclass(frozen=True)
class StockObservation:
    symbol: str
    price: float
    provider_timestamp: datetime
    source: str


class MarketDataError(RuntimeError):
    """A provider returned an unusable response."""


class MarketDataProvider(ABC):
    supports_live_streaming = False

    @abstractmethod
    async def get_minute_bars(self, symbol: str, start: date, end: date) -> list[Bar]:
        """Return one-minute bars in chronological order."""

    async def get_stock_observation(self, symbol: str) -> StockObservation | None:
        """Return the freshest normalized stock price observation when supported."""
        return None

    async def close(self) -> None:
        return None


class StockMarketDataProvider(Protocol):
    async def get_minute_bars(self, symbol: str, start: date, end: date) -> list[Bar]: ...
    async def get_stock_observation(self, symbol: str) -> StockObservation | None: ...


class OptionsDataProvider(Protocol):
    async def get_stock_snapshot(self, symbol: str) -> dict: ...
    async def get_active_option_contracts(self, underlying_symbol: str, as_of: date) -> list[dict]: ...
    async def get_options_chain_snapshot(self, underlying_symbol: str, expiration_date: str,
                                         strike_low: float, strike_high: float) -> list[dict]: ...


class FuturesDataProvider(Protocol):
    async def resolve_active_futures_contract(self, product_code: str, as_of: date) -> dict: ...
    async def get_futures_minute_bars(self, contract_ticker: str, start: date, end: date) -> list[Bar]: ...
