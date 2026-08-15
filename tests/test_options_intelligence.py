import asyncio
from datetime import date

from app.market_data.massive import MassiveMarketDataProvider
from app.models import ContractFitCriteria
from app.options_intelligence import load_contract_shortlist, load_options_intelligence


class FakeOptionsProvider:
    def __init__(self):
        self.chain_args = None

    async def get_stock_snapshot(self, symbol):
        assert symbol == "QQQ"
        return {"lastTrade": {"p": 500.0}, "todaysChangePerc": 1.25, "updated": 1786717800000000000}

    async def get_active_option_contracts(self, symbol, as_of):
        assert symbol == "QQQ"
        return [
            {"ticker": "O:QQQ260821C00500000", "expiration_date": "2026-08-21"},
            {"ticker": "O:QQQ260828C00500000", "expiration_date": "2026-08-28"},
        ]

    async def get_options_chain_snapshot(self, symbol, expiration, strike_low, strike_high):
        self.chain_args = (symbol, expiration, strike_low, strike_high)
        return [
            {"details": {"ticker": "O:QQQ260821C00500000", "contract_type": "call", "expiration_date": "2026-08-21", "strike_price": 500},
             "last_quote": {"bid": 5, "ask": 5.5, "timeframe": "REAL-TIME"},
             "last_trade": {"price": 5.2, "sip_timestamp": 1786717800000000000, "timeframe": "REAL-TIME"},
             "day": {"volume": 100}, "open_interest": 200, "implied_volatility": 0.3,
             "greeks": {"delta": 0.5, "gamma": 0.01, "theta": -0.03, "vega": 0.11}},
            {"details": {"ticker": "O:QQQ260821P00500000", "contract_type": "put", "expiration_date": "2026-08-21", "strike_price": 500},
             "day": {"volume": 40}, "open_interest": 80},
        ]

    async def get_minute_bars(self, *_args, **_kwargs):
        raise AssertionError("Equity strategy inputs must not be requested by options intelligence")


def test_options_intelligence_normalizes_provider_facts_without_strategy_calls():
    async def scenario():
        provider = FakeOptionsProvider()
        result = await load_options_intelligence(provider, "qqq", strike_window_pct=10, as_of=date(2026, 8, 14))
        assert result.underlying_symbol == "QQQ"
        assert result.selected_expiration == "2026-08-21"
        assert result.contracts_returned == 2
        assert result.contracts[0].midpoint == 5.25
        assert result.contracts[0].spread_pct == 0.5 / 5.25 * 100
        assert result.contracts[0].volume_open_interest_ratio == 0.5
        assert result.contracts[1].data_notes == ["Quote unavailable", "Greeks unavailable"]
        assert result.strategy_invoked is False
        assert result.execution_enabled is False
        assert provider.chain_args == ("QQQ", "2026-08-21", 450.0, 550.0)

    asyncio.run(scenario())


def test_contract_shortlist_applies_only_explicit_user_criteria():
    async def scenario():
        provider = FakeOptionsProvider()
        criteria = ContractFitCriteria(
            contract_type="CALL", min_days_to_expiry=3, max_days_to_expiry=10,
            min_abs_delta=0.35, max_abs_delta=0.65, min_open_interest=100,
            max_spread_pct=10.0, strike_window_pct=10.0, max_expirations_loaded=3,
        )
        result = await load_contract_shortlist(provider, "QQQ", criteria, as_of=date(2026, 8, 14))
        assert result.contracts_scanned == 2
        assert len(result.candidates) == 1
        assert result.candidates[0].contract.ticker == "O:QQQ260821C00500000"
        assert "7 days to expiry" in result.candidates[0].explanation[0]
        assert result.strategy_invoked is False
        assert result.execution_enabled is False
        assert "does not validate an underlying thesis" in result.message

    asyncio.run(scenario())


def test_options_adapter_uses_stock_snapshot_reference_and_chain_routes(monkeypatch):
    async def scenario():
        provider = MassiveMarketDataProvider("test-key")
        calls = []

        async def fake_payload(path, params):
            calls.append((path, params))
            if path.startswith("/v2/snapshot"):
                return {"status": "OK", "ticker": {"ticker": "QQQ"}}
            if path == "/v3/reference/options/contracts":
                return {"status": "OK", "results": [{"ticker": "O:QQQ260821C00500000", "expiration_date": "2026-08-21"}]}
            return {"status": "OK", "results": [{"details": {"ticker": "O:QQQ260821C00500000"}}]}

        monkeypatch.setattr(provider, "_get_payload", fake_payload)
        assert (await provider.get_stock_snapshot("QQQ"))["ticker"] == "QQQ"
        contracts = await provider.get_active_option_contracts("QQQ", date(2026, 8, 14))
        chain = await provider.get_options_chain_snapshot("QQQ", "2026-08-21", 450, 550)
        await provider.close()

        assert contracts[0]["ticker"].startswith("O:QQQ")
        assert chain[0]["details"]["ticker"].startswith("O:QQQ")
        assert calls[0][0].endswith("/QQQ")
        assert calls[1][0] == "/v3/reference/options/contracts"
        assert calls[1][1]["underlying_ticker"] == "QQQ"
        assert calls[2][0] == "/v3/snapshot/options/QQQ"
        assert calls[2][1]["expiration_date"] == "2026-08-21"

    asyncio.run(scenario())
