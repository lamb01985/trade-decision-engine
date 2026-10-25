"""Point-in-time options positioning analytics; never an execution service."""
from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timezone
from math import log, sqrt
from statistics import mean, pstdev
from typing import Any

MIN_VOLUME_BASELINE_OBSERVATIONS = 5
MIN_IV_HISTORY_OBSERVATIONS = 20


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _ns_time(value: object) -> datetime | None:
    return (datetime.fromtimestamp(value / 1_000_000_000, tz=timezone.utc)
            if isinstance(value, (int, float)) and value > 0 else None)


def positioning(chain: list[dict], previous: dict[str, float] | None = None) -> dict:
    """Aggregate observed OI without assigning direction, owner, or opening/closing intent."""
    by_strike: dict[tuple[float, str], float] = defaultdict(float)
    by_expiration: dict[tuple[str, str], float] = defaultdict(float)
    contracts, missing = [], 0
    previous = previous or {}
    for item in chain:
        details = item.get("details") or {}
        ticker, option_type = details.get("ticker"), details.get("contract_type")
        strike, expiration = _number(details.get("strike_price")), details.get("expiration_date")
        oi = _number(item.get("open_interest"))
        if not ticker or oi is None:
            missing += 1
            continue
        prior = previous.get(ticker)
        contracts.append({"ticker": ticker, "open_interest": oi,
                          "prior_open_interest": prior,
                          "open_interest_change": oi - prior if prior is not None else None,
                          "history_status": "COMPARABLE" if prior is not None else "NEW_OR_NO_PRIOR_SNAPSHOT"})
        if strike is not None: by_strike[(strike, option_type)] += oi
        if expiration: by_expiration[(expiration, option_type)] += oi
    return {
        "by_strike": [{"strike": key[0], "type": key[1], "open_interest": value}
                      for key, value in sorted(by_strike.items())],
        "by_expiration": [{"expiration": key[0], "type": key[1], "open_interest": value}
                          for key, value in sorted(by_expiration.items())],
        "contract_changes": contracts, "contracts_missing_open_interest": missing,
        "interpretation_limit": "Open interest is not directional, does not identify institutions, and does not reveal opening versus closing trades.",
    }


def unusual_activity(chain: list[dict], volume_history: dict[str, list[float]]) -> dict:
    rows = []
    for item in chain:
        details, day = item.get("details") or {}, item.get("day") or {}
        ticker = details.get("ticker")
        volume, oi = _number(day.get("volume")), _number(item.get("open_interest"))
        baseline_queried = ticker in volume_history
        history = [float(value) for value in volume_history.get(ticker, []) if value is not None]
        baseline = mean(history) if len(history) >= MIN_VOLUME_BASELINE_OBSERVATIONS else None
        midpoint = _number((item.get("last_quote") or {}).get("midpoint"))
        if midpoint is None:
            bid, ask = (_number((item.get("last_quote") or {}).get(key)) for key in ("bid", "ask"))
            midpoint = (bid + ask) / 2 if bid is not None and ask is not None and ask >= bid else None
        rows.append({"ticker": ticker, "volume": volume, "open_interest": oi,
                     "volume_oi_ratio": volume / oi if volume is not None and oi and oi > 0 else None,
                     "baseline_observations": len(history), "mean_historical_volume": baseline,
                     "volume_baseline_ratio": volume / baseline if volume is not None and baseline else None,
                     "baseline_status": "SUFFICIENT" if baseline is not None else
                                        "INSUFFICIENT_HISTORY" if history else
                                        "NEW_CONTRACT" if baseline_queried else "MISSING_BASELINE",
                     "estimated_notional_at_quote_midpoint": volume * midpoint * 100
                         if volume is not None and midpoint is not None else None,
                     "flagged": bool(baseline and volume is not None and volume >= 2 * baseline),
                     "limitation": "Notional is an estimate from session volume and latest midpoint; it is not trade premium or directional flow."})
    return {"contracts": rows, "flagged": [row for row in rows if row["flagged"]],
            "minimum_baseline_observations": MIN_VOLUME_BASELINE_OBSERVATIONS}


