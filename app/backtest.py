"""Event-driven backtesting around the exact production decision pipeline."""
import argparse
import asyncio
import gzip
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from pathlib import Path
from statistics import mean, median

from app.config import settings
from app.engines.market import ET, regular_session_bars
from app.engines.pipeline import ProductionEvaluation, evaluate_production
from app.market_data.base import MarketDataProvider
from app.market_data.massive import MassiveMarketDataProvider
from app.models import ActionNow, Bar, DecisionQuality, SetupProgress
from app.parameters import DEFAULT_PARAMETERS, EngineParameters


class SplitName(str, Enum):
    DEVELOPMENT = "DEVELOPMENT"
    VALIDATION = "VALIDATION"
    FINAL_HOLDOUT = "FINAL_HOLDOUT"


class ResearchPurpose(str, Enum):
    BASELINE = "BASELINE"
    OPTIMIZATION = "OPTIMIZATION"
    FINAL_EVALUATION = "FINAL_EVALUATION"


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date

    def contains(self, value: date) -> bool: return self.start <= value <= self.end


@dataclass(frozen=True)
class DataSplits:
    development: DateRange
    validation: DateRange
    final_holdout: DateRange

    def __post_init__(self):
        ranges=sorted((self.development,self.validation,self.final_holdout),key=lambda x:x.start)
        if any(a.end>=b.start for a,b in zip(ranges,ranges[1:])):
            raise ValueError("DEVELOPMENT, VALIDATION, and FINAL_HOLDOUT must not overlap")

    def get(self, split: SplitName, purpose: ResearchPurpose) -> DateRange:
        if split == SplitName.FINAL_HOLDOUT and purpose != ResearchPurpose.FINAL_EVALUATION:
            raise PermissionError("FINAL_HOLDOUT is sealed; only explicit FINAL_EVALUATION may access it")
        return {SplitName.DEVELOPMENT:self.development, SplitName.VALIDATION:self.validation,
                SplitName.FINAL_HOLDOUT:self.final_holdout}[split]


@dataclass(frozen=True)
class ExecutionConfig:
    slippage_bps: float = 1.0
    commission_per_share: float = 0.005
    target_1_fraction: float = 0.50


@dataclass(frozen=True)
class BacktestConfig:
    symbols: tuple[str, ...]
    splits: DataSplits
    bar_timeframe_minutes: int = 1
    session_start: time = time(9,30)
    session_end: time = time(16,0)
    preload_calendar_days: int = 14
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    parameters: EngineParameters = DEFAULT_PARAMETERS


@dataclass
class SetupEvent:
    symbol: str; timestamp: datetime; price: float; setup_family: str; direction: str
    state: str; action_now: str; quality: str; trigger: float | None; invalidation: float | None
    target_1: float | None; target_2: float | None; reasoning: list[str]; missing: list[str]
    visible_bar_count: int; maximum_visible_timestamp: datetime


@dataclass
class TradeResult:
    symbol: str; setup_family: str; direction: str; signal_time: datetime; entry_time: datetime
    exit_time: datetime; entry: float; trigger: float; invalidation: float; target_1: float
    target_2: float; initial_risk: float; realized_r: float; mfe_r: float; mae_r: float
    time_to_t1_minutes: float | None; time_to_t2_minutes: float | None
    time_to_invalidation_minutes: float | None; exit_reason: str; ambiguous: bool
    quality: str; structure_5m: str; structure_15m: str; vwap_state: str
    relative_volume: float | None; structural_risk_reward: float
    configuration: dict; levels_at_entry: list[dict]; event_history: list[dict]


