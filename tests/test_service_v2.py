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

    import app.freshness as freshness_module
    import app.service as service_module
    from app.freshness import MarketSession
    from app.universe import UniverseSelection
    from tests.test_alerts import analysis

    async def scenario():
        monkeypatch.setattr(freshness_module, "market_session", lambda now=None: MarketSession.REGULAR)
        monkeypatch.setattr(service_module, "market_session", lambda now=None: MarketSession.REGULAR)
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


def test_market_scan_partial_symbol_failure_does_not_invalidate_scan(monkeypatch):
    # BUG 1 regression: one symbol erroring out of a batch (timeout, rate limit,
    # transient provider error) must not poison results from symbols that
    # succeeded with live data. Only exclude and log the failed symbol.
    import app.freshness as freshness_module
    import app.service as service_module
    from app.freshness import MarketSession
    from app.universe import UniverseSelection
    from tests.test_alerts import analysis

    async def scenario():
        # Freshness classification depends on the real-world market session
        # (regular hours vs. weekend/overnight close). Pin it to REGULAR so
        # this test's outcome doesn't depend on when it happens to run.
        monkeypatch.setattr(freshness_module, "market_session", lambda now=None: MarketSession.REGULAR)
        monkeypatch.setattr(service_module, "market_session", lambda now=None: MarketSession.REGULAR)

        service = AnalysisService(FakeProvider())
        nvda = analysis("NVDA")
        amd = analysis("AMD")

        async def fake_analyze(symbol, include_cross_market=False, force=False):
            if symbol == "TSLA":
                raise TimeoutError("provider timeout")
            return {"NVDA": nvda, "AMD": amd}[symbol]

        monkeypatch.setattr(service, "analyze_symbol", fake_analyze)
        result = await service.market_scan(UniverseSelection(watchlist=("NVDA", "AMD", "TSLA")))

        assert result.failed_symbols == ["TSLA"]
        assert result.analyzed == 2
        assert result.data_status == "LIVE"
        assert result.data_state == "LIVE_CURRENT"
        assert result.message is None or "UNAVAILABLE" not in result.message

    asyncio.run(scenario())


def test_market_scan_all_symbols_failing_reports_full_error(monkeypatch):
    # Counterpart to the above: when every symbol in the batch fails, there is
    # no live data to fall back on, so the scan should report a hard error
    # rather than a silently empty "no setups" result.
    from app.universe import UniverseSelection

    async def scenario():
        service = AnalysisService(FakeProvider())

        async def fake_analyze(symbol, include_cross_market=False, force=False):
            raise TimeoutError("provider timeout")

        monkeypatch.setattr(service, "analyze_symbol", fake_analyze)
        result = await service.market_scan(UniverseSelection(watchlist=("NVDA", "AMD")))

        assert set(result.failed_symbols) == {"NVDA", "AMD"}
        assert result.analyzed == 0
        assert result.candidates == []
        assert result.data_status == "ERROR"
        assert result.data_state == "DATA_ERROR"
        assert result.message is not None and "UNAVAILABLE" in result.message

    asyncio.run(scenario())
