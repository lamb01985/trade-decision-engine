"""Shared rolling Redis admission. Every provider HTTP attempt must acquire a lease."""
import asyncio
import os
import re
from urllib.parse import urlsplit
from pathlib import Path
import httpx

POLICY_VERSION = 'market-data-budget-v1'


class BudgetUnavailable(RuntimeError):
    pass


class RedisBudget:
    def __init__(self, url, token, n=5, reserved=None, client=None):
        if not url or not url.startswith('https://') or not token or reserved is None or not 0 <= reserved < n:
            raise BudgetUnavailable('CONFIGURATION_UNAVAILABLE')
        self.url, self.token, self.n, self.reserved = url.rstrip('/'), token, n, reserved
        self.client = client or httpx.AsyncClient(timeout=3)

    async def command(self, *args):
        try:
            response = await self.client.post(self.url, headers={'Authorization': f'Bearer {self.token}'}, json=list(args))
            response.raise_for_status()
            body = response.json()
            if 'error' in body or 'result' not in body:
                raise ValueError()
            return body['result']
        except (httpx.HTTPError, ValueError, TypeError):
            raise BudgetUnavailable('DEPENDENCY_UNAVAILABLE') from None

    async def admit(self, account_scope, attempt_id, role, timeout_ms):
        if not re.fullmatch(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', attempt_id or '') or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', account_scope or '') or role not in ('new','legacy') or timeout_ms != (10000 if role=='new' else 20000):
            raise BudgetUnavailable('CONFIGURATION_UNAVAILABLE')
        keys = [f'md:{{{account_scope}}}:{suffix}' for suffix in ('policy','attempts','new_attempts','leases')]
        result = await self.command('EVAL', Path(__file__).with_name('admission.lua').read_text(), 4, *keys,
                                    self.n, self.reserved, POLICY_VERSION, attempt_id, role, timeout_ms)
        if not isinstance(result, list) or len(result) != 4 or type(result[0]) is not int or result[0] not in (0,1) or type(result[2]) is not int or result[2] < 0 or (result[0] == 1 and result[1] != attempt_id) or not isinstance(result[3], str):
            raise BudgetUnavailable('DEPENDENCY_UNAVAILABLE')
        return dict(granted=result[0] == 1, lease_id=result[1] or None, retry_after_seconds=int(result[2]), reason=result[3])

    async def release(self, account_scope, lease_id):
        await self.command('ZREM', f'md:{{{account_scope}}}:leases', lease_id)


def configured_budget():
    validate_configuration()
    try:
        return RedisBudget(os.getenv('MASSIVE_BUDGET_REDIS_REST_URL') or os.getenv('KV_REST_API_URL') or os.getenv('UPSTASH_REDIS_REST_URL'),
                           os.getenv('MASSIVE_BUDGET_REDIS_REST_TOKEN') or os.getenv('KV_REST_API_TOKEN') or os.getenv('UPSTASH_REDIS_REST_TOKEN'),
                           int(os.getenv('MASSIVE_REQUESTS_PER_MINUTE','5')),
                           int(os.environ['MASSIVE_LEGACY_RESERVED_REQUESTS_PER_MINUTE']))
    except (KeyError, ValueError):
        raise BudgetUnavailable('CONFIGURATION_UNAVAILABLE') from None


def validate_configuration():
    url = os.getenv('MASSIVE_BUDGET_REDIS_REST_URL') or os.getenv('KV_REST_API_URL') or os.getenv('UPSTASH_REDIS_REST_URL')
    token = os.getenv('MASSIVE_BUDGET_REDIS_REST_TOKEN') or os.getenv('KV_REST_API_TOKEN') or os.getenv('UPSTASH_REDIS_REST_TOKEN')
    try:
        n = int(os.getenv('MASSIVE_REQUESTS_PER_MINUTE', '5'))
        r = int(os.environ['MASSIVE_LEGACY_RESERVED_REQUESTS_PER_MINUTE'])
    except (KeyError, ValueError):
        raise BudgetUnavailable('CONFIGURATION_UNAVAILABLE') from None
    parsed=urlsplit(url or '')
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or not token or any(ord(c)<33 or ord(c)>126 for c in token) or not 0 <= r < n <= 9007199254740991:
        raise BudgetUnavailable('CONFIGURATION_UNAVAILABLE')
