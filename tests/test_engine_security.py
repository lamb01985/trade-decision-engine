import asyncio
from dataclasses import replace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.main as main
from app.security import EngineSecurityBoundary, HEALTH, SERVICE_ROUTES
from tests.auth_client import ADMIN_HEADERS, ADMIN_TOKEN, READ_HEADERS, READ_TOKEN, engine_security


def test_every_full_app_route_denies_anonymous_before_side_effects(monkeypatch):
    forbidden = Mock(side_effect=AssertionError('unauthorized handler executed'))
    monkeypatch.setattr(main, 'get_service', forbidden)
    monkeypatch.setattr(main, 'get_notification_service', forbidden)
    monkeypatch.setattr('app.intelligence_api.read_reports', forbidden)
    monkeypatch.setattr('app.thesis.api.SecSource', forbidden)
    monkeypatch.setattr('app.market_data.api.MassiveMarketDataProvider', forbidden)
    client = TestClient(main.app)
    covered = 0
    def routes(router):
        for route in router.routes:
            if hasattr(route, 'original_router'):
                yield from routes(route.original_router)
            else:
                yield route
    for route in routes(main.app):
        path = route.path.replace('{symbol}', 'QQQ').replace('{resource}', 'snapshot')
        if path == '/static':
            path += '/app.js'
        for method in getattr(route, 'methods', None) or {'GET'}:
            response = client.request(method, path, content=b'invalid-json')
            if path in HEALTH and method in ('GET', 'HEAD'):
                assert response.status_code == 200
            else:
                assert response.status_code == 401, (method, path, response.status_code)
            assert response.headers['cache-control'] == 'private, no-store'
            covered += 1
    assert covered >= 30
    forbidden.assert_not_called()


@pytest.mark.parametrize('path', sorted(HEALTH))
def test_public_health_minimal_and_invalid_credentials_never_downgrade(path):
    c = TestClient(main.app)
    assert c.get(path).json() == {'status': 'ok'}
    assert c.head(path).content == b''
    for headers in [ {'Authorization': 'Bearer invalid'},
                     {'X-TradeHub-Audience': 'edgeiq-engine-v1'},
                     {**READ_HEADERS, 'X-TradeHub-Audience': 'thesis-v1'},
                     {'Authorization': ''} ]:
        assert c.get(path, headers=headers).status_code in (401, 403)
    detailed = c.get(path, headers=READ_HEADERS)
    assert detailed.status_code == 200
    assert 'build' in detailed.json()
    assert c.head(path, headers=READ_HEADERS).content == b''


def test_read_admin_and_scanner_permissions_prevent_notification_side_effects(monkeypatch):
    class Notifications:
        changes = 0
        sent = 0
        def status(self): return {'status': 'TEST', 'changes': self.changes}
        def update_preferences(self, values): self.changes += 1
        async def send_test(self): self.sent += 1; return {'status': 'SENT_SYNTHETIC'}
    notifications = Notifications()
    monkeypatch.setattr(main, 'get_notification_service', lambda _: notifications)
    service = Mock(side_effect=AssertionError('read token invoked scanner'))
    monkeypatch.setattr(main, 'get_service', service)
    c = TestClient(main.app)
    assert c.get('/api/notifications/settings', headers=READ_HEADERS).status_code == 200
    for headers in ({}, READ_HEADERS):
        assert c.put('/api/notifications/settings', headers=headers, json={'ready': False}).status_code in (401, 403)
        assert c.post('/api/notifications/test', headers=headers).status_code in (401, 403)
        for path in ('/api/market-scanner', '/api/v1/scanner'):
            assert c.post(path, headers=headers, json={}).status_code in (401, 403)
    assert notifications.changes == notifications.sent == 0
    service.assert_not_called()
    assert c.put('/api/notifications/settings', headers=ADMIN_HEADERS, json={'ready': False}).status_code == 200
    assert c.post('/api/notifications/test', headers=ADMIN_HEADERS).status_code == 200
    assert notifications.changes == notifications.sent == 1
    # Admin is admitted to scanner validation, not denied by authentication.
    assert c.post('/api/v1/scanner', headers=ADMIN_HEADERS, content='invalid').status_code == 422


@pytest.mark.parametrize('headers', [
    {'Authorization': 'Bearer wrong', 'X-TradeHub-Audience': 'edgeiq-engine-v1'},
    {**READ_HEADERS, 'X-TradeHub-Audience': 'edgeiq-engine-admin-v1'},
    {**ADMIN_HEADERS, 'X-TradeHub-Audience': 'edgeiq-engine-v1'},
    [('Authorization', READ_HEADERS['Authorization']), ('Authorization', READ_HEADERS['Authorization']),
     ('X-TradeHub-Audience', 'edgeiq-engine-v1')],
    [('Authorization', READ_HEADERS['Authorization']), ('X-TradeHub-Audience', 'edgeiq-engine-v1'),
     ('X-TradeHub-Audience', 'edgeiq-engine-v1')],
])
def test_malformed_and_wrong_audience_headers(headers):
    assert TestClient(main.app).get('/api/notifications/settings', headers=headers).status_code in (401, 403)


