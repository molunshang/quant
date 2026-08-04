"""LLM config store + API tests (isolated tmp config, mocked network)."""
from __future__ import annotations

import json

import pytest

from api.agent.config import ConfigError, ProviderConfigStore


def _cfg(tmp_path, text=None):
    path = tmp_path / "llm.json"
    if text is not None:
        path.write_text(text, encoding="utf-8")
    return path


def test_list_empty_when_no_file(tmp_path):
    store = ProviderConfigStore(str(_cfg(tmp_path)))
    assert store.list() == {"default": None, "providers": [], "error": None}


def test_list_reads_existing_file(tmp_path):
    path = _cfg(tmp_path, '{"default": "a", "providers": [{"name": "a", "type": "anthropic"}]}')
    store = ProviderConfigStore(str(path))
    data = store.list()
    assert data["default"] == "a"
    assert [p["name"] for p in data["providers"]] == ["a"]


def test_corrupt_file_raises_config_error(tmp_path):
    store = ProviderConfigStore(str(_cfg(tmp_path, "{not json")))
    with pytest.raises(ConfigError):
        store.list()


def test_providers_builds_from_file(tmp_path):
    path = _cfg(tmp_path, '{"providers": [{"name": "local", "type": "openai_compat", '
                          '"base_url": "http://127.0.0.1:3456", "model": "m1", "api_key": "k"}]}')
    store = ProviderConfigStore(str(path))
    assert "local" in store.providers()


def test_providers_degrades_on_bad_entry(tmp_path):
    # provider missing 'name' -> load_providers KeyError -> empty providers + error exposed
    path = _cfg(tmp_path, '{"providers": [{"type": "anthropic", "api_key": "k", "model": "m"}]}')
    store = ProviderConfigStore(str(path))
    assert store.providers() == {}
    assert "解析失败" in (store.list()["error"] or "")


def test_add_valid_writes_file(tmp_path):
    store = ProviderConfigStore(str(_cfg(tmp_path)))
    data = store.add({"name": "p1", "type": "anthropic", "model": "m", "api_key": "k"})
    assert [p["name"] for p in data["providers"]] == ["p1"]
    assert json.loads(_cfg(tmp_path).read_text())["providers"][0]["name"] == "p1"
    assert "p1" in store.providers()  # reloaded, live


def test_add_duplicate_name_rejected_no_write(tmp_path):
    path = _cfg(tmp_path, '{"providers": [{"name": "p1", "type": "anthropic", "model": "m", "api_key": "k"}]}')
    store = ProviderConfigStore(str(path))
    with pytest.raises(ConfigError, match="已存在"):
        store.add({"name": "p1", "type": "anthropic", "model": "m2", "api_key": "k"})
    assert [p["name"] for p in store.list()["providers"]] == ["p1"]


def test_add_invalid_type_rejected(tmp_path):
    store = ProviderConfigStore(str(_cfg(tmp_path)))
    with pytest.raises(ConfigError, match="类型"):
        store.add({"name": "p1", "type": "unknown", "model": "m", "api_key": "k"})


def test_add_openai_compat_requires_base_url(tmp_path):
    store = ProviderConfigStore(str(_cfg(tmp_path)))
    with pytest.raises(ConfigError, match="base_url"):
        store.add({"name": "p1", "type": "openai_compat", "model": "m", "api_key": "k"})


def test_add_empty_model_rejected(tmp_path):
    store = ProviderConfigStore(str(_cfg(tmp_path)))
    with pytest.raises(ConfigError, match="模型"):
        store.add({"name": "p1", "type": "anthropic", "model": " ", "api_key": "k"})


def test_add_empty_key_rejected(tmp_path):
    store = ProviderConfigStore(str(_cfg(tmp_path)))
    with pytest.raises(ConfigError, match="API Key"):
        store.add({"name": "p1", "type": "anthropic", "model": "m", "api_key": ""})


def test_update_edits_provider(tmp_path):
    path = _cfg(tmp_path, '{"providers": [{"name": "p1", "type": "anthropic", "model": "m1", "api_key": "k"}]}')
    store = ProviderConfigStore(str(path))
    data = store.update("p1", {"type": "anthropic", "model": "m2", "api_key": "k"})
    p1 = next(p for p in data["providers"] if p["name"] == "p1")
    assert p1["model"] == "m2"


