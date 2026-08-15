import asyncio
from datetime import date, datetime, timezone

from app.futures_probe import load_nq_probe
from app.market_data.massive import MassiveMarketDataProvider
from app.models import Bar


class FakeFuturesProvider:
    def __init__(self):
        self.contract_calls = 0
        self.bar_calls = 0

    async def resolve_active_futures_contract(self, product_code, as_of):
        self.contract_calls += 1
        assert product_code == "NQ"
        return {
            "ticker": "NQU6", "name": "E-mini Nasdaq-100 Sep 2026",
            "trading_venue": "XCME", "trade_tick_size": 0.25,
            "first_trade_date": "2026-06-15", "last_trade_date": "2026-09-18",
        }

    async def get_futures_minute_bars(self, contract_ticker, start, end):
        self.bar_calls += 1
        assert contract_ticker == "NQU6"
        return [Bar(timestamp=datetime(2026, 8, 14, 14, 30, tzinfo=timezone.utc),
                    open=23000, high=23005, low=22990, close=23002, volume=1250)]

    async def get_minute_bars(self, *_args, **_kwargs):
        raise AssertionError("Equity minute-bar loading must not run in the data-only futures probe")


def test_nq_probe_isolated_from_strategy_and_equity_data_paths():
    async def scenario():
        provider = FakeFuturesProvider()
        result = await load_nq_probe(provider, datetime(2026, 8, 14, 12, tzinfo=timezone.utc))
        assert provider.contract_calls == 1
        assert provider.bar_calls == 1
        assert result.contract_ticker == "NQU6"
        assert result.latest_close == 23002
        assert result.strategy_invoked is False
        assert result.execution_enabled is False
        assert "no session filter" in result.message.lower()

    asyncio.run(scenario())


def test_massive_futures_adapter_uses_contract_and_futures_aggregate_routes(monkeypatch):
    async def scenario():
        provider = MassiveMarketDataProvider("test-key")
        requests = []

        async def fake_payload(path, params):
            requests.append((path, params))
            if path == "/futures/v1/contracts":
                return {"status": "OK", "results": [
                    {"ticker": "NQU6", "active": True, "days_to_maturity": 35},
                    {"ticker": "NQZ6", "active": True, "days_to_maturity": 125},
                ]}
            return {"status": "OK", "results": [{
                "window_start": 1786717800000000000,
                "open": 23000, "high": 23005, "low": 22990, "close": 23002, "volume": 1250,
            }]}

        monkeypatch.setattr(provider, "_get_payload", fake_payload)
        contract = await provider.resolve_active_futures_contract("NQ", date(2026, 8, 14))
        bars = await provider.get_futures_minute_bars(contract["ticker"], date(2026, 8, 13), date(2026, 8, 14))
        await provider.close()

        assert contract["ticker"] == "NQU6"
        assert bars[0].timestamp == datetime.fromtimestamp(1786717800, tz=timezone.utc)
        assert requests[0][0] == "/futures/v1/contracts"
        assert requests[0][1]["product_code"] == "NQ"
        assert requests[1][0] == "/futures/v1/aggs/NQU6"
        assert requests[1][1]["resolution"] == "1min"

    asyncio.run(scenario())