class HistoricalDataCache:
    def __init__(self, directory: Path = Path("data/historical_cache")) -> None:
        self.directory=directory; self.directory.mkdir(parents=True,exist_ok=True)

    def path(self,symbol,start,end,timeframe):
        return self.directory/f"{symbol}_{start}_{end}_{timeframe}m.json.gz"

    async def get(self,provider:MarketDataProvider,symbol:str,start:date,end:date,timeframe:int=1)->list[Bar]:
        path=self.path(symbol,start,end,timeframe)
        if path.exists():
            with gzip.open(path,"rt") as source: payload=json.load(source)
            return [Bar.model_validate(x) for x in payload]
        if timeframe != 1: raise ValueError("Existing Massive provider currently supplies one-minute bars")
        bars=await provider.get_minute_bars(symbol,start,end)
        with gzip.open(path,"wt") as target:
            json.dump([x.model_dump(mode="json") for x in bars],target,separators=(",",":"))
        return bars


def _in_session(bar:Bar,config:BacktestConfig)->bool:
    local=bar.timestamp.astimezone(ET).time()
    return config.session_start <= local < config.session_end


def _transition(previous,current)->bool:
    if previous is None:return current.setup_state != SetupProgress.NO_SETUP
    return any((previous.setup_family!=current.setup_family, previous.setup_state!=current.setup_state,
                previous.action_now!=current.action_now, previous.trigger!=current.trigger))


def _slipped(price:float,direction:str,is_entry:bool,bps:float)->float:
    adverse_sign = 1 if (direction=="LONG") == is_entry else -1
    return price*(1+adverse_sign*bps/10_000)