def classify_trade_flow(trades: list[dict], quotes: list[dict],
                        trades_complete: bool, quotes_complete: bool) -> dict:
    """Classify sampled executions against the most recent quote at or before each trade."""
    ordered_quotes = sorted(((int(q["sip_timestamp"]), q) for q in quotes
                             if isinstance(q.get("sip_timestamp"), (int, float))),
                            key=lambda item: item[0])
    quote_times = [item[0] for item in ordered_quotes]
    counts = defaultdict(int); premium = defaultdict(float); evidence = []
    for trade in trades:
        timestamp = trade.get("sip_timestamp")
        price, size = _number(trade.get("price")), _number(trade.get("size"))
        index = bisect_right(quote_times, timestamp) - 1 if isinstance(timestamp, (int, float)) else -1
        label, confidence, quote_age_ms = "AMBIGUOUS", 0.0, None
        bid = ask = None
        if index >= 0 and price is not None:
            quote_timestamp, quote = ordered_quotes[index]
            bid, ask = _number(quote.get("bid_price")), _number(quote.get("ask_price"))
            quote_age_ms = (timestamp - quote_timestamp) / 1_000_000
            if quote_age_ms <= 1000 and bid is not None and ask is not None and ask >= bid:
                if price >= ask: label, confidence = "BUYER_INITIATED_ESTIMATE", .85
                elif price <= bid: label, confidence = "SELLER_INITIATED_ESTIMATE", .85
                else: label, confidence = "AMBIGUOUS", .35
        counts[label] += 1
        if price is not None and size is not None: premium[label] += price * size * 100
        evidence.append({"trade_timestamp": _ns_time(timestamp).isoformat() if _ns_time(timestamp) else None,
                         "price": price, "size": size, "quote_bid": bid, "quote_ask": ask,
                         "quote_age_ms": quote_age_ms, "classification": label,
                         "confidence": confidence})
    return {"sampled_trades": len(trades), "sampled_quotes": len(quotes),
            "trades_pagination_complete": trades_complete,
            "quotes_pagination_complete": quotes_complete,
            "classification_counts": dict(counts), "estimated_premium_by_class": dict(premium),
            "evidence": evidence,
            "limitations": ["Classification estimates aggressor side from timestamp-aligned quotes.",
                            "It cannot identify institutions or opening/closing intent.",
                            "Incomplete pagination means results describe only the bounded recent sample."]}


