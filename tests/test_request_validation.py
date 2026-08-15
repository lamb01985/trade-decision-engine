import pytest
from pydantic import ValidationError

from app.models import MarketScanRequest


@pytest.mark.parametrize(
    "payload",
    [
        {"minimum_price": 0},
        {"minimum_price": -0.01},
        {"minimum_average_dollar_volume": -1},
        {"minimum_score": -1},
        {"minimum_score": 101},
    ],
)
def test_market_scanner_request_rejects_invalid_risk_thresholds(payload):
    with pytest.raises(ValidationError):
        MarketScanRequest(**payload)


def test_market_scanner_request_accepts_valid_risk_thresholds():
    request = MarketScanRequest(
        minimum_price=5.0,
        minimum_average_dollar_volume=25_000_000,
        minimum_score=75,
    )

    assert request.minimum_price == 5.0
    assert request.minimum_average_dollar_volume == 25_000_000
    assert request.minimum_score == 75
