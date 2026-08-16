import asyncio
import logging

import httpx
import pytest

from app.market_data.base import MarketDataError
from app.market_data.massive import MassiveMarketDataProvider, log_configuration_metadata


def test_successful_snapshot_uses_expected_path_and_query_authentication():
    async def scenario():
        provider = MassiveMarketDataProvider("known-test-key", "https://api.massive.com")
        captured = {}

        async def successful_get(path, params):
            captured.update(path=path, params=params)
            request = provider._client.build_request("GET", path, params=params)
            return httpx.Response(200, request=request, json={"status": "OK", "ticker": {"ticker": "QQQ"}})

        provider._client.get = successful_get
        snapshot = await provider.get_stock_snapshot(" qqq ")
        await provider.close()
        return captured, snapshot

    captured, snapshot = asyncio.run(scenario())
    assert captured == {
        "path": "/v2/snapshot/locale/us/markets/stocks/tickers/QQQ",
        "params": {"apiKey": "known-test-key"},
    }
    assert snapshot == {"ticker": "QQQ"}


def test_aggregate_request_construction_preserves_params_and_query_authentication():
    async def scenario():
        provider = MassiveMarketDataProvider("known-test-key")
        captured = {}

        async def successful_get(path, params):
            captured.update(path=path, params=params)
            request = provider._client.build_request("GET", path, params=params)
            return httpx.Response(200, request=request, json={
                "status": "OK",
                "results": [{"t": 1_700_000_000_000, "o": 1, "h": 2, "l": 1, "c": 2, "v": 10}],
            })

        provider._client.get = successful_get
        from datetime import date
        await provider.get_minute_bars("QQQ", date(2026, 8, 1), date(2026, 8, 15))
        await provider.close()
        return captured

    captured = asyncio.run(scenario())
    assert captured["path"] == "/v2/aggs/ticker/QQQ/range/1/minute/2026-08-01/2026-08-15"
    assert captured["params"] == {
        "adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": "known-test-key",
    }


def test_http_status_error_logs_safe_bounded_diagnostics(caplog):
    secret = "secret-test-key"
    body = f'{{"error":"denied", "apiKey":"{secret}", "detail":"' + ("x" * 400) + '"}'

    async def scenario():
        provider = MassiveMarketDataProvider(secret)

        async def failed_get(path, params):
            request = provider._client.build_request("GET", path, params=params)
            response = httpx.Response(429, request=request, text=body)
            raise httpx.HTTPStatusError("failed", request=request, response=response)

        provider._client.get = failed_get
        with pytest.raises(MarketDataError):
            await provider._get_payload("/v2/test", {})
        await provider.close()

    with caplog.at_level(logging.WARNING, logger="app.market_data.massive"):
        asyncio.run(scenario())

    assert "status=429" in caplog.text
    assert "path=/v2/test" in caplog.text
    assert "denied" in caplog.text
    assert secret not in caplog.text
    assert "apiKey=secret" not in caplog.text
    assert "https://" not in caplog.text
    assert len(caplog.records[0].args[2]) <= 300


def test_startup_diagnostics_report_metadata_without_url_or_key_secrets(caplog):
    secret = "x" * 32
    with caplog.at_level(logging.INFO, logger="app.market_data.massive"):
        log_configuration_metadata(secret, f"https://user:{secret}@api.massive.com?apiKey={secret}")

    assert "MASSIVE_API_KEY present: true" in caplog.text
    assert "MASSIVE_API_KEY length: 32" in caplog.text
    assert "MASSIVE_BASE_URL: https://api.massive.com" in caplog.text
    assert secret not in caplog.text
    assert "user:" not in caplog.text
    assert "?" not in caplog.text
