from dataclasses import replace

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import DatabaseBackend
from app.persistence import create_stores
from tests.test_service_v2 import FakeProvider


def test_unavailable_postgres_makes_readiness_false_without_leaking_url(monkeypatch):
    secret = "readiness-secret-password"
    configured = replace(main_module.settings, database_backend=DatabaseBackend.POSTGRES,
        database_url=f"postgresql+psycopg://user:{secret}@127.0.0.1:1/missing",
        db_connect_timeout_seconds=1, massive_api_key="configured")
    monkeypatch.setattr(main_module, "settings", configured)
    monkeypatch.setattr(main_module, "MassiveMarketDataProvider", lambda *_: FakeProvider())
    with TestClient(main_module.app) as client:
        readiness = client.get("/api/readiness")
        health = client.get("/health")
    assert readiness.status_code == 200 and readiness.json()["ready"] is False
    assert health.status_code == 200
    assert secret not in readiness.text and secret not in health.text


def test_postgres_readiness_requires_expected_alembic_revision(monkeypatch):
    class Connection:
        dialect = type("Dialect", (), {"name": "postgresql"})()
        def execute(self, statement):
            class Result:
                def scalar_one(self): return "wrong-revision"
            return Result()
        def __enter__(self): return self
        def __exit__(self, *_): return None
    class Engine:
        def connect(self): return Connection()
    store = create_stores("sqlite:///:memory:").signal
    store.engine = Engine()
    assert store.ping() is False