def simulate_trade(evaluation:ProductionEvaluation, all_session_bars:list[Bar], config:BacktestConfig)->TradeResult|None:
    d=evaluation.decision
    if not (d.setup_state==SetupProgress.CONFIRMED and d.quality in {DecisionQuality.A,DecisionQuality.B}
            and d.action_now in {ActionNow.LONG_TRIGGERED,ActionNow.SHORT_TRIGGERED}): return None
    if any(x is None for x in (d.trigger,d.invalidation,d.target_1,d.target_2,d.structural_risk_reward)):return None
    future=[b for b in all_session_bars if b.timestamp>d.timestamp]
    if not future:return None
    first=future[0]; entry=_slipped(first.open,d.direction,True,config.execution.slippage_bps)
    if d.direction=="LONG" and (entry<=d.invalidation or entry>=d.target_1):return None
    if d.direction=="SHORT" and (entry>=d.invalidation or entry<=d.target_1):return None
    risk=abs(entry-d.invalidation)
    if risk<=0:return None
    remaining=1.0; realized=0.0; fees=config.execution.commission_per_share
    t1_time=t2_time=stop_time=None; ambiguous=False; history=[{"timestamp":d.timestamp.isoformat(),"event":"CONFIRMED","price":d.market_price,"trigger":d.trigger},{"timestamp":first.timestamp.isoformat(),"event":"ENTRY","price":entry}]
    max_favorable=max_adverse=0.0; exit_time=first.timestamp; exit_reason="END_OF_SESSION"
    for bar in future:
        favorable=(bar.high-entry) if d.direction=="LONG" else (entry-bar.low)
        adverse=(entry-bar.low) if d.direction=="LONG" else (bar.high-entry)
        max_favorable=max(max_favorable,favorable); max_adverse=max(max_adverse,adverse)
        stop_hit=bar.low<=d.invalidation if d.direction=="LONG" else bar.high>=d.invalidation
        t1_hit=bar.high>=d.target_1 if d.direction=="LONG" else bar.low<=d.target_1
        t2_hit=bar.high>=d.target_2 if d.direction=="LONG" else bar.low<=d.target_2
        if stop_hit and (t1_hit or t2_hit):
            ambiguous=True; history.append({"timestamp":bar.timestamp.isoformat(),"event":"AMBIGUOUS_STOP_AND_TARGET","high":bar.high,"low":bar.low})
        if stop_hit:  # conservative ordering whenever OHLC cannot resolve sequence
            exit_price=_slipped(d.invalidation,d.direction,False,config.execution.slippage_bps)
            realized += remaining*((exit_price-entry)/risk if d.direction=="LONG" else (entry-exit_price)/risk)
            fees += remaining*config.execution.commission_per_share; stop_time=bar.timestamp
            history.append({"timestamp":bar.timestamp.isoformat(),"event":"STOP","price":exit_price,"quantity":remaining})
            remaining=0;exit_time=bar.timestamp;exit_reason="AMBIGUOUS_STOP" if ambiguous else "INVALIDATION";break
        if t1_hit and t1_time is None:
            quantity=min(remaining,config.execution.target_1_fraction)
            exit_price=_slipped(d.target_1,d.direction,False,config.execution.slippage_bps)
            realized += quantity*((exit_price-entry)/risk if d.direction=="LONG" else (entry-exit_price)/risk)
            fees += quantity*config.execution.commission_per_share;remaining-=quantity;t1_time=bar.timestamp
            history.append({"timestamp":bar.timestamp.isoformat(),"event":"TARGET_1","price":exit_price,"quantity":quantity})
        if t2_hit and remaining>0:
            exit_price=_slipped(d.target_2,d.direction,False,config.execution.slippage_bps)
            realized += remaining*((exit_price-entry)/risk if d.direction=="LONG" else (entry-exit_price)/risk)
            fees += remaining*config.execution.commission_per_share;t2_time=bar.timestamp
            history.append({"timestamp":bar.timestamp.isoformat(),"event":"TARGET_2","price":exit_price,"quantity":remaining})
            remaining=0;exit_time=bar.timestamp;exit_reason="TARGET_2";break
    if remaining>0:
        final=future[-1];exit_price=_slipped(final.close,d.direction,False,config.execution.slippage_bps)
        realized += remaining*((exit_price-entry)/risk if d.direction=="LONG" else (entry-exit_price)/risk)
        fees += remaining*config.execution.commission_per_share;exit_time=final.timestamp
        history.append({"timestamp":final.timestamp.isoformat(),"event":"END_OF_SESSION","price":exit_price,"quantity":remaining})
    realized -= fees/risk
    minutes=lambda value:(value-first.timestamp).total_seconds()/60 if value else None
    levels=[{"price":x.price,"types":x.types,"importance":x.importance.value,"confluence":x.confluence} for x in evaluation.levels]
    return TradeResult(d.symbol,d.setup_family.value,d.direction,d.timestamp,first.timestamp,exit_time,entry,d.trigger,d.invalidation,d.target_1,d.target_2,risk,realized,max_favorable/risk,max_adverse/risk,minutes(t1_time),minutes(t2_time),minutes(stop_time),exit_reason,ambiguous,d.quality.value,d.structure_5m.value,d.structure_15m.value,d.vwap_state,evaluation.instrument.timeframes["5m"].relative_volume,d.structural_risk_reward,config.parameters.as_dict()|asdict(config.execution),levels,history)


def replay_symbol(symbol:str,bars:list[Bar],period:DateRange,config:BacktestConfig)->tuple[list[SetupEvent],list[TradeResult],int]:
    ordered=sorted(bars,key=lambda x:x.timestamp); regular=[b for b in ordered if _in_session(b,config)]
    by_day=defaultdict(list)
    for bar in regular:by_day[bar.timestamp.astimezone(ET).date()].append(bar)
    events=[];trades=[];previous=None;active_until=None;seen=set()
    for day,session in sorted(by_day.items()):
        if not period.contains(day):continue
        for checkpoint in session:
            if checkpoint.timestamp.astimezone(ET).minute%5!=4:continue
            production_start=checkpoint.timestamp.astimezone(ET).date()-timedelta(days=10)
            visible=[b for b in ordered if production_start<=b.timestamp.astimezone(ET).date() and b.timestamp<=checkpoint.timestamp]
            assert not visible or max(b.timestamp for b in visible)<=checkpoint.timestamp
            try:evaluation=evaluate_production(symbol,visible,previous,config.parameters)
            except ValueError:continue
            d=evaluation.decision
            if _transition(previous,d):
                events.append(SetupEvent(symbol,d.timestamp,d.market_price,d.setup_family.value,d.direction,d.setup_state.value,d.action_now.value,d.quality.value,d.trigger,d.invalidation,d.target_1,d.target_2,d.reasoning,d.missing_confirmation,len(visible),max(x.timestamp for x in visible)))
            key=(day,d.setup_family.value,d.direction,round(d.trigger or 0,4))
            if (active_until is None or d.timestamp>=active_until) and key not in seen:
                trade=simulate_trade(evaluation,session,config)
                if trade:trades.append(trade);active_until=trade.exit_time;seen.add(key)
            previous=d
    processed=sum(len(session) for day,session in by_day.items() if period.contains(day))
    return events,trades,processed


