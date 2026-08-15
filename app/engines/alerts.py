from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.models import (ChaseStatus, MarketAlert, MarketAlertType, MarketSetupCandidate,
                        SetupState, SymbolAnalysis)


@dataclass(frozen=True)
class ScannerPolicy:
    minimum_price: float = 5.0
    minimum_average_dollar_volume: float = 25_000_000
    minimum_score: int = 75
    radar_distance_pct: float = 0.75
    minimum_risk_reward: float = 1.5
    too_late_pct: float = 0.75
    cooldown_minutes: int = 30


def quality(score: int) -> str:
    return "A" if score >= 85 else "B" if score >= 75 else "C" if score >= 65 else "D"


def candidate_from_analysis(result: SymbolAnalysis, policy: ScannerPolicy,
                            now: datetime | None = None,
                            allow_closed: bool = False) -> tuple[MarketSetupCandidate | None, str | None]:
    now = now or datetime.now(timezone.utc)
    instrument, setup, plan = result.instrument, result.setup, result.trade_plan
    closed_preparation = allow_closed and result.data_status == "MARKET_CLOSED" and result.market_session == "CLOSED"
    if not closed_preparation and (result.data_status not in {"UNKNOWN", "LIVE"} or result.market_session not in {"UNKNOWN", "REGULAR"}):
        return None, f"DATA_{result.data_status}"
    price = instrument.timeframes["1m"].price
    adv = instrument.average_dollar_volume
    if price < policy.minimum_price or adv is None or adv < policy.minimum_average_dollar_volume:
        return None, "ILLIQUID"
    decision = result.decision
    if decision is not None:
        if decision.quality.value in {"C", "NO_TRADE"}:
            return None, "LOW_QUALITY"
        if decision.setup_state.value == "TOO_LATE":
            return None, "TOO_LATE"
        if decision.action_now.value == "NO_TRADE":
            return None, "NO_TRADE"
        if any(x is None for x in (decision.trigger, decision.invalidation, decision.target_1,
                                   decision.target_2, decision.structural_risk_reward)):
            return None, "INSUFFICIENT_STRUCTURE"
        confirmed = decision.setup_state.value == "CONFIRMED"
        status = (SetupState.LONG_CONFIRMED if decision.direction == "LONG" else SetupState.SHORT_CONFIRMED) if confirmed else (SetupState.WATCH_LONG if decision.direction == "LONG" else SetupState.WATCH_SHORT)
        signed_distance = decision.trigger-price if decision.direction == "LONG" else price-decision.trigger
        distance_pct = signed_distance/price*100
        if not confirmed and abs(distance_pct) > policy.radar_distance_pct:
            return None, "TOO_FAR"
        return MarketSetupCandidate(symbol=instrument.symbol, direction=decision.direction,
            setup=decision.setup_family.value, quality=decision.quality.value,
            setup_score=result.setup_score.score, status=status, current_price=price,
            trigger=decision.trigger, distance_to_trigger=signed_distance,
            distance_to_trigger_pct=distance_pct, invalidation=decision.invalidation,
            target_1=decision.target_1, target_2=decision.target_2,
            risk_reward=decision.structural_risk_reward,
            chase_status=ChaseStatus.ACTIONABLE if confirmed else ChaseStatus.NOT_TRIGGERED,
            nearest_support=decision.nearest_support, nearest_resistance=decision.nearest_resistance,
            average_dollar_volume=adv, reasons=decision.reasoning + decision.missing_confirmation,
            timestamp=now), None
    if setup.state == SetupState.NO_TRADE or result.setup_score.score < policy.minimum_score:
        return None, "LOW_QUALITY"
    if any(x is None for x in (plan.trigger, plan.invalidation, plan.target_1, plan.target_2, plan.risk_reward)):
        return None, "INSUFFICIENT_STRUCTURE"
    if plan.risk_reward < policy.minimum_risk_reward:
        return None, "POOR_RISK_REWARD"
    direction = "LONG" if setup.state in {SetupState.WATCH_LONG, SetupState.LONG_CONFIRMED} else "SHORT"
    signed_distance = plan.trigger - price if direction == "LONG" else price - plan.trigger
    distance_pct = signed_distance / price * 100
    risk = abs(plan.trigger - plan.invalidation)
    extension = max(0.0, -signed_distance)
    chase = ChaseStatus.NOT_TRIGGERED
    if setup.trigger_occurred:
        chase = ChaseStatus.TOO_LATE if extension/price*100 > policy.too_late_pct or extension > risk else ChaseStatus.ACTIONABLE
    if chase == ChaseStatus.TOO_LATE:
        return None, "TOO_LATE"
    if not setup.trigger_occurred and abs(distance_pct) > policy.radar_distance_pct:
        return None, "TOO_FAR"
    # Target 1 must offer space beyond entry and cannot sit on the wrong side of the trade.
    target_space = plan.target_1 - plan.trigger if direction == "LONG" else plan.trigger - plan.target_1
    if target_space <= 0:
        return None, "OPPOSING_STRUCTURE"
    supports = [x.price for x in result.levels if x.price < price]
    resistances = [x.price for x in result.levels if x.price > price]
    reasons = list(setup.explanation) + [f"Score {result.setup_score.score}/100 ({quality(result.setup_score.score)})",
        f"Trigger distance {distance_pct:.2f}%", f"Risk/reward {plan.risk_reward:.2f}"]
    return MarketSetupCandidate(symbol=instrument.symbol, direction=direction, setup=setup.setup_type,
        quality=quality(result.setup_score.score), setup_score=result.setup_score.score, status=setup.state,
        current_price=price, trigger=plan.trigger, distance_to_trigger=signed_distance,
        distance_to_trigger_pct=distance_pct, invalidation=plan.invalidation, target_1=plan.target_1,
        target_2=plan.target_2, risk_reward=plan.risk_reward, chase_status=chase,
        nearest_support=max(supports, default=None), nearest_resistance=min(resistances, default=None),
        average_dollar_volume=adv, reasons=reasons, timestamp=now), None


