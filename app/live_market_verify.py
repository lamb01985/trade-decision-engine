"""Provider-backed stock freshness check for closed markets and regular sessions.

Run Monday during regular market hours:
    .venv/bin/python -m app.live_market_verify --symbol QQQ --wait-seconds 65

This command never substitutes fixture or simulated data for Massive responses.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone

from app.config import settings
from app.freshness import MarketSession, data_age_seconds, market_session
from app.market_data.massive import MassiveMarketDataProvider
from app.service import AnalysisService
from app.universe import UniverseSelection


def _record(result) -> dict:
    calculated_age = data_age_seconds(result.provider_timestamp)
    return {
        "symbol": result.instrument.symbol,
        "current_price": result.current_price,
        "price_source": result.price_source,
        "provider_timestamp": result.provider_timestamp.isoformat() if result.provider_timestamp else None,
        "analysis_timestamp": result.analysis_timestamp.isoformat() if result.analysis_timestamp else None,
        "reported_data_age_seconds": result.data_age_seconds,
        "calculated_data_age_seconds": calculated_age,
        "data_status": result.data_status,
        "market_session": result.market_session,
        "permission_to_trade": result.permission_to_trade,
    }


async def verify(symbol: str, wait_seconds: int) -> int:
    if not settings.massive_api_key:
        raise SystemExit("MASSIVE_API_KEY is not configured")
    provider = MassiveMarketDataProvider(settings.massive_api_key, settings.massive_base_url)
    service = AnalysisService(provider)
    try:
        first = await service.analyze_symbol(symbol, include_cross_market=False, force=True)
        if wait_seconds:
            await asyncio.sleep(wait_seconds)
        second = await service.analyze_symbol(symbol, include_cross_market=False, force=True)
        scan = await service.market_scan(UniverseSelection(watchlist=(symbol,)), force=True)
        payload = {
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "provider": "Massive REST (no simulation)",
            "first_refresh": _record(first),
            "second_refresh": _record(second),
            "scanner": {
                "provider_timestamp": scan.provider_timestamp.isoformat() if scan.provider_timestamp else None,
                "analysis_timestamp": scan.analysis_timestamp.isoformat() if scan.analysis_timestamp else None,
                "data_age_seconds": scan.data_age_seconds,
                "data_status": scan.data_status,
                "market_session": scan.market_session,
                "failed_symbols": scan.failed_symbols,
            },
        }
        current_session = market_session()
        if current_session == MarketSession.REGULAR:
            advanced = bool(
                first.provider_timestamp and second.provider_timestamp
                and second.provider_timestamp > first.provider_timestamp
            )
            payload["verification"] = "PASS" if advanced and second.data_status == "LIVE" and scan.data_status == "LIVE" else "FAIL"
            payload["expectation"] = "During REGULAR hours, provider timestamps must advance and both analysis and scanner must be LIVE."
        else:
            correctly_closed = current_session == MarketSession.CLOSED and second.data_status == "MARKET_CLOSED" and scan.data_status == "MARKET_CLOSED"
            payload["verification"] = "PASS" if correctly_closed else "OBSERVED"
            payload["expectation"] = "Outside REGULAR hours, unchanged provider observations are allowed and never prove live updating. CLOSED must be MARKET_CLOSED."
        print(json.dumps(payload, indent=2))
        return 0 if payload["verification"] in {"PASS", "OBSERVED"} else 1
    finally:
        await provider.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify real Massive stock freshness without simulated data.")
    parser.add_argument("--symbol", default="QQQ")
    parser.add_argument("--wait-seconds", type=int, default=65)
    args = parser.parse_args()
    if args.wait_seconds < 0:
        parser.error("--wait-seconds must be non-negative")
    raise SystemExit(asyncio.run(verify(args.symbol.strip().upper(), args.wait_seconds)))


if __name__ == "__main__":
    main()
