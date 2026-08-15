from datetime import date

import app.research as research
from app.engines.setups import build_standard_decision
from app.models import ActionNow, DecisionQuality, SetupFamily, SetupProgress, StandardDecision
from tests.test_alerts import analysis
from tests.test_v2 import market_bars


def test_historical_replay_setup_engine_cannot_access_future_bars(monkeypatch):
    original = research.build_standard_decision
    audited_calls = []

    def audited(instrument, levels, visible_bars, score, previous, parameters):
        assert all(bar.timestamp <= instrument.as_of for bar in visible_bars)
        audited_calls.append(instrument.as_of)
        return original(instrument, levels, visible_bars, score, previous, parameters)

    monkeypatch.setattr(research, "build_standard_decision", audited)
    periods = research.ResearchPeriods(date(2026,8,12),date(2026,8,12),date(2026,8,13),date(2026,8,13))
    result = research.replay("NVDA", market_bars(), periods)
    assert audited_calls
    assert "development" in result and "validation" in result


def test_scanner_uses_standardized_decision_over_legacy_score():
    result = analysis(score=95)
    result.decision = StandardDecision(symbol="NVDA", timestamp=result.instrument.as_of,
        market_price=result.instrument.timeframes["1m"].price, direction="NONE",
        setup_family=SetupFamily.NONE, setup_state=SetupProgress.NO_SETUP,
        quality=DecisionQuality.NO_TRADE, action_now=ActionNow.NO_TRADE,
        vwap_state="ABOVE", structure_5m=result.instrument.timeframes["5m"].trend,
        structure_15m=result.instrument.timeframes["15m"].trend, reasoning=["ambiguous"],
        missing_confirmation=["recognizable setup"], do_not_trade_if=["ambiguous"],
        what_changes_my_mind=["new structure"], state_changed_at=result.instrument.as_of)
    from app.engines.alerts import ScannerPolicy, candidate_from_analysis
    candidate, reason = candidate_from_analysis(result, ScannerPolicy())
    assert candidate is None and reason == "LOW_QUALITY"
