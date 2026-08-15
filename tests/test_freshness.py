import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import httpx

from app.engines.alerts import ScannerPolicy, candidate_from_analysis
from app.freshness import (DataStatus, MarketDataState, MarketSession, classify_data,
                           data_age_seconds, market_data_state, market_session)
from app.market_data.massive import MassiveMarketDataProvider
from app.market_data.base import MarketDataError
from app.models import ActionNow, DecisionQuality, SetupProgress, SetupState
from app.options_intelligence import assess_underlying_eligibility
from app.service import AnalysisService
from app.universe import UniverseSelection
from tests.test_alerts import analysis
from tests.test_options_plan import analysis as options_analysis, decision


def test_provider_timestamp_parsing_supports_seconds_milliseconds_and_nanoseconds():
    expected = datetime(2026, 8, 14, 20, 0, tzinfo=timezone.utc)
    seconds = expected.timestamp()
    assert MassiveMarketDataProvider._snapshot_timestamp(seconds) == expected
    assert MassiveMarketDataProvider._snapshot_timestamp(seconds * 1_000) == expected
    assert MassiveMarketDataProvider._snapshot_timestamp(seconds * 1_000_000_000) == expected
    assert MassiveMarketDataProvider._snapshot_timestamp(None) is None


def test_snapshot_adapter_selects_freshest_priced_observation(monkeypatch):
    async def scenario():
        provider = MassiveMarketDataProvider("test")
        base = datetime(2026, 8, 17, 15, 0, tzinfo=timezone.utc).timestamp() * 1_000_000_000

        async def snapshot(_):
            return {
                "lastTrade": {"p": 500.0, "t": base},
                "lastQuote": {"p": 500.1, "P": 500.3, "t": base + 2_000_000_000},
                "min": {"c": 499.9, "t": base + 1_000_000_000},
            }

        monkeypatch.setattr(provider, "get_stock_snapshot", snapshot)
        observation = await provider.get_stock_observation("qqq")
        await provider.close()
        assert observation.price == pytest.approx(500.2)
        assert observation.source == "MASSIVE_QUOTE_MIDPOINT"
        assert observation.provider_timestamp == datetime(2026, 8, 17, 15, 0, 2, tzinfo=timezone.utc)

    asyncio.run(scenario())


@pytest.mark.parametrize(("status", "session", "expected"), [
    (DataStatus.LIVE, MarketSession.REGULAR, MarketDataState.LIVE_CURRENT),
    (DataStatus.DELAYED, MarketSession.REGULAR, MarketDataState.LIVE_STALE),
    (DataStatus.STALE, MarketSession.REGULAR, MarketDataState.LIVE_STALE),
    (DataStatus.MARKET_CLOSED, MarketSession.CLOSED, MarketDataState.MARKET_CLOSED_LAST_SESSION),
    (DataStatus.ERROR, MarketSession.REGULAR, MarketDataState.DATA_ERROR),
    (DataStatus.UNAVAILABLE, MarketSession.CLOSED, MarketDataState.DATA_ERROR),
])
def test_product_market_data_states_are_explicit(status, session, expected):
    assert market_data_state(status, session) == expected


def test_provider_failure_logs_never_expose_query_string_api_key(caplog):
    async def scenario():
        provider = MassiveMarketDataProvider("secret-test-key")

        async def failed_get(path, params):
            request = httpx.Request("GET", f"https://api.massive.com{path}", params=params)
            response = httpx.Response(500, request=request)
            raise httpx.HTTPStatusError("failed", request=request, response=response)

        provider._client.get = failed_get
        with pytest.raises(MarketDataError):
            await provider._get_payload("/test", {})
        await provider.close()

    asyncio.run(scenario())
    assert "secret-test-key" not in caplog.text


def test_age_and_live_delayed_stale_classification_keep_existing_thresholds():
    now = datetime(2026, 8, 17, 15, 0, tzinfo=timezone.utc)  # Monday 11:00 ET.
    assert data_age_seconds(now - timedelta(seconds=17), now) == 17
    assert classify_data(now - timedelta(seconds=90), now)[0] == DataStatus.LIVE
    assert classify_data(now - timedelta(seconds=91), now)[0] == DataStatus.DELAYED
    assert classify_data(now - timedelta(seconds=301), now)[0] == DataStatus.STALE


def test_weekend_is_market_closed_even_when_latest_provider_timestamp_is_unchanged():
    saturday = datetime(2026, 8, 15, 15, 0, tzinfo=timezone.utc)
    friday_last = datetime(2026, 8, 14, 23, 59, 57, tzinfo=timezone.utc)
    status, session, age = classify_data(friday_last, saturday)
    assert market_session(saturday) == MarketSession.CLOSED
    assert status == DataStatus.MARKET_CLOSED
    assert session == MarketSession.CLOSED
    assert age and age > 0


