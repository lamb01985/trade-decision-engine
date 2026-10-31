import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from app import local_intelligence as local
from app.market_data.base import MarketDataError
from app.market_intelligence import IntelligenceStore
from app.models import Bar

SESSION = date(2026, 10, 26)
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
FRIDAY = datetime(2026, 10, 23, 20, 0, tzinfo=timezone.utc)


def report():
    return dict(schema_version='market-intelligence-v1', report_date=str(SESSION),
        generated_at='2026-10-25T12:00:00+00:00', observation_cutoff=FRIDAY.isoformat(),
        daily_features={s:dict(close=100, session_date='2026-10-23', source_timestamp='2026-10-23T04:00:00+00:00') for s in ('QQQ','SPY')},
        intraday_playbook=[dict(symbol='QQQ', source_timestamp=FRIDAY.isoformat())])


def test_friday_is_verified_as_completed_data_for_monday():
    result = local.verify_report(report(), SESSION)
    assert result['completed_equity_session'] == '2026-10-23'
    assert result['trading_session'] == '2026-10-26'
    assert local.target_session(datetime(2026,10,25,20,tzinfo=timezone.utc)) == SESSION
    assert local.target_session(datetime(2026,10,26,20,1,tzinfo=timezone.utc)) == date(2026,10,27)


@pytest.mark.parametrize('mutate', [
    lambda r:r.update(report_date='2026-10-23'),
    lambda r:r['daily_features']['QQQ'].update(session_date='2026-10-22'),
    lambda r:r.update(observation_cutoff='2026-10-26T14:00:00+00:00'),
    lambda r:r['intraday_playbook'][0].update(source_timestamp='2026-10-26T12:00:00+00:00'),
])
def test_verification_rejects_wrong_session_or_lookahead(mutate):
    r=report(); mutate(r)
    with pytest.raises(ValueError): local.verify_report(r, SESSION)


@pytest.mark.parametrize('timestamp,status', [(FRIDAY,'PRIOR_SESSION_OBSERVATION'),(NOW-timedelta(minutes=2),'CURRENT_SESSION_RECENT'),(NOW-timedelta(minutes=20),'CURRENT_SESSION_STALE'),(NOW+timedelta(seconds=1),'FUTURE_TIMESTAMP'),(None,'UNAVAILABLE')])
def test_retrieval_does_not_relabel_stale_market_observations(timestamp,status):
    assert local.observation_status(timestamp,NOW,SESSION)==status


def test_managed_store_preserves_original_and_makes_backup(tmp_path,monkeypatch):
    original=tmp_path/'original.sqlite3'
    store=IntelligenceStore(original); store.close()
    before=original.read_bytes()
    monkeypatch.setattr(local,'SOURCE_DB',original)
    monkeypatch.setattr(local,'LOCAL',tmp_path/'managed')
    run=tmp_path/'run'; run.mkdir()
    managed=local.managed_store(run); managed.close()
    assert original.read_bytes()==before
    assert (run/'state_before.sqlite3').is_file()


class PremarketProvider:
    async def get_stock_snapshot(self,symbol):
        return {'lastTrade': {'p':100, 't':int(FRIDAY.timestamp()*1e9)}}
    async def get_minute_bars(self,symbol,start,end):
        assert start==end==SESSION
        # A bad/stale provider response is never allowed to become Monday minute structure.
        return [Bar(timestamp=FRIDAY,open=100,high=101,low=99,close=100,volume=100)]
    async def get_active_option_contracts(self,symbol,as_of):
        assert as_of==SESSION
        return [dict(ticker='O:'+symbol,expiration_date=str(SESSION))]
    async def get_options_chain_snapshot(self,symbol,expiration,low,high):
        return [dict(details=dict(ticker='O:'+symbol,expiration_date=expiration,strike_price=100,contract_type='call'),last_quote=dict(last_updated=int(FRIDAY.timestamp()*1e9),bid=1,ask=2),day=dict(volume=5),open_interest=10)]
    async def get_option_trades(self,ticker,session,cutoff):
        assert session==SESSION and cutoff==NOW
        return dict(results=[],complete=True)
    async def get_option_quotes(self,ticker,session,cutoff):
        assert session==SESSION and cutoff==NOW
        return dict(results=[],complete=True)


def test_premarket_keeps_friday_context_and_current_requests_separate(tmp_path,monkeypatch):
    monkeypatch.setattr(local,'LOCAL',tmp_path)
    store=IntelligenceStore(tmp_path/'state.sqlite3')
    r=report()
    result=asyncio.run(local.collect_premarket(PremarketProvider(),store,r,SESSION,NOW))
    for row in result['symbols'].values():
        assert row['stock']['status']=='PRIOR_SESSION_OBSERVATION'
        assert row['current_session_minutes']['bars']==[]
        assert row['current_session_minutes']['regular_session_structure']['available'] is False
        assert row['options']['status']=='PRIOR_SESSION_OBSERVATION'
        assert row['options']['analytics']['trade_flow']['status']=='NO_CURRENT_SESSION_TRADES'
        assert row['options']['analytics']['source_cutoff']==FRIDAY.isoformat()
    assert store.connection.execute('SELECT count(*) FROM premarket_captures').fetchone()[0]==1
    assert result['signal_status']=='NOT_VALIDATED_NO_LIVE_TRADE_SIGNALS'
    store.close()


class DeniedProvider(PremarketProvider):
    async def get_stock_snapshot(self,*args): raise MarketDataError('denied')
    async def get_minute_bars(self,*args): raise MarketDataError('denied')
    async def get_active_option_contracts(self,*args): raise MarketDataError('denied')


def test_missing_entitlements_are_recorded_without_fake_fallback(tmp_path,monkeypatch):
    monkeypatch.setattr(local,'LOCAL',tmp_path)
    store=IntelligenceStore(tmp_path/'state.sqlite3')
    result=asyncio.run(local.collect_premarket(DeniedProvider(),store,report(),SESSION,NOW))
    assert all(e['status']=='UNAVAILABLE' for row in result['symbols'].values() for e in row.values())
    store.close()