def test_query_cookies_and_origin_do_not_authenticate():
    response = TestClient(main.app).get('/api/analysis?token=' + READ_TOKEN,
        headers={'Origin': 'http://localhost', 'Cookie': 'th_operator=trusted', 'X-Forwarded-For': '127.0.0.1'})
    assert response.status_code == 401
    assert READ_TOKEN not in response.text


@pytest.mark.parametrize('path', ['/api/notifications/settings/', '/api/notifications/%73ettings',
                                  '/api//notifications/settings', '/docs', '/openapi.json',
                                  '/api/v1/market-data/private', '/api/new-feature'])
def test_aliases_and_unknown_paths_deny_even_admin(path):
    assert TestClient(main.app).get(path, headers=ADMIN_HEADERS, follow_redirects=False).status_code == 403


def test_future_routes_and_unknown_writes_require_explicit_policy():
    app = FastAPI()
    handler = Mock(return_value={'unsafe': True})
    app.get('/api/future')(handler)
    app.add_middleware(EngineSecurityBoundary)
    c = TestClient(app)
    assert c.get('/api/future', headers=ADMIN_HEADERS).status_code == 403
    assert c.post('/api/notifications/settings', headers=ADMIN_HEADERS).status_code == 403
    assert c.options('/api/notifications/settings', headers=READ_HEADERS).status_code == 403
    handler.assert_not_called()


@pytest.mark.parametrize('setting,value', [
    ('ENGINE_API_TOKEN', ''), ('ENGINE_API_TOKEN', 'short'),
    ('ENGINE_ADMIN_API_TOKEN', READ_TOKEN), ('ENGINE_ADMIN_API_TOKEN', 'é' * 40),
    ('MARKET_DATA_SERVICE_TOKEN', READ_TOKEN), ('MARKET_DATA_SERVICE_TOKEN', ADMIN_TOKEN),
])
def test_bad_configuration_fails_closed(setting, value, monkeypatch):
    monkeypatch.setenv(setting, value)
    c = TestClient(main.app)
    assert c.get('/api/notifications/settings', headers=READ_HEADERS).status_code == 503
    assert c.get('/health').json() == {'status': 'ok'}


def test_missing_admin_keeps_reads_but_never_authorizes_writes(monkeypatch):
    monkeypatch.delenv('ENGINE_ADMIN_API_TOKEN')
    c = TestClient(main.app)
    assert c.get('/health', headers=READ_HEADERS).status_code == 200
    assert c.post('/api/notifications/test', headers=READ_HEADERS).status_code == 403
    assert c.post('/api/notifications/test', headers=ADMIN_HEADERS).status_code == 401


def test_service_audiences_are_isolated_and_collision_disables_them(monkeypatch):
    service = 'synthetic-service-token-different-from-engine-0003'
    monkeypatch.setenv('MARKET_DATA_SERVICE_TOKEN', service)
    monkeypatch.setenv('THESIS_ENABLED', 'false')
    c = TestClient(main.app)
    for path, audience in SERVICE_ROUTES.items():
        for headers in (READ_HEADERS, ADMIN_HEADERS):
            assert c.get(path, headers=headers).status_code == 401
        assert c.get(path, headers={'Authorization': 'Bearer '+service,
            'X-TradeHub-Audience': 'edgeiq-engine-v1'}).status_code == 403
        headers = {'Authorization': 'Bearer '+service, 'X-TradeHub-Audience': audience.decode()}
        assert c.get(path, headers=headers).status_code == 503  # existing disabled source contract
    headers = {'Authorization': 'Bearer '+service, 'X-TradeHub-Audience': 'thesis-v1'}
    assert c.get('/health', headers=headers).status_code == 401
    monkeypatch.setenv('ENGINE_API_TOKEN', service)
    assert c.get('/api/v1/thesis/company', headers=headers).status_code == 503


