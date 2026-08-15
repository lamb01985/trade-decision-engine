from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from app.market_data.base import MarketDataError
from app.market_data.base import OptionsDataProvider
from app.freshness import classify_data
from app.models import (
    ActionNow,
    ContractFitCandidate,
    ContractFitCriteria,
    ContractQuality,
    ContractQualityAssessment,
    DecisionQuality,
    OptionContractInsight,
    OptionsIntelligenceResult,
    OptionsPlanCriteria,
    OptionsPlanResult,
    OptionsRecommendationState,
    OptionsShortlistResult,
    SetupProgress,
    SymbolAnalysis,
    TimeAssessment,
    UnderlyingEligibilityAssessment,
    UnderlyingEligibilityState,
)

ET = ZoneInfo("America/New_York")


def _timestamp(value: int | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromtimestamp(value / 1_000_000_000, tz=timezone.utc)


def _first_number(*values: object) -> float | None:
    for value in values:
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _underlying_price(snapshot: dict) -> float | None:
    trade = snapshot.get("lastTrade") or {}
    quote = snapshot.get("lastQuote") or {}
    minute = snapshot.get("min") or {}
    day = snapshot.get("day") or {}
    quote_mid = None
    if isinstance(quote.get("p"), (int, float)) and isinstance(quote.get("P"), (int, float)):
        quote_mid = (quote["p"] + quote["P"]) / 2
    return _first_number(trade.get("p"), quote_mid, minute.get("c"), day.get("c"))


def _contract_insight(item: dict) -> OptionContractInsight:
    details = item.get("details") or {}
    quote = item.get("last_quote") or {}
    trade = item.get("last_trade") or {}
    greeks = item.get("greeks") or {}
    day = item.get("day") or {}
    bid = _first_number(quote.get("bid"))
    ask = _first_number(quote.get("ask"))
    midpoint = _first_number(quote.get("midpoint"))
    if midpoint is None and bid is not None and ask is not None:
        midpoint = (bid + ask) / 2
    spread = ask - bid if ask is not None and bid is not None and ask >= bid else None
    spread_pct = spread / midpoint * 100 if spread is not None and midpoint and midpoint > 0 else None
    volume = int(day["volume"]) if isinstance(day.get("volume"), (int, float)) else None
    open_interest = int(item["open_interest"]) if isinstance(item.get("open_interest"), (int, float)) else None
    notes = []
    if bid is None or ask is None:
        notes.append("Quote unavailable")
    if not greeks:
        notes.append("Greeks unavailable")
    if open_interest is None:
        notes.append("Open interest unavailable")
    return OptionContractInsight(
        ticker=details.get("ticker", ""),
        contract_type=details.get("contract_type", "other").upper(),
        expiration_date=details.get("expiration_date", ""),
        strike_price=float(details.get("strike_price", 0)),
        bid=bid, ask=ask, midpoint=midpoint, spread=spread, spread_pct=spread_pct,
        last_trade_price=_first_number(trade.get("price")),
        last_trade_timestamp=_timestamp(trade.get("sip_timestamp")),
        volume=volume, open_interest=open_interest,
        volume_open_interest_ratio=(volume / open_interest if volume is not None and open_interest and open_interest > 0 else None),
        implied_volatility=_first_number(item.get("implied_volatility")),
        delta=_first_number(greeks.get("delta")), gamma=_first_number(greeks.get("gamma")),
        theta=_first_number(greeks.get("theta")), vega=_first_number(greeks.get("vega")),
        quote_timeframe=quote.get("timeframe"), trade_timeframe=trade.get("timeframe"),
        data_notes=notes,
    )


async def load_options_intelligence(provider: OptionsDataProvider, symbol: str,
                                    expiration: str | None = None, strike_window_pct: float = 10.0,
                                    as_of: date | None = None) -> OptionsIntelligenceResult:
    """Load provider facts for an option chain; no setup, signal, or trade logic is invoked."""
    underlying = symbol.strip().upper()
    if not underlying or not underlying.replace(".", "").replace("-", "").isalnum():
        raise ValueError("Underlying symbol must contain letters, numbers, dots, or hyphens")
    window_pct = min(50.0, max(1.0, float(strike_window_pct)))
    today = as_of or datetime.now(ET).date()
    snapshot, contract_index = await provider.get_stock_snapshot(underlying), await provider.get_active_option_contracts(underlying, today)
    expirations = sorted({item["expiration_date"] for item in contract_index if item["expiration_date"] >= today.isoformat()})
    if not expirations:
        raise MarketDataError(f"No unexpired options contracts returned for {underlying}")
    selected = expiration if expiration in expirations else expirations[0]
    price = _underlying_price(snapshot)
    if price is None or price <= 0:
        raise MarketDataError(f"No usable underlying price returned for {underlying}")
    low, high = price * (1 - window_pct / 100), price * (1 + window_pct / 100)
    chain = await provider.get_options_chain_snapshot(underlying, selected, low, high)
    contracts = sorted((_contract_insight(item) for item in chain if (item.get("details") or {}).get("ticker")),
                       key=lambda item: (item.strike_price, item.contract_type, item.ticker))
    underlying_timestamp = _timestamp(snapshot.get("updated"))
    freshness, session, age = classify_data(underlying_timestamp)
    quote_status = ("REAL_TIME" if contracts and all(item.quote_timeframe == "REAL-TIME" for item in contracts)
                    else "AVAILABLE" if contracts else "UNAVAILABLE")
    data_status = "MARKET_CLOSED_LATEST_SESSION" if freshness.value == "MARKET_CLOSED" else quote_status
    return OptionsIntelligenceResult(
        underlying_symbol=underlying,
        underlying_price=price,
        underlying_change_pct=_first_number(snapshot.get("todaysChangePerc")),
        underlying_timestamp=underlying_timestamp, data_age_seconds=age, market_session=session.value,
        expirations=expirations[:24], selected_expiration=selected,
        strike_window_pct=window_pct, contracts=contracts, contracts_returned=len(contracts),
        data_status=data_status, strategy_invoked=False, execution_enabled=False,
        message=(("Market closed: quotes are latest-session research data, not live pricing. " if freshness.value == "MARKET_CLOSED" else "") +
                 "Read-only option-chain intelligence. Contract ranking is limited to display sorting; "
                 "no flow classification, trade signal, setup, target, or execution instruction is generated."),
    )


def _criteria_explanation(contract: OptionContractInsight, price: float, days: int,
                          criteria: ContractFitCriteria) -> list[str]:
    distance = abs(contract.strike_price - price) / price * 100
    return [
        f"{days} days to expiry within your {criteria.min_days_to_expiry}–{criteria.max_days_to_expiry} day horizon",
        f"absolute delta {abs(contract.delta or 0):.2f} within your {criteria.min_abs_delta:.2f}–{criteria.max_abs_delta:.2f} range",
        f"open interest {contract.open_interest:,} meets your {criteria.min_open_interest:,} minimum",
        f"spread {contract.spread_pct:.2f}% is within your {criteria.max_spread_pct:.2f}% cap",
        f"strike is {distance:.2f}% from the underlying price",
        f"quote timeframe: {contract.quote_timeframe or 'not supplied'}",
    ]


async def load_contract_shortlist(provider: OptionsDataProvider, symbol: str,
                                  criteria: ContractFitCriteria, as_of: date | None = None) -> OptionsShortlistResult:
    """Filter option-chain facts using visible user criteria; never creates a trade signal."""
    if criteria.contract_type not in {"CALL", "PUT"}:
        raise ValueError("contract_type must be CALL or PUT")
    if criteria.min_days_to_expiry > criteria.max_days_to_expiry:
        raise ValueError("Minimum horizon cannot exceed maximum horizon")
    if criteria.min_abs_delta > criteria.max_abs_delta:
        raise ValueError("Minimum delta cannot exceed maximum delta")
    underlying = symbol.strip().upper()
    if not underlying or not underlying.replace(".", "").replace("-", "").isalnum():
        raise ValueError("Underlying symbol must contain letters, numbers, dots, or hyphens")
    today = as_of or datetime.now(ET).date()
    snapshot, contract_index = await provider.get_stock_snapshot(underlying), await provider.get_active_option_contracts(underlying, today)
    price = _underlying_price(snapshot)
    if price is None or price <= 0:
        raise MarketDataError(f"No usable underlying price returned for {underlying}")
    expiration_days = []
    for item in contract_index:
        try:
            expiration = date.fromisoformat(item["expiration_date"])
        except (KeyError, TypeError, ValueError):
            continue
        days = (expiration - today).days
        if criteria.min_days_to_expiry <= days <= criteria.max_days_to_expiry:
            expiration_days.append((item["expiration_date"], days))
    selected_expirations = sorted(dict(expiration_days).items(), key=lambda item: item[0])[:criteria.max_expirations_loaded]
    if not selected_expirations:
        raise MarketDataError("No listed expirations meet the selected horizon")
    low = price * (1 - criteria.strike_window_pct / 100)
    high = price * (1 + criteria.strike_window_pct / 100)
    scanned: list[tuple[OptionContractInsight, int]] = []
    for expiration, days in selected_expirations:
        chain = await provider.get_options_chain_snapshot(underlying, expiration, low, high)
        scanned.extend((_contract_insight(item), days) for item in chain if (item.get("details") or {}).get("ticker"))
    candidates = []
    for contract, days in scanned:
        absolute_delta = abs(contract.delta) if contract.delta is not None else None
        if (contract.contract_type != criteria.contract_type or absolute_delta is None or
                not criteria.min_abs_delta <= absolute_delta <= criteria.max_abs_delta or
                contract.open_interest is None or contract.open_interest < criteria.min_open_interest or
                contract.spread_pct is None or contract.spread_pct > criteria.max_spread_pct):
            continue
        candidates.append(ContractFitCandidate(
            contract=contract,
            days_to_expiry=days,
            distance_from_underlying_pct=abs(contract.strike_price - price) / price * 100,
            explanation=_criteria_explanation(contract, price, days, criteria),
        ))
    midpoint_delta = (criteria.min_abs_delta + criteria.max_abs_delta) / 2
    candidates.sort(key=lambda item: (
        item.contract.spread_pct if item.contract.spread_pct is not None else float("inf"),
        -(item.contract.open_interest or 0),
        abs(abs(item.contract.delta or 0) - midpoint_delta),
        item.days_to_expiry,
        item.distance_from_underlying_pct,
        item.contract.ticker,
    ))
    timeframes = {item.contract.quote_timeframe for item in candidates if item.contract.quote_timeframe}
    return OptionsShortlistResult(
        underlying_symbol=underlying,
        underlying_price=price,
        criteria=criteria,
        expirations_considered=[item[0] for item in selected_expirations],
        contracts_scanned=len(scanned),
        candidates=candidates[:12],
        sort_order="Tightest displayed spread, then highest open interest, then closest to your selected delta midpoint",
        data_status="REAL_TIME" if candidates and timeframes == {"REAL-TIME"} else "AVAILABLE" if candidates else "NO_MATCHES",
        strategy_invoked=False,
        execution_enabled=False,
        message=("This is a contract-fit filter using only the criteria you selected. It does not validate an underlying thesis, "
                 "classify flow, predict an option outcome, or create a trade recommendation."),
    )


_ACTIONABLE_QUALITY = {DecisionQuality.A, DecisionQuality.B}
_EXPECTED_CONTRACT_TYPE = {"LONG": "CALL", "SHORT": "PUT"}


def assess_underlying_eligibility(analysis: SymbolAnalysis) -> UnderlyingEligibilityAssessment:
    """Read the existing StandardDecision without creating or changing a setup signal."""
    decision = analysis.decision
    if decision is None:
        return UnderlyingEligibilityAssessment(
            state=UnderlyingEligibilityState.INSUFFICIENT_DATA,
            eligible_for_contract_evaluation=False,
            reasons=["The existing underlying decision was unavailable; no contract can be evaluated as an expression of it."],
        )
    base = {
        "action_now": decision.action_now,
        "setup_quality": decision.quality,
        "setup_family": decision.setup_family,
        "setup_state": decision.setup_state,
        "direction": decision.direction,
        "market_price": decision.market_price,
        "trigger": decision.trigger,
        "invalidation": decision.invalidation,
        "target_1": decision.target_1,
        "target_2": decision.target_2,
        "decision_timestamp": decision.timestamp,
        "provider_timestamp": getattr(analysis, "provider_timestamp", None),
        "analysis_timestamp": getattr(analysis, "analysis_timestamp", None),
        "data_age_seconds": getattr(analysis, "data_age_seconds", None),
        "data_status": getattr(analysis, "data_status", "UNKNOWN"),
        "market_session": getattr(analysis, "market_session", "UNKNOWN"),
    }
    closed_preparation = base["data_status"] == "MARKET_CLOSED" and base["market_session"] == "CLOSED"
    if not closed_preparation and (base["data_status"] not in {"UNKNOWN", "LIVE"} or base["market_session"] not in {"UNKNOWN", "REGULAR"}):
        return UnderlyingEligibilityAssessment(
            state=UnderlyingEligibilityState.INSUFFICIENT_DATA,
            eligible_for_contract_evaluation=False,
            reasons=[f"Underlying market data is {base['data_status']} during {base['market_session']}; options evaluation is non-actionable."],
            **base,
        )
    if decision.action_now == ActionNow.DO_NOT_CHASE or decision.setup_state == SetupProgress.TOO_LATE:
        return UnderlyingEligibilityAssessment(
            state=UnderlyingEligibilityState.DO_NOT_CHASE,
            eligible_for_contract_evaluation=False,
            reasons=["The frozen underlying engine reports DO NOT CHASE; an option contract cannot repair a late entry."],
            **base,
        )
    expected_type = _EXPECTED_CONTRACT_TYPE.get(decision.direction)
    if (decision.action_now == ActionNow.NO_TRADE or decision.setup_state in {SetupProgress.NO_SETUP, SetupProgress.INVALIDATED}
            or decision.quality not in _ACTIONABLE_QUALITY or expected_type is None):
        return UnderlyingEligibilityAssessment(
            state=UnderlyingEligibilityState.PASS_UNDERLYING_SETUP,
            eligible_for_contract_evaluation=False,
            reasons=["The frozen underlying engine is not in an eligible A/B long or short setup state; no options recommendation is produced."],
            **base,
        )
    required_levels = (decision.trigger, decision.invalidation, decision.target_1, decision.target_2)
    if any(level is None for level in required_levels):
        return UnderlyingEligibilityAssessment(
            state=UnderlyingEligibilityState.INSUFFICIENT_DATA,
            eligible_for_contract_evaluation=False,
            expected_contract_type=expected_type,
            reasons=["The underlying plan is missing a trigger, invalidation, or mapped target; contract evaluation is withheld."],
            **base,
        )
    if closed_preparation:
        return UnderlyingEligibilityAssessment(
            state=UnderlyingEligibilityState.WAIT_FOR_TRIGGER,
            eligible_for_contract_evaluation=True,
            expected_contract_type=expected_type,
            reasons=["Market closed: contract research may use latest-session data, but fresh live underlying analysis must revalidate the setup before entry."],
            **base,
        )
    if decision.action_now in {ActionNow.LONG_TRIGGERED, ActionNow.SHORT_TRIGGERED}:
        return UnderlyingEligibilityAssessment(
            state=UnderlyingEligibilityState.CONFIRMED,
            eligible_for_contract_evaluation=True,
            expected_contract_type=expected_type,
            reasons=["The existing underlying engine reports a confirmed A/B setup. This only permits contract evaluation; execution remains disabled."],
            **base,
        )
    if decision.action_now in {ActionNow.PREPARE_LONG, ActionNow.PREPARE_SHORT, ActionNow.WAIT}:
        return UnderlyingEligibilityAssessment(
            state=UnderlyingEligibilityState.WAIT_FOR_TRIGGER,
            eligible_for_contract_evaluation=True,
            expected_contract_type=expected_type,
            reasons=["The existing underlying engine is awaiting confirmation. Contracts may be reviewed, but no entry is authorized before the underlying trigger."],
            **base,
        )
    return UnderlyingEligibilityAssessment(
        state=UnderlyingEligibilityState.INSUFFICIENT_DATA,
        eligible_for_contract_evaluation=False,
        expected_contract_type=expected_type,
        reasons=["The existing underlying action could not be mapped to a safe options-plan state."],
        **base,
    )


def _time_assessment(days_to_expiry: int, criteria: OptionsPlanCriteria) -> TimeAssessment:
    if days_to_expiry < 0:
        return TimeAssessment.INSUFFICIENT_DATA
    if days_to_expiry <= 1:
        return TimeAssessment.DANGEROUS
    if days_to_expiry < criteria.min_days_to_expiry:
        return TimeAssessment.TIGHT
    if days_to_expiry <= criteria.max_days_to_expiry:
        return TimeAssessment.GOOD
    return TimeAssessment.PLENTY


def assess_contract_quality(contract: OptionContractInsight, days_to_expiry: int,
                            expected_contract_type: str, criteria: OptionsPlanCriteria) -> ContractQualityAssessment:
    """Assess only provider-returned contract facts using visible Phase 1 criteria."""
    reasons: list[str] = []
    missing: list[str] = []
    time = _time_assessment(days_to_expiry, criteria)
    if contract.contract_type != expected_contract_type:
        reasons.append(f"Contract type {contract.contract_type} does not match the underlying direction's required {expected_contract_type} exposure.")
    if contract.midpoint is None or contract.spread_pct is None:
        missing.append("usable bid/ask spread")
    if contract.delta is None:
        missing.append("delta")
    if contract.open_interest is None:
        missing.append("open interest")
    if missing:
        return ContractQualityAssessment(
            contract=contract, days_to_expiry=days_to_expiry, time_assessment=time,
            quality=ContractQuality.INSUFFICIENT_DATA, passes_hard_criteria=False,
            reasons=reasons + ["Required contract-quality fields are unavailable; the contract is not ranked as a recommendation."],
            missing_data=missing,
        )
    absolute_delta = abs(contract.delta)
    criteria_failures = []
    if contract.contract_type != expected_contract_type:
        criteria_failures.append(f"wrong contract type (need {expected_contract_type})")
    if time not in {TimeAssessment.GOOD, TimeAssessment.PLENTY}:
        criteria_failures.append(f"time assessment is {time.value}")
    if not criteria.min_abs_delta <= absolute_delta <= criteria.max_abs_delta:
        criteria_failures.append(f"absolute delta {absolute_delta:.2f} is outside {criteria.min_abs_delta:.2f}–{criteria.max_abs_delta:.2f}")
    if contract.open_interest < criteria.min_open_interest:
        criteria_failures.append(f"open interest {contract.open_interest:,} is below {criteria.min_open_interest:,}")
    if contract.spread_pct > criteria.max_spread_pct:
        criteria_failures.append(f"spread {contract.spread_pct:.2f}% exceeds {criteria.max_spread_pct:.2f}%")
    if criteria_failures:
        return ContractQualityAssessment(
            contract=contract, days_to_expiry=days_to_expiry, time_assessment=time,
            quality=ContractQuality.POOR, passes_hard_criteria=False,
            reasons=reasons + criteria_failures, missing_data=[],
        )
    midpoint_delta = (criteria.min_abs_delta + criteria.max_abs_delta) / 2
    near_delta_midpoint = abs(absolute_delta - midpoint_delta) <= 0.08
    exceptional_liquidity = contract.open_interest >= criteria.min_open_interest * 5 and (contract.volume or 0) > 0
    tight_spread = contract.spread_pct <= criteria.max_spread_pct * 0.30
    if exceptional_liquidity and tight_spread and near_delta_midpoint:
        quality = ContractQuality.EXCELLENT
    elif contract.open_interest >= criteria.min_open_interest * 2 and contract.spread_pct <= criteria.max_spread_pct * 0.60:
        quality = ContractQuality.GOOD
    else:
        quality = ContractQuality.ACCEPTABLE
    reasons.extend([
        f"{days_to_expiry} days to expiry is assessed as {time.value} for the configured {criteria.min_days_to_expiry}–{criteria.max_days_to_expiry} day working horizon.",
        f"Absolute delta {absolute_delta:.2f} is inside the configured {criteria.min_abs_delta:.2f}–{criteria.max_abs_delta:.2f} range.",
        f"Displayed spread {contract.spread_pct:.2f}% is within the {criteria.max_spread_pct:.2f}% cap.",
        f"Open interest {contract.open_interest:,} meets the {criteria.min_open_interest:,} minimum.",
    ])
    if contract.volume is None:
        reasons.append("Session volume was not returned by the provider; the contract cannot receive an EXCELLENT liquidity classification.")
    elif contract.volume == 0:
        reasons.append("Session volume is zero; this does not fail the visible criteria, but it prevents an EXCELLENT liquidity classification.")
    return ContractQualityAssessment(
        contract=contract, days_to_expiry=days_to_expiry, time_assessment=time,
        quality=quality, passes_hard_criteria=True, reasons=reasons, missing_data=[],
    )


def _quality_rank(assessment: ContractQualityAssessment) -> int:
    return {
        ContractQuality.EXCELLENT: 0,
        ContractQuality.GOOD: 1,
        ContractQuality.ACCEPTABLE: 2,
        ContractQuality.POOR: 3,
        ContractQuality.INSUFFICIENT_DATA: 4,
    }[assessment.quality]


def _rejection_summary(assessments: list[ContractQualityAssessment]) -> list[str]:
    counts: dict[str, int] = {}
    for assessment in assessments:
        for reason in assessment.reasons:
            counts[reason] = counts.get(reason, 0) + 1
        for field in assessment.missing_data:
            label = f"Missing {field}"
            counts[label] = counts.get(label, 0) + 1
    return [f"{count} contract(s): {reason}" for reason, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:5]]


