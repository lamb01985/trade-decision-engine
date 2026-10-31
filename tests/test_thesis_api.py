import asyncio
from datetime import date
import json
import time
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.thesis import api
from app.thesis.sec import SecSource,ThesisService,ThesisError


@pytest.fixture
def configured(monkeypatch):
    for key,value in {'MARKET_DATA_SERVICE_TOKEN':'s'*40,'MARKET_DATA_DEPLOYMENT_PROFILE':'fixture-test','THESIS_ENABLED':'true','THESIS_DEV_FIXTURES':'true','NODE_ENV':'development','APP_STORAGE_MODE':'local'}.items():monkeypatch.setenv(key,value)
    monkeypatch.delenv('VERCEL',raising=False)
    monkeypatch.setattr(api,'_service',None);monkeypatch.setattr(api,'_configuration',None)
    app=FastAPI();app.include_router(api.router)
    return TestClient(app),{'Authorization':'Bearer '+'s'*40,'X-TradeHub-Audience':'thesis-v1'}


def test_authenticated_company_without_massive_configuration(configured,monkeypatch):
    client,headers=configured
    for key in ('MASSIVE_API_KEY','POLYGON_API_KEY','MASSIVE_ACCOUNT_BUDGET_SCOPE','KV_REST_API_TOKEN'):monkeypatch.delenv(key,raising=False)
    result=client.get('/api/v1/thesis/company?symbol=DEMOA',headers=headers)
    assert result.status_code==200
    data=result.json();assert data['source_type']=='DEVELOPMENT_FIXTURE' and data['issuer']['symbol']=='DEMOA'
    assert data['profile']=='fixture-test' and data['readiness']=='NOT_EVALUATED'


def test_auth_and_feature_fail_before_source_access(configured,monkeypatch):
    client,headers=configured
    def unexpected(*args,**kwargs):raise AssertionError('provider must not be initialized')
    monkeypatch.setattr(api,'SecSource',unexpected)
    assert client.get('/api/v1/thesis/company?symbol=DEMOA').status_code==401
    assert client.get('/api/v1/thesis/company?symbol=DEMOA',headers={**headers,'X-TradeHub-Audience':'market-data-v1'}).status_code==403
    monkeypatch.setenv('THESIS_ENABLED','false')
    assert client.get('/api/v1/thesis/company?symbol=DEMOA',headers=headers).status_code==503


@pytest.mark.parametrize('query',['symbol=DEMOA&symbol=DEMOB','symbol=DEMOA&url=https://evil.test','symbol=../DEMOA','symbol=DEMOA&as_of=2099-01-01','symbol=DEMOA&as_of=2026-02-30','symbol=DEMOA&as_of=20261031'])
def test_strict_selection(configured,query):
    client,headers=configured
    assert client.get('/api/v1/thesis/company?'+query,headers=headers).status_code==400


def test_unknown_fixture_never_becomes_a_different_company(configured):
    client,headers=configured
    assert client.get('/api/v1/thesis/company?symbol=AAPL',headers=headers).status_code==404


def test_fixtures_cannot_be_enabled_in_hosted_environment(configured,monkeypatch):
    client,headers=configured;monkeypatch.setenv('VERCEL','1')
    result=client.get('/api/v1/thesis/company?symbol=DEMOA',headers=headers)
    assert result.status_code==503 and result.json()['error']['code']=='FIXTURE_MODE_FORBIDDEN'


def test_real_sec_requires_explicit_read_enable_and_contact(configured,monkeypatch):
    client,headers=configured;monkeypatch.setenv('THESIS_DEV_FIXTURES','false');monkeypatch.delenv('THESIS_SEC_FETCH_ENABLED',raising=False)
    assert client.get('/api/v1/thesis/company?symbol=AAPL',headers=headers).status_code==503
    monkeypatch.setenv('THESIS_SEC_FETCH_ENABLED','true');monkeypatch.delenv('SEC_USER_AGENT',raising=False)
    assert client.get('/api/v1/thesis/company?symbol=AAPL',headers=headers).json()['error']['code']=='SEC_CONTACT_UNCONFIGURED'