def test_security_preview_never_initializes_or_executes_live_services(monkeypatch):
    forbidden = Mock(side_effect=AssertionError('preview touched live service'))
    monkeypatch.setattr(main, 'create_stores', forbidden)
    monkeypatch.setattr(main, 'MassiveMarketDataProvider', forbidden)
    monkeypatch.setattr(main, 'HostedBackgroundScanner', forbidden)
    monkeypatch.setattr(main, 'get_service', forbidden)
    monkeypatch.setattr(main, 'get_notification_service', forbidden)
    monkeypatch.setattr('app.intelligence_api.read_reports', forbidden)
    monkeypatch.setattr('app.thesis.api.SecSource', forbidden)
    monkeypatch.setattr('app.market_data.api.MassiveMarketDataProvider', forbidden)
    from app.security_preview import app
    with TestClient(app) as c:
        assert c.get('/health').json() == {'status': 'ok'}
        assert c.get('/health', headers=READ_HEADERS).json() == {
            'status': 'ok', 'mode': 'SECURITY_PREVIEW', 'live_services': False}
        assert c.head('/health', headers=ADMIN_HEADERS).content == b''
        for path in ('/api/analysis', '/api/notifications/settings', '/api/v1/research/evidence',
                     '/api/v1/market-intelligence/latest'):
            assert c.get(path).status_code == 401
            r = c.get(path, headers=READ_HEADERS)
            assert r.status_code == 503 and r.json()['error']['code'] == 'PREVIEW_LIVE_SERVICES_DISABLED'
        assert c.post('/api/notifications/test', headers=READ_HEADERS).status_code == 403
        assert c.post('/api/notifications/test', headers=ADMIN_HEADERS).status_code == 503
        service = 'synthetic-preview-service-token-for-test-0004'
        monkeypatch.setenv('MARKET_DATA_SERVICE_TOKEN', service)
        for path, audience in SERVICE_ROUTES.items():
            r = c.get(path, headers={'Authorization': 'Bearer '+service,
                'X-TradeHub-Audience': audience.decode()})
            assert r.json()['error']['code'] == 'PREVIEW_LIVE_SERVICES_DISABLED'
    forbidden.assert_not_called()


def test_missing_security_initializes_no_live_dependencies(monkeypatch):
    monkeypatch.delenv('ENGINE_API_TOKEN')
    monkeypatch.setattr(main, 'settings', replace(main.settings, hosted_scanner_enabled=False, notifications_enabled=False))
    forbidden = Mock(side_effect=AssertionError('unconfigured startup touched service'))
    monkeypatch.setattr(main, 'create_stores', forbidden)
    monkeypatch.setattr(main, 'MassiveMarketDataProvider', forbidden)
    monkeypatch.setattr(main, 'NotificationService', forbidden)
    with TestClient(main.app) as c:
        assert c.get('/health').json() == {'status': 'ok'}
        assert c.get('/api/analysis', headers=READ_HEADERS).status_code == 503
    forbidden.assert_not_called()


def test_background_requires_separate_authorization_before_initialization(monkeypatch):
    monkeypatch.setenv('ENGINE_BACKGROUND_SERVICES_ENABLED', 'false')
    monkeypatch.setattr(main, 'settings', replace(main.settings, hosted_scanner_enabled=True))
    forbidden = Mock(side_effect=AssertionError('startup touched service'))
    monkeypatch.setattr(main, 'create_stores', forbidden)
    monkeypatch.setattr(main, 'MassiveMarketDataProvider', forbidden)
    with pytest.raises(RuntimeError, match='BACKGROUND_AUTHORIZATION_REQUIRED'):
        with TestClient(main.app): pass
    forbidden.assert_not_called()


def test_unexpected_admitted_error_is_private_and_head_has_no_asgi_body(monkeypatch):
    monkeypatch.setattr(main, 'get_notification_service', Mock(side_effect=RuntimeError('synthetic failure')))
    c = TestClient(main.app, raise_server_exceptions=False)
    r = c.get('/api/notifications/settings', headers=READ_HEADERS)
    assert r.status_code == 500
    assert r.headers['cache-control'] == 'private, no-store'
    assert r.headers['x-content-type-options'] == 'nosniff'
    assert 'synthetic failure' not in r.text

    async def scenario():
        messages = []
        scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
                 'http_version': '1.1', 'method': 'HEAD', 'scheme': 'http',
                 'path': '/api/notifications/settings', 'raw_path': b'/api/notifications/settings',
                 'query_string': b'', 'root_path': '', 'server': ('testserver', 80),
                 'client': ('testclient', 123),
                 'headers': [(k.lower().encode(), v.encode()) for k, v in READ_HEADERS.items()]}
        async def receive(): return {'type': 'http.request', 'body': b'', 'more_body': False}
        async def send(message): messages.append(message)
        with pytest.raises(RuntimeError):
            await main.app(scope, receive, send)
        start = next(m for m in messages if m['type'] == 'http.response.start')
        assert start['status'] == 500
        assert dict(start['headers'])[b'cache-control'] == b'private, no-store'
        assert all(not m.get('body') for m in messages if m['type'] == 'http.response.body')
    asyncio.run(scenario())