def metrics(trades:list[TradeResult])->dict:
    if not trades:return {"sample_size":0,"win_rate":None,"loss_rate":None,"average_win":None,"average_loss":None,"expectancy_r":None,"median_r":None,"profit_factor":None,"maximum_drawdown_r":None,"average_mfe_r":None,"average_mae_r":None}
    values=[x.realized_r for x in trades];wins=[x for x in values if x>0];losses=[x for x in values if x<0]
    win_rate=len(wins)/len(values);loss_rate=len(losses)/len(values);avg_win=mean(wins) if wins else 0;avg_loss=mean(losses) if losses else 0
    cumulative=peak=max_drawdown=0.0
    for trade in sorted(trades,key=lambda x:x.exit_time):
        cumulative+=trade.realized_r;peak=max(peak,cumulative);max_drawdown=max(max_drawdown,peak-cumulative)
    return {"sample_size":len(values),"win_rate":win_rate,"loss_rate":loss_rate,"average_win":avg_win,"average_loss":avg_loss,
        "expectancy_r":win_rate*avg_win-loss_rate*abs(avg_loss),"median_r":median(values),
        "profit_factor":sum(wins)/abs(sum(losses)) if losses else None,"maximum_drawdown_r":max_drawdown,
        "average_mfe_r":mean(x.mfe_r for x in trades),"average_mae_r":mean(x.mae_r for x in trades)}


def _bucket(trade:TradeResult,dimension:str)->str:
    if dimension=="setup_family":return trade.setup_family
    if dimension=="direction":return trade.direction
    if dimension=="ticker":return trade.symbol
    if dimension=="time_of_day":
        local=trade.entry_time.astimezone(ET).time();return "OPEN" if local<time(11) else "MIDDAY" if local<time(14) else "AFTERNOON"
    if dimension=="alignment":return f"{trade.structure_5m}/{trade.structure_15m}"
    if dimension=="vwap_state":return trade.vwap_state
    if dimension=="relative_volume":return "NA" if trade.relative_volume is None else "<1.0" if trade.relative_volume<1 else "1.0-1.5" if trade.relative_volume<1.5 else ">=1.5"
    if dimension=="structural_rr":return "<2" if trade.structural_risk_reward<2 else "2-3" if trade.structural_risk_reward<3 else ">=3"
    raise ValueError(dimension)


def report(events:list[SetupEvent],trades:list[TradeResult],bars_processed:int)->dict:
    dimensions=("setup_family","direction","ticker","time_of_day","alignment","vwap_state","relative_volume","structural_rr")
    breakdowns={}
    for dimension in dimensions:
        groups=defaultdict(list)
        for trade in trades:groups[_bucket(trade,dimension)].append(trade)
        breakdowns[dimension]={key:metrics(value) for key,value in sorted(groups.items())}
    setup_metrics=breakdowns["setup_family"]
    ranked=[(key,value) for key,value in setup_metrics.items() if value["expectancy_r"] is not None]
    return {"bars_processed":bars_processed,"setups_detected":len(events),"trades_generated":len(trades),
        "overall":metrics(trades),"breakdowns":breakdowns,
        "best_setup":max(ranked,key=lambda x:x[1]["expectancy_r"])[0] if ranked else None,
        "worst_setup":min(ranked,key=lambda x:x[1]["expectancy_r"])[0] if ranked else None,
        "insufficient_sample_setups":[key for key,value in setup_metrics.items() if value["sample_size"]<30],
        "ambiguous_trades":sum(x.ambiguous for x in trades),"events":[asdict(x) for x in events],"trades":[asdict(x) for x in trades]}


