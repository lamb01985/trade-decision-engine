from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field
from app.versioning import ENGINE_VERSION, PARAMETER_VERSION, SETUP_RULES_VERSION


class Trend(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class SetupState(str, Enum):
    NO_TRADE = "NO_TRADE"
    WATCH_LONG = "WATCH_LONG"
    WATCH_SHORT = "WATCH_SHORT"
    LONG_CONFIRMED = "LONG_CONFIRMED"
    SHORT_CONFIRMED = "SHORT_CONFIRMED"


class SetupProgress(str, Enum):
    NO_SETUP = "NO_SETUP"
    FORMING = "FORMING"
    WATCHING = "WATCHING"
    TRIGGERED = "TRIGGERED"
    CONFIRMED = "CONFIRMED"
    INVALIDATED = "INVALIDATED"
    TOO_LATE = "TOO_LATE"


class SetupFamily(str, Enum):
    OPENING_RANGE_BREAKOUT = "OPENING_RANGE_BREAKOUT"
    OPENING_RANGE_BREAKDOWN = "OPENING_RANGE_BREAKDOWN"
    BREAKOUT_RETEST = "BREAKOUT_RETEST"
    BREAKDOWN_RETEST = "BREAKDOWN_RETEST"
    VWAP_RECLAIM = "VWAP_RECLAIM"
    VWAP_REJECTION = "VWAP_REJECTION"
    FAILED_BREAKOUT = "FAILED_BREAKOUT"
    FAILED_BREAKDOWN = "FAILED_BREAKDOWN"
    TREND_PULLBACK = "TREND_PULLBACK"
    MOMENTUM_CONTINUATION = "MOMENTUM_CONTINUATION"
    COMPRESSION_BREAK = "COMPRESSION_BREAK"
    NONE = "NONE"


class DecisionQuality(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    NO_TRADE = "NO_TRADE"


class ActionNow(str, Enum):
    WAIT = "WAIT"
    PREPARE_LONG = "PREPARE_LONG"
    PREPARE_SHORT = "PREPARE_SHORT"
    LONG_TRIGGERED = "LONG_TRIGGERED"
    SHORT_TRIGGERED = "SHORT_TRIGGERED"
    DO_NOT_CHASE = "DO_NOT_CHASE"
    NO_TRADE = "NO_TRADE"


class RecommendedAction(str, Enum):
    IGNORE = "IGNORE"
    MONITOR = "MONITOR"
    WAIT_FOR_CONFIRMATION = "WAIT_FOR_CONFIRMATION"
    READY = "READY"
    MANAGE = "MANAGE"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"


class ConfirmationComponentState(str, Enum):
    PASS = "PASS"
    PENDING = "PENDING"
    UNAVAILABLE = "UNAVAILABLE"


class ConfirmationComponent(BaseModel):
    id: str
    label: str
    state: ConfirmationComponentState
    required: bool = True
    evidence: str | float | bool | None = None


class NextStateRequirement(BaseModel):
    id: str
    label: str
    state: ConfirmationComponentState
    target_state: str


class DecisionZone(BaseModel):
    lower_bound: float
    upper_bound: float
    role: str
    source: str
    active: bool


class InvalidationRule(BaseModel):
    type: str
    price: float | None = None
    lower_bound: float | None = None
    upper_bound: float | None = None
    condition: str
    timeframe: str | None = None
    human_label: str


class DecisionLevelMetadata(BaseModel):
    price: float
    categories: list[str]
    setup_specific: bool
    currently_interacting: bool
    confluence_count: int
    source_timeframes: list[str]
    freshness_context: str


class DecisionIntelligence(BaseModel):
    recommended_action: RecommendedAction
    recommended_action_label: str
    lifecycle_state: str
    confirmation_components: list[ConfirmationComponent] = Field(default_factory=list)
    next_state_requirements: list[NextStateRequirement] = Field(default_factory=list)
    decision_zone: DecisionZone | None = None
    invalidation: InvalidationRule | None = None
    targets: list[float] = Field(default_factory=list)
    structural_risk_reward: float | None = None
    levels: list[DecisionLevelMetadata] = Field(default_factory=list)


class LevelImportance(str, Enum):
    MAJOR_RESISTANCE = "MAJOR_RESISTANCE"
    RESISTANCE = "RESISTANCE"
    PIVOT = "PIVOT"
    SUPPORT = "SUPPORT"
    MAJOR_SUPPORT = "MAJOR_SUPPORT"


class ConfirmationStatus(str, Enum):
    STRONG_BEARISH_CONFIRMATION = "STRONG_BEARISH_CONFIRMATION"
    PARTIAL_BEARISH_CONFIRMATION = "PARTIAL_BEARISH_CONFIRMATION"
    NEUTRAL = "NEUTRAL"
    PARTIAL_BULLISH_CONFIRMATION = "PARTIAL_BULLISH_CONFIRMATION"
    STRONG_BULLISH_CONFIRMATION = "STRONG_BULLISH_CONFIRMATION"


class Bar(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class TimeframeAnalysis(BaseModel):
    timeframe: str
    price: float
    vwap: float
    ema_9: float
    ema_21: float
    vwap_state: str
    ema_alignment: str
    distance_from_vwap: float
    distance_from_vwap_pct: float
    trend: Trend
    volume: float
    relative_volume: float | None = None


class InstrumentAnalysis(BaseModel):
    symbol: str
    available: bool
    message: str | None = None
    as_of: datetime | None = None
    timeframes: dict[str, TimeframeAnalysis] = Field(default_factory=dict)
    high_of_day: float | None = None
    low_of_day: float | None = None
    prior_day_high: float | None = None
    prior_day_low: float | None = None
    prior_day_close: float | None = None
    opening_range_high: float | None = None
    opening_range_low: float | None = None
    premarket_high: float | None = None
    premarket_low: float | None = None
    change_pct: float | None = None
    average_dollar_volume: float | None = None
    atr_5m: float | None = None


class PriceLevel(BaseModel):
    price: float
    types: list[str]
    importance: LevelImportance
    distance: float
    distance_pct: float
    position: str
    confluence: int = 1


class LevelActionStatus(str, Enum):
    WAIT = "WAIT"
    APPROACHING_LEVEL = "APPROACHING_LEVEL"
    TESTING_LEVEL = "TESTING_LEVEL"
    LONG_SETUP_FORMING = "LONG_SETUP_FORMING"
    SHORT_SETUP_FORMING = "SHORT_SETUP_FORMING"
    LONG_CONFIRMED = "LONG_CONFIRMED"
    SHORT_CONFIRMED = "SHORT_CONFIRMED"
    LEVEL_BROKEN = "LEVEL_BROKEN"
    LEVEL_RECLAIMED = "LEVEL_RECLAIMED"


class LevelActionPlan(BaseModel):
    level_price: float
    level_type: list[str]
    importance: LevelImportance
    confluence: int
    current_price_distance: float
    current_price_distance_pct: float
    bullish_scenario: str
    bearish_scenario: str
    long_trigger: str
    short_trigger: str
    long_invalidation: str
    short_invalidation: str
    long_target_1: float | None = None
    long_target_2: float | None = None
    short_target_1: float | None = None
    short_target_2: float | None = None
    status: LevelActionStatus
    entry: float | None = None
    risk_reward: float | None = None
    structure_status: str


class CrossMarketAnalysis(BaseModel):
    qqq_vwap_state: str
    nq_vwap_state: str
    qqq_5m_trend: Trend
    nq_5m_trend: Trend | None
    qqq_15m_trend: Trend
    nq_15m_trend: Trend | None
    confirmation_status: ConfirmationStatus
    explanation: str


class SetupDecision(BaseModel):
    state: SetupState
    explanation: list[str]
    breakout_level: float | None = None
    breakdown_level: float | None = None
    volume_confirmed: bool = False
    setup_type: str = "NO_SETUP"
    trigger_occurred: bool = False


class TradePlan(BaseModel):
    direction: str | None = None
    trigger: float | None = None
    invalidation: float | None = None
    target_1: float | None = None
    target_2: float | None = None
    risk_reward: float | None = None
    status: str
    explanation: str


class DashboardAnalysis(BaseModel):
    qqq: InstrumentAnalysis
    nq: InstrumentAnalysis
    cross_market: CrossMarketAnalysis
    setup: SetupDecision
    trade_plan: TradePlan
    what_would_change_state: list[str]
    permission_to_trade: bool
    thesis: str
    confirmed_setup: bool


class ScoreFactor(BaseModel):
    name: str
    points: int
    max_points: int
    explanation: str


class SetupScore(BaseModel):
    score: int
    factors: list[ScoreFactor]


class SymbolAnalysis(BaseModel):
    instrument: InstrumentAnalysis
    levels: list[PriceLevel]
    setup: SetupDecision
    setup_score: SetupScore
    trade_plan: TradePlan
    what_would_change_state: list[str]
    thesis: str
    current_structure: str
    confirmation: str
    permission_to_trade: bool
    cross_market: CrossMarketAnalysis | None = None
    futures_confirmation: str | None = None
    level_playbooks: list[LevelActionPlan] = Field(default_factory=list)
    decision: "StandardDecision | None" = None
    decision_intelligence: DecisionIntelligence | None = None
    provider_timestamp: datetime | None = None
    analysis_timestamp: datetime | None = None
    decision_bar_timestamp: datetime | None = None
    data_age_seconds: int | None = None
    data_status: str = "UNKNOWN"
    data_state: str = "DATA_ERROR"
    market_session: str = "UNKNOWN"
    current_price: float | None = None
    price_source: str = "UNAVAILABLE"
    cache_hit: bool = False
    cache_age_seconds: int = 0
    # Internal measurement handoff; excluded from every API serialization.
    research_candidate_instance_ids: list[str] = Field(default_factory=list, exclude=True)


class StandardDecision(BaseModel):
    engine_version: str = ENGINE_VERSION
    setup_rules_version: str = SETUP_RULES_VERSION
    parameter_version: str = PARAMETER_VERSION
    symbol: str
    timestamp: datetime
    market_price: float
    direction: str
    setup_family: SetupFamily
    setup_state: SetupProgress
    quality: DecisionQuality
    quality_is_heuristic: bool = True
    action_now: ActionNow
    trigger: float | None = None
    invalidation: float | None = None
    target_1: float | None = None
    target_2: float | None = None
    structural_risk_reward: float | None = None
    nearest_support: float | None = None
    nearest_resistance: float | None = None
    vwap_state: str
    structure_5m: Trend
    structure_15m: Trend
    atr: float | None = None
    reasoning: list[str]
    missing_confirmation: list[str]
    do_not_trade_if: list[str]
    what_changes_my_mind: list[str]
    prior_state: SetupProgress | None = None
    state_changed_at: datetime


class ScannerRow(BaseModel):
    symbol: str
    price: float
    bias: str
    setup: str
    setup_score: int
    score_factors: list[ScoreFactor]
    vwap_state: str
    trend_1m: Trend
    trend_5m: Trend
    trend_15m: Trend
    relative_volume: float | None
    nearest_support: float | None
    nearest_resistance: float | None
    trigger: float | None
    invalidation: float | None
    target_1: float | None
    target_2: float | None
    status: SetupState
    quality: DecisionQuality | None = None
    action_now: ActionNow | None = None
    setup_progress: SetupProgress | None = None
    standardized_decision: StandardDecision | None = None
    decision_intelligence: DecisionIntelligence | None = None
    provider_timestamp: datetime | None = None
    analysis_timestamp: datetime | None = None
    data_age_seconds: int | None = None
    data_status: str = "UNKNOWN"
    data_state: str = "DATA_ERROR"
    market_session: str = "UNKNOWN"


class MarketAlertType(str, Enum):
    RADAR = "RADAR"
    TRIGGERED = "TRIGGERED"
    INVALIDATED = "INVALIDATED"
    TOO_LATE = "TOO_LATE"


class ChaseStatus(str, Enum):
    NOT_TRIGGERED = "NOT_TRIGGERED"
    ACTIONABLE = "ACTIONABLE"
    EXTENDED = "EXTENDED"
    TOO_LATE = "TOO_LATE"


class MarketSetupCandidate(BaseModel):
    symbol: str
    direction: str
    setup: str
    quality: str
    setup_score: int
    status: SetupState
    current_price: float
    trigger: float
    distance_to_trigger: float
    distance_to_trigger_pct: float
    invalidation: float
    target_1: float
    target_2: float
    risk_reward: float
    chase_status: ChaseStatus
    nearest_support: float | None = None
    nearest_resistance: float | None = None
    average_dollar_volume: float | None = None
    reasons: list[str]
    timestamp: datetime
    decision_intelligence: DecisionIntelligence | None = None


class MarketAlert(BaseModel):
    symbol: str
    alert_type: MarketAlertType
    direction: str
    setup: str
    quality: str
    status: SetupState
    action_now: str
    current_price: float
    trigger: float
    distance_to_trigger: float
    invalidation: float
    target_1: float
    target_2: float
    risk_reward: float
    reasons: list[str]
    timestamp: datetime


class MarketScanResult(BaseModel):
    universe_size: int
    analyzed: int
    filtered_illiquid: int
    candidates: list[MarketSetupCandidate]
    alerts: list[MarketAlert]
    data_as_of: datetime | None = None
    provider_timestamp: datetime | None = None
    analysis_timestamp: datetime | None = None
    data_age_seconds: int | None = None
    data_status: str = "UNKNOWN"
    data_state: str = "DATA_ERROR"
    market_session: str = "CLOSED"
    scan_duration_seconds: float = 0
    failed_symbols: list[str] = Field(default_factory=list)
    notification_status: str = "DISABLED"
    notifications_sent: int = 0
    message: str | None = None
    next_refresh_seconds: int


class MarketScanRequest(BaseModel):
    watchlist: list[str] = Field(default_factory=list)
    include_nasdaq_100: bool = False
    include_sp_500: bool = False
    additional_symbols: list[str] = Field(default_factory=list)
    minimum_price: float | None = Field(default=None, gt=0)
    minimum_average_dollar_volume: float | None = Field(default=None, ge=0)
    minimum_score: int | None = Field(default=None, ge=0, le=100)
    refresh: bool = False


class NotificationPreferenceUpdate(BaseModel):
    radar: bool | None = None
    ready: bool | None = None
    invalidated: bool | None = None
    too_late: bool | None = None


class FuturesProbeResult(BaseModel):
    root_symbol: str
    contract_ticker: str
    contract_name: str | None = None
    trading_venue: str | None = None
    trade_tick_size: float | None = None
    first_trade_date: str | None = None
    last_trade_date: str | None = None
    bars_loaded: int
    latest_timestamp: datetime | None = None
    latest_open: float | None = None
    latest_high: float | None = None
    latest_low: float | None = None
    latest_close: float | None = None
    latest_volume: int | None = None
    data_status: str
    strategy_invoked: bool = False
    execution_enabled: bool = False
    message: str


class OptionContractInsight(BaseModel):
    ticker: str
    contract_type: str
    expiration_date: str
    strike_price: float
    bid: float | None = None
    ask: float | None = None
    midpoint: float | None = None
    spread: float | None = None
    spread_pct: float | None = None
    last_trade_price: float | None = None
    last_trade_timestamp: datetime | None = None
    volume: int | None = None
    open_interest: int | None = None
    volume_open_interest_ratio: float | None = None
    implied_volatility: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    quote_timeframe: str | None = None
    trade_timeframe: str | None = None
    data_notes: list[str] = Field(default_factory=list)


class OptionsIntelligenceResult(BaseModel):
    underlying_symbol: str
    underlying_price: float | None = None
    underlying_change_pct: float | None = None
    underlying_timestamp: datetime | None = None
    data_age_seconds: int | None = None
    market_session: str = "UNKNOWN"
    expirations: list[str]
    selected_expiration: str | None = None
    strike_window_pct: float
    contracts: list[OptionContractInsight]
    contracts_returned: int
    data_status: str
    strategy_invoked: bool = False
    execution_enabled: bool = False
    message: str


class ContractFitCriteria(BaseModel):
    contract_type: str
    min_days_to_expiry: int = Field(ge=0, le=365)
    max_days_to_expiry: int = Field(ge=0, le=365)
    min_abs_delta: float = Field(ge=0.0, le=1.0)
    max_abs_delta: float = Field(ge=0.0, le=1.0)
    min_open_interest: int = Field(ge=0)
    max_spread_pct: float = Field(gt=0.0, le=100.0)
    strike_window_pct: float = Field(ge=1.0, le=50.0)
    max_expirations_loaded: int = Field(ge=1, le=8)


class ContractFitCandidate(BaseModel):
    contract: OptionContractInsight
    days_to_expiry: int
    distance_from_underlying_pct: float
    explanation: list[str]


class OptionsShortlistResult(BaseModel):
    underlying_symbol: str
    underlying_price: float
    criteria: ContractFitCriteria
    expirations_considered: list[str]
    contracts_scanned: int
    candidates: list[ContractFitCandidate]
    sort_order: str
    data_status: str
    strategy_invoked: bool = False
    execution_enabled: bool = False
    message: str


class UnderlyingEligibilityState(str, Enum):
    CONFIRMED = "CONFIRMED"
    WAIT_FOR_TRIGGER = "WAIT_FOR_TRIGGER"
    PASS_UNDERLYING_SETUP = "PASS_UNDERLYING_SETUP"
    DO_NOT_CHASE = "DO_NOT_CHASE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class ContractQuality(str, Enum):
    EXCELLENT = "EXCELLENT"
    GOOD = "GOOD"
    ACCEPTABLE = "ACCEPTABLE"
    POOR = "POOR"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class TimeAssessment(str, Enum):
    PLENTY = "PLENTY"
    GOOD = "GOOD"
    TIGHT = "TIGHT"
    DANGEROUS = "DANGEROUS"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class OptionsRecommendationState(str, Enum):
    GOOD_TRADE_CONFIRMED = "GOOD_TRADE_CONFIRMED"
    WAIT_FOR_UNDERLYING_TRIGGER = "WAIT_FOR_UNDERLYING_TRIGGER"
    PASS_UNDERLYING_SETUP_TOO_WEAK = "PASS_UNDERLYING_SETUP_TOO_WEAK"
    GOOD_SETUP_NO_GOOD_CONTRACT = "GOOD_SETUP_NO_GOOD_CONTRACT"
    PASS_POOR_LIQUIDITY = "PASS_POOR_LIQUIDITY"
    TOO_LATE_DO_NOT_CHASE = "TOO_LATE_DO_NOT_CHASE"
    NOT_ENOUGH_DATA = "NOT_ENOUGH_DATA"


class OptionsPlanCriteria(BaseModel):
    min_days_to_expiry: int = Field(default=3, ge=0, le=365)
    max_days_to_expiry: int = Field(default=10, ge=0, le=365)
    min_abs_delta: float = Field(default=0.35, ge=0.0, le=1.0)
    max_abs_delta: float = Field(default=0.65, ge=0.0, le=1.0)
    min_open_interest: int = Field(default=100, ge=0)
    max_spread_pct: float = Field(default=10.0, gt=0.0, le=100.0)
    strike_window_pct: float = Field(default=10.0, ge=1.0, le=50.0)
    max_expirations_loaded: int = Field(default=3, ge=1, le=8)


class UnderlyingEligibilityAssessment(BaseModel):
    state: UnderlyingEligibilityState
    eligible_for_contract_evaluation: bool
    expected_contract_type: str | None = None
    action_now: ActionNow | None = None
    setup_quality: DecisionQuality | None = None
    setup_family: SetupFamily | None = None
    setup_state: SetupProgress | None = None
    direction: str | None = None
    market_price: float | None = None
    trigger: float | None = None
    invalidation: float | None = None
    target_1: float | None = None
    target_2: float | None = None
    decision_timestamp: datetime | None = None
    provider_timestamp: datetime | None = None
    analysis_timestamp: datetime | None = None
    data_age_seconds: int | None = None
    data_status: str = "UNKNOWN"
    market_session: str = "UNKNOWN"
    reasons: list[str] = Field(default_factory=list)


class ContractQualityAssessment(BaseModel):
    contract: OptionContractInsight
    days_to_expiry: int
    time_assessment: TimeAssessment
    quality: ContractQuality
    passes_hard_criteria: bool
    reasons: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)


class OptionsPlanResult(BaseModel):
    underlying_symbol: str
    underlying_eligibility: UnderlyingEligibilityAssessment
    criteria: OptionsPlanCriteria
    recommendation: OptionsRecommendationState
    headline: str
    plain_english_action: str
    price_iv_assessment: str = "INSUFFICIENT_DATA"
    price_iv_explanation: str
    contracts_scanned: int = 0
    eligible_contracts: int = 0
    best_contract: ContractQualityAssessment | None = None
    alternative_contract: ContractQualityAssessment | None = None
    rejected_contract_reasons: list[str] = Field(default_factory=list)
    expirations_considered: list[str] = Field(default_factory=list)
    data_status: str
    underlying_engine_read: bool = True
    underlying_engine_modified: bool = False
    execution_enabled: bool = False
    provider_timestamp: datetime | None = None
    analysis_timestamp: datetime | None = None
    data_age_seconds: int | None = None
    market_session: str = "CLOSED"
    message: str
