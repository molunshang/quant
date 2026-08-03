"""Agent API tests (fastapi TestClient)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_chat_returns_session():
    r = client.post("/api/chat", json={"message": "做年化10%", "goal": "年化>=10%"})
    assert r.status_code == 200
    assert "session_id" in r.json()


def test_providers_endpoint():
    r = client.get("/api/providers")
    assert r.status_code == 200
    assert "providers" in r.json()


def test_published_strategies_endpoint():
    r = client.get("/api/strategies/published")
    assert r.status_code == 200
    assert "strategies" in r.json()
