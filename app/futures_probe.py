from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.market_data.massive import MassiveMarketDataProvider
from app.models import FuturesProbeResult

ET = ZoneInfo("America/New_York")


async def load_nq_probe(provider: MassiveMarketDataProvider, now: datetime | None = None) -> FuturesProbeResult:
    """Load NQ contract metadata and raw minute bars without invoking strategy code."""
    reference = (now or datetime.now(ET)).astimezone(ET)
    contract = await provider.resolve_active_futures_contract("NQ", reference.date())
    bars = await provider.get_futures_minute_bars(
        contract["ticker"], reference.date() - timedelta(days=7), reference.date())
    latest = bars[-1]
    return FuturesProbeResult(
        root_symbol="NQ",
        contract_ticker=contract["ticker"],
        contract_name=contract.get("name"),
        trading_venue=contract.get("trading_venue"),
        trade_tick_size=contract.get("trade_tick_size"),
        first_trade_date=contract.get("first_trade_date"),
        last_trade_date=contract.get("last_trade_date"),
        bars_loaded=len(bars),
        latest_timestamp=latest.timestamp,
        latest_open=latest.open,
        latest_high=latest.high,
        latest_low=latest.low,
        latest_close=latest.close,
        latest_volume=latest.volume,
        data_status="DATA_LOADED",
        strategy_invoked=False,
        execution_enabled=False,
        message=("Data-only NQ compatibility probe. Contract discovery and raw futures bars completed; "
                 "no session filter, setup engine, signal, target, alert, or execution logic was invoked."),
    )
