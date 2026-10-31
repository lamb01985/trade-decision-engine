import asyncio
import httpx
import pytest
from fastapi.testclient import TestClient
from app.market_data.massive import MassiveMarketDataProvider, _safe_response_excerpt
from app.market_data import budget


def test_independent_budget_survives_unified_rollback(monkeypatch):
    monkeypatch.setenv('MARKET_DATA_UNIFIED_ENABLED','false')
    monkeypatch.setenv('MASSIVE_BUDGET_ENFORCED','true')
    def unavailable(): raise budget.BudgetUnavailable()
    monkeypatch.setattr(budget,'configured_budget',unavailable)
    async def scenario():
        provider=MassiveMarketDataProvider('synthetic')
        try:
            with pytest.raises(Exception,match='DEPENDENCY_UNAVAILABLE'):
                await provider._get_payload('/v2/test',{})
        finally: await provider.close()
    asyncio.run(scenario())


def test_budget_store_is_separate_from_operator_store(monkeypatch):
    values={'MASSIVE_BUDGET_REDIS_REST_URL':'https://account-budget.example','MASSIVE_BUDGET_REDIS_REST_TOKEN':'synthetic-account','KV_REST_API_URL':'https://preview-auth.example','KV_REST_API_TOKEN':'synthetic-session','MASSIVE_LEGACY_RESERVED_REQUESTS_PER_MINUTE':'1'}
    for key,value in values.items(): monkeypatch.setenv(key,value)
    async def scenario():
        store=budget.configured_budget()
        assert store.url=='https://account-budget.example'
        await store.client.aclose()
    asyncio.run(scenario())


def test_preview_app_excludes_legacy_and_docs_routes():
    from app.market_data.preview_app import app
    client=TestClient(app)
    assert client.get('/health').json()=={'status':'ok'}
    for path in ('/docs','/openapi.json','/api/v1/analyze/QQQ','/api/v1/options/QQQ','/api/notifications'):
        assert client.get(path).status_code==404
    assert client.get('/api/v1/market-data/snapshot?symbol=QQQ').status_code==401


def test_preview_startup_requires_explicit_configuration(monkeypatch):
    from app.market_data.preview_app import app
    monkeypatch.setenv('MASSIVE_BUDGET_ENFORCED','false')
    with pytest.raises(RuntimeError,match='PREVIEW_CONFIGURATION_INVALID'):
        with TestClient(app): pass


def test_redacts_long_encoded_key_before_bounded_logging():
    from urllib.parse import quote
    secret='synthetic+secret/'*50
    response=httpx.Response(403,text='denied '+quote(secret,safe=''))
    text=_safe_response_excerpt(response,secret)
    assert 'synthetic' not in text
    assert text=='denied [REDACTED]'
