from datetime import datetime, timezone

import pytest

from app.models import MarketAlert, MarketAlertType, SetupState
from app.notifications import NotificationService


class FakeProvider:
    def __init__(self, fail: bool = False):
        self.messages = []
        self.fail = fail

    async def send(self, message):
        self.messages.append(message)
        if self.fail:
            raise TimeoutError("provider timeout")

    async def close(self):
        return None


def alert(alert_type=MarketAlertType.RADAR, quality="A", symbol="NVDA"):
    ready = alert_type == MarketAlertType.TRIGGERED
    return MarketAlert(
        symbol=symbol, alert_type=alert_type, direction="SHORT", setup="BREAKDOWN_RETEST",
        quality=quality, status=SetupState.SHORT_CONFIRMED if ready else SetupState.WATCH_SHORT,
        action_now="SHORT_TRIGGERED" if ready else "WAIT", current_price=182.2,
        trigger=182.4, distance_to_trigger=-0.2, invalidation=184.1,
        target_1=179.8, target_2=176.5, risk_reward=2.1,
        reasons=["Existing engine state"], timestamp=datetime.now(timezone.utc),
    )


@pytest.mark.anyio
async def test_missing_credentials_are_disabled_and_non_fatal(tmp_path):
    service = NotificationService(None, tmp_path / "state.sqlite3")
    assert service.status()["status"] == "DISABLED"
    assert await service.process([alert()], "LIVE", "REGULAR") == 0
    assert (await service.send_test())["status"] == "DISABLED"


@pytest.mark.anyio
async def test_test_notification_and_optional_deep_link(tmp_path):
    provider = FakeProvider()
    service = NotificationService(provider, tmp_path / "state.sqlite3")
    assert (await service.send_test())["sent"] is True
    assert provider.messages[0].url is None

    linked_provider = FakeProvider()
    linked = NotificationService(linked_provider, tmp_path / "linked.sqlite3", "https://trade.example/app")
    await linked.process([alert()], "LIVE", "REGULAR")
    assert linked_provider.messages[0].url == "https://trade.example/app/?symbol=NVDA&view=overview"


@pytest.mark.anyio
async def test_radar_ready_state_change_and_duplicate_suppression(tmp_path):
    provider = FakeProvider()
    service = NotificationService(provider, tmp_path / "state.sqlite3")
    assert await service.process([alert()], "LIVE", "REGULAR") == 1
    assert await service.process([alert()], "LIVE", "REGULAR") == 0
    assert await service.process([alert(MarketAlertType.TRIGGERED)], "LIVE", "REGULAR") == 1
    assert await service.process([alert(MarketAlertType.TRIGGERED)], "LIVE", "REGULAR") == 0
    assert [message.priority for message in provider.messages] == [0, 1]


@pytest.mark.anyio
async def test_invalidated_and_too_late_require_prior_watch_or_ready(tmp_path):
    provider = FakeProvider()
    service = NotificationService(provider, tmp_path / "state.sqlite3")
    assert await service.process([alert(MarketAlertType.INVALIDATED)], "LIVE", "REGULAR") == 0
    await service.process([alert()], "LIVE", "REGULAR")
    assert await service.process([alert(MarketAlertType.INVALIDATED)], "LIVE", "REGULAR") == 1

    other = alert(symbol="AMD")
    await service.process([other], "LIVE", "REGULAR")
    assert await service.process([alert(MarketAlertType.TOO_LATE, symbol="AMD")], "LIVE", "REGULAR") == 1
    assert "Do not chase" in provider.messages[-1].body


@pytest.mark.anyio
async def test_b_and_c_quality_and_non_current_data_are_suppressed(tmp_path):
    provider = FakeProvider()
    service = NotificationService(provider, tmp_path / "state.sqlite3")
    assert await service.process([alert(quality="B")], "LIVE", "REGULAR") == 0
    assert await service.process([alert(quality="C", symbol="AMD")], "LIVE", "REGULAR") == 0
    for status in ("STALE", "DELAYED", "UNAVAILABLE", "MARKET_CLOSED"):
        assert await service.process([alert(symbol=status)], status, "REGULAR") == 0
    assert provider.messages == []


@pytest.mark.anyio
async def test_provider_failure_does_not_break_scanner_or_retry_unchanged_event(tmp_path):
    provider = FakeProvider(fail=True)
    service = NotificationService(provider, tmp_path / "state.sqlite3")
    assert await service.process([alert()], "LIVE", "REGULAR") == 0
    assert await service.process([alert()], "LIVE", "REGULAR") == 0
    assert len(provider.messages) == 1


@pytest.mark.anyio
async def test_deduplication_survives_restart(tmp_path):
    path = tmp_path / "state.sqlite3"
    first = FakeProvider()
    await NotificationService(first, path).process([alert()], "LIVE", "REGULAR")
    second = FakeProvider()
    assert await NotificationService(second, path).process([alert()], "LIVE", "REGULAR") == 0


def test_settings_and_frontend_never_expose_secrets(tmp_path):
    service = NotificationService(FakeProvider(), tmp_path / "state.sqlite3")
    serialized = str(service.status()).lower()
    assert "token" not in serialized and "user_key" not in serialized
    html = open("app/static/index.html", encoding="utf-8").read().lower()
    assert "pushover_user_key" not in html and "pushover_app_token" not in html
