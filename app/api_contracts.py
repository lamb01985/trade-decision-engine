"""Stable, frontend-facing API contracts and adapters.

Internal engine models remain intentionally richer. Web/mobile clients should bind
to these contracts and never infer a trading decision from raw evidence.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.models import (MarketScanResult, MarketSetupCandidate, OptionsPlanResult,
                        PriceLevel, SymbolAnalysis)
from app.signal_identity import signal_fingerprint
from app.versioning import ENGINE_VERSION, PARAMETER_VERSION, SETUP_RULES_VERSION


class MarketDataStatus(str, Enum):
    LIVE_CURRENT = "LIVE_CURRENT"
    LIVE_STALE = "LIVE_STALE"
    MARKET_CLOSED_LAST_SESSION = "MARKET_CLOSED_LAST_SESSION"
    DATA_ERROR = "DATA_ERROR"


class MarketSessionStatus(str, Enum):
    PREMARKET = "PREMARKET"
    REGULAR = "REGULAR"
    AFTER_HOURS = "AFTER_HOURS"
    CLOSED = "CLOSED"
    UNKNOWN = "UNKNOWN"


class ApiErrorCode(str, Enum):
    PROVIDER_ERROR = "PROVIDER_ERROR"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    DATA_STALE = "DATA_STALE"
    UNSUPPORTED_SYMBOL = "UNSUPPORTED_SYMBOL"
    INVALID_REQUEST = "INVALID_REQUEST"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class SignalLifecycle(str, Enum):
    FORMING = "FORMING"
    WATCHING = "WATCHING"
    TRIGGERED = "TRIGGERED"
    CONFIRMED = "CONFIRMED"
    INVALIDATED = "INVALIDATED"
    TOO_LATE = "TOO_LATE"


class ErrorDetail(BaseModel):
    code: ApiErrorCode
    message: str
    retryable: bool = False


class ErrorResponse(BaseModel):
    error: ErrorDetail
    detail: str  # compatibility with the current frontend


class MarketStatus(BaseModel):
    data_status: MarketDataStatus
    session: MarketSessionStatus
    provider_timestamp: datetime | None = None
    analysis_timestamp: datetime | None = None
    data_age_seconds: int | None = None
    actionable: bool = False


class Level(BaseModel):
    price: float
    types: list[str]
    importance: str
    distance: float | None = None
    distance_pct: float | None = None
    position: str | None = None
    confluence: int = 1


class LevelZone(BaseModel):
    price: float
    labels: list[str]
    confluence: int
    importance: str


class AnalysisResponse(BaseModel):
    symbol: str
    engine_version: str
    parameter_version: str
    setup_rules_version: str
    signal_id: str | None = None
    provider_timestamp: datetime | None = None
    analysis_timestamp: datetime | None = None
    data_age_seconds: int | None = None
    market_status: MarketStatus
    current_or_last_price: float | None = None
    setup_family: str | None = None
    direction: str | None = None
    setup_state: str | None = None
    quality: str | None = None
    score: int | None = None
    evidence_status: str | None = None
    decision_zone: LevelZone | None = None
    trigger: float | None = None
    invalidation: float | None = None
    target_1: float | None = None
    target_2: float | None = None
    structural_risk_reward: float | None = None
    waiting_for: list[str] = Field(default_factory=list)
    missing_confirmation: list[str] = Field(default_factory=list)
    what_changes_my_mind: list[str] = Field(default_factory=list)
    next_action: str
    vwap_state: str | None = None
    structure_5m: str | None = None
    structure_15m: str | None = None
    important_levels: list[Level] = Field(default_factory=list)
    full_levels: list[Level] = Field(default_factory=list)


class ScannerCandidate(BaseModel):
    signal_id: str
    symbol: str
    setup_family: str
    direction: str
    lifecycle: SignalLifecycle
    quality: str
    score: int
    current_or_last_price: float
    trigger: float
    distance_to_trigger_pct: float
    invalidation: float
    target_1: float
    target_2: float
    structural_risk_reward: float
    chase_state: str
    reasons: list[str]


class ScannerResponse(BaseModel):
    engine_version: str
    parameter_version: str
    setup_rules_version: str
    market_status: MarketStatus
    candidates: list[ScannerCandidate]
    universe_size: int
    symbols_attempted: int
    symbols_successful: int
    symbols_skipped: list[str]
    filtered_illiquid: int
    scan_duration_seconds: float
    notifications_sent: int = 0


class OptionsCandidate(BaseModel):
    ticker: str
    contract_type: str
    expiration_date: str
    strike_price: float
    midpoint: float | None = None
    spread_pct: float | None = None
    delta: float | None = None
    open_interest: int | None = None
    quality: str | None = None


class OptionsPlan(BaseModel):
    underlying_symbol: str
    recommendation: str
    headline: str
    action: str
    data_status: str
    provider_timestamp: datetime | None = None
    candidates: list[OptionsCandidate] = Field(default_factory=list)
    execution_enabled: bool = False


class NotificationStatus(BaseModel):
    provider: str
    status: str
    enabled: bool
    alerts: dict[str, bool]
    public_url_configured: bool
    priorities: dict[str, int]


class HealthResponse(BaseModel):
    app: str = "ok"
    engine_version: str = ENGINE_VERSION


class ReadinessResponse(BaseModel):
    ready: bool
    massive_configured: bool
    pushover_configured: bool
    signal_store_available: bool
    notification_store_available: bool


def _market_status(data_status: str, session: str, provider_timestamp: datetime | None,
                   analysis_timestamp: datetime | None, age: int | None,
                   actionable: bool = False) -> MarketStatus:
    try:
        normalized = MarketDataStatus(data_status)
    except ValueError:
        normalized = MarketDataStatus.DATA_ERROR
    try:
        normalized_session = MarketSessionStatus(session)
    except ValueError:
        normalized_session = MarketSessionStatus.UNKNOWN
    return MarketStatus(data_status=normalized, session=normalized_session,
        provider_timestamp=provider_timestamp, analysis_timestamp=analysis_timestamp,
        data_age_seconds=age, actionable=actionable)


def _level(level: PriceLevel) -> Level:
    return Level(price=level.price, types=level.types, importance=level.importance.value,
        distance=level.distance, distance_pct=level.distance_pct, position=level.position,
        confluence=level.confluence)


def analysis_response(result: SymbolAnalysis) -> AnalysisResponse:
    decision = result.decision
    levels = [_level(item) for item in result.levels]
    important = [item for item in levels if item.confluence > 1 or item.importance in {"MAJOR", "CRITICAL"}]
    trigger = decision.trigger if decision else result.trade_plan.trigger
    setup = decision.setup_family.value if decision else result.setup.setup_type
    direction = decision.direction if decision else result.trade_plan.direction
    signal_id = signal_fingerprint(symbol=result.instrument.symbol, setup_family=setup,
        direction=direction or "NONE", trigger=trigger,
        session=result.provider_timestamp or result.analysis_timestamp or datetime.min)
    if result.data_state == MarketDataStatus.MARKET_CLOSED_LAST_SESSION.value:
        next_action = "Watch for required confirmation next session; fresh live analysis must revalidate the setup."
    elif not result.permission_to_trade:
        next_action = "Wait for the existing confirmation requirements."
    else:
        next_action = "Decision is currently actionable; revalidate risk and chase state."
    return AnalysisResponse(symbol=result.instrument.symbol, engine_version=ENGINE_VERSION,
        parameter_version=PARAMETER_VERSION, setup_rules_version=SETUP_RULES_VERSION,
        signal_id=signal_id, provider_timestamp=result.provider_timestamp,
        analysis_timestamp=result.analysis_timestamp, data_age_seconds=result.data_age_seconds,
        market_status=_market_status(result.data_state, result.market_session,
            result.provider_timestamp, result.analysis_timestamp, result.data_age_seconds,
            result.permission_to_trade), current_or_last_price=result.current_price,
        setup_family=setup, direction=direction,
        setup_state=decision.setup_state.value if decision else result.setup.state.value,
        quality=decision.quality.value if decision else None, score=result.setup_score.score,
        evidence_status="HEURISTIC" if decision and decision.quality_is_heuristic else None,
        decision_zone=LevelZone(price=trigger, labels=["TRIGGER"], confluence=1,
            importance="SETUP_LEVEL") if trigger is not None else None,
        trigger=trigger, invalidation=decision.invalidation if decision else result.trade_plan.invalidation,
        target_1=decision.target_1 if decision else result.trade_plan.target_1,
        target_2=decision.target_2 if decision else result.trade_plan.target_2,
        structural_risk_reward=decision.structural_risk_reward if decision else result.trade_plan.risk_reward,
        waiting_for=list(decision.missing_confirmation) if decision else [],
        missing_confirmation=list(decision.missing_confirmation) if decision else [],
        what_changes_my_mind=list(decision.what_changes_my_mind) if decision else result.what_would_change_state,
        next_action=next_action, vwap_state=decision.vwap_state if decision else None,
        structure_5m=decision.structure_5m.value if decision else None,
        structure_15m=decision.structure_15m.value if decision else None,
        important_levels=important, full_levels=levels)


def _candidate(candidate: MarketSetupCandidate) -> ScannerCandidate:
    lifecycle = SignalLifecycle.CONFIRMED if candidate.status.value.endswith("CONFIRMED") else SignalLifecycle.WATCHING
    return ScannerCandidate(signal_id=signal_fingerprint(symbol=candidate.symbol,
        setup_family=candidate.setup, direction=candidate.direction, trigger=candidate.trigger,
        session=candidate.timestamp), symbol=candidate.symbol, setup_family=candidate.setup,
        direction=candidate.direction, lifecycle=lifecycle, quality=candidate.quality,
        score=candidate.setup_score, current_or_last_price=candidate.current_price,
        trigger=candidate.trigger, distance_to_trigger_pct=candidate.distance_to_trigger_pct,
        invalidation=candidate.invalidation, target_1=candidate.target_1, target_2=candidate.target_2,
        structural_risk_reward=candidate.risk_reward, chase_state=candidate.chase_status.value,
        reasons=candidate.reasons)


def scanner_response(result: MarketScanResult) -> ScannerResponse:
    return ScannerResponse(engine_version=ENGINE_VERSION, parameter_version=PARAMETER_VERSION,
        setup_rules_version=SETUP_RULES_VERSION,
        market_status=_market_status(result.data_state, result.market_session,
            result.provider_timestamp, result.analysis_timestamp, result.data_age_seconds,
            result.data_state == "LIVE_CURRENT"), candidates=[_candidate(item) for item in result.candidates],
        universe_size=result.universe_size, symbols_attempted=result.universe_size,
        symbols_successful=result.analyzed, symbols_skipped=result.failed_symbols,
        filtered_illiquid=result.filtered_illiquid, scan_duration_seconds=result.scan_duration_seconds,
        notifications_sent=result.notifications_sent)


def options_plan_response(result: OptionsPlanResult) -> OptionsPlan:
    assessments = [item for item in (result.best_contract, result.alternative_contract) if item]
    candidates = [OptionsCandidate(ticker=item.contract.ticker,
        contract_type=item.contract.contract_type, expiration_date=item.contract.expiration_date,
        strike_price=item.contract.strike_price, midpoint=item.contract.midpoint,
        spread_pct=item.contract.spread_pct, delta=item.contract.delta,
        open_interest=item.contract.open_interest, quality=item.quality.value)
        for item in assessments]
    return OptionsPlan(underlying_symbol=result.underlying_symbol,
        recommendation=result.recommendation.value, headline=result.headline,
        action=result.plain_english_action, data_status=result.data_status,
        provider_timestamp=result.provider_timestamp, candidates=candidates,
        execution_enabled=result.execution_enabled)
