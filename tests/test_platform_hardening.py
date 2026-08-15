import asyncio
import inspect
from datetime import date, datetime, timezone

from fastapi.testclient import TestClient

from app.api_contracts import AnalysisResponse, MarketDataStatus, analysis_response
from app.config import _origins
from app.main import app
from app.market_data.base import (FuturesDataProvider, MarketDataError,
                                  OptionsDataProvider, StockMarketDataProvider)
from app.models import StandardDecision
from app.service import AnalysisService
from app.signal_identity import signal_fingerprint
from app.versioning import ENGINE_VERSION, PARAMETER_VERSION, SETUP_RULES_VERSION
from tests.test_service_v2 import FakeProvider


def test_canonical_analysis_contract_serializes_supported_fields():
    async def scenario():
        result = await AnalysisService(FakeProvider()).analyze_symbol("NVDA")
        contract = analysis_response(result)
        payload = contract.model_dump(mode="json")
        assert AnalysisResponse.model_validate(payload) == contract
        assert payload["symbol"] == "NVDA"
        assert payload["engine_version"] == ENGINE_VERSION
        assert payload["parameter_version"] == PARAMETER_VERSION
        assert payload["setup_rules_version"] == SETUP_RULES_VERSION
        assert payload["market_status"]["data_status"] in {item.value for item in MarketDataStatus}
        assert "instrument" not in payload and "standardized_decision" not in payload
    asyncio.run(scenario())


def test_standard_decisions_always_carry_logic_versions():
    fields = StandardDecision.model_fields
    assert fields["engine_version"].default == ENGINE_VERSION
    assert fields["setup_rules_version"].default == SETUP_RULES_VERSION
    assert fields["parameter_version"].default == PARAMETER_VERSION


def test_signal_identity_is_stable_across_lifecycle_but_changes_with_thesis():
    inputs = dict(symbol="nvda", setup_family="BREAKOUT_RETEST", direction="LONG",
                  trigger=110.123456, session=date(2026, 8, 14))
    first = signal_fingerprint(**inputs)
    assert first == signal_fingerprint(**inputs)
    assert first != signal_fingerprint(**(inputs | {"direction": "SHORT"}))
    assert first != signal_fingerprint(**(inputs | {"session": date(2026, 8, 17)}))


def test_provider_capabilities_are_frontend_and_massive_agnostic():
    assert "massive" not in inspect.getsource(StockMarketDataProvider).lower()
    assert hasattr(OptionsDataProvider, "get_options_chain_snapshot")
    assert hasattr(FuturesDataProvider, "get_futures_minute_bars")


def test_health_and_readiness_never_expose_secrets():
    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        readiness = client.get("/api/v1/readiness")
    assert health.status_code == 200 and health.json()["app"] == "ok"
    assert readiness.status_code == 200
    serialized = str(readiness.json()).lower()
    assert "api_key" not in serialized and "token" not in serialized and "user_key" not in serialized


def test_provider_failures_have_structured_sanitized_errors():
    class FailedService:
        async def analyze_symbol(self, *_, **__):
            raise MarketDataError("secret-provider-url?apiKey=must-not-leak")

    original = getattr(app.state, "service", None)
    app.state.service = FailedService()
    try:
        response = TestClient(app).get("/api/v1/analysis/NVDA")
    finally:
        app.state.service = original
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "PROVIDER_ERROR"
    assert "must-not-leak" not in response.text


def test_cors_configuration_requires_explicit_origins():
    assert _origins("https://app.example.com, http://localhost:3000/") == (
        "https://app.example.com", "http://localhost:3000")
    try:
        _origins("*")
    except ValueError as exc:
        assert "wildcard" in str(exc)
    else:
        raise AssertionError("Wildcard CORS must be rejected")


def test_research_and_live_import_the_same_production_pipeline():
    import app.backtesting as research
    import app.service as live
    assert live.evaluate_production is research.evaluate_production