def test_stale_or_closed_ready_setup_is_suppressed_without_mutating_setup():
    result = analysis(state=SetupState.LONG_CONFIRMED, price=110.1)
    stale = result.model_copy(update={"data_status": "STALE", "market_session": "REGULAR"})
    closed = result.model_copy(update={"data_status": "MARKET_CLOSED", "market_session": "CLOSED"})
    assert candidate_from_analysis(stale, ScannerPolicy()) == (None, "DATA_STALE")
    assert candidate_from_analysis(closed, ScannerPolicy()) == (None, "DATA_MARKET_CLOSED")
    assert stale.setup.state == SetupState.LONG_CONFIRMED
    assert closed.setup.state == SetupState.LONG_CONFIRMED


def test_closed_market_candidate_is_available_only_for_explicit_preparation():
    result = analysis(state=SetupState.LONG_CONFIRMED, price=110.1).model_copy(update={
        "data_status": "MARKET_CLOSED", "market_session": "CLOSED",
    })
    assert candidate_from_analysis(result, ScannerPolicy())[0] is None
    candidate, rejection = candidate_from_analysis(result, ScannerPolicy(), allow_closed=True)
    assert rejection is None
    assert candidate is not None
    assert candidate.status == SetupState.LONG_CONFIRMED
    assert result.permission_to_trade is True  # Fixture remains untouched; presentation/action gate is separate.


def test_stale_underlying_vetoes_options_before_contract_evaluation():
    result = options_analysis(decision(ActionNow.LONG_TRIGGERED, SetupProgress.CONFIRMED, DecisionQuality.A))
    result.data_status, result.market_session = "STALE", "REGULAR"
    result.provider_timestamp = datetime(2026, 8, 17, 14, 0, tzinfo=timezone.utc)
    result.analysis_timestamp, result.data_age_seconds = datetime(2026, 8, 17, 15, 0, tzinfo=timezone.utc), 3600
    eligibility = assess_underlying_eligibility(result)
    assert eligibility.eligible_for_contract_evaluation is False
    assert eligibility.state.value == "INSUFFICIENT_DATA"
    assert "STALE" in eligibility.reasons[0]


def test_market_scan_forces_provider_revalidation_and_partial_failure_is_error(monkeypatch):
    async def scenario():
        service = AnalysisService(object())
        calls = []
        sample = analysis("QQQ").model_copy(update={
            "provider_timestamp": datetime(2026, 8, 17, 15, 0, tzinfo=timezone.utc),
            "data_status": "LIVE", "market_session": "REGULAR",
        })

        async def fake_analyze(symbol, include_cross_market=False, force=False):
            calls.append((symbol, force))
            if symbol == "FAIL":
                raise TimeoutError("provider timeout")
            return sample.model_copy(update={"instrument": sample.instrument.model_copy(update={"symbol": symbol})})

        monkeypatch.setattr(service, "analyze_symbol", fake_analyze)
        monkeypatch.setattr("app.service.classify_data", lambda _: (DataStatus.LIVE, MarketSession.REGULAR, 1))
        result = await service.market_scan(UniverseSelection(watchlist=("QQQ", "FAIL")))
        assert sorted(calls) == [("FAIL", True), ("QQQ", True)]
        assert result.data_status == "ERROR"
        assert result.failed_symbols == ["FAIL"]
        assert result.candidates == [] and result.alerts == []

    asyncio.run(scenario())


def test_closed_market_scan_returns_ranked_preparation_candidates_without_alerts(monkeypatch):
    async def scenario():
        service = AnalysisService(object())
        sample = analysis("QQQ", state=SetupState.LONG_CONFIRMED, price=110.1).model_copy(update={
            "provider_timestamp": datetime(2026, 8, 14, 20, 0, tzinfo=timezone.utc),
            "data_status": "MARKET_CLOSED", "market_session": "CLOSED",
            "permission_to_trade": False,
        })

        async def fake_analyze(symbol, include_cross_market=False, force=False):
            assert force is True
            return sample

        monkeypatch.setattr(service, "analyze_symbol", fake_analyze)
        monkeypatch.setattr("app.service.classify_data", lambda _: (DataStatus.MARKET_CLOSED, MarketSession.CLOSED, 60_000))
        result = await service.market_scan(UniverseSelection(watchlist=("QQQ",)))
        assert result.data_status == "MARKET_CLOSED"
        assert [item.symbol for item in result.candidates] == ["QQQ"]
        assert result.alerts == []
        assert sample.permission_to_trade is False

    asyncio.run(scenario())
