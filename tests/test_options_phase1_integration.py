from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.models import ActionNow, DecisionQuality, SetupFamily, SetupProgress
from tests.test_options_plan import FakePlanProvider, analysis, decision, liquid_call


def test_options_plan_api_consumes_service_decision_and_returns_downstream_contract():
    class FrozenService:
        async def analyze_symbol(self, symbol, include_cross_market=False, force=False):
            assert (symbol, include_cross_market, force) == ("QQQ", False, True)
            return analysis(decision(ActionNow.LONG_TRIGGERED, SetupProgress.CONFIRMED, DecisionQuality.B))

    provider = FakePlanProvider([liquid_call()])
    with TestClient(app) as client:
        app.state.service = FrozenService()
        app.state.market_data_provider = provider
        response = client.get("/api/options-plan/QQQ")

    assert response.status_code == 200
    payload = response.json()
    assert payload["headline"] == "GOOD TRADE — CONFIRMED"
    assert payload["underlying_eligibility"]["setup_family"] == SetupFamily.BREAKOUT_RETEST.value
    assert payload["underlying_eligibility"]["setup_quality"] == "B"
    assert payload["underlying_eligibility"]["direction"] == "LONG"
    assert payload["best_contract"]["contract"]["ticker"] == "O:QQQ260821C00500000"
    assert payload["underlying_engine_modified"] is False
    assert payload["execution_enabled"] is False


def test_options_page_exposes_plan_first_and_keeps_raw_chain_collapsed():
    html = Path("app/static/index.html").read_text()
    assert html.index('id="options-plan-form"') < html.index('id="options-form"')
    assert "Build options plan" in html
    assert "BOTTOM LINE" in Path("app/static/app.js").read_text()
    advanced = "Advanced details / raw option-chain evidence"
    assert advanced in html
    assert html.index(advanced) < html.index('id="options-chain-body"')
