"""LLMAgent loop tests (fake provider, no network)."""
from __future__ import annotations

import json
from collections import deque

import pytest

from api.agent.agent import LLMAgent, build_system_prompt
from api.agent.provider import LLMResponse, ToolCall


class FakeProvider:
    def __init__(self, script):
        """script: list of LLMResponse to return in order, then final."""
        self.script = list(script)
        self.calls = []
    def complete(self, *, system, messages, tools, model=None, max_tokens=4096):
        self.calls.append({"system": system, "messages": messages, "tools": tools})
        if self.script:
            return self.script.pop(0)
        return LLMResponse(text="完成", tool_uses=[])


class FakeExecutor:
    def submit(self, *a, **k):
        return 1
    def wait_all(self, timeout=300):
        return [{"job_id": 1, "symbol": "510300", "status": "done",
                 "result": {"metrics": {"annual_return": 0.12, "max_drawdown": -0.10}, "symbol_name": "沪深300ETF"},
                 "error": None}]
    def reset_batch(self):
        pass


class FakeStore:
    def __init__(self):
        self.drafts = {}
        self.published = []
    def register_draft(self, name, source, description=""):
        v = len(self.drafts.get(name, [])) + 1
        self.drafts[name] = [source]
        return {"name": name, "version": v, "status": "draft"}
    def get_strategy(self, name):
        if name not in self.drafts:
            return None
        return {"name": name, "status": "draft", "current_version": len(self.drafts[name])}
    def get_source(self, name, version=None):
        if name not in self.drafts:
            return None
        return self.drafts[name][-1]
    def list_strategies(self, include_drafts=True):
        return [{"name": n, "status": "draft", "current_version": len(v)} for n, v in self.drafts.items()]
    def publish_version(self, name, version, metrics, goal):
        self.published.append((name, version))
        return {"name": name, "version": version, "status": "published", "metrics": metrics}


class FakeBus:
    def __init__(self):
        self.events = []
    def publish(self, session_id, event):
        self.events.append(event)


def test_agent_loops_until_publish():
    # Turn 1: register draft + run backtest; Turn 2: check_goal (met) + publish; Turn 3: final text.
    provider = FakeProvider([
        LLMResponse(text=None, tool_uses=[
            ToolCall(id="1", name="register_strategy", input={"name": "ma", "source": "def strategy(ctx,p): pass"}),
            ToolCall(id="2", name="run_backtest", input={"symbol": "510300", "strategy_ref": "ma"}),
        ]),
        LLMResponse(text=None, tool_uses=[
            ToolCall(id="3", name="check_goal", input={"metrics": {"annual_return": 0.12}, "constraints": {"annual_return": 0.10}}),
            ToolCall(id="4", name="publish_strategy", input={"name": "ma", "goal_met": True, "metrics": {"annual_return": 0.12}, "goal": "年化>=10%"}),
        ]),
        LLMResponse(text="目标达成，已发布", tool_uses=[]),
    ])
    agent = LLMAgent(provider=provider, store=FakeStore(), executor=FakeExecutor(),
                     max_turns=10, max_tools_per_turn=5)
    bus = FakeBus()
    report = agent.run("s1", "做年化10%", goal="年化>=10%", bus=bus)
    assert "目标达成" in report.get("report", "")
    assert len(provider.calls) == 3
    # second turn state snapshot includes backtest result
    assert "0.12" in json.dumps(provider.calls[1]["messages"])


def test_agent_stops_at_max_turns():
    provider = FakeProvider([
        LLMResponse(text=None, tool_uses=[ToolCall(id="1", name="list_strategies", input={})])
    ] * 3)  # 3 identical turns; max_turns=2 should stop before using all
    agent = LLMAgent(provider=provider, store=FakeStore(), executor=FakeExecutor(),
                     max_turns=2, max_tools_per_turn=5)
    bus = FakeBus()
    report = agent.run("s1", "试试", goal=None, bus=bus)
    assert len(provider.calls) == 2  # hit max_turns


def test_build_system_prompt_contains_goal():
    p = build_system_prompt("年化>=10%")
    assert "年化>=10%" in p
    assert "run_backtest" in p or "回测" in p


def test_hydrated_manager_resolves_draft_name(monkeypatch, tmp_path):
    """Store drafts reach run_backtest: strategy_ref=<draft name> resolves via the
    hydrated StrategyManager threaded through the BacktestExecutor."""
    from api.agent.executor import BacktestExecutor
    from api.agent.store import StrategyStore

    store = StrategyStore(db_path=str(tmp_path / "t.db"))
    store.register_draft("ma", "def strategy(ctx, params):\n    pass", "sma")

    captured = {}

    def fake_run_backtest(symbol, strategy_ref, params=None, freq="daily", start="2020-01-01",
                          end="2024-12-31", adjust="qfq", initial_cash=100_000.0,
                          strategy_manager=None, data_layer=None):
        # A fresh empty StrategyManager would raise KeyError here; the hydrated
        # manager loaded from the store must resolve the draft name.
        func, name = strategy_manager.resolve(strategy_ref)
        captured["resolved"] = name
        return {"success": True, "symbol": symbol, "symbol_name": "测试ETF", "freq": freq,
                "metrics": {"total_return": 0.2, "annual_return": 0.12, "max_drawdown": -0.10},
                "equity_curve": [], "trades": [], "strategy": name, "params": params or {}}

    import api.agent.executor as exmod
    monkeypatch.setattr(exmod, "run_backtest", fake_run_backtest)

    provider = FakeProvider([
        LLMResponse(text=None, tool_uses=[
            ToolCall(id="1", name="run_backtest", input={"symbol": "510300", "strategy_ref": "ma"}),
        ]),
        LLMResponse(text="完成", tool_uses=[]),
    ])
    ex = BacktestExecutor()
    agent = LLMAgent(provider=provider, store=store, executor=ex,
                     max_turns=5, max_tools_per_turn=5)
    bus = FakeBus()
    try:
        agent.run("s1", "回测 ma", bus=bus)
    finally:
        ex.shutdown()
    assert captured.get("resolved") == "ma"
