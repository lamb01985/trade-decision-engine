from datetime import datetime, timedelta, timezone

from app.engines.alerts import AlertEngine, ScannerPolicy, candidate_from_analysis
from app.models import (InstrumentAnalysis, MarketAlertType, PriceLevel, LevelImportance,
    ScoreFactor, SetupDecision, SetupScore, SetupState, SymbolAnalysis, TimeframeAnalysis,
    TradePlan, Trend)


def analysis(symbol="NVDA", state=SetupState.WATCH_LONG, score=88, price=109.5,
             adv=100_000_000, trigger=110, invalidation=109, target_1=112, target_2=114):
    trend = Trend.BULLISH if "LONG" in state.value else Trend.BEARISH
    frame = TimeframeAnalysis(timeframe="5m", price=price, vwap=108, ema_9=109, ema_21=108,
        vwap_state="ABOVE" if trend == Trend.BULLISH else "BELOW", ema_alignment=trend.value,
        distance_from_vwap=1.5, distance_from_vwap_pct=1.3, trend=trend, volume=10000, relative_volume=1.5)
    instrument = InstrumentAnalysis(symbol=symbol, available=True, as_of=datetime.now(timezone.utc),
        timeframes={x: frame.model_copy(update={"timeframe": x}) for x in ("1m", "5m", "15m")},
        average_dollar_volume=adv)
    confirmed = state in {SetupState.LONG_CONFIRMED, SetupState.SHORT_CONFIRMED}
    setup = SetupDecision(state=state, setup_type="BREAKOUT_RETEST", trigger_occurred=confirmed,
        explanation=["Aligned structure", "Touch alone is not confirmation"])
    plan = TradePlan(direction="LONG" if "LONG" in state.value else "SHORT", trigger=trigger,
        invalidation=invalidation, target_1=target_1, target_2=target_2, risk_reward=2,
        status="CONFIRMED" if confirmed else "AWAITING_TRIGGER", explanation="Structural plan")
    levels = [PriceLevel(price=x, types=["SWING"], importance=LevelImportance.RESISTANCE,
        distance=x-price, distance_pct=(x-price)/price*100, position="ABOVE" if x > price else "BELOW")
        for x in (invalidation, trigger, target_1, target_2)]
    return SymbolAnalysis(instrument=instrument, levels=levels, setup=setup,
        setup_score=SetupScore(score=score, factors=[ScoreFactor(name="test", points=10, max_points=10, explanation="deterministic")]),
        trade_plan=plan, what_would_change_state=[], thesis="test", current_structure="aligned",
        confirmation="confirmed" if confirmed else "waiting", permission_to_trade=confirmed)


def test_radar_qualification():
    candidate, reason = candidate_from_analysis(analysis(), ScannerPolicy())
    assert reason is None and candidate.quality == "A"
    alerts = AlertEngine().evaluate([analysis()])[1]
    assert alerts[0].alert_type == MarketAlertType.RADAR and alerts[0].action_now == "WAIT"


def test_triggered_setup_requires_confirmed_state():
    alerts = AlertEngine().evaluate([analysis(state=SetupState.LONG_CONFIRMED, price=110.1)])[1]
    assert alerts[0].alert_type == MarketAlertType.TRIGGERED


def test_previously_alerted_setup_emits_invalidation():
    engine = AlertEngine()
    engine.evaluate([analysis()])
    alerts = engine.evaluate([analysis(state=SetupState.NO_TRADE, score=20)])[1]
    assert alerts[0].alert_type == MarketAlertType.INVALIDATED
    assert alerts[0].action_now == "DO NOT ENTER"


def test_unchanged_alert_is_deduplicated_even_after_cooldown_without_new_information():
    engine = AlertEngine(ScannerPolicy(cooldown_minutes=5))
    now = datetime.now(timezone.utc)
    assert engine.evaluate([analysis()], now)[1]
    assert not engine.evaluate([analysis()], now + timedelta(minutes=10))[1]


def test_candidates_rank_confirmed_then_score():
    engine = AlertEngine()
    candidates = engine.evaluate([analysis("A", score=92), analysis("B", state=SetupState.LONG_CONFIRMED, score=85, price=110.1)])[0]
    assert [x.symbol for x in candidates] == ["B", "A"]


def test_liquidity_filter_excludes_low_dollar_volume():
    candidate, reason = candidate_from_analysis(analysis(adv=1_000_000), ScannerPolicy())
    assert candidate is None and reason == "ILLIQUID"


def test_too_late_setup_is_suppressed():
    candidate, reason = candidate_from_analysis(analysis(state=SetupState.LONG_CONFIRMED, price=112), ScannerPolicy())
    assert candidate is None and reason == "TOO_LATE"


def test_previously_watched_setup_emits_distinct_too_late_transition():
    engine = AlertEngine()
    engine.evaluate([analysis()])
    alerts = engine.evaluate([analysis(state=SetupState.LONG_CONFIRMED, price=112)])[1]
    assert alerts[0].alert_type == MarketAlertType.TOO_LATE
    assert alerts[0].action_now == "DO NOT ENTER"


def test_no_qualifying_setups_returns_empty_collection():
    candidates, alerts, _ = AlertEngine().evaluate([analysis(score=40)])
    assert candidates == [] and alerts == []
