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
