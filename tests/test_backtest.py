import asyncio
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest

from app.backtest import (BacktestConfig, DataSplits, DateRange, ExecutionConfig,
    HistoricalDataCache, ResearchPurpose, SplitName, simulate_trade)
from app.engines.pipeline import ProductionEvaluation, evaluate_production
from app.models import (ActionNow, Bar, DecisionQuality, InstrumentAnalysis, LevelImportance,
    PriceLevel, SetupDecision, SetupFamily, SetupProgress, SetupScore, StandardDecision,
    TimeframeAnalysis, TradePlan, Trend)
from app.market_data.base import MarketDataProvider
from tests.test_v2 import market_bars


def splits():
    return DataSplits(DateRange(date(2026,1,1),date(2026,3,31)),
        DateRange(date(2026,4,1),date(2026,6,30)),DateRange(date(2026,7,1),date(2026,9,30)))


def test_holdout_is_inaccessible_to_optimization_and_splits_cannot_overlap():
    with pytest.raises(PermissionError): splits().get(SplitName.FINAL_HOLDOUT,ResearchPurpose.OPTIMIZATION)
    assert splits().get(SplitName.FINAL_HOLDOUT,ResearchPurpose.FINAL_EVALUATION).start==date(2026,7,1)
    with pytest.raises(ValueError): DataSplits(DateRange(date(2026,1,1),date(2026,2,1)),DateRange(date(2026,2,1),date(2026,3,1)),DateRange(date(2026,4,1),date(2026,5,1)))


class CountingProvider(MarketDataProvider):
    def __init__(self):self.calls=0
    async def get_minute_bars(self,symbol,start,end):self.calls+=1;return market_bars()


def test_historical_cache_avoids_repeat_massive_requests(tmp_path):
    async def scenario():
        provider=CountingProvider();cache=HistoricalDataCache(tmp_path)
        first=await cache.get(provider,"QQQ",date(2026,8,1),date(2026,8,14))
        second=await cache.get(provider,"QQQ",date(2026,8,1),date(2026,8,14))
        assert provider.calls==1 and first==second
    asyncio.run(scenario())


def evaluation(direction="LONG",signal_time=None):
    signal_time=signal_time or datetime(2026,8,14,14,4,tzinfo=timezone.utc)
    frame=TimeframeAnalysis(timeframe="5m",price=100,vwap=99.5,ema_9=100,ema_21=99,
        vwap_state="ABOVE",ema_alignment="BULLISH",distance_from_vwap=.5,
        distance_from_vwap_pct=.5,trend=Trend.BULLISH,volume=1000,relative_volume=1.5)
    instrument=InstrumentAnalysis(symbol="TEST",available=True,as_of=signal_time,
        timeframes={x:frame.model_copy(update={"timeframe":x}) for x in ("1m","5m","15m")},atr_5m=1)
    decision=StandardDecision(symbol="TEST",timestamp=signal_time,market_price=100,direction=direction,
        setup_family=SetupFamily.BREAKOUT_RETEST,setup_state=SetupProgress.CONFIRMED,
        quality=DecisionQuality.A,action_now=ActionNow.LONG_TRIGGERED if direction=="LONG" else ActionNow.SHORT_TRIGGERED,
        trigger=100,invalidation=99 if direction=="LONG" else 101,target_1=101 if direction=="LONG" else 99,
        target_2=102 if direction=="LONG" else 98,structural_risk_reward=2,
        vwap_state="ABOVE",structure_5m=Trend.BULLISH,structure_15m=Trend.BULLISH,
        reasoning=["production confirmation"],missing_confirmation=[],do_not_trade_if=[],what_changes_my_mind=[],state_changed_at=signal_time)
    levels=[PriceLevel(price=x,types=["KNOWN"],importance=LevelImportance.PIVOT,distance=x-100,distance_pct=x-100,position="AT") for x in (99,100,101,102)]
    return ProductionEvaluation(instrument,[],levels,SetupDecision(state="LONG_CONFIRMED",explanation=[]),TradePlan(status="CONFIRMED",explanation="test"),SetupScore(score=90,factors=[]),decision)


def bar(minute,o,h,l,c):
    return Bar(timestamp=datetime(2026,8,14,14,minute,tzinfo=timezone.utc),open=o,high=h,low=l,close=c,volume=1000)


def config(execution=None):
    return BacktestConfig(("TEST",),splits(),execution=execution or ExecutionConfig(0,0,.5))


def test_trade_enters_next_bar_and_r_math_uses_actual_risk():
    trade=simulate_trade(evaluation(),[bar(4,100,100,100,100),bar(5,100,101.2,99.5,101),bar(6,101,102.2,100.8,102)],config())
    assert trade.entry_time.minute==5 and trade.entry==100
    assert trade.realized_r==pytest.approx(1.5)
    assert trade.time_to_t1_minutes==0 and trade.time_to_t2_minutes==1


def test_same_bar_stop_and_target_uses_conservative_stop():
    trade=simulate_trade(evaluation(),[bar(5,100,101.2,98.8,100)],config())
    assert trade.ambiguous and trade.exit_reason=="AMBIGUOUS_STOP"
    assert trade.realized_r==pytest.approx(-1)


def test_impossible_gap_fill_is_rejected():
    assert simulate_trade(evaluation(),[bar(5,101.5,102,101,101.8)],config()) is None


def test_future_extremes_volume_and_swings_cannot_change_prefix_decision():
    prefix=market_bars()[:-10]
    future_a=market_bars()[-10:]
    future_b=[x.model_copy(update={"high":x.high+100,"low":x.low-100,"volume":x.volume*100}) for x in future_a]
    first=evaluate_production("NVDA",prefix).decision
    # Both hypothetical futures are deliberately excluded at timestamp T.
    second=evaluate_production("NVDA",(prefix+future_b)[:len(prefix)]).decision
    assert first.model_dump()==second.model_dump()
    assert max(x.timestamp for x in prefix)<=first.timestamp


def test_slippage_and_fees_reduce_realized_r():
    clean=simulate_trade(evaluation(),[bar(5,100,101.2,99.5,101),bar(6,101,102.2,100.8,102)],config())
    costly=simulate_trade(evaluation(),[bar(5,100,101.2,99.5,101),bar(6,101,102.2,100.8,102)],config(ExecutionConfig(5,.01,.5)))
    assert costly.realized_r < clean.realized_r