def test_fixed_urls_cache_and_rate_spacing():
    async def scenario():
        starts=[]
        async def handler(request):starts.append(time.monotonic());return httpx.Response(200,json={'ok':True})
        source=SecSource('EdgeIQ test contact@example.invalid',httpx.AsyncClient(transport=httpx.MockTransport(handler)),interval=.01)
        with pytest.raises(ThesisError):await source.get('https://evil.test/')
        await source.get('https://www.sec.gov/files/company_tickers.json');await source.get('https://www.sec.gov/files/company_tickers.json')
        await source.get('https://data.sec.gov/submissions/CIK0000000001.json')
        assert len(starts)==2 and starts[1]-starts[0]>=.009
        await source.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('status',[302,403,429,500])
def test_sec_failures_and_redirects_are_not_retried(status):
    async def scenario():
        calls=[]
        async def handler(request):calls.append(request);return httpx.Response(status,headers={'Location':'https://evil.test'},json={'secret':'not returned'})
        source=SecSource('EdgeIQ test contact@example.invalid',httpx.AsyncClient(transport=httpx.MockTransport(handler)),interval=0)
        with pytest.raises(ThesisError,match='SEC_UNAVAILABLE'):await source.get('https://www.sec.gov/files/company_tickers.json')
        assert len(calls)==1;await source.close()
    asyncio.run(scenario())


def test_bounded_response_and_cache_capacity():
    async def scenario():
        async def handler(request):return httpx.Response(200,content=b'x'*(8*1024*1024+1))
        source=SecSource('EdgeIQ test contact@example.invalid',httpx.AsyncClient(transport=httpx.MockTransport(handler)),interval=0)
        with pytest.raises(ThesisError,match='TOO_LARGE'):await source.get('https://www.sec.gov/files/company_tickers.json')
        await source.close()
        source=SecSource('EdgeIQ test contact@example.invalid',httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={}))),interval=0)
        for i in range(10):await source.get(f'https://data.sec.gov/submissions/CIK{i:010d}.json')
        assert len(source.cache)==8;await source.close()
    asyncio.run(scenario())


def test_coalescing_overload_and_asof_separation():
    async def scenario():
        service=ThesisService(fixture=True);original=service._company;calls=[]
        async def delayed(symbol,asof):calls.append((symbol,asof));await asyncio.sleep(.01);return await original(symbol,asof)
        service._company=delayed
        reports=await asyncio.gather(*(service.company('DEMOA',date(2026,10,31)) for _ in range(20)))
        assert len(calls)==1 and len(reports)==20
        older=await service.company('DEMOA',date(2025,6,1));assert older['periods'][0]['end']=='2024-12-31'
        gate=asyncio.Event()
        async def pending(*args):await gate.wait();return {}
        service._company=pending
        tasks=[asyncio.create_task(service.company('DEMOA',date(2026,1,day))) for day in range(1,5)]
        await asyncio.sleep(.01)
        with pytest.raises(ThesisError,match='BUSY'):await service.company('DEMOB',date(2026,1,5))
        gate.set();await asyncio.gather(*tasks);await service.close()
    asyncio.run(scenario())


def test_http_and_company_deadlines_cancel_late_work(monkeypatch):
    async def scenario():
        events=[]
        async def slow(request):
            try:await asyncio.sleep(1);events.append('late')
            finally:events.append('cancelled')
            return httpx.Response(200,json={})
        source=SecSource('EdgeIQ test contact@example.invalid',httpx.AsyncClient(transport=httpx.MockTransport(slow)),interval=0)
        source.REQUEST_TIMEOUT=.01
        with pytest.raises(ThesisError,match='DEADLINE'):await source.get('https://www.sec.gov/files/company_tickers.json')
        assert events==['cancelled'];await source.close()
        service=ThesisService(fixture=True);service.COMPANY_TIMEOUT=.01
        async def queued(*args):await asyncio.sleep(1);events.append('late company')
        service._company=queued
        with pytest.raises(ThesisError,match='DEADLINE'):await service.company('DEMOA',date(2026,10,31))
        await asyncio.sleep(.01);assert 'late company' not in events;await service.close()
    asyncio.run(scenario())
