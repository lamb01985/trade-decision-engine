"""Local report operations around the existing Phase 2 engine, no cloud services."""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from app.config import settings
from app.freshness import ET, is_trading_day
from app.market_data.base import MarketDataError
from app.market_data.massive import MassiveMarketDataProvider
from app.market_intelligence import (IntelligenceStore, DEFAULT_SYMBOLS, build_market_map,
    render_markdown, _phase2_options_report, _snapshot_market_timestamp, _provider_timestamp,
    intraday_features)

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / 'data/market_intelligence_reports'
LOCAL = ROOT / 'data/local_intelligence'
SOURCE_DB = ROOT / 'data/market_intelligence.sqlite3'
PREMARKET_START = time(4, 0)
REGULAR_OPEN = time(9, 30)
MAX_OBSERVATION_AGE_SECONDS = 15 * 60


def previous_session(session: date) -> date:
    day = session - timedelta(days=1)
    while not is_trading_day(day):
        day -= timedelta(days=1)
    return day


def target_session(now: datetime) -> date:
    local = now.astimezone(ET)
    day = local.date() + (timedelta(days=1) if local.time().replace(tzinfo=None) >= time(16) else timedelta())
    while not is_trading_day(day):
        day += timedelta(days=1)
    return day


def read_report(session: date) -> tuple[dict, Path]:
    manifest = LOCAL / f'latest_nightly_{session}.json'
    path = Path(json.loads(manifest.read_text())['report']) if manifest.is_file() else REPORTS / f'market_map_{session}.json'
    return json.loads(path.read_text()), path


def verify_report(report: dict, session: date) -> dict:
    if not is_trading_day(session) or report.get('report_date') != session.isoformat():
        raise ValueError('Report does not match the requested trading session')
    if report.get('schema_version') != 'market-intelligence-v1':
        raise ValueError('Unsupported Phase 2 report schema')
    expected = previous_session(session)
    opening = datetime.combine(session, REGULAR_OPEN, ET)
    cutoff = datetime.fromisoformat(report['observation_cutoff'])
    generated = datetime.fromisoformat(report['generated_at'])
    if cutoff.tzinfo is None or generated.tzinfo is None or cutoff >= opening or cutoff > generated:
        raise ValueError('Report cutoff is invalid for pre-session preparation')
    features = report.get('daily_features', {})
    for symbol in ('QQQ', 'SPY'):
        if symbol not in features:
            raise ValueError(f'{symbol} daily observations are missing')
    for symbol, feature in features.items():
        source = datetime.fromisoformat(feature['source_timestamp'])
        if source.tzinfo is None or source.astimezone(ET).date() != expected or feature.get('session_date') != expected.isoformat():
            raise ValueError(f'{symbol} does not contain the expected completed session')
    for row in report.get('intraday_playbook', []):
        source = datetime.fromisoformat(row['source_timestamp'])
        if source.tzinfo is None or source.astimezone(ET).date() != expected or source >= opening:
            raise ValueError('Intraday scenario source is not the completed prior session')
    return {'trading_session': session.isoformat(), 'completed_equity_session': expected.isoformat(),
            'observation_cutoff': cutoff.isoformat(), 'generated_at': generated.isoformat(),
            'status': 'VERIFIED_PRIOR_SESSION_PREPARATION',
            'limitations': ['Intraday scenarios use prior-session bars, not current-session signals.',
                           'The legacy previous_day field is the bar before the latest completed bar.',
                           'An elapsed-time stale label over the weekend does not make this a live report.']}


def bars_for(symbol: str, database: Path, session: date) -> list[dict]:
    if not database.is_file():
        return []
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        rows = db.execute('SELECT payload_json FROM source_batches WHERE symbol=? AND adjusted=1 ORDER BY last_timestamp DESC', (symbol,)).fetchall()
    for (payload,) in rows:
        bars = [b for b in json.loads(payload) if datetime.fromisoformat(b['timestamp'].replace('Z', '+00:00')).astimezone(ET).date() < session]
        if bars and datetime.fromisoformat(bars[-1]['timestamp'].replace('Z', '+00:00')).astimezone(ET).date() == previous_session(session):
            return bars
    return []


