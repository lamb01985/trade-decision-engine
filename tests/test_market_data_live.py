import asyncio
from datetime import date, datetime, timedelta, timezone
import httpx
import pytest
from app.market_data.quality import freshness, timestamp
from app.market_data.sessions import Sessions
from app.market_data.service import MarketService
from app.market_data.massive import MassiveMarketDataProvider
from app.market_data.budget import BudgetUnavailable

UTC=timezone.utc

@pytest.mark.parametrize('age,state', [(-6,'INVALID_CLOCK'),(-5,'UNKNOWN'),(-1,'UNKNOWN'),(0,'CURRENT'),(30,'CURRENT'),(31,'STALE')])
def test_exact_freshness(age,state):
    now=datetime(2026,10,23,15,tzinfo=UTC)
    actual,observed,_=freshness(now-timedelta(seconds=age),now,Sessions(),'REAL_TIME')
    assert actual==state
    assert observed==age


def test_calendar_boundaries_and_special_sessions():
    s=Sessions()
    assert s.window(date(2026,12,25))==()
    assert s.window(date(2026,11,27))[1].hour==18
    assert s.window(date(2026,3,6))[0].hour==14
    assert s.window(date(2026,3,9))[0].hour==13
    assert s.window(date(2024,2,29))
    assert s.window(date(1900,1,1)) is None
    opening,closing=s.window(date(2026,10,23))
    assert s.at(opening)=='2026-10-23'
    assert s.at(closing) is None
    assert s.latest_closed(closing)=='2026-10-23'


def test_units_no_magnitude_heuristic():
    assert timestamp(1700000000000,'MS')==timestamp(1700000000000000000,'NS')
    for raw,unit in [(True,'NS'),(1.2,'NS'),('abc','NS'),(123,'UNKNOWN')]:
        with pytest.raises(ValueError): timestamp(raw,unit)


def test_no_upstream_without_shared_store(monkeypatch):
    from app.market_data import budget
    monkeypatch.setenv('MARKET_DATA_UNIFIED_ENABLED','true')
    def unavailable(): raise BudgetUnavailable()
    monkeypatch.setattr(budget,'configured_budget',unavailable)
    async def scenario():
        p=MassiveMarketDataProvider('test')
        with pytest.raises(Exception,match='DEPENDENCY_UNAVAILABLE'):
            await p.get_stock_snapshot('QQQ')
        await p.close()
    asyncio.run(scenario())


def test_all_http_attempts_reserve_release_and_pagination(monkeypatch):
    from app.market_data import budget
    monkeypatch.setenv('MARKET_DATA_UNIFIED_ENABLED','true')
    monkeypatch.setenv('MASSIVE_ACCOUNT_BUDGET_SCOPE','test')
    events=[]
    class Budget:
        async def admit(self,scope,attempt,role,timeout):
            events.append(('admit',role,attempt)); return dict(granted=True,lease_id=attempt)
        async def release(self,scope,lease): events.append(('release',lease))
    monkeypatch.setattr(budget,'configured_budget',Budget)
    async def scenario():
        p=MassiveMarketDataProvider('test')
        async def handler(request):
            events.append(('fetch',request.url.path))
            return httpx.Response(200,json={'status':'OK','ticker':'QQQ','adjusted':True,'results':[]})
        await p._client.aclose()
        p._client=httpx.AsyncClient(base_url='https://api.massive.com',transport=httpx.MockTransport(handler))
        await p._get_payload('/legacy',{})
        batch=await p.get_bounded('/v2/aggs/ticker/QQQ/range/1/minute/a/b',{},paginate=True)
        assert batch['pagination_complete']
        await p.close()
    asyncio.run(scenario())
    assert [e[0] for e in events]==['admit','fetch','release','admit','fetch','release']
    assert events[0][1]=='legacy' and events[3][1]=='new'
    assert events[0][2]!=events[3][2]


def test_coalescing_and_expired_queue():
    class Provider:
        calls=0
        async def get_bounded(self,*args):
            self.calls+=1
            await asyncio.sleep(.01)
            return dict(items=[{'ticker':'QQQ','lastTrade':{'p':123,'t':1}}],pages_read=1,pagination_complete=True,error=None)
    async def scenario():
        p=Provider(); s=MarketService(p,'test')
        results=await asyncio.gather(*(s.request('snapshot',{'symbol':'QQQ'}) for _ in range(20)))
        assert p.calls==1
        assert all(r[1]['quality']['freshness']!='CURRENT' for r in results)
        assert results[0][1]['data']['bid'] is None
    asyncio.run(scenario())


