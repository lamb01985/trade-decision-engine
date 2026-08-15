from datetime import datetime, timedelta, timezone

from app.engines.playbook import build_level_playbooks
from app.models import Bar, LevelActionStatus, LevelImportance, PriceLevel


def level(price):
    return PriceLevel(price=price, types=["5M_SWING_HIGH"], importance=LevelImportance.RESISTANCE,
        distance=price-101, distance_pct=(price-101)/101*100,
        position="ABOVE" if price > 101 else "BELOW", confluence=1)


def completed_behavior_bars():
    start = datetime(2026, 8, 14, 14, 4, tzinfo=timezone.utc)
    values = [(99.8, 100.1, 99.7, 99.9), (100.2, 100.7, 100.1, 100.5), (100.4, 101.2, 100.05, 101.0)]
    return [Bar(timestamp=start+timedelta(minutes=i*5), open=o, high=h, low=l, close=c, volume=2000)
            for i, (o, h, l, c) in enumerate(values)]


def test_level_playbook_requires_close_and_retest_for_confirmation():
    plans = build_level_playbooks([level(99), level(100), level(102), level(104)], completed_behavior_bars(), 101, 1.5)
    plan = next(x for x in plans if x.level_price == 100)
    assert plan.status == LevelActionStatus.LONG_CONFIRMED
    assert plan.entry == 101
    assert (plan.long_target_1, plan.long_target_2) == (102, 104)
    assert plan.risk_reward == 1


def test_touch_without_volume_does_not_confirm_and_targets_are_not_fabricated():
    plans = build_level_playbooks([level(100), level(102)], completed_behavior_bars(), 101, .8)
    lower = next(x for x in plans if x.level_price == 100)
    upper = next(x for x in plans if x.level_price == 102)
    assert lower.status != LevelActionStatus.LONG_CONFIRMED
    assert lower.entry is None
    assert upper.long_target_1 is None
    assert "long targets insufficient" in upper.structure_status