def number(value) -> str:
    return 'unavailable' if value is None else f'{value:,.2f}' if isinstance(value, (int, float)) else str(value)


def monday_summary(report: dict, database: Path) -> str:
    session = date.fromisoformat(report['report_date'])
    check = verify_report(report, session)
    lines = [f'# TradeHub preparation: {session.strftime("%A %B %d, %Y")}', '',
             f'Cached point-in-time report, generated {report["generated_at"]}.',
             f'Completed equity session: {check["completed_equity_session"]}. Observation cutoff: {report["observation_cutoff"]}.',
             f'Market regime: {report["market_regime"]["label"]}. {report["market_regime"].get("method", "")}', '',
             'Research scenarios are unvalidated and are not current-session signals or trade instructions.',
             'No institutional identity, opening/closing intent or verified dealer direction is inferred.', '']
    for symbol in ('QQQ', 'SPY'):
        f = report['daily_features'][symbol]
        bars = bars_for(symbol, database, session)
        completed = bars[-1] if bars else None
        lines += [f'## {symbol}', f'Completed-session close: {number(f["close"])}; daily trend {f["daily_trend"]}; weekly trend {f["weekly_trend"]}.']
        if completed:
            lines += [f'Completed-session high / low: {number(completed["high"])} / {number(completed["low"])} (stored source bar, not the legacy previous_day field).']
        else:
            lines += ['Completed-session high / low unavailable in the local source archive.']
        lines += ['Structural levels from the unchanged engine: ' + ', '.join(number(x) for x in f['support_resistance']) + '.',
                  f'Source timestamp: {f["source_timestamp"]}; completed-session close time: {f.get("session_close", "unavailable")}.']
        for label, key in [('Intraday, prior-session structure', 'intraday_playbook'), ('Swing', 'swing_playbook')]:
            row = next((x for x in report[key] if x['symbol'] == symbol), None)
            if row:
                lines += [f'{label}: {row["direction"]}; trigger {number(row["trigger"])}; invalidation {number(row["invalidation"])}; targets ' + (', '.join(number(x) for x in row['targets']) or 'none') + '.',
                          f'Confirmation: {row["confirmation"]}. Source: {row["source_timestamp"]}.',
                          'Stand aside: ' + '; '.join(row['no_trade_if']) + '.']
            else:
                lines += [label + ': unavailable.']
        o = next((x for x in report.get('options_flow_intelligence', []) if x['symbol'] == symbol), None)
        if not o:
            lines += ['Options intelligence unavailable.', '']; continue
        lines += [f'Options source cutoff: {o["source_cutoff"]}; {o["contracts_observed"]} observed contracts; OI history: {o["data_quality"]["snapshot_history_status"]}.',
                  'Call / put OI by expiration (prior-session OI, not directional):']
        for expiry in o['expirations']:
            rows = o['positioning']['by_expiration']
            call = next((x['open_interest'] for x in rows if x['expiration'] == expiry and x['type'] == 'call'), None)
            put = next((x['open_interest'] for x in rows if x['expiration'] == expiry and x['type'] == 'put'), None)
            move = next((x.get('straddle_midpoint_expected_move') for x in o['volatility']['expected_moves'] if x['expiration'] == expiry), None)
            lines += [f'- {expiry}: calls {number(call)}; puts {number(put)}; ATM straddle expected move {number(move)} price units.']
        ranked = sorted(o['positioning']['by_strike'], key=lambda x: x['open_interest'], reverse=True)[:6]
        lines += ['Largest observed strike/type OI rows across included expirations: ' + '; '.join(f'{x["type"]} {number(x["strike"])}: {number(x["open_interest"])}' for x in ranked) + '.']
        unusual = o['unusual_activity']['contracts']
        sufficient = [x for x in unusual if x['baseline_status'] == 'SUFFICIENT']
        flagged = [x for x in unusual if x['flagged']]
        lines += [f'Unusual activity: {len(flagged)} flagged among {len(sufficient)} contracts with sufficient baselines; {len(unusual)-len(sufficient)} lack sufficient baselines. This does not establish normal activity for unmeasured contracts.']
        for x in flagged:
            lines += [f'- {x["ticker"]}: volume {number(x["volume"])}; {number(x["volume_baseline_ratio"])}x baseline from {x["baseline_observations"]} observations.']
        flow = o['trade_flow']
        lines += [f'Bounded flow: {flow.get("status")}; trades {flow.get("sampled_trades", "unavailable")}; quotes {flow.get("sampled_quotes", "unavailable")}; trade pagination complete {flow.get("trades_pagination_complete", False)}; quote pagination complete {flow.get("quotes_pagination_complete", False)}.',
                  'Flow is a recent sample of one selected contract, not total market flow. Aggressor side is estimated from timestamp-aligned quotes.',
                  f'IV history: {o["volatility"].get("history_status")}; rank {number(o["volatility"].get("iv_rank"))}; percentile {number(o["volatility"].get("iv_percentile"))}.',
                  'Gamma: ' + o['gamma']['limitation'], '']
    lines += ['## Data quality', *['- ' + x for x in report['data_quality'].get('warnings', [])],
              *['- ' + str(x) for x in report.get('data_errors', [])], '',
              'Expected moves are option-price estimates, not guaranteed ranges. Reconfirm levels against fresh Monday observations.', '']
    return '\n'.join(lines)