def test_update_unknown_name_rejected(tmp_path):
    store = ProviderConfigStore(str(_cfg(tmp_path)))
    with pytest.raises(ConfigError, match="不存在"):
        store.update("nope", {"type": "anthropic", "model": "m", "api_key": "k"})


def test_delete_removes_provider(tmp_path):
    path = _cfg(tmp_path, '{"providers": [{"name": "p1", "type": "anthropic", "model": "m", "api_key": "k"}]}')
    store = ProviderConfigStore(str(path))
    data = store.delete("p1")
    assert data["providers"] == []
    assert "p1" not in store.providers()


def test_delete_default_clears_default(tmp_path):
    path = _cfg(tmp_path, '{"default": "p1", "providers": [{"name": "p1", "type": "anthropic", "model": "m", "api_key": "k"}]}')
    store = ProviderConfigStore(str(path))
    assert store.delete("p1")["default"] is None


def test_delete_unknown_rejected(tmp_path):
    store = ProviderConfigStore(str(_cfg(tmp_path)))
    with pytest.raises(ConfigError, match="不存在"):
        store.delete("nope")


def test_set_default(tmp_path):
    path = _cfg(tmp_path, '{"providers": [{"name": "p1", "type": "anthropic", "model": "m", "api_key": "k"},'
                          '{"name": "p2", "type": "anthropic", "model": "m", "api_key": "k"}]}')
    store = ProviderConfigStore(str(path))
    data = store.set_default("p2")
    assert data["default"] == "p2"
    assert store.get_default() == "p2"


def test_set_default_unknown_rejected(tmp_path):
    store = ProviderConfigStore(str(_cfg(tmp_path)))
    with pytest.raises(ConfigError, match="不存在"):
        store.set_default("nope")


class _FakeProvider:
    def __init__(self, exc=None):
        self.exc = exc
        self.called = False

    def complete(self, **kwargs):
        self.called = True
        if self.exc:
            raise self.exc
        from api.agent.provider import LLMResponse
        return LLMResponse(text="pong")


class _StatusError(Exception):
    def __init__(self, status_code, msg):
        super().__init__(msg)
        self.status_code = status_code


def _store_with_fake_build(monkeypatch, tmp_path, exc=None):
    store = ProviderConfigStore(str(_cfg(tmp_path)))
    fake = _FakeProvider(exc)
    monkeypatch.setattr(store, "_build", lambda p: fake)
    return store, fake


def test_test_connection_ok(monkeypatch, tmp_path):
    store, fake = _store_with_fake_build(monkeypatch, tmp_path)
    res = store.test({"name": "p", "type": "anthropic", "model": "m", "api_key": "k"})
    assert res["ok"] is True
    assert res["error"] is None
    assert fake.called


def test_test_env_missing(tmp_path):
    store = ProviderConfigStore(str(_cfg(tmp_path)))
    res = store.test({"name": "p", "type": "anthropic", "model": "m", "api_key": "env:NOT_SET_XYZ"})
    assert res["ok"] is False
    assert "NOT_SET_XYZ" in res["error"]


def test_test_error_includes_base_url_and_detail(monkeypatch, tmp_path):
    store, _ = _store_with_fake_build(monkeypatch, tmp_path, exc=ConnectionError("Connection refused"))
    res = store.test({"name": "p", "type": "openai_compat", "base_url": "http://127.0.0.1:9",
                      "model": "m", "api_key": "k"})
    assert res["ok"] is False
    assert "http://127.0.0.1:9" in res["error"]
    assert "Connection refused" in res["error"]


def test_test_error_includes_http_status(monkeypatch, tmp_path):
    store, _ = _store_with_fake_build(monkeypatch, tmp_path, exc=_StatusError(401, "Unauthorized"))
    res = store.test({"name": "p", "type": "anthropic", "model": "m", "api_key": "k"})
    assert res["ok"] is False
    assert "401" in res["error"]


def _make_client(tmp_path, monkeypatch):
    import os

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.agent.api import register_agent_routes

    cfg_path = tmp_path / "llm.json"
    cfg_path.write_text('{"providers": []}', encoding="utf-8")
    monkeypatch.setenv("QUANT_LLM_CONFIG", str(cfg_path))
    app = FastAPI()
    register_agent_routes(app)
    return TestClient(app), cfg_path


