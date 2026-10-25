"""Point-in-time market maps for research; never an execution or order service."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Protocol

from app.config import settings
from app.engines.indicators import average_true_range, ema, session_vwap
from app.engines.market import ET, regular_session_bars, resample
from app.market_data.base import AggregateBatch, MarketDataError
from app.market_data.massive import MassiveMarketDataProvider
from app.models import Bar
from app.versioning import ENGINE_VERSION, PARAMETER_VERSION, SETUP_RULES_VERSION

DEFAULT_SYMBOLS = ("QQQ", "SPY", "IWM", "SMH", "AMD", "NVDA", "MU", "CRWV")
REPORT_SCHEMA_VERSION = "market-intelligence-v1"


class IntelligenceProvider(Protocol):
    async def get_daily_bars(self, symbol: str, start: date, end: date,
                             adjusted: bool = True) -> AggregateBatch: ...
    async def get_minute_bars(self, symbol: str, start: date, end: date) -> list[Bar]: ...
    async def get_stock_snapshot(self, symbol: str) -> dict: ...
    async def get_active_option_contracts(self, underlying_symbol: str, as_of: date) -> list[dict]: ...
    async def get_options_chain_snapshot(self, underlying_symbol: str, expiration_date: str,
                                         strike_low: float, strike_high: float) -> list[dict]: ...


def _plain(value: Any) -> Any:
    if isinstance(value, (datetime, date)): return value.isoformat()
    if isinstance(value, tuple): return [_plain(item) for item in value]
    if isinstance(value, list): return [_plain(item) for item in value]
    if isinstance(value, dict): return {key: _plain(item) for key, item in value.items()}
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _checksum(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


class IntelligenceStore:
    """Small append-oriented SQLite store for source facts, reports and outcomes."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript("""
        CREATE TABLE IF NOT EXISTS source_batches (
          batch_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
          adjusted INTEGER NOT NULL, request_id TEXT, provider_status TEXT NOT NULL,
          retrieved_at TEXT NOT NULL, first_timestamp TEXT NOT NULL, last_timestamp TEXT NOT NULL,
          row_count INTEGER NOT NULL, payload_checksum TEXT NOT NULL, payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS intelligence_reports (
          report_id TEXT PRIMARY KEY, report_date TEXT NOT NULL, generated_at TEXT NOT NULL,
          observation_cutoff TEXT NOT NULL, schema_version TEXT NOT NULL,
          engine_version TEXT NOT NULL, parameter_version TEXT NOT NULL,
          setup_rules_version TEXT NOT NULL, input_checksum TEXT NOT NULL,
          report_checksum TEXT NOT NULL, report_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS hypotheses (
          hypothesis_id TEXT PRIMARY KEY, report_id TEXT NOT NULL, symbol TEXT NOT NULL,
          playbook TEXT NOT NULL, direction TEXT NOT NULL, trigger REAL,
          invalidation REAL, targets_json TEXT NOT NULL, source_timestamp TEXT NOT NULL,
          hypothesis_json TEXT NOT NULL, FOREIGN KEY(report_id) REFERENCES intelligence_reports(report_id)
        );
        CREATE TABLE IF NOT EXISTS next_session_outcomes (
          hypothesis_id TEXT PRIMARY KEY, session_date TEXT NOT NULL, status TEXT NOT NULL,
          evaluated_at TEXT NOT NULL, outcome_json TEXT NOT NULL,
          FOREIGN KEY(hypothesis_id) REFERENCES hypotheses(hypothesis_id)
        );
        """)
        self.connection.commit()

    def save_batch(self, batch: AggregateBatch) -> str:
        payload = [bar.model_dump(mode="json") for bar in batch.bars]
        checksum = _checksum(payload)
        batch_id = _checksum({"symbol": batch.symbol, "timeframe": batch.timespan,
                              "adjusted": batch.adjusted, "payload": checksum})
        self.connection.execute("""INSERT OR IGNORE INTO source_batches VALUES
          (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
            batch_id, batch.symbol, batch.timespan, int(batch.adjusted), batch.request_id,
            batch.provider_status, batch.retrieved_at.isoformat(), batch.bars[0].timestamp.isoformat(),
            batch.bars[-1].timestamp.isoformat(), len(batch.bars), checksum, canonical_json(payload)))
        self.connection.commit()
        return batch_id

    def save_report(self, report: dict, input_checksum: str) -> str:
        content = canonical_json(report)
        report_checksum = hashlib.sha256(content.encode()).hexdigest()
        report_id = _checksum({"date": report["report_date"], "input": input_checksum,
                               "schema": REPORT_SCHEMA_VERSION})
        self.connection.execute("""INSERT OR REPLACE INTO intelligence_reports VALUES
          (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
            report_id, report["report_date"], report["generated_at"], report["observation_cutoff"],
            REPORT_SCHEMA_VERSION, ENGINE_VERSION, PARAMETER_VERSION, SETUP_RULES_VERSION,
            input_checksum, report_checksum, content))
        self.connection.execute("DELETE FROM hypotheses WHERE report_id = ?", (report_id,))
        for playbook in ("intraday", "swing"):
            for setup in report[f"{playbook}_playbook"]:
                hypothesis_id = _checksum({"report": report_id, "playbook": playbook,
                                           "symbol": setup["symbol"]})
                self.connection.execute("""INSERT INTO hypotheses VALUES
                  (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                    hypothesis_id, report_id, setup["symbol"], playbook, setup["direction"],
                    setup.get("trigger"), setup.get("invalidation"),
                    canonical_json(setup.get("targets", [])), setup["source_timestamp"],
                    canonical_json(setup)))
        self.connection.commit()
        return report_id

    def record_available_outcomes(self, batches: dict[str, AggregateBatch], evaluated_at: datetime) -> int:
        """Evaluate older hypotheses only after a later daily bar is available."""
        rows = self.connection.execute("""SELECT h.hypothesis_id, h.symbol, h.source_timestamp,
          h.hypothesis_json FROM hypotheses h LEFT JOIN next_session_outcomes o
          ON o.hypothesis_id=h.hypothesis_id WHERE o.hypothesis_id IS NULL""").fetchall()
        recorded = 0
        for hypothesis_id, symbol, source_timestamp, payload in rows:
            batch = batches.get(symbol)
            if not batch: continue
            source_date = datetime.fromisoformat(source_timestamp).astimezone(ET).date()
            future = next((bar for bar in batch.bars
                           if bar.timestamp.astimezone(ET).date() > source_date), None)
            if future is None: continue
            hypothesis = json.loads(payload)
            trigger, invalidation = hypothesis.get("trigger"), hypothesis.get("invalidation")
            targets = hypothesis.get("targets") or []
            direction = hypothesis.get("direction")
            status = "NOT_TRIGGERED"
            if direction in {"LONG", "SHORT"} and trigger is not None and invalidation is not None:
                triggered = future.high >= trigger if direction == "LONG" else future.low <= trigger
                stopped = future.low <= invalidation if direction == "LONG" else future.high >= invalidation
                target_hits = [future.high >= target if direction == "LONG" else future.low <= target
                               for target in targets]
                if triggered and stopped: status = "AMBIGUOUS_STOP_FIRST"
                elif triggered and len(target_hits) > 1 and target_hits[1]: status = "TARGET_2"
                elif triggered and target_hits and target_hits[0]: status = "TARGET_1"
                elif triggered: status = "OPEN_AFTER_SESSION"
            outcome = {"session_open": future.open, "session_high": future.high,
                       "session_low": future.low, "session_close": future.close,
                       "policy": "Daily OHLC collision is conservatively classified stop-first"}
            self.connection.execute("INSERT INTO next_session_outcomes VALUES (?, ?, ?, ?, ?)",
                (hypothesis_id, future.timestamp.astimezone(ET).date().isoformat(), status,
                 evaluated_at.isoformat(), canonical_json(outcome)))
            recorded += 1
        self.connection.commit()
        return recorded

    def close(self) -> None:
        self.connection.close()


def rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) <= period: return None
    changes = [right - left for left, right in zip(values, values[1:])][-period:]
    gains = sum(max(change, 0) for change in changes) / period
    losses = sum(max(-change, 0) for change in changes) / period
    if losses == 0: return 100.0
    return 100 - 100 / (1 + gains / losses)