def volatility(chain: list[dict], daily_closes: list[float],
               iv_history: list[float] | None = None) -> dict:
    expirations: dict[str, list[dict]] = defaultdict(list)
    for item in chain:
        expiration = (item.get("details") or {}).get("expiration_date")
        if expiration: expirations[expiration].append(item)
    expected_moves, term, skew = [], [], []
    for expiration, items in sorted(expirations.items()):
        prices = [_number((item.get("underlying_asset") or {}).get("price")) for item in items]
        spot = next((value for value in prices if value), daily_closes[-1] if daily_closes else None)
        if not spot: continue
        strikes = sorted({_number((item.get("details") or {}).get("strike_price")) for item in items}
                         - {None}, key=lambda value: abs(value - spot))
        atm = strikes[0] if strikes else None
        mids = {}
        ivs = []
        for item in items:
            details, quote = item.get("details") or {}, item.get("last_quote") or {}
            bid, ask = _number(quote.get("bid")), _number(quote.get("ask"))
            if details.get("strike_price") == atm and bid is not None and ask is not None and ask >= bid:
                mids[details.get("contract_type")] = (bid + ask) / 2
            iv = _number(item.get("implied_volatility"))
            delta = _number((item.get("greeks") or {}).get("delta"))
            if iv is not None: ivs.append(iv)
            if iv is not None and delta is not None and (.20 <= abs(delta) <= .30):
                skew.append({"expiration": expiration, "type": details.get("contract_type"),
                             "delta": delta, "iv": iv})
        expected_moves.append({"expiration": expiration, "atm_strike": atm,
                               "straddle_midpoint_expected_move": mids.get("call", 0) + mids.get("put", 0)
                                   if {"call", "put"} <= mids.keys() else None})
        term.append({"expiration": expiration, "median_like_mean_iv": mean(ivs) if ivs else None,
                     "contracts_with_iv": len(ivs)})
    returns = [log(right / left) for left, right in zip(daily_closes, daily_closes[1:]) if left > 0 and right > 0]
    realized = pstdev(returns[-20:]) * sqrt(252) if len(returns) >= 20 else None
    history = list(iv_history or [])
    current_ivs = [row["median_like_mean_iv"] for row in term if row["median_like_mean_iv"] is not None]
    current = mean(current_ivs) if current_ivs else None
    rank = ((current - min(history)) / (max(history) - min(history)) * 100
            if current is not None and len(history) >= MIN_IV_HISTORY_OBSERVATIONS and max(history) > min(history) else None)
    percentile = (sum(value <= current for value in history) / len(history) * 100
                  if current is not None and len(history) >= MIN_IV_HISTORY_OBSERVATIONS else None)
    return {"expected_moves": expected_moves, "iv_term_structure": term, "delta_skew_observations": skew,
            "realized_volatility_20d_annualized": realized, "current_chain_mean_iv": current,
            "iv_rank": rank, "iv_percentile": percentile, "iv_history_observations": len(history),
            "history_status": "SUFFICIENT" if len(history) >= MIN_IV_HISTORY_OBSERVATIONS else "INSUFFICIENT_HISTORY"}


def gamma_scenarios(chain: list[dict], spot: float) -> dict:
    totals = {"dealer_short_all_customer_options": 0.0,
              "dealer_short_calls_long_puts": 0.0,
              "dealer_long_all_customer_options": 0.0}
    by_strike: dict[float, dict[str, float]] = defaultdict(lambda: {key: 0.0 for key in totals})
    observed = 0
    for item in chain:
        details = item.get("details") or {}
        gamma, oi = _number((item.get("greeks") or {}).get("gamma")), _number(item.get("open_interest"))
        strike = _number(details.get("strike_price"))
        if gamma is None or oi is None or strike is None: continue
        observed += 1
        magnitude = gamma * oi * 100 * spot * spot * .01
        signs = {"dealer_short_all_customer_options": -1,
                 "dealer_short_calls_long_puts": -1 if details.get("contract_type") == "call" else 1,
                 "dealer_long_all_customer_options": 1}
        for scenario, sign in signs.items():
            totals[scenario] += sign * magnitude; by_strike[strike][scenario] += sign * magnitude
    return {"unit": "modeled dollar gamma per 1% underlying move", "contracts_observed": observed,
            "scenario_totals": totals,
            "largest_scenario_strikes": [{"strike": strike, **values} for strike, values in
                sorted(by_strike.items(), key=lambda row: max(abs(v) for v in row[1].values()), reverse=True)[:10]],
            "limitation": "OI and provider Greeks are observations; dealer/customer signs are assumptions. Modeled concentrations are not verified support or resistance."}


def stock_confirmation(daily: dict, intraday: dict) -> dict:
    return {"source_timestamps": [value for value in
            (daily.get("source_timestamp"), intraday.get("source_timestamp")) if value],
            "daily_trend": daily.get("daily_trend"), "weekly_trend": daily.get("weekly_trend"),
            "relative_strength_20d_vs_spy_pct": daily.get("relative_strength_20d_vs_spy_pct"),
            "daily_volume_ratio_20d": daily.get("volume_ratio_20d"),
            "vwap": intraday.get("vwap") if intraday.get("available") else None,
            "intraday_relative_volume": intraday.get("relative_volume") if intraday.get("available") else None,
            "confirmation": "Options observations are corroborating research evidence only and never override price invalidation."}
