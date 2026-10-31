import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.intelligence_api import router


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / 'reports.sqlite3'
    monkeypatch.setenv('MARKET_INTELLIGENCE_DB', str(path))
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client, path


def payload(hours=1):
    now = datetime.now(timezone.utc)
    return dict(schema_version='market-intelligence-v1', report_date=now.date().isoformat(),
                generated_at=now.isoformat(), observation_cutoff=(now-timedelta(hours=hours)).isoformat(),
                market_regime={}, daily_features={}, intraday_features={}, intraday_playbook=[],
                swing_playbook=[], options_intelligence=[], options_flow_intelligence=[], data_quality={}, data_errors=[])


def save(path, reports):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE intelligence_reports (report_id TEXT, generated_at TEXT, report_json TEXT)')
        for i, r in enumerate(reports):
            db.execute('INSERT INTO intelligence_reports VALUES (?, ?, ?)', (str(i), r.get('generated_at', ''), json.dumps(r)))


def test_missing_store_is_not_created(client):
    c, path = client
    assert c.get('/api/v1/market-intelligence/latest').status_code == 404
    assert c.get('/api/v1/market-intelligence/history').json()['reports'] == []
    assert not path.exists()


@pytest.mark.parametrize('hours,status', [(1,'RECENT'), (25,'STALE'), (-1,'FUTURE_TIMESTAMP')])
def test_freshness_and_read_only(client, hours, status):
    c, path = client
    save(path, [payload(hours)])
    before = path.read_bytes()
    response = c.get('/api/v1/market-intelligence/latest')
    assert response.status_code == 200
    assert response.json()['freshness'] == status
    assert response.json()['report']['report_id'] == '0'
    assert path.read_bytes() == before
    assert c.post('/api/v1/market-intelligence/latest').status_code == 405


def test_history_order_and_bounds(client):
    c, path = client
    older, newer = payload(), payload()
    older['generated_at'] = '2020-01-01T00:00:00+00:00'
    save(path, [older, newer])
    assert c.get('/api/v1/market-intelligence/latest').json()['report']['report_id'] == '1'
    assert c.get('/api/v1/market-intelligence/history?limit=1&offset=1').json()['reports'][0]['report']['report_id'] == '0'
    for query in ['limit=101', 'limit=0', 'offset=-1']:
        assert c.get('/api/v1/market-intelligence/history?' + query).status_code == 422


@pytest.mark.parametrize('change', [dict(schema_version='unknown'), dict(generated_at='bad'), dict(daily_features={'QQQ': {'symbol': 'QQQ'}})])
def test_invalid_schema_fails_closed(client, change):
    c, path = client
    save(path, [dict(payload(), **change)])
    assert c.get('/api/v1/market-intelligence/latest').status_code == 503


def test_corrupt_store(client):
    c, path = client
    path.write_text('invalid database')
    assert c.get('/api/v1/market-intelligence/latest').status_code == 503