def _weekly_bars(bars: list[Bar]) -> list[Bar]:
    groups: dict[tuple[int, int], list[Bar]] = defaultdict(list)
    for bar in bars:
        local = bar.timestamp.astimezone(ET)
        iso = local.isocalendar()
        groups[(iso.year, iso.week)].append(bar)
    return [Bar(timestamp=group[0].timestamp, open=group[0].open,
                high=max(item.high for item in group), low=min(item.low for item in group),
                close=group[-1].close, volume=sum(item.volume for item in group))
            for _, group in sorted(groups.items())]


def _swings(bars: list[Bar], window: int = 2, count: int = 3) -> dict[str, list[float]]:
    highs, lows = [], []
    for index in range(window, len(bars) - window):
        neighbours = bars[index-window:index] + bars[index+1:index+window+1]
        if all(bars[index].high > item.high for item in neighbours): highs.append(bars[index].high)
        if all(bars[index].low < item.low for item in neighbours): lows.append(bars[index].low)
    return {"highs": highs[-count:], "lows": lows[-count:]}


def _return(closes: list[float], sessions: int) -> float | None:
    return ((closes[-1] / closes[-sessions-1]) - 1) * 100 if len(closes) > sessions else None


def daily_features(batch: AggregateBatch, benchmark: AggregateBatch | None = None) -> dict:
    bars = list(batch.bars)
    closes = [bar.close for bar in bars]
    weekly = _weekly_bars(bars)
    completed_week = weekly[-2] if len(weekly) > 1 else None
    prior = bars[-2] if len(bars) > 1 else None
    ema20, ema50 = ema(closes, 20)[-1], ema(closes, 50)[-1]
    atr14 = average_true_range(bars, 14)
    ret20 = _return(closes, 20)
    benchmark_return = _return([bar.close for bar in benchmark.bars], 20) if benchmark else None
    volume_average = mean(bar.volume for bar in bars[-21:-1]) if len(bars) > 1 else None
    price = bars[-1].close
    trend = "BULLISH" if price > ema20 > ema50 else "BEARISH" if price < ema20 < ema50 else "MIXED"
    levels = sorted(set(_swings(bars)["highs"] + _swings(bars)["lows"] +
                        ([prior.high, prior.low, prior.close] if prior else []) +
                        ([completed_week.high, completed_week.low] if completed_week else [])))
    return {
        "symbol": batch.symbol, "source_timestamp": bars[-1].timestamp.isoformat(),
        "adjusted": batch.adjusted, "close": price,
        "previous_day": ({"open": prior.open, "high": prior.high, "low": prior.low,
                          "close": prior.close, "volume": prior.volume} if prior else None),
        "previous_week": ({"open": completed_week.open, "high": completed_week.high,
                           "low": completed_week.low, "close": completed_week.close,
                           "volume": completed_week.volume} if completed_week else None),
        "ema20": ema20, "ema50": ema50, "rsi14": rsi(closes), "atr14": atr14,
        "return_20d_pct": ret20,
        "relative_strength_20d_vs_spy_pct": (ret20 - benchmark_return
                                               if ret20 is not None and benchmark_return is not None else None),
        "volume_ratio_20d": (bars[-1].volume / volume_average if volume_average else None),
        "daily_trend": trend, "weekly_trend": (_weekly_trend(weekly)),
        "swing_levels": _swings(bars), "support_resistance": levels[-10:],
    }