async def load_options_plan(provider: OptionsDataProvider, analysis: SymbolAnalysis,
                            criteria: OptionsPlanCriteria, as_of: date | None = None) -> OptionsPlanResult:
    """Build a downstream, read-only options plan from an already-produced frozen underlying decision."""
    if criteria.min_days_to_expiry > criteria.max_days_to_expiry:
        raise ValueError("Minimum horizon cannot exceed maximum horizon")
    if criteria.min_abs_delta > criteria.max_abs_delta:
        raise ValueError("Minimum delta cannot exceed maximum delta")
    eligibility = assess_underlying_eligibility(analysis)
    common = {
        "underlying_symbol": analysis.instrument.symbol,
        "underlying_eligibility": eligibility,
        "criteria": criteria,
        "price_iv_explanation": "INSUFFICIENT_DATA: Phase 1 has no validated volatility history, surface, realized-volatility series, rate/dividend assumptions, or pricing model. It does not label an option cheap, fair, or expensive.",
        "provider_timestamp": getattr(analysis, "provider_timestamp", None),
        "analysis_timestamp": getattr(analysis, "analysis_timestamp", None),
        "data_age_seconds": getattr(analysis, "data_age_seconds", None),
        "market_session": getattr(analysis, "market_session", "UNKNOWN"),
    }
    if not eligibility.eligible_for_contract_evaluation:
        recommendation = {
            UnderlyingEligibilityState.DO_NOT_CHASE: OptionsRecommendationState.TOO_LATE_DO_NOT_CHASE,
            UnderlyingEligibilityState.PASS_UNDERLYING_SETUP: OptionsRecommendationState.PASS_UNDERLYING_SETUP_TOO_WEAK,
        }.get(eligibility.state, OptionsRecommendationState.NOT_ENOUGH_DATA)
        headline = {
            OptionsRecommendationState.TOO_LATE_DO_NOT_CHASE: "TOO LATE — DO NOT CHASE",
            OptionsRecommendationState.PASS_UNDERLYING_SETUP_TOO_WEAK: "PASS — UNDERLYING SETUP TOO WEAK",
        }.get(recommendation, "NOT ENOUGH DATA")
        return OptionsPlanResult(
            recommendation=recommendation, headline=headline,
            plain_english_action="Do not select an option contract. The existing underlying decision does not permit a downstream options evaluation.",
            data_status=(f"UNDERLYING_{getattr(analysis, 'data_status', 'UNKNOWN')}" if getattr(analysis, "data_status", "UNKNOWN") not in {"UNKNOWN", "LIVE"} else "NOT_REQUESTED"),
            message="Underlying veto applied before any option-chain request. The frozen strategy was read but not changed.",
            **common,
        )
    today = as_of or datetime.now(ET).date()
    snapshot, contract_index = await provider.get_stock_snapshot(analysis.instrument.symbol), await provider.get_active_option_contracts(analysis.instrument.symbol, today)
    price = _underlying_price(snapshot)
    if price is None or price <= 0:
        raise MarketDataError(f"No usable underlying price returned for {analysis.instrument.symbol}")
    expirations: list[tuple[str, int]] = []
    for item in contract_index:
        try:
            expiration = date.fromisoformat(item["expiration_date"])
        except (KeyError, TypeError, ValueError):
            continue
        days = (expiration - today).days
        if 0 <= days <= criteria.max_days_to_expiry:
            expirations.append((item["expiration_date"], days))
    selected_expirations = sorted(dict(expirations).items(), key=lambda item: item[0])[:criteria.max_expirations_loaded]
    if not selected_expirations:
        return OptionsPlanResult(
            recommendation=OptionsRecommendationState.NOT_ENOUGH_DATA, headline="NOT ENOUGH DATA",
            plain_english_action="No listed option expiration is available inside the configured working horizon.",
            data_status="NO_EXPIRATIONS", message="The underlying passed the veto, but provider reference data returned no usable expiration.",
            **common,
        )
    low, high = price * (1 - criteria.strike_window_pct / 100), price * (1 + criteria.strike_window_pct / 100)
    assessments: list[ContractQualityAssessment] = []
    for expiration, days in selected_expirations:
        chain = await provider.get_options_chain_snapshot(analysis.instrument.symbol, expiration, low, high)
        assessments.extend(assess_contract_quality(_contract_insight(item), days, eligibility.expected_contract_type or "", criteria)
                           for item in chain if (item.get("details") or {}).get("ticker"))
    passing = [item for item in assessments if item.passes_hard_criteria]
    midpoint_delta = (criteria.min_abs_delta + criteria.max_abs_delta) / 2
    passing.sort(key=lambda item: (
        _quality_rank(item), item.contract.spread_pct if item.contract.spread_pct is not None else float("inf"),
        -(item.contract.open_interest or 0), abs(abs(item.contract.delta or 0) - midpoint_delta),
        item.days_to_expiry, item.contract.ticker,
    ))
    best, alternative = (passing[0], passing[1] if len(passing) > 1 else None) if passing else (None, None)
    closed_preparation = (getattr(analysis, "data_status", "UNKNOWN") == "MARKET_CLOSED"
                          and getattr(analysis, "market_session", "UNKNOWN") == "CLOSED")
    data_status = ("MARKET_CLOSED_LATEST_SESSION" if closed_preparation else
                   "REAL_TIME" if assessments and all(item.contract.quote_timeframe == "REAL-TIME" for item in assessments)
                   else "AVAILABLE" if assessments else "NO_CONTRACTS")
    if best:
        if eligibility.state == UnderlyingEligibilityState.CONFIRMED:
            recommendation, headline = OptionsRecommendationState.GOOD_TRADE_CONFIRMED, "GOOD TRADE — CONFIRMED"
            action = "The existing underlying trigger is confirmed. Review the displayed contract facts and your own risk limits; execution is not enabled."
        else:
            recommendation, headline = OptionsRecommendationState.WAIT_FOR_UNDERLYING_TRIGGER, "WAIT FOR UNDERLYING TRIGGER"
            action = "A contract passed the visible quality criteria, but wait for the existing underlying trigger before considering any entry."
    else:
        liquidity_or_quote_failures = any(
            any("spread" in reason.lower() or "open interest" in reason.lower() for reason in item.reasons)
            or any(field in {"usable bid/ask spread", "open interest"} for field in item.missing_data)
            for item in assessments
        )
        recommendation = OptionsRecommendationState.PASS_POOR_LIQUIDITY if liquidity_or_quote_failures else OptionsRecommendationState.GOOD_SETUP_NO_GOOD_CONTRACT
        headline = "PASS — POOR LIQUIDITY" if recommendation == OptionsRecommendationState.PASS_POOR_LIQUIDITY else "GOOD SETUP — NO GOOD CONTRACT"
        action = "Do not force an options expression. No provider-returned contract passed every visible Phase 1 criterion."
    return OptionsPlanResult(
        recommendation=recommendation, headline=headline, plain_english_action=action,
        contracts_scanned=len(assessments), eligible_contracts=len(passing), best_contract=best,
        alternative_contract=alternative, rejected_contract_reasons=_rejection_summary([item for item in assessments if not item.passes_hard_criteria]),
        expirations_considered=[item[0] for item in selected_expirations], data_status=data_status,
        message="The underlying decision was reused unchanged. Contract Quality reflects only explicit Phase 1 data criteria; no options trade, flow classification, price/IV valuation, scenario outcome, or execution instruction is generated.",
        **common,
    )