def backup(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)


def managed_store(run: Path) -> IntelligenceStore:
    target = LOCAL / 'state.sqlite3'
    if not target.exists() and SOURCE_DB.exists():
        backup(SOURCE_DB, target)
    if target.exists():
        backup(target, run / 'state_before.sqlite3')
    return IntelligenceStore(target)


def observation_status(timestamp: datetime | None, now: datetime, session: date) -> str:
    if timestamp is None:
        return 'UNAVAILABLE'
    age = (now - timestamp).total_seconds()
    if age < 0:
        return 'FUTURE_TIMESTAMP'
    if timestamp.astimezone(ET).date() != session:
        return 'PRIOR_SESSION_OBSERVATION'
    return 'CURRENT_SESSION_RECENT' if age <= MAX_OBSERVATION_AGE_SECONDS else 'CURRENT_SESSION_STALE'


async def collect_premarket(provider, store: IntelligenceStore, cached: dict, session: date, now: datetime) -> dict:
    """Collect current-session evidence separately; never relabel cached Friday bars."""
    result = {'schema_version': 'tradehub-premarket-v1', 'trading_session': session.isoformat(),
              'retrieved_at': now.isoformat(), 'mode': 'FRESH_PROVIDER_REQUESTS', 'symbols': {},
              'signal_status': 'NOT_VALIDATED_NO_LIVE_TRADE_SIGNALS',
              'cached_context_report_id': cached.get('report_id'),
              'cached_context_cutoff': cached['observation_cutoff'],
              'limitations': ['Stock observations may be delayed; recent does not prove real-time entitlement.',
                             'Options OI normally represents the previous session. Quotes may remain from Friday before options open.',
                             'No options observation is labeled current solely because it was just retrieved.']}
    store.connection.execute('CREATE TABLE IF NOT EXISTS premarket_captures (capture_id TEXT PRIMARY KEY, session_date TEXT NOT NULL, retrieved_at TEXT NOT NULL, payload_json TEXT NOT NULL)')
    for symbol in ('QQQ', 'SPY'):
        row = {'stock': {'status': 'UNAVAILABLE'}, 'current_session_minutes': {'status': 'UNAVAILABLE'}, 'options': {'status': 'UNAVAILABLE'}}
        result['symbols'][symbol] = row
        try:
            snapshot = await provider.get_stock_snapshot(symbol)
            timestamp = _snapshot_market_timestamp(snapshot)
            row['stock'] = {'status': observation_status(timestamp, now, session),
                            'source_timestamp': timestamp.isoformat() if timestamp else None, 'snapshot': snapshot}
        except MarketDataError:
            row['stock']['reason'] = 'Provider denied access, returned no data, or could not be reached.'
        try:
            bars = await provider.get_minute_bars(symbol, session, session)
            bars = [b for b in bars if b.timestamp.astimezone(ET).date() == session and b.timestamp <= now]
            row['current_session_minutes'] = {'status': observation_status(bars[-1].timestamp if bars else None, now, session),
                'source_timestamp': bars[-1].timestamp.isoformat() if bars else None,
                'bars': [b.model_dump(mode='json') for b in bars],
                'regular_session_structure': intraday_features(bars),
                'limitation': 'Premarket bars are observations. The existing regular-session opening range and VWAP signal gates are not premarket signals.'}
        except MarketDataError:
            row['current_session_minutes']['reason'] = 'No entitled current-session minute data returned.'
        try:
            cached_daily = cached['daily_features'][symbol]
            spot = ((row['stock'].get('snapshot', {}).get('lastTrade') or {}).get('p') or cached_daily['close'])
            contracts = await provider.get_active_option_contracts(symbol, session)
            expirations = sorted({c['expiration_date'] for c in contracts})[:3]
            if not expirations:
                raise MarketDataError('No expirations')
            groups = [{'expiration': expiry, 'contracts': await provider.get_options_chain_snapshot(symbol, expiry, spot * .85, spot * 1.15)} for expiry in expirations]
            valid_times = [_provider_timestamp((item.get('last_quote') or {}).get('last_updated')) for group in groups for item in group['contracts']]
            latest_quote = max((t for t in valid_times if t), default=None)
            # Engine flow requests use today's cutoff, never Friday's date. Preserve the actual quote timestamp separately.
            daily_bars = bars_for(symbol, LOCAL / 'state.sqlite3', session)
            options = await _phase2_options_report(provider, store, symbol, session,
                row['stock'].get('snapshot', {}), groups, cached_daily,
                row['current_session_minutes'].get('regular_session_structure', {}),
                [b['close'] for b in daily_bars], now)
            options['retrieval_cutoff'] = options.pop('source_cutoff')
            options['source_cutoff'] = latest_quote.isoformat() if latest_quote else None
            flow = options['trade_flow']
            if flow.get('status') == 'AVAILABLE' and not flow.get('sampled_trades'):
                flow['status'] = 'NO_CURRENT_SESSION_TRADES'
            row['options'] = {'status': observation_status(latest_quote, now, session),
                'source_timestamp': options['source_cutoff'], 'analytics': options,
                'daily_feature_context': 'CACHED_PRIOR_SESSION',
                'stock_confirmation_status': 'NOT_VALIDATED_FOR_CURRENT_SESSION',
                'limitation': 'This is the latest observed quote timestamp, not proof that every contract is current. Stale chain evidence is context only.'}
        except MarketDataError:
            row['options']['reason'] = 'Current options data unavailable under the configured entitlement or provider connection.'
    capture_id = now.strftime('%Y%m%dT%H%M%S%fZ')
    store.connection.execute('INSERT INTO premarket_captures VALUES (?, ?, ?, ?)', (capture_id, session.isoformat(), now.isoformat(), json.dumps(result)))
    store.connection.commit()
    return result


