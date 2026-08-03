"""LLM provider abstraction tests (mocked, no network)."""
from __future__ import annotations

from api.agent.provider import (
    ToolCall,
    LLMResponse,
    OpenAICompatProvider,
    load_providers,
)


def test_load_providers_expands_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_LLM_KEY", "sk-test")
    cfg = tmp_path / "llm.json"
    cfg.write_text(
        '{"providers": [{"name": "local", "type": "openai_compat", '
        '"base_url": "http://127.0.0.1:3456", "model": "m1", "api_key": "env:TEST_LLM_KEY"}]}'
    )
    providers = load_providers(str(cfg))
    assert "local" in providers
    assert isinstance(providers["local"], OpenAICompatProvider)
    assert providers["local"].model == "m1"


def test_openai_provider_builds_tool_uses(monkeypatch):
    # Capture the request body; return a fake completion with one tool_use.
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return {"choices": [{"message": {
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "run_backtest", "arguments": '{"symbol":"510300"}'},
                }],
            }}]}

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr("api.agent.provider.OpenAI", FakeClient)
    p = OpenAICompatProvider(api_key="k", base_url="http://x", model="m1")
    resp = p.complete(system="sys", messages=[], tools=[])
    assert isinstance(resp, LLMResponse)
    assert len(resp.tool_uses) == 1
    assert resp.tool_uses[0].name == "run_backtest"
    assert resp.tool_uses[0].input == {"symbol": "510300"}
    assert captured["model"] == "m1"
    assert captured["tools"] == []
