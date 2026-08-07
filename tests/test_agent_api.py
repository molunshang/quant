"""Agent API tests (fastapi TestClient)."""
from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.agent.api import register_agent_routes
from api.agent.config import ProviderConfigStore
from api.agent.provider import LLMResponse
from api.agent.store import AgentSessionStore, ChatStore, StrategyStore


class FakeGateProvider:
    """Returns scripted LLMResponse in order; records calls."""
    def __init__(self, script):
        self.script = list(script)
        self.calls = []
    def complete(self, *, system, messages, tools, model=None, max_tokens=4096):
        self.calls.append({"system": system, "messages": messages, "tools": tools})
        if self.script:
            return self.script.pop(0)
        return type("R", (), {"text": "完成", "tool_uses": []})()


class FakeExecutor:
    def submit(self, *a, **k):
        return 1
    def wait_all(self, timeout=300):
        return []
    def reset_batch(self):
        pass


def test_chat_returns_session(tmp_path, monkeypatch):
    app = FastAPI()
    store = StrategyStore(str(tmp_path / "s.db"))
    chat = ChatStore(str(tmp_path / "c.db"))
    sess = AgentSessionStore(str(tmp_path / "a.db"))

    # 假 config：写一个 provider 配置，再把 providers() 返回替换为 FakeGateProvider，
    # 这样 handle_chat -> gate_extract 走假 provider，绝不触发真实 LLM 或网络
    cfg_path = tmp_path / "llm.json"
    cfg_path.write_text(json.dumps({"default": "p1", "providers": [{
        "name": "p1", "type": "openai_compat", "base_url": "http://127.0.0.1:1",
        "model": "m", "api_key": "k"}]}), encoding="utf-8")
    cfg = ProviderConfigStore(str(cfg_path))
    fake = FakeGateProvider([LLMResponse(
        text='{"universe": ["沪深300"], "constraints": {"annual_return": 0.10}}', tool_uses=[])])
    cfg.providers = lambda: {"p1": fake}

    register_agent_routes(app, store=store, chat_store=chat, session_store=sess,
                          config=cfg, executor=FakeExecutor())
    client = TestClient(app)
    r = client.post("/api/chat", json={"message": "做年化10%", "goal": "年化>=10%"})
    assert r.status_code == 200
    assert "session_id" in r.json()
    # 后台线程落库后再断言消息已持久化到隔离 store
    import time
    for _ in range(100):
        if chat.list_messages(r.json()["session_id"]):
            break
        time.sleep(0.05)
    assert len(chat.list_messages(r.json()["session_id"])) >= 2


def test_providers_endpoint(tmp_path):
    client, *_ = _make_client(tmp_path)
    r = client.get("/api/providers")
    assert r.status_code == 200
    assert "providers" in r.json()


def test_published_strategies_endpoint(tmp_path):
    client, *_ = _make_client(tmp_path)
    r = client.get("/api/strategies/published")
    assert r.status_code == 200
    assert "strategies" in r.json()


def _make_client(tmp_path):
    """隔离的 agent 路由：注入 tmp_path 下的 store，不碰真实 data/agent.db。"""
    app = FastAPI()
    store = StrategyStore(str(tmp_path / "s.db"))
    chat = ChatStore(str(tmp_path / "c.db"))
    sess = AgentSessionStore(str(tmp_path / "a.db"))
    register_agent_routes(app, store=store, chat_store=chat, session_store=sess)
    return TestClient(app), store, chat, sess


def test_chat_history_empty(tmp_path):
    client, *_ = _make_client(tmp_path)
    r = client.get("/api/chat/history")
    assert r.status_code == 200
    assert r.json() == {"sessions": [], "has_more": False}


def test_chat_history_and_detail(tmp_path):
    client, store, chat, sess = _make_client(tmp_path)
    # 直接写 store，避免 /api/chat 触发真实 LLM 调用
    msg_id = chat.add_message("s1", "user", "在沪深300做到年化10%")
    chat.add_message("s1", "assistant", "完成")
    chat.add_tool_call("s1", msg_id, 1, "list_symbols", {"type": "stock"}, '{"symbols": []}', 0)
    store.register_draft("ma", "def strategy(ctx, p):\n    pass", "sma")
    store.link_session_strategy("s1", "ma", 1)
    # history
    r = client.get("/api/chat/history")
    data = r.json()
    assert len(data["sessions"]) == 1
    s = data["sessions"][0]
    assert s["session_id"] == "s1"
    assert "在沪深300做到年化10%" in s["title"]
    assert s["message_count"] == 2
    assert s["strategy_names"] == ["ma"]
    assert s["status"] == "done"  # agent_sessions 无行时视为 done
    # detail
    r2 = client.get("/api/chat/sessions/s1")
    d = r2.json()
    assert d["session_id"] == "s1"
    assert len(d["messages"]) == 2
    msg = d["messages"][0]
    assert msg["role"] == "user"
    assert len(msg["tool_calls"]) == 1
    assert msg["tool_calls"][0]["name"] == "list_symbols"
    assert msg["tool_calls"][0]["input"] == '{"type": "stock"}'
    assert msg["tool_calls"][0]["is_error"] == 0
    assert d["messages"][1]["tool_calls"] == []
    assert len(d["strategies"]) == 1
    assert d["strategies"][0]["name"] == "ma"
    assert "def strategy" in d["strategies"][0]["source"]


def test_chat_session_detail_unknown_404(tmp_path):
    client, *_ = _make_client(tmp_path)
    r = client.get("/api/chat/sessions/__nope__")
    assert r.status_code == 404


def test_chat_history_pagination(tmp_path):
    client, store, chat, sess = _make_client(tmp_path)
    for i in range(5):
        chat.add_message(f"s{i}", "user", f"目标 {i}")
    r = client.get("/api/chat/history", params={"offset": 0, "limit": 2})
    data = r.json()
    assert len(data["sessions"]) == 2
    assert data["has_more"] is True
    assert data["sessions"][0]["session_id"] == "s4"  # 最新在前
    r2 = client.get("/api/chat/history", params={"offset": 4, "limit": 2})
    data2 = r2.json()
    assert len(data2["sessions"]) == 1
    assert data2["has_more"] is False
    assert data2["sessions"][0]["session_id"] == "s0"
    # 非法参数 clamp
    r3 = client.get("/api/chat/history", params={"offset": -1, "limit": 1000})
    assert r3.status_code == 200
    assert len(r3.json()["sessions"]) == 5
    assert r3.json()["has_more"] is False
