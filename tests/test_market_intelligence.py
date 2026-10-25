import asyncio
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

from app.market_data.base import AggregateBatch
from app.market_intelligence import (IntelligenceStore, _completed_daily_batch, _session_close,
                                     build_market_map, daily_features, intraday_features,
                                     options_report, rsi)
from app.models import Bar


def daily_batch(symbol="SPY", days=90, adjusted=True):
    start = datetime(2026, 6, 1, 4, tzinfo=timezone.utc)
    bars = tuple(Bar(timestamp=start + timedelta(days=index), open=100+index,
                     high=102+index, low=99+index, close=101+index,
                     volume=1_000_000+index*1_000) for index in range(days))
    return AggregateBatch(symbol, "1day", adjusted, bars, "request-1",
                          datetime(2026, 10, 25, tzinfo=timezone.utc), "OK")


def minute_bars():
    start = datetime(2026, 10, 23, 13, 30, tzinfo=timezone.utc)
    return [Bar(timestamp=start + timedelta(minutes=index), open=100+index*.01,
                high=100.1+index*.01, low=99.9+index*.01, close=100.05+index*.01,
                volume=1000+index) for index in range(390)]


class FakeProvider:
    async def get_daily_bars(self, symbol, start, end, adjusted=True):
        assert end == date(2026, 10, 26)
        return daily_batch(symbol, adjusted=adjusted)

    async def get_minute_bars(self, symbol, start, end):
        assert start == end
        return minute_bars()

    async def get_stock_snapshot(self, symbol):
        return {"lastTrade": {"p": 190, "t": 1792799960000000000},
                "updated": 1792800000000000000}

    async def get_active_option_contracts(self, symbol, as_of):
        return [{"ticker": f"O:{symbol}", "expiration_date": "2026-10-26"}]

    async def get_options_chain_snapshot(self, symbol, expiration, low, high):
        return [{"details": {"ticker": f"O:{symbol}261026C00190000", "contract_type": "call",
                             "expiration_date": expiration, "strike_price": 190},
                 "last_quote": {"bid": 4.9, "ask": 5.1, "timeframe": "REAL-TIME",
                                "last_updated": 1792799950000000000},
                 "day": {"volume": 200}, "open_interest": 500, "implied_volatility": .3,
                 "greeks": {"delta": .5, "gamma": .02, "theta": -.1, "vega": .08}}]


def test_daily_features_are_backward_looking_and_include_required_indicators():
    batch = daily_batch("QQQ")
    feature = daily_features(batch, daily_batch("SPY"))
    assert feature["source_timestamp"] == batch.bars[-1].timestamp.isoformat()
    assert feature["previous_day"]["close"] == batch.bars[-2].close
    assert feature["ema20"] < feature["close"]
    assert feature["ema50"] < feature["ema20"]
    assert feature["rsi14"] == 100
    assert feature["atr14"] is not None
    assert feature["relative_strength_20d_vs_spy_pct"] == 0
    assert rsi([1] * 14) is None


def test_intraday_features_require_sufficient_data_and_include_one_hour_structure():
    assert intraday_features(minute_bars()[:20])["available"] is False
    result = intraday_features(minute_bars())
    assert result["available"] is True
    assert set(result["frames"]) == {"5m", "15m", "1h"}
    assert result["opening_range_high"] > result["opening_range_low"]
    assert result["vwap"] > 0


def test_options_report_separates_observations_from_estimates():
    chain = asyncio.run(FakeProvider().get_options_chain_snapshot("QQQ", "2026-10-26", 0, 999))
    result = options_report("QQQ", {"lastTrade": {"p": 190, "t": 1792799960000000000}},
                            "2026-10-26", chain)
    assert result["liquid_contracts"] == 1
    assert result["strike_concentration"] == [{"strike": 190.0, "open_interest": 500.0}]
    assert result["contracts"][0]["midpoint"] == 5.0
    assert result["contracts"][0]["liquidity_pass"] is True
    assert result["contracts"][0]["trade_volume_status"] == "OBSERVED"
    assert "No unusual" in result["prohibited_inference"]


