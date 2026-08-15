from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime

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