def _weekly_trend(bars: list[Bar]) -> str:
    if len(bars) < 4: return "INSUFFICIENT_DATA"
    return "BULLISH" if bars[-1].close > bars[-2].close > bars[-3].close else \
           "BEARISH" if bars[-1].close < bars[-2].close < bars[-3].close else "MIXED"


def intraday_features(bars: list[Bar]) -> dict:
    regular = regular_session_bars(bars)
    if not regular: return {"available": False, "reason": "No regular-session minute bars"}
    day = regular[-1].timestamp.astimezone(ET).date()
    current = [bar for bar in regular if bar.timestamp.astimezone(ET).date() == day]
    if len(current) < 30:
        return {"available": False, "reason": "Fewer than 30 regular-session minute bars"}
    frames = {}
    for label, minutes in (("5m", 5), ("15m", 15), ("1h", 60)):
        sampled = resample(current, minutes)
        closes = [bar.close for bar in sampled]
        e9, e21 = ema(closes, 9)[-1], ema(closes, 21)[-1]
        frames[label] = {"close": closes[-1], "ema9": e9, "ema21": e21,
                         "structure": "BULLISH" if closes[-1] > e9 > e21 else
                         "BEARISH" if closes[-1] < e9 < e21 else "MIXED"}
    opening = current[:30]
    completed_volumes = [bar.volume for bar in current[:-1]]
    return {"available": True, "source_timestamp": current[-1].timestamp.isoformat(),
            "vwap": session_vwap(current), "opening_range_high": max(bar.high for bar in opening),
            "opening_range_low": min(bar.low for bar in opening), "frames": frames,
            "relative_volume": (current[-1].volume / mean(completed_volumes[-20:])
                                if completed_volumes[-20:] and mean(completed_volumes[-20:]) else None)}