def test_options_liquidity_rejects_missing_or_stale_fields_instead_of_coercing_to_zero():
    chain = [{"details": {"ticker": "O:QQQ", "contract_type": "call",
                          "expiration_date": "2026-10-26", "strike_price": 190},
              "last_quote": {"bid": 4.9, "ask": 5.1,
                             "last_updated": 1792790000000000000},
              "day": {}},
             {"details": {"ticker": "O:QQQ2", "contract_type": "call",
                          "expiration_date": "2026-10-26", "strike_price": 191},
              "last_quote": {"bid": 4.0, "ask": 4.1,
                             "last_updated": 1792799960000000000},
              "day": {"volume": 1}, "open_interest": 1}]
    result = options_report("QQQ", {"lastTrade": {"p": 190, "t": 1792799960000000000}},
                            "2026-10-26", chain)
    contract = result["contracts"][0]
    assert result["liquid_contracts"] == 0
    assert contract["open_interest"] is None
    assert contract["volume"] is None
    assert contract["liquidity_pass"] is False
    assert {"stale_quote", "missing_open_interest", "missing_trade_volume"} <= set(contract["liquidity_failures"])


def test_market_map_is_reproducible_and_persists_sources_reports_and_hypotheses(tmp_path):
    async def scenario():
        store = IntelligenceStore(tmp_path / "intelligence.sqlite3")
        try:
            return await build_market_map(FakeProvider(), store, date(2026, 10, 26),
                                          ("QQQ", "SPY", "IWM", "SMH"), True)
        finally:
            store.close()

    report = asyncio.run(scenario())
    assert report["report_date"] == "2026-10-26"
    assert report["observation_cutoff"] < "2026-10-26T23:59:59"
    assert report["outcomes"]["status"] == "PENDING"
    assert len(report["intraday_playbook"]) == 4
    assert len(report["swing_playbook"]) == 4
    assert {item["symbol"] for item in report["options_intelligence"]} == {"QQQ", "SPY"}
    assert {item["symbol"] for item in report["options_flow_intelligence"]} == {"QQQ", "SPY"}
    assert all(item["trade_flow"]["status"] == "UNAVAILABLE"
               for item in report["options_flow_intelligence"])
    with sqlite3.connect(tmp_path / "intelligence.sqlite3") as connection:
        assert connection.execute("SELECT count(*) FROM source_batches").fetchone()[0] == 8
        stored = json.loads(connection.execute("SELECT report_json FROM intelligence_reports").fetchone()[0])
        assert stored["point_in_time_policy"].startswith("Only observations")
        assert connection.execute("SELECT count(*) FROM hypotheses").fetchone()[0] == 8
        assert connection.execute("SELECT count(*) FROM option_contract_snapshots").fetchone()[0] == 2


def test_outcomes_are_recorded_only_when_a_later_bar_exists(tmp_path):
    store = IntelligenceStore(tmp_path / "intelligence.sqlite3")
    first = daily_batch("SPY", days=80)
    feature = daily_features(first, first)
    report = {"report_date": "2026-08-19", "generated_at": "2026-08-19T22:00:00+00:00",
              "observation_cutoff": feature["source_timestamp"], "intraday_playbook": [],
              "swing_playbook": [{"symbol": "SPY", "direction": "LONG", "trigger": 178,
                                  "invalidation": 175, "targets": [181, 184],
                                  "source_timestamp": feature["source_timestamp"]}]}
    store.save_report(report, "input")
    assert store.record_available_outcomes({"SPY": first}, datetime.now(timezone.utc)) == 0
    extended = AggregateBatch("SPY", "1day", True,
        first.bars + (Bar(timestamp=first.bars[-1].timestamp + timedelta(days=1),
                          open=180, high=182, low=179, close=181, volume=1_000_000),),
        "request-2", datetime.now(timezone.utc), "OK")
    assert store.record_available_outcomes({"SPY": extended}, datetime.now(timezone.utc)) == 1
    row = store.connection.execute("SELECT status FROM next_session_outcomes").fetchone()
    store.close()
    assert row[0] == "TARGET_1"


def test_report_date_bars_are_excluded_and_cutoff_is_session_close():
    batch = daily_batch("QQQ", days=2)
    from app.engines.market import ET
    report_date = batch.bars[-1].timestamp.astimezone(ET).date()
    completed, excluded = _completed_daily_batch(batch, report_date)
    assert excluded == 1
    assert completed.bars == batch.bars[:-1]
    assert _session_close(completed.bars[-1].timestamp).astimezone(ET).time().isoformat() == "16:00:00"