def test_api_list_empty(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    r = client.get("/api/llm/providers/list")
    assert r.status_code == 200
    assert r.json()["providers"] == []


def test_api_add_and_list(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    body = {"name": "p1", "type": "openai_compat", "base_url": "http://127.0.0.1:3456",
            "model": "m1", "api_key": "k"}
    r = client.post("/api/llm/providers/add", json=body)
    assert r.status_code == 200
    assert [p["name"] for p in r.json()["providers"]] == ["p1"]
    r2 = client.get("/api/llm/providers/list")
    assert [p["name"] for p in r2.json()["providers"]] == ["p1"]


def test_api_add_invalid_returns_400(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    r = client.post("/api/llm/providers/add",
                    json={"name": "", "type": "anthropic", "model": "m", "api_key": "k"})
    assert r.status_code == 400
    assert "名称" in r.json()["detail"]


def test_api_update(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    client.post("/api/llm/providers/add", json={"name": "p1", "type": "openai_compat",
                "base_url": "http://x", "model": "m1", "api_key": "k"})
    r = client.post("/api/llm/providers/update", json={"name": "p1", "type": "openai_compat",
                    "base_url": "http://x", "model": "m2", "api_key": "k"})
    assert r.status_code == 200
    p1 = next(p for p in r.json()["providers"] if p["name"] == "p1")
    assert p1["model"] == "m2"


def test_api_delete(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    client.post("/api/llm/providers/add", json={"name": "p1", "type": "openai_compat",
                "base_url": "http://x", "model": "m1", "api_key": "k"})
    r = client.post("/api/llm/providers/delete", json={"name": "p1"})
    assert r.status_code == 200
    assert r.json()["providers"] == []


def test_api_default_set(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    client.post("/api/llm/providers/add", json={"name": "p1", "type": "openai_compat",
                "base_url": "http://x", "model": "m1", "api_key": "k"})
    r = client.post("/api/llm/default/set", json={"name": "p1"})
    assert r.status_code == 200
    assert r.json()["default"] == "p1"


def test_api_test_env_missing(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    r = client.post("/api/llm/providers/test", json={"name": "p1", "type": "anthropic",
                    "model": "m", "api_key": "env:NOT_SET_XYZ"})
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert "NOT_SET_XYZ" in r.json()["error"]


def test_api_chat_uses_default_provider(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    client.post("/api/llm/providers/add", json={"name": "p1", "type": "openai_compat",
                "base_url": "http://127.0.0.1:1", "model": "m1", "api_key": "k"})
    client.post("/api/llm/default/set", json={"name": "p1"})
    r = client.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 200
    assert "session_id" in r.json()


def test_add_provider_with_unset_env_degrades_gracefully(tmp_path):
    store = ProviderConfigStore(str(_cfg(tmp_path)))
    # api_key env var that is NOT set -> reload would raise OpenAIError if not caught
    data = store.add({"name": "p1", "type": "openai_compat", "base_url": "http://127.0.0.1:1",
                      "model": "m", "api_key": "env:SURELY_NOT_SET_XYZ"})
    # must NOT raise; provider persisted; providers() degrades to {} with a surfaced error
    assert [p["name"] for p in data["providers"]] == ["p1"]
    assert "解析失败" in (data["error"] or "")
    assert store.providers() == {}


def test_add_anthropic_non_string_base_url_rejected(tmp_path):
    store = ProviderConfigStore(str(_cfg(tmp_path)))
    with pytest.raises(ConfigError, match="base_url"):
        store.add({"name": "p1", "type": "anthropic", "base_url": 123, "model": "m", "api_key": "k"})


def test_build_sets_short_timeout(tmp_path):
    store = ProviderConfigStore(str(_cfg(tmp_path)))
    a = store._build({"name": "p", "type": "anthropic", "model": "m", "api_key": "k"})
    assert a._client.timeout == 10.0
    o = store._build({"name": "p", "type": "openai_compat", "base_url": "http://127.0.0.1:1",
                      "model": "m", "api_key": "k"})
    assert o._client.timeout == 10.0
