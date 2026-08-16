import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from time import monotonic
from zoneinfo import ZoneInfo

from app.engines.decision import (build_symbol_trade_plan, build_trade_plan, cross_market_confirmation,
    evaluate_setup, evaluate_symbol_setup, score_setup, state_changes)
from app.engines.levels import build_levels
from app.engines.market import analyze_symbol, regular_session_bars, unavailable_nq
from app.engines.playbook import build_level_playbooks
from app.engines.setups import build_standard_decision
from app.engines.pipeline import evaluate_production
from app.engines.alerts import AlertEngine, ScannerPolicy, candidate_from_analysis
from app.config import settings
from app.market_data.base import MarketDataProvider
from app.market_data.base import MarketDataError, StockObservation
from app.freshness import DataStatus, classify_data, is_actionable_data, market_data_state
from app.models import ActionNow, DashboardAnalysis, MarketScanResult, ScannerRow, SetupState, SymbolAnalysis, Trend
from app.universe import UniverseSelection
from app.parameters import DEFAULT_PARAMETERS
from app.signal_log import SignalLogger, SignalStore
from app.persistence.contracts import ScannerStateStore
from app.decision_intelligence import build_decision_intelligence

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
DEFAULT_WATCHLIST = ["QQQ", "SPY", "IWM", "SMH", "NVDA", "AMD", "AMZN", "META", "AAPL", "MSFT", "AVGO", "TSLA", "GOOGL"]


