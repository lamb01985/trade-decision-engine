from datetime import datetime, timezone

import pytest

from app.engines.indicators import ema, session_vwap
from app.models import Bar


def bar(high, low, close, volume):
    return Bar(timestamp=datetime.now(timezone.utc), open=close, high=high, low=low, close=close, volume=volume)


def test_vwap_uses_typical_price_weighted_by_volume():
    bars = [bar(11, 9, 10, 100), bar(13, 11, 12, 300)]
    assert session_vwap(bars) == pytest.approx(11.5)


def test_vwap_rejects_zero_volume():
    with pytest.raises(ValueError):
        session_vwap([bar(1, 1, 1, 0)])


def test_ema_calculation():
    assert ema([10, 12, 14], 3) == pytest.approx([10, 11, 12.5])

