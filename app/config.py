import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    massive_api_key: str | None = os.getenv("MASSIVE_API_KEY") or None
    massive_base_url: str = os.getenv("MASSIVE_BASE_URL", "https://api.massive.com")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    scanner_refresh_seconds: int = int(os.getenv("SCANNER_REFRESH_SECONDS", "300"))
    scanner_batch_size: int = int(os.getenv("SCANNER_BATCH_SIZE", "8"))
    scanner_max_symbols: int = int(os.getenv("SCANNER_MAX_SYMBOLS", "250"))
    scanner_min_price: float = float(os.getenv("SCANNER_MIN_PRICE", "5"))
    scanner_min_average_dollar_volume: float = float(os.getenv("SCANNER_MIN_AVERAGE_DOLLAR_VOLUME", "25000000"))
    scanner_min_score: int = int(os.getenv("SCANNER_MIN_SCORE", "75"))
    signal_log_path: Path = Path(os.getenv("SIGNAL_LOG_PATH", "data/signals.sqlite3"))
    pushover_user_key: str | None = os.getenv("PUSHOVER_USER_KEY") or None
    pushover_app_token: str | None = os.getenv("PUSHOVER_APP_TOKEN") or None
    pushover_api_url: str = os.getenv("PUSHOVER_API_URL", "https://api.pushover.net/1/messages.json")
    pushover_priority_radar: int = int(os.getenv("PUSHOVER_PRIORITY_RADAR", "0"))
    pushover_priority_ready: int = int(os.getenv("PUSHOVER_PRIORITY_READY", "1"))
    pushover_priority_invalidated: int = int(os.getenv("PUSHOVER_PRIORITY_INVALIDATED", "0"))
    pushover_priority_too_late: int = int(os.getenv("PUSHOVER_PRIORITY_TOO_LATE", "0"))
    public_app_base_url: str | None = os.getenv("PUBLIC_APP_BASE_URL") or None
    notification_state_path: Path = Path(os.getenv("NOTIFICATION_STATE_PATH", "data/notifications.sqlite3"))


settings = Settings()