class AnalysisService:
    def __init__(self, provider: MarketDataProvider, cache_seconds: int = 60,
                 signal_logger: SignalStore | None = None,
                 scanner_state_store: ScannerStateStore | None = None) -> None:
        self.provider = provider
        self.cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, SymbolAnalysis]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._market_scan_lock = asyncio.Lock()
        self.scanner_state_store = scanner_state_store
        self.alert_engine = AlertEngine(ScannerPolicy(
            minimum_price=settings.scanner_min_price,
            minimum_average_dollar_volume=settings.scanner_min_average_dollar_volume,
            minimum_score=settings.scanner_min_score), state_store=scanner_state_store)
        self._alert_engines: dict[ScannerPolicy, AlertEngine] = {self.alert_engine.policy: self.alert_engine}
        self._decisions = {}
        self.signal_logger = signal_logger or SignalLogger(settings.signal_log_path)

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        normalized = symbol.strip().upper()
        if not SYMBOL_RE.fullmatch(normalized):
            raise ValueError("Ticker must contain 1-10 letters, numbers, dots, or hyphens")
        return normalized

    @staticmethod
    def _with_current_freshness(result: SymbolAnalysis, cached_at: float | None = None) -> SymbolAnalysis:
        status, session, age = classify_data(result.provider_timestamp)
        cache_age = max(0, int(monotonic() - cached_at)) if cached_at is not None else 0
        data_state = market_data_state(status, session).value
        return result.model_copy(update={
            "data_status": status.value,
            "data_state": data_state,
            "market_session": session.value,
            "data_age_seconds": age,
            "cache_hit": cached_at is not None,
            "cache_age_seconds": cache_age,
            "permission_to_trade": bool(
                result.decision
                and result.decision.action_now in {ActionNow.LONG_TRIGGERED, ActionNow.SHORT_TRIGGERED}
                and is_actionable_data(status, session)
            ),
            "decision_intelligence": (build_decision_intelligence(result.decision, result.levels,
                data_state=data_state, market_session=session.value) if result.decision else None),
        })

    async def analyze_symbol(self, symbol: str, include_cross_market: bool = True, force: bool = False,
                             persist_actionable_only: bool = False) -> SymbolAnalysis:
        symbol = self.normalize_symbol(symbol)
        cached = self._cache.get(symbol)
        if cached and not force and monotonic() - cached[0] < self.cache_seconds:
            logger.info("analysis_cache symbol=%s result=hit", symbol)
            return self._with_current_freshness(cached[1], cached[0])
        logger.info("analysis_cache symbol=%s result=miss forced=%s", symbol, force)
        lock = self._locks.setdefault(symbol, asyncio.Lock())
        async with lock:
            cached = self._cache.get(symbol)
            if cached and not force and monotonic() - cached[0] < self.cache_seconds:
                return self._with_current_freshness(cached[1], cached[0])
            today = datetime.now(ET).date()
            bars_result, observation_result = await asyncio.gather(
                self.provider.get_minute_bars(symbol, today - timedelta(days=10), today),
                self.provider.get_stock_observation(symbol),
                return_exceptions=True,
            )
            if isinstance(bars_result, Exception):
                raise bars_result
            now = datetime.now(timezone.utc)
            bars = [bar for bar in bars_result if bar.timestamp + timedelta(minutes=1) <= now]
            if not bars:
                raise MarketDataError(f"No completed minute bars returned for {symbol}")
            latest_bar = max(bars, key=lambda bar: bar.timestamp)
            bar_observation = StockObservation(symbol, latest_bar.close, latest_bar.timestamp, "MASSIVE_COMPLETED_MINUTE_AGGREGATE")
            observation = observation_result if isinstance(observation_result, StockObservation) else None
            if isinstance(observation_result, Exception):
                logger.warning("snapshot_fallback symbol=%s error_type=%s", symbol,
                               type(observation_result).__name__)
            if observation is None or observation.provider_timestamp < bar_observation.provider_timestamp:
                observation = bar_observation
            prior = self._decisions.get(symbol)
            if prior is None and hasattr(self.signal_logger, "get_latest_decision"):
                prior = self.signal_logger.get_latest_decision(symbol)
            evaluation = evaluate_production(symbol, bars, prior)
            instrument, current_bars, levels = evaluation.instrument, evaluation.current_bars, evaluation.levels
            setup, plan, score, decision = evaluation.legacy_setup, evaluation.legacy_plan, evaluation.score, evaluation.decision
            playbooks = build_level_playbooks(levels, current_bars, instrument.timeframes["1m"].price,
                                              instrument.timeframes["5m"].relative_volume, instrument.atr_5m)
            f5, f15 = instrument.timeframes["5m"], instrument.timeframes["15m"]
            direction = "bullish" if setup.state in {SetupState.WATCH_LONG, SetupState.LONG_CONFIRMED} else "bearish" if setup.state in {SetupState.WATCH_SHORT, SetupState.SHORT_CONFIRMED} else "neutral/mixed"
            cross = None
            futures = None
            if include_cross_market and symbol in {"QQQ", "SPY", "IWM"}:
                futures = "FUTURES CONFIRMATION UNAVAILABLE"
                if symbol == "QQQ":
                    cross = cross_market_confirmation(instrument, unavailable_nq())
            analysis_timestamp = datetime.now(timezone.utc)
            data_status, session, age = classify_data(observation.provider_timestamp, analysis_timestamp)
            strategy_permission = decision.action_now in {ActionNow.LONG_TRIGGERED, ActionNow.SHORT_TRIGGERED}
            result = SymbolAnalysis(instrument=instrument, levels=levels, setup=setup, setup_score=score,
                trade_plan=plan, what_would_change_state=state_changes(instrument, setup),
                thesis=f"Current deterministic {symbol} thesis is {direction}.",
                current_structure=f"5m {f5.trend.value.lower()}, 15m {f15.trend.value.lower()}, price {f5.vwap_state.lower()} VWAP.",
                confirmation=("Confirmed standardized setup." if decision.action_now in {ActionNow.LONG_TRIGGERED, ActionNow.SHORT_TRIGGERED}
                              else "Awaiting standardized confirmation; setup is not authorized."),
                permission_to_trade=strategy_permission and is_actionable_data(data_status, session),
                cross_market=cross, futures_confirmation=futures,
                level_playbooks=playbooks, decision=decision,
                provider_timestamp=observation.provider_timestamp,
                analysis_timestamp=analysis_timestamp,
                decision_bar_timestamp=current_bars[-1].timestamp if current_bars else None,
                data_age_seconds=age, data_status=data_status.value, market_session=session.value,
                data_state=market_data_state(data_status, session).value,
                current_price=observation.price, price_source=observation.source)
            result.decision_intelligence = build_decision_intelligence(decision, levels,
                data_state=result.data_state, market_session=result.market_session)
            # Persistence/lifecycle state advances only from current regular-session data.
            # Premarket, after-hours, closed, stale, and unavailable observations remain
            # visible to callers but cannot manufacture a production state transition.
            if not persist_actionable_only or (data_status == DataStatus.LIVE and session.value == "REGULAR"):
                self._decisions[symbol] = decision
                self.signal_logger.observe(result, current_bars, DEFAULT_PARAMETERS)
            self._cache[symbol] = (monotonic(), result)
            return result

    async def scan(self, symbols: list[str], force: bool = False) -> list[ScannerRow]:
        normalized = list(dict.fromkeys(self.normalize_symbol(x) for x in symbols))[:30]
        results = await asyncio.gather(*(self.analyze_symbol(x, include_cross_market=False, force=force) for x in normalized), return_exceptions=True)
        rows = []
        for symbol, result in zip(normalized, results):
            if isinstance(result, Exception):
                logger.warning("scanner_symbol_skipped symbol=%s error_type=%s", symbol,
                               type(result).__name__)
                continue
            inst, setup, plan = result.instrument, result.setup, result.trade_plan
            price = inst.timeframes["1m"].price
            supports = [x.price for x in result.levels if x.price < price]
            resistances = [x.price for x in result.levels if x.price > price]
            f1, f5, f15 = (inst.timeframes[x] for x in ("1m", "5m", "15m"))
            bias = "BULLISH" if setup.state in {SetupState.WATCH_LONG, SetupState.LONG_CONFIRMED} else "BEARISH" if setup.state in {SetupState.WATCH_SHORT, SetupState.SHORT_CONFIRMED} else "NEUTRAL"
            decision = result.decision
            if decision:
                bias = decision.direction
            rows.append(ScannerRow(symbol=symbol, price=price, bias=bias, setup=decision.setup_family.value if decision else setup.setup_type,
                setup_score=result.setup_score.score, score_factors=result.setup_score.factors,
                vwap_state=f5.vwap_state, trend_1m=f1.trend, trend_5m=f5.trend, trend_15m=f15.trend,
                relative_volume=f5.relative_volume, nearest_support=max(supports, default=None), nearest_resistance=min(resistances, default=None),
                trigger=decision.trigger if decision else plan.trigger,
                invalidation=decision.invalidation if decision else plan.invalidation,
                target_1=decision.target_1 if decision else plan.target_1,
                target_2=decision.target_2 if decision else plan.target_2, status=setup.state,
                quality=result.decision.quality if result.decision else None,
                action_now=result.decision.action_now if result.decision else None,
                setup_progress=result.decision.setup_state if result.decision else None,
                standardized_decision=result.decision,
                decision_intelligence=result.decision_intelligence,
                provider_timestamp=result.provider_timestamp, analysis_timestamp=result.analysis_timestamp,
                data_age_seconds=result.data_age_seconds, data_status=result.data_status,
                data_state=result.data_state,
                market_session=result.market_session))
        quality_rank = {"A": 3, "B": 2, "C": 1, "NO_TRADE": 0, None: 0}
        return sorted(rows, key=lambda row: (quality_rank[row.quality.value if row.quality else None],
            row.setup_progress.value == "CONFIRMED" if row.setup_progress else False, row.setup_score), reverse=True)

    async def market_scan(self, selection: UniverseSelection, force: bool = False,
                          policy: ScannerPolicy | None = None,
                          durable_transitions_only: bool = False) -> MarketScanResult:
        async with self._market_scan_lock:
            return await self._market_scan_unlocked(selection, force, policy,
                                                    durable_transitions_only)

    async def _market_scan_unlocked(self, selection: UniverseSelection, force: bool = False,
                                    policy: ScannerPolicy | None = None,
                                    durable_transitions_only: bool = False) -> MarketScanResult:
        scan_started = monotonic()
        symbols = [self.normalize_symbol(x) for x in selection.symbols()]
        symbols = list(dict.fromkeys(symbols))[:settings.scanner_max_symbols]
        logger.info("scanner_start symbols_attempted=%d forced=%s", len(symbols), force)
        analyses: list[SymbolAnalysis] = []
        failed_symbols: list[str] = []
        # Small sequential batches avoid an API burst while still keeping market-wide scans practical.
        for start in range(0, len(symbols), settings.scanner_batch_size):
            batch = symbols[start:start + settings.scanner_batch_size]
            # Live Radar always bypasses the analysis cache. The request flag is retained for API compatibility.
            async def analyze_for_scan(symbol: str):
                if durable_transitions_only:
                    return await self.analyze_symbol(symbol, include_cross_market=False, force=True,
                        persist_actionable_only=True)
                return await self.analyze_symbol(symbol, include_cross_market=False, force=True)
            results = await asyncio.gather(*(analyze_for_scan(x) for x in batch), return_exceptions=True)
            for symbol, result in zip(batch, results):
                if isinstance(result, SymbolAnalysis):
                    analyses.append(result)
                else:
                    failed_symbols.append(symbol)
                    logger.warning("scanner_symbol_skipped symbol=%s error_type=%s", symbol,
                                   type(result).__name__)
        effective_policy = policy or self.alert_engine.policy
        engine = self._alert_engines.setdefault(effective_policy, AlertEngine(effective_policy,
            state_store=self.scanner_state_store))
        timestamps = [
            analysis.provider_timestamp or analysis.instrument.as_of
            for analysis in analyses
            if analysis.provider_timestamp is not None or analysis.instrument.as_of is not None
        ]
        provider_timestamp = min(timestamps) if timestamps else None
        data_status, session, age = classify_data(provider_timestamp)
        if failed_symbols:
            data_status = DataStatus.ERROR
        if data_status == DataStatus.LIVE and session.value == "REGULAR" and not failed_symbols:
            candidates, alerts, filtered = engine.evaluate(analyses)
        elif data_status == DataStatus.MARKET_CLOSED and not failed_symbols:
            # Closed-market preparation is a read-only ranking of the latest
            # completed session. Do not advance live alert-transition state.
            evaluated = [candidate_from_analysis(item, effective_policy, allow_closed=True) for item in analyses]
            candidates = [candidate for candidate, _ in evaluated if candidate is not None]
            candidates.sort(key=lambda item: (
                item.status in {SetupState.LONG_CONFIRMED, SetupState.SHORT_CONFIRMED},
                item.setup_score, item.risk_reward, -abs(item.distance_to_trigger_pct),
            ), reverse=True)
            alerts = []
            filtered = sum(rejection == "ILLIQUID" for _, rejection in evaluated)
        else:
            candidates, alerts, filtered = [], [], 0
        analysis_timestamp = datetime.now(timezone.utc)
        duration = round(monotonic() - scan_started, 3)
        logger.info("scanner_end symbols_attempted=%d successful=%d skipped=%d candidates=%d status=%s age_seconds=%s duration_seconds=%.3f",
                    len(symbols), len(analyses), len(failed_symbols), len(candidates),
                    data_status.value, age, duration)
        return MarketScanResult(universe_size=len(symbols), analyzed=len(analyses), filtered_illiquid=filtered,
            candidates=candidates, alerts=alerts, data_as_of=provider_timestamp,
            provider_timestamp=provider_timestamp, analysis_timestamp=analysis_timestamp,
            data_age_seconds=age, data_status=data_status.value, market_session=session.value,
            data_state=market_data_state(data_status, session).value,
            scan_duration_seconds=duration, failed_symbols=failed_symbols,
            message=None if candidates else ("MARKET CLOSED — historical analysis only." if data_status == DataStatus.MARKET_CLOSED else "NO HIGH-QUALITY SETUPS RIGHT NOW."),
            next_refresh_seconds=settings.scanner_refresh_seconds)

    async def analyze(self) -> DashboardAnalysis:
        """V1 compatibility endpoint and response contract."""
        result = await self.analyze_symbol("QQQ")
        qqq, nq = result.instrument, unavailable_nq()
        cross = cross_market_confirmation(qqq, nq)
        setup = evaluate_setup(qqq, cross)
        plan = build_trade_plan(qqq, setup)
        confirmed = setup.state.value.endswith("CONFIRMED")
        direction = "bullish" if "LONG" in setup.state.value else "bearish" if "SHORT" in setup.state.value else "neutral/mixed"
        return DashboardAnalysis(qqq=qqq, nq=nq, cross_market=cross, setup=setup, trade_plan=plan,
            what_would_change_state=state_changes(qqq, setup), permission_to_trade=confirmed,
            thesis=f"Current deterministic QQQ thesis is {direction}.", confirmed_setup=confirmed)
