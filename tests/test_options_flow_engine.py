from app.options_flow_engine import (classify_trade_flow, gamma_scenarios, positioning,
                                     unusual_activity, volatility)


def contract(ticker="O:QQQ", kind="call", strike=100, volume=20, oi=100, iv=.25,
             gamma=.02, bid=2, ask=2.2, expiration="2026-10-30"):
    return {"details": {"ticker": ticker, "contract_type": kind, "strike_price": strike,
                         "expiration_date": expiration},
            "day": {"volume": volume}, "open_interest": oi, "implied_volatility": iv,
            "greeks": {"delta": .25 if kind == "call" else -.25, "gamma": gamma},
            "last_quote": {"bid": bid, "ask": ask},
            "underlying_asset": {"price": 100}}


def test_positioning_does_not_infer_direction_and_preserves_missing_oi():
    chain = [contract(), contract("O:QQQ2", "put", 95, oi=None)]
    report = positioning(chain, {"O:QQQ": 90})
    assert report["contract_changes"][0]["open_interest_change"] == 10
    assert report["contracts_missing_open_interest"] == 1
    assert "not directional" in report["interpretation_limit"]


def test_unusual_activity_requires_history_and_does_not_coerce_missing_values():
    chain = [contract(), contract("O:NEW", volume=None, oi=None)]
    report = unusual_activity(chain, {"O:QQQ": [5, 6, 7, 8, 9]})
    assert report["contracts"][0]["flagged"] is True
    assert report["contracts"][1]["volume_oi_ratio"] is None
    assert report["contracts"][1]["baseline_status"] == "NEW_CONTRACT"


def test_trade_flow_uses_only_prior_timestamp_aligned_quote():
    quotes = [{"sip_timestamp": 1_000_000_000, "bid_price": 1.0, "ask_price": 1.2},
              {"sip_timestamp": 3_000_000_000, "bid_price": 1.2, "ask_price": 1.4}]
    trades = [{"sip_timestamp": 2_000_000_000, "price": 1.2, "size": 10}]
    report = classify_trade_flow(trades, quotes, False, False)
    assert report["classification_counts"] == {"BUYER_INITIATED_ESTIMATE": 1}
    assert report["evidence"][0]["quote_ask"] == 1.2
    assert report["trades_pagination_complete"] is False


def test_volatility_withholds_iv_rank_until_history_is_sufficient():
    chain = [contract(), contract("O:QQQP", "put", bid=1.8, ask=2.0)]
    closes = [100 + index * .1 for index in range(30)]
    report = volatility(chain, closes, [.2] * 19)
    assert report["expected_moves"][0]["straddle_midpoint_expected_move"] == 4.0
    assert report["realized_volatility_20d_annualized"] is not None
    assert report["iv_rank"] is None
    assert report["history_status"] == "INSUFFICIENT_HISTORY"


def test_gamma_is_scenario_based_and_not_labeled_as_verified_levels():
    report = gamma_scenarios([contract(), contract("O:QQQP", "put")], 100)
    assert report["scenario_totals"]["dealer_short_all_customer_options"] < 0
    assert report["scenario_totals"]["dealer_long_all_customer_options"] > 0
    assert "not verified support" in report["limitation"]
