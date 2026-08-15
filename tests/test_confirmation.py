from app.engines.decision import cross_market_confirmation
from app.engines.market import unavailable_nq
from app.models import ConfirmationStatus, Trend
from tests.helpers import instrument


def test_unavailable_nq_caps_bullish_confirmation_at_partial():
    result = cross_market_confirmation(instrument(), unavailable_nq())
    assert result.confirmation_status == ConfirmationStatus.PARTIAL_BULLISH_CONFIRMATION
    assert result.nq_5m_trend is None


def test_all_markets_aligned_is_strong_confirmation():
    result = cross_market_confirmation(instrument(), instrument())
    assert result.confirmation_status == ConfirmationStatus.STRONG_BULLISH_CONFIRMATION


def test_cross_market_disagreement_is_neutral():
    result = cross_market_confirmation(instrument(), instrument(Trend.BEARISH, 90, "BELOW"))
    assert result.confirmation_status == ConfirmationStatus.NEUTRAL

