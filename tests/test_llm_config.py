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
