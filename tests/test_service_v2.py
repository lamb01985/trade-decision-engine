import asyncio

from app.market_data.base import MarketDataProvider
from app.service import AnalysisService
from tests.test_v2 import market_bars


class FakeProvider(MarketDataProvider):
    def __init__(self): self.calls = 0
    async def get_minute_bars(self, symbol, start, end):
        self.calls += 1
        return market_bars(100 + len(symbol))


def test_watchlist_scanner_ranks_and_caches_generic_symbols():
    async def scenario():
        provider = FakeProvider()
        service = AnalysisService(provider)
        rows = await service.scan(["nvda", "AMD", "NVDA"])
        assert {row.symbol for row in rows} == {"NVDA", "AMD"}
        assert [x.setup_score for x in rows] == sorted([x.setup_score for x in rows], reverse=True)
        await service.analyze_symbol("NVDA")
        assert provider.calls == 2
    asyncio.run(scenario())


def test_permission_to_trade_follows_standardized_decision_action():
    async def scenario():
        provider = FakeProvider()
        result = await AnalysisService(provider).analyze_symbol("NVDA")

        assert result.decision is not None
        assert result.permission_to_trade == (
            result.decision.action_now.value in {"LONG_TRIGGERED", "SHORT_TRIGGERED"}
        )

    asyncio.run(scenario())



def test_market_scan_reports_freshness_for_evaluated_symbols(monkeypatch):
    from datetime import datetime, timedelta, timezone

    from app.universe import UniverseSelection
    from tests.test_alerts import analysis

    async def scenario():
        service = AnalysisService(FakeProvider())
        fresh = analysis("NVDA")
        stale = analysis("AMD")
        stale.instrument.as_of = datetime.now(timezone.utc) - timedelta(minutes=10)

        async def fake_analyze(symbol, include_cross_market=False, force=False):
            return fresh if symbol == "NVDA" else stale

        monkeypatch.setattr(service, "analyze_symbol", fake_analyze)
        result = await service.market_scan(UniverseSelection(watchlist=("NVDA", "AMD")))

        assert result.data_as_of == (fresh.provider_timestamp or fresh.instrument.as_of)
        assert result.data_age_seconds is not None and result.data_age_seconds <= 90
        assert result.data_status == "LIVE"

    asyncio.run(scenario())
