"""Bounded, explicitly approved deployment-profile evidence. No raw output or recency promotion."""
import argparse
import asyncio
import json
import os
import time
from datetime import datetime, timezone
from app.market_data.api import configuration
from app.market_data.massive import MassiveMarketDataProvider, _live_context
from app.market_data.sessions import Sessions


def inspect_configuration():
    valid, _, _ = configuration()
    return {'configuration_valid': valid,
            'deployment_account_attested': os.getenv('MARKET_DATA_ACCOUNT_VERIFIED') == 'true',
            'rights_attested': os.getenv('MARKET_DATA_RIGHTS_VERIFIED') == 'true',
            'independent_budget': os.getenv('MASSIVE_BUDGET_ENFORCED') == 'true'}


async def probe():
    checks = inspect_configuration()
    if not all(checks.values()):
        return {'status': 'BLOCKED_CONFIGURATION_OR_ACCOUNT', 'checks': checks, 'provider_attempts': 0}
    day = Sessions().latest_closed(datetime.now(timezone.utc))
    if day is None:
        return {'status': 'SCHEDULE_UNKNOWN', 'provider_attempts': 0}
    provider = MassiveMarketDataProvider(os.environ['MASSIVE_API_KEY'])
    rows = []
    requests = []
    for symbol in ('QQQ', 'SPY', 'IWM'):
        requests.append((symbol, 'snapshot', f'/v2/snapshot/locale/us/markets/stocks/tickers/{symbol}', {}))
        requests.append((symbol, 'bars', f'/v2/aggs/ticker/{symbol}/range/1/minute/{day}/{day}', {'adjusted':'true','sort':'asc','limit':1}))
    requests += [('QQQ','options_reference','/v3/reference/options/contracts',{'underlying_ticker':'QQQ','expired':'false','limit':1}),
                 ('QQQ','options_snapshot','/v3/snapshot/options/QQQ',{'limit':1})]
    token = _live_context.set(time.monotonic()+90)
    try:
        for symbol, family, path, params in requests:
            try:
                payload = await provider._get_payload(path, params)
                if family == 'snapshot':
                    ticker = payload.get('ticker') or {}
                    valid = ticker.get('ticker') == symbol
                    observed = [field for field in ('lastTrade','lastQuote') if ticker.get(field)] if valid else []
                    empty = not observed
                else:
                    empty = not payload.get('results')
                    observed = ['results'] if not empty else []
                    valid = family != 'bars' or payload.get('ticker') == symbol
                rows.append({'symbol':symbol,'family':family,'state':'OBSERVED_AVAILABLE' if valid and not empty else 'UNKNOWN','observed_fields':observed,'pagination_complete':not bool(payload.get('next_url')),'feed_timeframe':'UNKNOWN'})
            except Exception as error:
                code = str(error)
                rows.append({'symbol':symbol,'family':family,'state':'NOT_ENTITLED' if code=='PROVIDER_NOT_ENTITLED' else 'TEMPORARILY_UNVERIFIED','error_code':code if code in ('PROVIDER_NOT_ENTITLED','BUDGET_EXHAUSTED','DEPENDENCY_UNAVAILABLE','REFRESH_DEADLINE') else 'PROBE_FAILED'})
    finally:
        _live_context.reset(token)
        await provider.close()
    return {'status':'OBSERVATIONS_ONLY','logical_requests':len(rows),'max_provider_attempts':8,'checked_at':datetime.now(timezone.utc).isoformat(),'records':rows,'live_recency_verified':False,'plan_name_verified':False}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--allow-provider-reads',action='store_true')
    args=parser.parse_args()
    if not args.allow_provider_reads:
        print(json.dumps({'status':'CONFIGURATION_ONLY','checks':inspect_configuration(),'provider_attempts':0}))
        return
    print(json.dumps(asyncio.run(probe()),indent=2))

if __name__ == '__main__': main()
