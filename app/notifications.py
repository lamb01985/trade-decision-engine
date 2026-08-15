from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode

import httpx

from app.models import MarketAlert, MarketAlertType

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PushMessage:
    title: str
    body: str
    priority: int = 0
    url: str | None = None
    url_title: str | None = None


class NotificationProvider(Protocol):
    async def send(self, message: PushMessage) -> None: ...
    async def close(self) -> None: ...


class PushoverProvider:
    """Small delivery adapter. It contains no scanner or setup interpretation."""

    def __init__(self, user_key: str, app_token: str, api_url: str, timeout_seconds: float = 8.0) -> None:
        self._user_key = user_key
        self._app_token = app_token
        self._api_url = api_url
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def send(self, message: PushMessage) -> None:
        payload = {
            "user": self._user_key,
            "token": self._app_token,
            "title": message.title,
            "message": message.body,
            "priority": max(-2, min(1, message.priority)),  # Never send emergency/repeating priority 2.
        }
        if message.url:
            payload["url"] = message.url
            payload["url_title"] = message.url_title or "Open current analysis"
        response = await self._client.post(self._api_url, data=payload)
        response.raise_for_status()
        result = response.json()
        if result.get("status") != 1:
            raise RuntimeError("Pushover rejected the notification")

    async def close(self) -> None:
        await self._client.aclose()


@dataclass
class AlertPreferences:
    radar: bool = True
    ready: bool = True
    invalidated: bool = True
    too_late: bool = True
    allow_b_quality: bool = False


