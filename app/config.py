import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class StorageMode(str, Enum):
    LOCAL = "local"
    HOSTED = "hosted"


class DatabaseBackend(str, Enum):
    SQLITE = "sqlite"
    POSTGRES = "postgres"


def _database_backend() -> DatabaseBackend:
    value = os.getenv("DATABASE_BACKEND", "sqlite").strip().lower()
    try:
        return DatabaseBackend(value)
    except ValueError as exc:
        raise ValueError("DATABASE_BACKEND must be 'sqlite' or 'postgres'") from exc


def _storage_mode() -> StorageMode:
    configured = os.getenv("APP_STORAGE_MODE")
    if configured:
        try:
            return StorageMode(configured.strip().lower())
        except ValueError as exc:
            raise ValueError("APP_STORAGE_MODE must be 'local' or 'hosted'") from exc
    return StorageMode.HOSTED if os.getenv("VERCEL") == "1" else StorageMode.LOCAL


def _origins(value: str | None) -> tuple[str, ...]:
    origins = tuple(origin.strip().rstrip("/") for origin in (value or "").split(",") if origin.strip())
    if "*" in origins:
        raise ValueError("CORS_ALLOWED_ORIGINS must list explicit origins; wildcard is not allowed")
    return origins


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _symbols(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.strip().upper() for item in
        os.getenv(name, default).split(",") if item.strip()))


@dataclass(frozen=True)
class Settings:
    storage_mode: StorageMode = _storage_mode()
    database_backend: DatabaseBackend = _database_backend()
    database_url: str | None = os.getenv("DATABASE_URL") or None
    db_connect_timeout_seconds: int = int(os.getenv("DB_CONNECT_TIMEOUT_SECONDS", "5"))
    db_pool_size: int = int(os.getenv("DB_POOL_SIZE", "5"))
    db_max_overflow: int = int(os.getenv("DB_MAX_OVERFLOW", "0"))
    db_pool_timeout_seconds: int = int(os.getenv("DB_POOL_TIMEOUT_SECONDS", "5"))
    db_pool_recycle_seconds: int = int(os.getenv("DB_POOL_RECYCLE_SECONDS", "300"))
    massive_api_key: str | None = os.getenv("MASSIVE_API_KEY") or None
    massive_base_url: str = os.getenv("MASSIVE_BASE_URL", "https://api.massive.com")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    scanner_refresh_seconds: int = int(os.getenv("SCANNER_REFRESH_SECONDS", "300"))
    hosted_scanner_enabled: bool = _bool("HOSTED_SCANNER_ENABLED")
    notifications_enabled: bool = _bool("NOTIFICATIONS_ENABLED")
    scanner_premarket_interval_seconds: int = int(os.getenv("SCANNER_PREMARKET_INTERVAL_SECONDS", "600"))
    scanner_closed_interval_seconds: int = int(os.getenv("SCANNER_CLOSED_INTERVAL_SECONDS", "1800"))
    scanner_stale_after_seconds: int = int(os.getenv("SCANNER_STALE_AFTER_SECONDS", "900"))
    scanner_cycle_lease_seconds: int = int(os.getenv("SCANNER_CYCLE_LEASE_SECONDS", "900"))
    hosted_scanner_watchlist: tuple[str, ...] = _symbols("HOSTED_SCANNER_WATCHLIST",
        "QQQ,SPY,IWM,SMH,NVDA,AMD,AMZN,META,AAPL,MSFT,AVGO,TSLA,GOOGL")
    hosted_scanner_additional_symbols: tuple[str, ...] = _symbols("HOSTED_SCANNER_ADDITIONAL_SYMBOLS")
    hosted_scanner_include_nasdaq_100: bool = _bool("HOSTED_SCANNER_INCLUDE_NASDAQ_100")
    hosted_scanner_include_sp_500: bool = _bool("HOSTED_SCANNER_INCLUDE_SP500")
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
    cors_allowed_origins: tuple[str, ...] = _origins(os.getenv("CORS_ALLOWED_ORIGINS"))
    cors_allowed_origin_regex: str | None = os.getenv("CORS_ALLOWED_ORIGIN_REGEX") or None

    @property
    def hosted(self) -> bool:
        return self.storage_mode is StorageMode.HOSTED

    def __post_init__(self) -> None:
        if self.db_pool_size < 1 or self.db_max_overflow < 0:
            raise ValueError("DB_POOL_SIZE must be positive and DB_MAX_OVERFLOW cannot be negative")
        if self.db_pool_timeout_seconds < 1 or self.db_pool_recycle_seconds < 1:
            raise ValueError("Database pool timeout and recycle settings must be positive")
        if min(self.scanner_refresh_seconds, self.scanner_premarket_interval_seconds,
               self.scanner_closed_interval_seconds, self.scanner_stale_after_seconds,
               self.scanner_cycle_lease_seconds) < 1:
            raise ValueError("Scanner intervals, staleness, and lease settings must be positive")
        if self.database_backend is DatabaseBackend.POSTGRES:
            if not self.database_url:
                raise ValueError("DATABASE_URL is required when DATABASE_BACKEND=postgres")
            if not self.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
                raise ValueError("DATABASE_URL must be a PostgreSQL URL when DATABASE_BACKEND=postgres")
        if self.database_backend is DatabaseBackend.SQLITE and self.database_url:
            if not self.database_url.startswith("sqlite:///"):
                raise ValueError("DATABASE_URL must be a SQLite URL when DATABASE_BACKEND=sqlite")


settings = Settings()