def premarket_markdown(result: dict) -> str:
    lines = [f'# TradeHub premarket observations: {result["trading_session"]}', '',
             f'Retrieved: {result["retrieved_at"]}. {result["signal_status"]}.',
             'Fresh provider requests do not imply fresh market observations.',
             f'Cached context cutoff: {result["cached_context_cutoff"]}.', '']
    for symbol, row in result['symbols'].items():
        lines += [f'## {symbol}']
        for component, evidence in row.items():
            lines += [f'- {component}: {evidence["status"]}; source {evidence.get("source_timestamp") or "unavailable"}.']
            if evidence.get('reason'):
                lines += ['  ' + evidence['reason']]
        lines += ['No current-session trading signal is validated by this collection.', '']
    return '\n'.join(lines + result['limitations'] + [''])


async def run(args) -> int:
    now = datetime.now(timezone.utc)
    session = date.fromisoformat(args.session) if args.session else target_session(now)
    if not is_trading_day(session):
        raise ValueError('Choose an exchange trading session')
    if args.action in ('verify', 'summary'):
        report, source = read_report(session)
        check = verify_report(report, session)
        if args.action == 'verify':
            print(json.dumps({**check, 'report': str(source)}, indent=2)); return 0
        database = LOCAL / 'state.sqlite3' if (LOCAL / 'state.sqlite3').is_file() else SOURCE_DB
        path = LOCAL / f'monday_summary_{session}.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(monday_summary(report, database))
        print(path.read_text()); print(f'Saved summary: {path}'); return 0
    if args.action == 'premarket' and not (now.astimezone(ET).date() == session and PREMARKET_START <= now.astimezone(ET).time().replace(tzinfo=None) < REGULAR_OPEN):
        raise ValueError('Premarket collection runs only on the target trading day between 04:00 and 09:30 America/New_York. Use summary tonight.')
    if args.action == 'nightly' and now >= datetime.combine(session, REGULAR_OPEN, ET):
        raise ValueError('Nightly map must precede the target session open. Choose the next trading session.')
    if not settings.massive_api_key:
        raise ValueError('MASSIVE_API_KEY is not configured in the existing local .env; no secret is printed.')
    LOCAL.mkdir(parents=True, exist_ok=True)
    with (LOCAL / 'refresh.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('A local refresh is already running; wait and retry') from None
        run_dir = LOCAL / 'runs' / (now.strftime('%Y%m%dT%H%M%S%fZ') + '_' + args.action)
        run_dir.mkdir(parents=True)
        provider = MassiveMarketDataProvider(settings.massive_api_key, settings.massive_base_url)
        store = managed_store(run_dir)
        try:
            if args.action == 'nightly':
                report = await build_market_map(provider, store, session, DEFAULT_SYMBOLS)
                verify_report(report, session)
                (run_dir / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
                (run_dir / 'report.md').write_text(render_markdown(report))
                (run_dir / 'summary.md').write_text(monday_summary(report, LOCAL / 'state.sqlite3'))
                # Partial reports remain archived but cannot replace a usable Monday map.
                if report['data_errors'] or any(s not in {o['symbol'] for o in report['options_flow_intelligence']} for s in ('QQQ', 'SPY')):
                    print(f'Partial live-request report archived at {run_dir}; prior cached map retained. Check provider access/network.'); return 2
                manifest = {'mode': 'FRESH_PROVIDER_REQUESTS', 'report': str(run_dir / 'report.json'), 'summary': str(run_dir / 'summary.md'), 'observation_cutoff': report['observation_cutoff']}
                (LOCAL / f'latest_nightly_{session}.json').write_text(json.dumps(manifest, indent=2) + '\n')
                print(f'Nightly refresh saved: {run_dir / "summary.md"}\nObservation cutoff: {report["observation_cutoff"]}\nOriginal Phase 2 report files and source database were preserved.')
            else:
                cached, _ = read_report(session)
                verify_report(cached, session)
                result = await collect_premarket(provider, store, cached, session, now)
                (run_dir / 'premarket.json').write_text(json.dumps(result, indent=2) + '\n')
                (run_dir / 'premarket.md').write_text(premarket_markdown(result))
                (LOCAL / f'latest_premarket_{session}.json').write_text(json.dumps({'report': str(run_dir / 'premarket.json'), 'summary': str(run_dir / 'premarket.md')}, indent=2) + '\n')
                print(premarket_markdown(result)); print(f'Saved premarket report: {run_dir / "premarket.md"}')
                return 0 if all(x['stock']['status'] == 'CURRENT_SESSION_RECENT' for x in result['symbols'].values()) else 2
        finally:
            await provider.close(); store.close()
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('verify', 'summary', 'nightly', 'premarket'))
    parser.add_argument('--session', help='Target trading date, defaults to today/next trading session in ET')
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(run(args)))
    except (ValueError, FileNotFoundError, KeyError, sqlite3.Error) as error:
        # Provider exceptions are sanitized by its adapter. Never print chained HTTP URLs.
        print(f'Local intelligence unavailable: {error}. Existing reports were retained.')
        raise SystemExit(2) from None


if __name__ == '__main__':
    main()