class NotificationService:
    """Durable, downstream delivery policy for existing AlertEngine transitions."""

    def __init__(self, provider: NotificationProvider | None, state_path: Path,
                 public_app_base_url: str | None = None,
                 priorities: dict[str, int] | None = None) -> None:
        self.provider = provider
        self.state_path = state_path
        self.public_app_base_url = public_app_base_url.rstrip("/") if public_app_base_url else None
        self.priorities = {"RADAR": 0, "READY": 1, "INVALIDATED": 0, "TOO_LATE": 0, **(priorities or {})}
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @property
    def enabled(self) -> bool:
        return self.provider is not None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.state_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS notification_state (
                symbol TEXT PRIMARY KEY, setup TEXT NOT NULL, direction TEXT NOT NULL,
                fingerprint TEXT NOT NULL, previous_state TEXT NOT NULL,
                last_notified_state TEXT, last_notification_timestamp TEXT,
                last_delivery_ok INTEGER
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS notification_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1), radar INTEGER NOT NULL,
                ready INTEGER NOT NULL, invalidated INTEGER NOT NULL,
                too_late INTEGER NOT NULL, allow_b_quality INTEGER NOT NULL
            )""")
            db.execute("""INSERT OR IGNORE INTO notification_settings
                (id, radar, ready, invalidated, too_late, allow_b_quality)
                VALUES (1, 1, 1, 1, 1, 0)""")

    def preferences(self) -> AlertPreferences:
        with self._connect() as db:
            row = db.execute("SELECT * FROM notification_settings WHERE id = 1").fetchone()
        return AlertPreferences(**{key: bool(row[key]) for key in asdict(AlertPreferences())})

    def update_preferences(self, values: dict[str, bool]) -> AlertPreferences:
        allowed = set(asdict(AlertPreferences()))
        updates = {key: bool(value) for key, value in values.items() if key in allowed}
        if updates:
            assignments = ", ".join(f"{key} = ?" for key in updates)
            with self._connect() as db:
                db.execute(f"UPDATE notification_settings SET {assignments} WHERE id = 1",
                           [int(value) for value in updates.values()])
        return self.preferences()

    def status(self) -> dict:
        return {
            "provider": "PUSHOVER",
            "status": "ENABLED" if self.enabled else "DISABLED",
            "enabled": self.enabled,
            "alerts": asdict(self.preferences()),
            "public_url_configured": bool(self.public_app_base_url),
            "priorities": dict(self.priorities),
        }

    @staticmethod
    def _event(alert: MarketAlert) -> str:
        return "READY" if alert.alert_type == MarketAlertType.TRIGGERED else alert.alert_type.value

    @staticmethod
    def _fingerprint(alert: MarketAlert) -> str:
        return json.dumps([
            alert.setup, alert.direction, round(alert.trigger, 4), round(alert.invalidation, 4),
            round(alert.target_1, 4), round(alert.target_2, 4), round(alert.risk_reward, 2),
        ], separators=(",", ":"))

    def _deep_link(self, symbol: str) -> str | None:
        if not self.public_app_base_url:
            return None
        return f"{self.public_app_base_url}/?{urlencode({'symbol': symbol, 'view': 'overview'})}"

    @staticmethod
    def _money(value: float | None) -> str:
        return "—" if value is None else f"${value:,.2f}"

    def _message(self, alert: MarketAlert, event: str) -> PushMessage:
        setup = alert.setup.replace("_", " ").title()
        heading = f"{alert.symbol} {alert.direction}"
        if event == "RADAR":
            title = f"A SETUP FORMING — {heading}"
            body = (f"{setup}\nQuality: {alert.quality}\n\nNear trigger: {self._money(alert.trigger)}"
                    "\n\nNEXT:\nWait for confirmation.")
        elif event == "READY":
            title = f"READY — {heading}"
            body = (f"{setup} · {alert.quality}\n\nTrigger: {self._money(alert.trigger)}"
                    f"\nInvalidation: {self._money(alert.invalidation)}"
                    f"\nT1: {self._money(alert.target_1)}\nT2: {self._money(alert.target_2)}"
                    "\n\nNEXT:\nSetup confirmed. Check current entry/chase status.")
        elif event == "TOO_LATE":
            title = f"{heading} — TOO LATE"
            body = "Setup moved beyond valid entry.\n\nNEXT:\nDo not chase. Wait for a new setup/retest."
        else:
            title = f"{heading} INVALIDATED"
            body = "Prior setup no longer valid.\n\nNEXT:\nDo not enter."
        return PushMessage(title=title, body=body, priority=self.priorities[event],
                           url=self._deep_link(alert.symbol), url_title="Open current analysis")

    def _prior(self, symbol: str) -> sqlite3.Row | None:
        with self._connect() as db:
            return db.execute("SELECT * FROM notification_state WHERE symbol = ?", (symbol,)).fetchone()

    def _record(self, alert: MarketAlert, event: str, fingerprint: str,
                notified_state: str | None, delivered: bool | None) -> None:
        prior = self._prior(alert.symbol)
        last_state = notified_state if notified_state is not None else (prior["last_notified_state"] if prior else None)
        last_time = datetime.now(timezone.utc).isoformat() if notified_state is not None else (prior["last_notification_timestamp"] if prior else None)
        last_ok = int(delivered) if delivered is not None else (prior["last_delivery_ok"] if prior else None)
        with self._connect() as db:
            db.execute("""INSERT INTO notification_state
                (symbol, setup, direction, fingerprint, previous_state, last_notified_state,
                 last_notification_timestamp, last_delivery_ok)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET setup=excluded.setup, direction=excluded.direction,
                fingerprint=excluded.fingerprint, previous_state=excluded.previous_state,
                last_notified_state=excluded.last_notified_state,
                last_notification_timestamp=excluded.last_notification_timestamp,
                last_delivery_ok=excluded.last_delivery_ok""",
                (alert.symbol, alert.setup, alert.direction, fingerprint, event, last_state, last_time, last_ok))

    async def process(self, alerts: list[MarketAlert], data_status: str, market_session: str) -> int:
        if data_status != "LIVE" or market_session != "REGULAR":
            return 0
        preferences = self.preferences()
        sent = 0
        for alert in alerts:
            event = self._event(alert)
            fingerprint = self._fingerprint(alert)
            prior = self._prior(alert.symbol)
            toggle = {"RADAR": preferences.radar, "READY": preferences.ready,
                      "INVALIDATED": preferences.invalidated, "TOO_LATE": preferences.too_late}[event]
            quality_allowed = alert.quality == "A" or (preferences.allow_b_quality and alert.quality == "B")
            was_notified = bool(prior and prior["last_notified_state"] in {"RADAR", "READY"})
            unchanged = bool(prior and prior["last_notified_state"] == event and prior["fingerprint"] == fingerprint)
            eligible = toggle and not unchanged
            if event in {"RADAR", "READY"}:
                eligible = eligible and quality_allowed
            else:
                eligible = eligible and was_notified
            if not eligible or not self.enabled:
                self._record(alert, event, fingerprint, None, None)
                continue
            try:
                await self.provider.send(self._message(alert, event))  # type: ignore[union-attr]
                sent += 1
                delivered = True
            except Exception as exc:
                # Record the attempt so a transient outage cannot create a retry storm.
                delivered = False
                logger.warning("Pushover delivery failed for %s %s (%s)", alert.symbol, event, type(exc).__name__)
            self._record(alert, event, fingerprint, event, delivered)
        return sent

    async def send_test(self) -> dict:
        if not self.enabled:
            return {"sent": False, "status": "DISABLED", "message": "Pushover credentials are not configured."}
        message = PushMessage(
            title="Trade Decision Engine — Test",
            body="Pushover is connected. Scanner alerts remain transition-based and execution is disabled.",
            priority=0,
            url=f"{self.public_app_base_url}/?view=overview" if self.public_app_base_url else None,
            url_title="Open Trade Decision Engine",
        )
        try:
            await self.provider.send(message)  # type: ignore[union-attr]
            return {"sent": True, "status": "ENABLED", "message": "Test notification sent."}
        except Exception as exc:
            logger.warning("Pushover test delivery failed (%s)", type(exc).__name__)
            return {"sent": False, "status": "ERROR", "message": "Pushover delivery failed. Scanner operation is unaffected."}

    async def close(self) -> None:
        if self.provider:
            await self.provider.close()
