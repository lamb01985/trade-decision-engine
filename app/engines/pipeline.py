"""Single production decision pipeline shared by live analysis and historical replay."""
from dataclasses import dataclass

from app.engines.decision import build_symbol_trade_plan, evaluate_symbol_setup, score_setup
from app.engines.levels import build_levels
from app.engines.market import ET, analyze_symbol, regular_session_bars
from app.engines.setups import CandidateEvidence, build_standard_decision, detect_candidates
from app.models import Bar, InstrumentAnalysis, PriceLevel, SetupDecision, SetupScore, StandardDecision, TradePlan
from app.parameters import DEFAULT_PARAMETERS, EngineParameters


@dataclass(frozen=True)
class ProductionEvaluation:
    instrument: InstrumentAnalysis
    current_bars: list[Bar]
    levels: list[PriceLevel]
    legacy_setup: SetupDecision
    legacy_plan: TradePlan
    score: SetupScore
    decision: StandardDecision
    # Complete pre-selection detector denominator for research persistence.
    # The production decision continues to be built by the unchanged path.
    candidates: tuple[CandidateEvidence, ...] = ()


def evaluate_production(symbol: str, visible_bars: list[Bar], previous: StandardDecision | None = None,
                        parameters: EngineParameters = DEFAULT_PARAMETERS) -> ProductionEvaluation:
    """Evaluate only supplied bars. Callers own the chronological visibility boundary."""
    instrument = analyze_symbol(symbol, visible_bars)
    if not instrument.available or instrument.as_of is None:
        raise ValueError(instrument.message or f"Insufficient visible bars for {symbol}")
    current_date = instrument.as_of.astimezone(ET).date()
    current = [bar for bar in regular_session_bars(visible_bars)
               if bar.timestamp.astimezone(ET).date() == current_date]
    levels = build_levels(instrument, current, parameters)
    setup = evaluate_symbol_setup(instrument, levels)
    plan = build_symbol_trade_plan(instrument, setup, levels)
    score = score_setup(instrument, setup, levels, plan)
    decision = build_standard_decision(instrument, levels, current, score.score, previous, parameters)
    candidates = tuple(detect_candidates(instrument, levels, current, parameters))
    return ProductionEvaluation(instrument, current, levels, setup, plan, score, decision, candidates)
