from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class EngineParameters:
    """Central, unoptimized deterministic parameters used by live and research engines."""
    opening_range_minutes: int = 30
    structure_lookback_bars: int = 60
    swing_window_bars: int = 2
    maximum_swings_per_timeframe: int = 8
    level_merge_atr_fraction: float = 0.10
    level_merge_minimum_pct: float = 0.08
    breakout_buffer_atr: float = 0.05
    retest_tolerance_atr: float = 0.15
    vwap_tolerance_atr: float = 0.12
    volume_confirmation_rvol: float = 1.20
    compression_lookback_bars: int = 6
    compression_range_atr: float = 1.25
    chase_threshold_atr: float = 1.0
    trigger_proximity_atr: float = 0.75
    minimum_structural_risk_reward: float = 1.50
    opposing_level_buffer_atr: float = 0.35
    atr_period: int = 14
    gap_detection_pct: float = 0.20
    quality_a_score: int = 85
    quality_b_score: int = 75
    score_rvol_baseline: float = 0.70
    score_level_proximity_pct: float = 0.35
    trend_pullback_tolerance_atr: float = 0.25
    momentum_pause_range_atr: float = 0.75
    level_testing_atr: float = 0.15
    level_approaching_atr: float = 0.50

    def as_dict(self) -> dict[str, int | float]:
        return asdict(self)


DEFAULT_PARAMETERS = EngineParameters()
