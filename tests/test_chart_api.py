from datetime import date, datetime, timezone

from fastapi.testclient import TestClient

from app.main import app
from app.market_data.base import MarketDataError
from app.models import Bar
from app.service import AnalysisService


class ChartService:
    def __init__(self, unavailable: bool = False):
        self.unavailable = unavailable
        self.requested = None

    normalize_symbol = staticmethod(AnalysisService.normalize_symbol)

    async def chart_bars(self, symbol, start, end):
        self.requested = (symbol, start, end)
        if self.unavailable:
            raise MarketDataError(f"No market data returned for {symbol}")
        return [Bar(timestamp=datetime(2026, 8, 14, 13, minute, tzinfo=timezone.utc),
                    open=100, high=101, low=99, close=100.5, volume=10)
                for minute in range(30, 35)]

    def cached_analysis(self, symbol):
        return None


def request_with(service, path):
    original_service = getattr(app.state, "service", None)
    original_provider = getattr(app.state, "market_data_provider", None)
    app.state.service = service
    app.state.market_data_provider = object()
    try:
        return TestClient(app).get(path)
    finally:
        app.state.service = original_service
        app.state.market_data_provider = original_provider


def test_chart_endpoint_returns_typed_snapshot_for_bounded_range():
    service = ChartService()
    response = request_with(service,
        "/api/v1/chart/qqq?timeframe=5m&start=2026-08-14&end=2026-08-14")
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "QQQ" and body["timeframe"] == "5m"
    assert len(body["candles"]) == 1
    assert service.requested == ("QQQ", date(2026, 8, 14), date(2026, 8, 14))


def test_chart_endpoint_rejects_malformed_timeframe():
    response = request_with(ChartService(), "/api/v1/chart/QQQ?timeframe=2m")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_chart_endpoint_returns_explicit_unavailable_data_response():
    response = request_with(ChartService(unavailable=True),
        "/api/v1/chart/NONE?start=2026-08-14&end=2026-08-14")
    assert response.status_code == 404
    assert "unavailable" in response.json()["detail"].lower()