async def run(config:BacktestConfig,split:SplitName,purpose:ResearchPurpose,provider:MarketDataProvider,cache:HistoricalDataCache)->dict:
    period=config.splits.get(split,purpose);start=period.start-timedelta(days=config.preload_calendar_days)
    all_events=[];all_trades=[];processed=0
    for symbol in config.symbols:
        bars=await cache.get(provider,symbol,start,period.end,config.bar_timeframe_minutes)
        events,trades,count=replay_symbol(symbol,bars,period,config);all_events+=events;all_trades+=trades;processed+=count
    return report(all_events,all_trades,processed)


def render_markdown(result:dict)->str:
    m=result["overall"];lines=["# BASELINE RESULTS","",f"Trades: {m['sample_size']}",f"Win rate: {m['win_rate']}",f"Average win: {m['average_win']}",f"Average loss: {m['average_loss']}",f"Expectancy: {m['expectancy_r']}",f"Profit factor: {m['profit_factor']}",f"Maximum drawdown: {m['maximum_drawdown_r']}","","| SETUP | N | WIN % | AVG WIN R | AVG LOSS R | EXPECTANCY R | PROFIT FACTOR | MAX DD |","|---|---:|---:|---:|---:|---:|---:|---:|"]
    for setup,x in result["breakdowns"]["setup_family"].items():lines.append(f"| {setup} | {x['sample_size']} | {x['win_rate']} | {x['average_win']} | {x['average_loss']} | {x['expectancy_r']} | {x['profit_factor']} | {x['maximum_drawdown_r']} |")
    lines += ["",f"Best-performing setup: {result['best_setup']}",f"Worst-performing setup: {result['worst_setup']}",f"Insufficient samples: {', '.join(result['insufficient_sample_setups']) or 'None'}",f"Ambiguous trades: {result['ambiguous_trades']}"]
    return "\n".join(lines)


async def _main(args):
    if not settings.massive_api_key:raise SystemExit("MASSIVE_API_KEY is required")
    period=DateRange(date.fromisoformat(args.start),date.fromisoformat(args.end))
    validation=DateRange(period.end+timedelta(days=1),period.end+timedelta(days=1))
    holdout=DateRange(period.end+timedelta(days=2),period.end+timedelta(days=2))
    config=BacktestConfig(tuple(x.strip().upper() for x in args.symbols.split(",")),DataSplits(period,validation,holdout),execution=ExecutionConfig(args.slippage_bps,args.commission_per_share))
    provider=MassiveMarketDataProvider(settings.massive_api_key,settings.massive_base_url)
    try:result=await run(config,SplitName.DEVELOPMENT,ResearchPurpose.BASELINE,provider,HistoricalDataCache())
    finally:await provider.close()
    Path(args.output).write_text(json.dumps(result,default=str,indent=2));Path(args.report).write_text(render_markdown(result))
    print(json.dumps({key:result[key] for key in ("bars_processed","setups_detected","trades_generated","overall","best_setup","worst_setup","ambiguous_trades")},indent=2))


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--symbols",required=True);parser.add_argument("--start",required=True);parser.add_argument("--end",required=True);parser.add_argument("--output",default="data/backtest_baseline.json");parser.add_argument("--report",default="data/backtest_baseline.md");parser.add_argument("--slippage-bps",type=float,default=1.0);parser.add_argument("--commission-per-share",type=float,default=.005)
    asyncio.run(_main(parser.parse_args()))