def market_regime(features: dict[str, dict]) -> dict:
    leaders = [features[symbol] for symbol in ("SPY", "QQQ", "IWM", "SMH") if symbol in features]
    bullish = sum(item["daily_trend"] == "BULLISH" for item in leaders)
    bearish = sum(item["daily_trend"] == "BEARISH" for item in leaders)
    label = "RISK_ON" if bullish >= 3 else "RISK_OFF" if bearish >= 3 else "MIXED"
    return {"label": label, "method": "Trend agreement across SPY, QQQ, IWM and SMH ETF proxies",
            "bullish_proxies": bullish, "bearish_proxies": bearish, "instruments": len(leaders),
            "observation": True, "estimate": False}


def _risk_levels(price: float, atr: float | None, direction: str) -> tuple[float, list[float]]:
    unit = atr or price * .02
    if direction == "LONG": return price - unit, [price + unit, price + 2 * unit]
    return price + unit, [price - unit, price - 2 * unit]


def swing_setup(feature: dict) -> dict:
    direction = "LONG" if feature["daily_trend"] == feature["weekly_trend"] == "BULLISH" else \
                "SHORT" if feature["daily_trend"] == feature["weekly_trend"] == "BEARISH" else "NEUTRAL"
    price, atr = feature["close"], feature["atr14"]
    invalidation, targets = _risk_levels(price, atr, direction if direction != "NEUTRAL" else "LONG")
    trigger = price + (0.1 * atr if direction == "LONG" and atr else 0) if direction != "SHORT" else price - (0.1 * atr if atr else 0)
    risk = abs(trigger - invalidation) if direction != "NEUTRAL" else None
    return {"symbol": feature["symbol"], "timeframe": "DAILY/WEEKLY", "direction": direction,
            "source_timestamp": feature["source_timestamp"], "trigger": trigger if direction != "NEUTRAL" else None,
            "confirmation": "Daily close beyond trigger with daily trend and 20-day relative strength aligned",
            "invalidation": invalidation if direction != "NEUTRAL" else None,
            "targets": targets if direction != "NEUTRAL" else [],
            "estimated_reward_risk": ([abs(target-trigger)/risk for target in targets] if risk else []),
            "momentum": {"rsi14": feature["rsi14"], "relative_strength_20d_vs_spy_pct": feature["relative_strength_20d_vs_spy_pct"]},
            "catalysts": {"status": "NOT_INTEGRATED", "observation": "No catalyst claim is made."},
            "no_trade_if": ["Daily and weekly structures are not aligned", "Relative strength contradicts direction",
                            "Trigger gaps beyond the first target", "Data is stale or incomplete"]}


def intraday_setup(feature: dict, intra: dict) -> dict:
    frames = intra.get("frames", {})
    structures = [frame.get("structure") for frame in frames.values()]
    direction = "LONG" if structures and all(item == "BULLISH" for item in structures) else \
                "SHORT" if structures and all(item == "BEARISH" for item in structures) else "NEUTRAL"
    price, atr = feature["close"], feature["atr14"]
    if direction == "LONG": trigger = max(intra["opening_range_high"], intra["vwap"]); invalidation = min(intra["opening_range_low"], intra["vwap"])
    elif direction == "SHORT": trigger = min(intra["opening_range_low"], intra["vwap"]); invalidation = max(intra["opening_range_high"], intra["vwap"])
    else: trigger = invalidation = None
    risk = abs(trigger - invalidation) if trigger is not None else None
    targets = ([trigger + risk, trigger + 2*risk] if direction == "LONG" else
               [trigger - risk, trigger - 2*risk] if direction == "SHORT" else [])
    return {"symbol": feature["symbol"], "timeframe": "5m/15m/1h", "direction": direction,
            "source_timestamp": intra.get("source_timestamp", feature["source_timestamp"]), "trigger": trigger,
            "confirmation": "Completed 5m break/retest of trigger with price on the directional side of VWAP",
            "invalidation": invalidation, "targets": targets,
            "estimated_reward_risk": ([1.0, 2.0] if risk else []),
            "relative_volume": intra.get("relative_volume"), "atr14_daily": atr,
            "no_trade_if": ["Opening range or VWAP is unavailable", "5m/15m/1h structures conflict",
                            "Retest fails or closes through invalidation", "Data is not current for the session"]}


