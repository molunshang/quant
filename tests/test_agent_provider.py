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


def test_anthropic_messages_preserves_thinking_blocks():
    """Assistant messages must carry thinking blocks back to the model verbatim
    (deepseek-v4-flash etc. require the reasoning_content to be passed back)."""
    from api.agent.provider import _anthropic_messages

    msg = {
        "role": "assistant",
        "tool_calls": [{"id": "t1", "name": "list_symbols", "input": {}}],
        "assistant_blocks": [
            {"type": "thinking", "thinking": "let me think", "signature": "sig-abc"},
            {"type": "tool_use", "id": "t1", "name": "list_symbols", "input": {}},
        ],
    }
    out = _anthropic_messages([msg])
    content = out[0]["content"]
    assert content[0] == {"type": "thinking", "thinking": "let me think", "signature": "sig-abc"}
    assert content[1]["type"] == "tool_use"


def test_anthropic_messages_fallback_without_blocks():
    """Without assistant_blocks, the old tool_calls-only reconstruction still works."""
    from api.agent.provider import _anthropic_messages

    msg = {"role": "assistant", "tool_calls": [{"id": "t1", "name": "list_symbols", "input": {}}]}
    out = _anthropic_messages([msg])
    content = out[0]["content"]
    assert len(content) == 1
    assert content[0]["type"] == "tool_use"
    assert content[0]["id"] == "t1"


def test_anthropic_provider_keeps_thinking_block(monkeypatch):
    """complete() must surface non-text/non-tool blocks so the agent loop can
    pass them back (thinking blocks from reasoning models)."""
    from api.agent.provider import AnthropicProvider

    captured = {}

    class FakeContent:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class FakeResp:
        content = [
            FakeContent(type="thinking", thinking="reason...", signature="sig-x"),
            FakeContent(type="tool_use", id="tu1", name="list_symbols", input={}),
        ]

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return FakeResp()

    class FakeClient:
        def __init__(self, **kwargs):
            self.messages = FakeMessages()

    monkeypatch.setattr("api.agent.provider.anthropic", type("An", (), {"Anthropic": FakeClient}))
    p = AnthropicProvider(api_key="k", base_url="http://x", model="m1")
    resp = p.complete(system="sys", messages=[], tools=[])
    assert resp.assistant_blocks is not None
    types = [b["type"] for b in resp.assistant_blocks]
    assert "thinking" in types
    assert "tool_use" in types


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
