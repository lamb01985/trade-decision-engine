"""Explicit authenticated clients for existing full-app contract tests only."""
import pytest
from fastapi.testclient import TestClient

READ_TOKEN = 'synthetic-engine-read-token-for-tests-00001'
ADMIN_TOKEN = 'synthetic-engine-admin-token-for-tests-0002'
READ_HEADERS = {'Authorization': 'Bearer ' + READ_TOKEN, 'X-TradeHub-Audience': 'edgeiq-engine-v1'}
ADMIN_HEADERS = {'Authorization': 'Bearer ' + ADMIN_TOKEN, 'X-TradeHub-Audience': 'edgeiq-engine-admin-v1'}


@pytest.fixture(autouse=True)
def engine_security(monkeypatch):
    monkeypatch.setenv('ENGINE_API_TOKEN', READ_TOKEN)
    monkeypatch.setenv('ENGINE_ADMIN_API_TOKEN', ADMIN_TOKEN)
    monkeypatch.setenv('ENGINE_BACKGROUND_SERVICES_ENABLED', 'true')
    monkeypatch.delenv('MARKET_DATA_SERVICE_TOKEN', raising=False)


class EngineTestClient(TestClient):
    def __init__(self, *args, **kwargs):
        kwargs['headers'] = {**READ_HEADERS, **(kwargs.get('headers') or {})}
        super().__init__(*args, **kwargs)