def options_report(symbol: str, snapshot: dict, expiration: str, chain: list[dict]) -> dict:
    rows = []
    concentration: dict[float, float] = defaultdict(float)
    for item in chain:
        details, quote, greeks = item.get("details") or {}, item.get("last_quote") or {}, item.get("greeks") or {}
        bid, ask = quote.get("bid"), quote.get("ask")
        midpoint = (bid + ask) / 2 if isinstance(bid, (int, float)) and isinstance(ask, (int, float)) and ask >= bid else None
        spread_pct = ((ask - bid) / midpoint * 100) if midpoint else None
        oi = item.get("open_interest")
        strike = details.get("strike_price")
        if isinstance(strike, (int, float)) and isinstance(oi, (int, float)): concentration[float(strike)] += float(oi)
        rows.append({"ticker": details.get("ticker"), "type": details.get("contract_type"), "strike": strike,
                     "expiration": details.get("expiration_date"), "bid": bid, "ask": ask,
                     "midpoint": midpoint, "spread_pct": spread_pct, "open_interest": oi,
                     "volume": (item.get("day") or {}).get("volume"), "iv": item.get("implied_volatility"),
                     "greeks": {key: greeks.get(key) for key in ("delta", "gamma", "theta", "vega")},
                     "quote_timeframe": quote.get("timeframe")})
    liquid = [row for row in rows if isinstance(row["open_interest"], (int, float)) and row["open_interest"] >= 100
              and isinstance(row["spread_pct"], (int, float)) and row["spread_pct"] <= 10]
    underlying_timestamp = snapshot.get("updated")
    return {"symbol": symbol, "expiration": expiration, "underlying_price":
            (snapshot.get("lastTrade") or {}).get("p") or (snapshot.get("day") or {}).get("c"),
            "underlying_raw_timestamp": underlying_timestamp, "contracts_observed": len(rows),
            "liquid_contracts": len(liquid),
            "strike_concentration": [{"strike": strike, "open_interest": oi} for strike, oi in
                                     sorted(concentration.items(), key=lambda pair: pair[1], reverse=True)[:10]],
            "contracts": rows, "observations": ["Open interest is provider-returned and generally prior-day data",
              "Liquidity screen requires OI >= 100 and quoted spread <= 10%"],
            "estimates": ["Midpoint and spread percentage are calculated from the returned bid/ask"],
            "prohibited_inference": "No unusual or institutional activity is inferred."}