def test_api_auth_query_and_later_resources(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.market_data import api
    app=FastAPI(); app.include_router(api.router)
    monkeypatch.setenv('MARKET_DATA_SERVICE_TOKEN','x'*40)
    monkeypatch.setattr(api,'configuration',lambda:(True,'x'*40,'test'))
    monkeypatch.setattr(api,'_service',MarketService(None,'test'))
    c=TestClient(app)
    assert c.get('/api/v1/market-data/snapshot').status_code==401
    headers={'authorization':'Bearer '+'x'*40,'x-tradehub-audience':'market-data-v1'}
    assert c.get('/api/v1/market-data/capabilities',headers=headers).status_code==200
    assert c.get('/api/v1/market-data/options',headers=headers).json()['error']['code']=='FEATURE_NOT_ENABLED'
    assert c.get('/api/v1/market-data/snapshot?symbol=QQQ&symbol=SPY',headers=headers).status_code==400
    assert c.get('/api/v1/market-data/snapshot?symbol=VIX',headers=headers).status_code==400
    assert c.get('/api/v1/market-data/bars?symbol=QQQ&interval=1d',headers=headers).json()['error']['code']=='UNSUPPORTED_INTERVAL'
    assert c.get('/api/v1/market-data/snapshot',headers={'authorization':headers['authorization']}).status_code==403

@pytest.mark.parametrize('bid,ask,trade,expected',[(100,101,None,'CURRENT'),(100,99,100,'UNKNOWN'),(None,101,100,'UNKNOWN'),(0,101,100,'UNKNOWN')])
def test_exact_quote_pair_gate(bid,ask,trade,expected):
    from app.market_data.capabilities import Capabilities
    class Verified(Capabilities):
        def feed(self,*args): return 'REAL_TIME'
    class AlwaysOpen:
        def at(self,t): return 'test-session'
        def window(self,d): return (datetime(2026,1,1,tzinfo=UTC),datetime(2027,1,1,tzinfo=UTC))
        def latest_closed(self,t): return None
    class Provider:
        async def get_bounded(self,*args):
            raw={'ticker':'QQQ','lastQuote':{'t':int(datetime.now(UTC).timestamp())*10**9}}
            if bid is not None: raw['lastQuote']['p']=bid
            if ask is not None: raw['lastQuote']['P']=ask
            if trade is not None: raw['lastTrade']={'p':trade,'t':1}
            return dict(items=[raw],pages_read=1,pagination_complete=True,error=None)
    async def scenario():
        s=MarketService(Provider(),'test',AlwaysOpen(),Verified())
        status,result=await s.fetch('snapshot',{'symbol':'QQQ'},999999999)
        assert status==200
        assert result['quality']['freshness']==expected
        if trade is not None: assert result['data']['last_trade']['field_quality']['freshness']!='CURRENT'
    asyncio.run(scenario())


def test_deadline_prevents_late_provider_attempt():
    async def scenario():
        p=MassiveMarketDataProvider('test')
        result=await p.get_bounded('/anything',{},deadline=1)
        assert result['pages_read']==0 and result['error']=='REFRESH_DEADLINE'
        await p.close()
    asyncio.run(scenario())

@pytest.mark.parametrize('next_url',['https://evil.example/v2/aggs/ticker/QQQ/range/1/minute/a/b','https://api.massive.com/private','https://api.massive.com/v2/aggs/ticker/QQQ/range/1/minute/a/b#fragment'])
def test_bounded_cursor_cannot_change_origin_or_path(next_url):
    async def scenario():
        p=MassiveMarketDataProvider('test')
        calls=[]
        async def payload(path,params):
            calls.append(path)
            return {'ticker':'QQQ','adjusted':True,'results':[],'next_url':next_url}
        p._get_payload=payload
        result=await p.get_bounded('/v2/aggs/ticker/QQQ/range/1/minute/a/b',{},paginate=True)
        assert result['error']=='INVALID_CURSOR' and len(calls)==1
        await p.close()
    asyncio.run(scenario())

@pytest.mark.parametrize('feed,age,delay,expected',[('DELAYED',960,900,'DELAYED'),('DELAYED',961,900,'STALE'),('EOD',0,None,'UNKNOWN'),('UNKNOWN',0,None,'UNKNOWN')])
def test_feed_precedence(feed,age,delay,expected):
    now=datetime(2026,10,23,15,tzinfo=UTC)
    assert freshness(now-timedelta(seconds=age),now,Sessions(),feed,delay)[0]==expected


def test_timestamp_nanoseconds_truncate_without_float_rounding():
    assert timestamp('1792767600123456789','NS').microsecond==123456
    with pytest.raises(ValueError): timestamp('999999999999999999999999999999999','NS')

@pytest.mark.parametrize('age,expected',[(90,'CURRENT'),(91,'STALE')])
def test_bar_end_freshness_exact(age,expected):
    now=datetime(2026,10,23,15,tzinfo=UTC)
    assert freshness(now-timedelta(seconds=age),now,Sessions(),'REAL_TIME',threshold=90)[0]==expected

@pytest.mark.parametrize('result,valid', [(row['result'],row['valid']) for row in __import__('json').loads((__import__('pathlib').Path(__file__).parent/'fixtures/market-budget-protocol.json').read_text())])
def test_redis_wrapper_lua_contract(result,valid):
    from app.market_data.budget import RedisBudget
    async def scenario():
        captured=[]
        async def handler(request):
            import json
            captured.append(json.loads(request.content))
            return httpx.Response(200,json={'result':result})
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        budget=RedisBudget('https://redis.example','secret',5,1,client)
        if valid:
            admission=await budget.admit('shared','11111111-1111-1111-1111-111111111111','new',10000)
            assert admission['granted']==bool(result[0])
        else:
            with pytest.raises(BudgetUnavailable):
                await budget.admit('shared','11111111-1111-1111-1111-111111111111','new',10000)
        assert captured[0][0]=='EVAL' and captured[0][2]==4
        assert captured[0][3:7]==['md:{shared}:policy','md:{shared}:attempts','md:{shared}:new_attempts','md:{shared}:leases']
        await client.aclose()
    asyncio.run(scenario())