class AlertEngine:
    """Stateful transition detector; deliberately independent of notification delivery."""
    def __init__(self, policy: ScannerPolicy | None = None) -> None:
        self.policy = policy or ScannerPolicy()
        self._states: dict[str, tuple[str, tuple, datetime, float, int]] = {}

    @staticmethod
    def _fingerprint(candidate: MarketSetupCandidate) -> tuple:
        return (candidate.direction, candidate.setup, round(candidate.trigger, 4),
                round(candidate.invalidation, 4), round(candidate.target_1, 4),
                round(candidate.target_2, 4), round(candidate.risk_reward, 2))

    def evaluate(self, analyses: list[SymbolAnalysis], now: datetime | None = None) -> tuple[list[MarketSetupCandidate], list[MarketAlert], int]:
        now = now or datetime.now(timezone.utc)
        candidates, alerts, filtered = [], [], 0
        seen = set()
        for analysis in analyses:
            symbol = analysis.instrument.symbol
            seen.add(symbol)
            candidate, rejection = candidate_from_analysis(analysis, self.policy, now)
            previous = self._states.get(symbol)
            if candidate is None:
                if rejection == "ILLIQUID": filtered += 1
                if previous and previous[0] in {"RADAR", "TRIGGERED"}:
                    current_price = analysis.instrument.timeframes["1m"].price
                    alerts.append(self._alert_from_previous(symbol, previous, current_price, rejection, now))
                    self._states.pop(symbol, None)
                continue
            state = "TRIGGERED" if candidate.status in {SetupState.LONG_CONFIRMED, SetupState.SHORT_CONFIRMED} else "RADAR"
            fingerprint = self._fingerprint(candidate)
            changed = not previous or previous[0] != state or previous[1] != fingerprint
            cooldown = previous and now - previous[2] >= timedelta(minutes=self.policy.cooldown_minutes)
            meaningful = previous and (abs(candidate.current_price-previous[3])/previous[3] >= .0025 or abs(candidate.setup_score-previous[4]) >= 5)
            if changed or (cooldown and meaningful):
                alerts.append(self._alert(candidate, MarketAlertType(state), now))
                self._states[symbol] = (state, fingerprint, now, candidate.current_price, candidate.setup_score)
            candidates.append(candidate)
        candidates.sort(key=lambda x: (x.status in {SetupState.LONG_CONFIRMED, SetupState.SHORT_CONFIRMED}, x.setup_score, x.risk_reward, -abs(x.distance_to_trigger_pct)), reverse=True)
        return candidates, alerts, filtered

    @staticmethod
    def _alert(candidate: MarketSetupCandidate, alert_type: MarketAlertType, now: datetime) -> MarketAlert:
        return MarketAlert(symbol=candidate.symbol, alert_type=alert_type, direction=candidate.direction,
            setup=candidate.setup, quality=candidate.quality, status=candidate.status,
            action_now="CONFIRMED — REVALIDATE RISK" if alert_type == MarketAlertType.TRIGGERED else "WAIT",
            current_price=candidate.current_price, trigger=candidate.trigger,
            distance_to_trigger=candidate.distance_to_trigger, invalidation=candidate.invalidation,
            target_1=candidate.target_1, target_2=candidate.target_2, risk_reward=candidate.risk_reward,
            reasons=candidate.reasons, timestamp=now)

    @staticmethod
    def _alert_from_previous(symbol: str, previous: tuple, current_price: float,
                             reason: str | None, now: datetime) -> MarketAlert:
        state, fingerprint, _, _, _ = previous
        direction, setup, trigger, invalidation, target_1, target_2, risk_reward = fingerprint
        alert_type = MarketAlertType.TOO_LATE if reason == "TOO_LATE" else MarketAlertType.INVALIDATED
        return MarketAlert(symbol=symbol, alert_type=alert_type, direction=direction,
            setup=setup, quality="N/A", status=SetupState.NO_TRADE, action_now="DO NOT ENTER",
            current_price=current_price, trigger=trigger, distance_to_trigger=trigger-current_price,
            invalidation=invalidation, target_1=target_1, target_2=target_2, risk_reward=risk_reward,
            reasons=[f"Previously active {state} setup no longer meets deterministic requirements",
                     f"State-change reason: {reason or 'STATE_CHANGED'}"], timestamp=now)
