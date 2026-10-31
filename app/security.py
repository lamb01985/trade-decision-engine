"""Server-only, default-deny admission before handlers or request-body parsing."""
import hmac
import os
import re

from starlette.responses import JSONResponse

HEALTH = frozenset({'/health', '/api/health', '/api/v1/health'})
READ_PATHS = HEALTH | frozenset({
    '/', '/api/readiness', '/api/v1/readiness', '/api/scanner/status',
    '/api/v1/scanner/status', '/api/analysis', '/api/notifications/settings',
    '/api/v1/notifications/status', '/api/futures-probe/nq', '/api/scanner',
    '/api/v1/research/evidence', '/api/v1/research/excursions',
    '/api/v1/market-intelligence/latest', '/api/v1/market-intelligence/history',
})
SYMBOL_READ = re.compile(
    r'/api/(?:options-plan|v1/options-plan|options-shortlist|options-intelligence|'
    r'analyze|v1/analysis|v1/chart)/[A-Za-z][A-Za-z0-9.-]{0,14}')
STATIC_READ = re.compile(r'/static/[A-Za-z0-9/_.-]+')
ADMIN_WRITES = frozenset({
    ('PUT', '/api/notifications/settings'), ('POST', '/api/notifications/test'),
    ('POST', '/api/market-scanner'), ('POST', '/api/v1/scanner'),
})
SERVICE_ROUTES = {
    '/api/v1/thesis/company': b'thesis-v1',
    **{f'/api/v1/market-data/{resource}': b'market-data-v1'
       for resource in ('capabilities', 'snapshot', 'bars', 'options', 'intelligence')},
}


def valid_token(value):
    return isinstance(value, str) and 32 <= len(value) <= 512 and all(33 <= ord(c) <= 126 for c in value)


def configuration():
    tokens = tuple(os.getenv(name, '') for name in (
        'ENGINE_API_TOKEN', 'ENGINE_ADMIN_API_TOKEN', 'MARKET_DATA_SERVICE_TOKEN'))
    populated = [token for token in tokens if token]
    valid = all(valid_token(token) for token in populated) and len(set(populated)) == len(populated)
    return valid, tokens


def legacy_configured():
    valid, tokens = configuration()
    return valid and valid_token(tokens[0])


def background_authorized():
    valid, tokens = configuration()
    return (valid and valid_token(tokens[0]) and valid_token(tokens[1])
            and os.getenv('ENGINE_BACKGROUND_SERVICES_ENABLED') == 'true')


def canonical_path(scope):
    path = scope.get('path', '')
    raw = scope.get('raw_path', path.encode('utf8'))
    return (len(raw) <= 1024 and b'%' not in raw and '\\' not in path
            and '//' not in path and not any(part in ('.', '..') for part in path.split('/')))


def read_path(path):
    return path in READ_PATHS or bool(SYMBOL_READ.fullmatch(path) or STATIC_READ.fullmatch(path))


class EngineSecurityBoundary:
    def __init__(self, app, preview=False):
        self.app = app
        self.preview = preview

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'websocket':
            return await send({'type': 'websocket.close', 'code': 4401})
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        method, path = scope['method'], scope['path']

        async def private_send(message):
            if message['type'] == 'http.response.start':
                headers = [(k, v) for k, v in message.get('headers', [])
                           if k.lower() not in (b'cache-control', b'x-content-type-options')]
                message = {**message, 'headers': headers + [
                    (b'cache-control', b'private, no-store'),
                    (b'x-content-type-options', b'nosniff'),
                ]}
            elif message['type'] == 'http.response.body' and method == 'HEAD':
                message = {**message, 'body': b''}
            await send(message)

        async def reply(status, code=None, body=None):
            response = JSONResponse(body if body is not None else {'error': {'code': code}}, status_code=status)
            await response(scope, receive, private_send)

        auth = [v for k, v in scope['headers'] if k.lower() == b'authorization']
        audience = [v for k, v in scope['headers'] if k.lower() == b'x-tradehub-audience']
        if not canonical_path(scope):
            return await reply(403, 'ACCESS_DENIED')
        if path in HEALTH and method in ('GET', 'HEAD') and not auth and not audience:
            return await reply(200, body={'status': 'ok'})
        if len(auth) != 1 or len(audience) != 1 or not auth[0].startswith(b'Bearer '):
            return await reply(401, 'AUTHENTICATION_REQUIRED')
        valid, tokens = configuration()
        if not valid:
            return await reply(503, 'AUTHENTICATION_UNCONFIGURED')
        read, admin, service = tokens
        if path in SERVICE_ROUTES:
            if method != 'GET':
                return await reply(403, 'ACCESS_DENIED')
            if not valid_token(service):
                return await reply(503, 'AUTHENTICATION_UNCONFIGURED')
            if not hmac.compare_digest(auth[0], b'Bearer ' + service.encode('ascii')):
                return await reply(401, 'AUTHENTICATION_REQUIRED')
            if audience[0] != SERVICE_ROUTES[path]:
                return await reply(403, 'ACCESS_DENIED')
        else:
            if not valid_token(read):
                return await reply(503, 'AUTHENTICATION_UNCONFIGURED')
            role = None
            if hmac.compare_digest(auth[0], b'Bearer ' + read.encode('ascii')):
                role = 'read'
            elif valid_token(admin) and hmac.compare_digest(auth[0], b'Bearer ' + admin.encode('ascii')):
                role = 'admin'
            if role is None:
                return await reply(401, 'AUTHENTICATION_REQUIRED')
            expected = b'edgeiq-engine-admin-v1' if role == 'admin' else b'edgeiq-engine-v1'
            if audience[0] != expected:
                return await reply(403, 'ACCESS_DENIED')
            allowed = ((method in ('GET', 'HEAD') and read_path(path))
                       or (role == 'admin' and (method, path) in ADMIN_WRITES))
            if not allowed:
                return await reply(403, 'ACCESS_DENIED')
        if self.preview:
            if path in HEALTH:
                return await reply(200, body={'status': 'ok', 'mode': 'SECURITY_PREVIEW', 'live_services': False})
            return await reply(503, 'PREVIEW_LIVE_SERVICES_DISABLED')
        # HEAD uses the authorized GET handler but never transmits its body.
        if method == 'HEAD':
            scope = {**scope, 'method': 'GET'}
        return await self.app(scope, receive, private_send)