async def build_market_map(provider: IntelligenceProvider, store: IntelligenceStore, as_of: date,
                           symbols: tuple[str, ...] = DEFAULT_SYMBOLS, include_options: bool = True) -> dict:
    start = as_of - timedelta(days=400)
    adjusted: dict[str, AggregateBatch] = {}
    raw: dict[str, AggregateBatch] = {}
    errors: list[dict] = []
    for symbol in symbols:
        try:
            adjusted[symbol] = await provider.get_daily_bars(symbol, start, as_of, True)
            raw[symbol] = await provider.get_daily_bars(symbol, start, as_of, False)
            store.save_batch(adjusted[symbol]); store.save_batch(raw[symbol])
        except MarketDataError as exc:
            errors.append({"symbol": symbol, "component": "daily", "error": str(exc)})
    benchmark = adjusted.get("SPY")
    features = {symbol: daily_features(batch, benchmark) for symbol, batch in adjusted.items()}
    intraday: dict[str, dict] = {}
    for symbol, batch in adjusted.items():
        latest_day = batch.bars[-1].timestamp.astimezone(ET).date()
        try: intraday[symbol] = intraday_features(await provider.get_minute_bars(symbol, latest_day, latest_day))
        except MarketDataError as exc: intraday[symbol] = {"available": False, "reason": str(exc)}
    option_reports = []
    if include_options:
        for symbol in ("QQQ", "SPY"):
            try:
                snapshot = await provider.get_stock_snapshot(symbol)
                contracts = await provider.get_active_option_contracts(symbol, as_of)
                expirations = sorted({item["expiration_date"] for item in contracts})
                if not expirations: raise MarketDataError(f"No option expirations returned for {symbol}")
                expiration = expirations[0]
                price = (snapshot.get("lastTrade") or {}).get("p") or (snapshot.get("day") or {}).get("c")
                if not isinstance(price, (int, float)): raise MarketDataError(f"No underlying price returned for {symbol}")
                chain = await provider.get_options_chain_snapshot(symbol, expiration, price * .85, price * 1.15)
                option_reports.append(options_report(symbol, snapshot, expiration, chain))
            except MarketDataError as exc:
                errors.append({"symbol": symbol, "component": "options", "error": str(exc)})
    cutoff = max((batch.bars[-1].timestamp for batch in adjusted.values()), default=datetime.now(timezone.utc))
    report = {"schema_version": REPORT_SCHEMA_VERSION, "report_date": as_of.isoformat(),
              "generated_at": datetime.now(timezone.utc).isoformat(), "observation_cutoff": cutoff.isoformat(),
              "point_in_time_policy": "Only observations timestamped on or before the report cutoff are inputs",
              "market_regime": market_regime(features), "daily_features": features,
              "intraday_features": intraday,
              "intraday_playbook": [intraday_setup(feature, intraday[symbol]) for symbol, feature in features.items()
                                    if intraday.get(symbol, {}).get("available")],
              "swing_playbook": [swing_setup(feature) for feature in features.values()],
              "options_intelligence": option_reports, "data_errors": errors,
              "outcomes": {"status": "PENDING", "reason": "Next-session outcomes require a later session"}}
    report["prior_outcomes_recorded"] = store.record_available_outcomes(adjusted, datetime.now(timezone.utc))
    input_checksum = _checksum({symbol: [store.save_batch(batch), store.save_batch(raw[symbol])]
                                for symbol, batch in adjusted.items() if symbol in raw})
    report["report_id"] = store.save_report(report, input_checksum)
    return report


def render_markdown(report: dict) -> str:
    lines = [f"# TradeHub Market Map — {report['report_date']}", "",
             f"Observation cutoff: `{report['observation_cutoff']}`", "",
             f"Market regime: **{report['market_regime']['label']}**", "",
             "## Swing playbook", ""]
    for item in report["swing_playbook"]:
        lines.append(f"- **{item['symbol']}** — {item['direction']}; trigger {item['trigger']}; "
                     f"invalidation {item['invalidation']}; targets {item['targets']}")
    lines += ["", "## Intraday playbook", ""]
    for item in report["intraday_playbook"]:
        lines.append(f"- **{item['symbol']}** — {item['direction']}; trigger {item['trigger']}; "
                     f"invalidation {item['invalidation']}; targets {item['targets']}")
    lines += ["", "## Options observations", ""]
    for item in report["options_intelligence"]:
        lines.append(f"- **{item['symbol']} {item['expiration']}** — {item['contracts_observed']} contracts; "
                     f"{item['liquid_contracts']} passed the first-pass liquidity screen.")
    if report["data_errors"]:
        lines += ["", "## Data limitations", ""] + [f"- {item}" for item in report["data_errors"]]
    lines += ["", "No trade execution is implemented. Observations are research inputs, not orders.", ""]
    return "\n".join(lines)


async def _run(args: argparse.Namespace) -> None:
    if not settings.massive_api_key: raise SystemExit("MASSIVE_API_KEY is required")
    provider = MassiveMarketDataProvider(settings.massive_api_key, settings.massive_base_url)
    store = IntelligenceStore(Path(args.database))
    try:
        report = await build_market_map(provider, store, date.fromisoformat(args.as_of),
                                        tuple(item.strip().upper() for item in args.symbols.split(",") if item.strip()),
                                        not args.skip_options)
    finally:
        await provider.close(); store.close()
    output = Path(args.output_dir); output.mkdir(parents=True, exist_ok=True)
    stem = f"market_map_{args.as_of}"
    (output / f"{stem}.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    (output / f"{stem}.md").write_text(render_markdown(report))
    print(json.dumps({"report_id": report["report_id"], "json": str(output / f"{stem}.json"),
                      "markdown": str(output / f"{stem}.md"), "errors": report["data_errors"]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build a reproducible TradeHub nightly market map")
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--database", default="data/market_intelligence.sqlite3")
    parser.add_argument("--output-dir", default="data/market_intelligence_reports")
    parser.add_argument("--skip-options", action="store_true")
    asyncio.run(_run(parser.parse_args()))
