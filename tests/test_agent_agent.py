"""LLMAgent loop tests (fake provider, no network)."""
from __future__ import annotations

import json
from collections import deque
from types import SimpleNamespace

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
        return [{"job_id": 1, "strategy": "ma", "universe": {"symbols": ["510300"]}, "status": "done",
                 "result": {"metrics": {"annual_return": 0.12, "max_drawdown": -0.10},
                            "symbol": "510300", "symbol_name": "沪深300ETF"},
                 "error": None}]
    def reset_batch(self):
        pass


class FakeStore:
    def __init__(self):
        self.drafts = {}
        self.published = []
        self.linked = []
    def register_draft(self, name, source, description=""):
        v = len(self.drafts.get(name, [])) + 1
        self.drafts[name] = [source]
        return {"name": name, "version": v, "status": "draft", "strategy_id": v}
    def link_session_strategy(self, session_id, name, version):
        self.linked.append((session_id, name, version))
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


class FakeChatStore:
    def __init__(self):
        self.calls = []
    def add_tool_call(self, session_id, message_id, turn, name, input, output, is_error):
        self.calls.append({
            "session_id": session_id, "message_id": message_id, "turn": turn,
            "name": name, "input": input, "output": output, "is_error": 1 if is_error else 0,
        })
        return len(self.calls)
    def list_tool_calls(self, session_id):
        return [c for c in self.calls if c["session_id"] == session_id]


def test_agent_loops_until_publish():
    # Turn 1: register draft + run backtest; Turn 2: check_goal (met) + publish; Turn 3: final text.
    provider = FakeProvider([
        LLMResponse(text=None, tool_uses=[
            ToolCall(id="1", name="register_strategy", input={"name": "ma", "source": "def handle_data(ctx):\n    pass"}),
            ToolCall(id="2", name="run_backtest", input={"strategy_ref": "ma"}),
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


def test_build_system_prompt_injects_structured_goal():
    p = build_system_prompt({
        "universe": ["沪深300"],
        "constraints": {"annual_return": 0.10, "max_drawdown": -0.15},
        "period": {"start": "2020-01-01", "end": "2024-12-31"},
        "benchmark": "沪深300 绝对收益",
    })
    assert "沪深300" in p
    assert "annual_return" in p
    assert "2020-01-01" in p
    assert "标的范围" in p
    assert "必须满足" in p


def test_hydrated_manager_resolves_draft_name(monkeypatch, tmp_path):
    """Store drafts reach run_backtest: strategy_ref=<draft name> resolves via the
    hydrated StrategyManager threaded through the BacktestExecutor."""
    from api.agent.executor import BacktestExecutor
    from api.agent.store import StrategyStore

    store = StrategyStore(db_path=str(tmp_path / "t.db"))
    store.register_draft("ma", "def handle_data(ctx):\n    pass", "sma")

    captured = {}

    def fake_run_backtest(strategy, universe=None, freq="daily", start="2020-01-01",
                          end="2024-12-31", adjust="qfq", initial_cash=100_000.0,
                          strategy_manager=None, data_layer=None):
        # A fresh empty StrategyManager would raise KeyError here; the hydrated
        # manager loaded from the store must resolve the draft name.
        func, name = strategy_manager.resolve(strategy)
        captured["resolved"] = name
        return {"success": True, "universe": ["510300"], "symbol": "510300", "symbol_name": "测试ETF", "freq": freq,
                "metrics": {"total_return": 0.2, "annual_return": 0.12, "max_drawdown": -0.10},
                "equity_curve": [], "trades": [], "strategy": name}

    import api.agent.executor as exmod
    monkeypatch.setattr(exmod, "run_backtest", fake_run_backtest)

    provider = FakeProvider([
        LLMResponse(text=None, tool_uses=[
            ToolCall(id="1", name="run_backtest", input={"strategy_ref": "ma"}),
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


def test_cross_turn_register_then_backtest_resolves(monkeypatch, tmp_path):
    """Turn 1 calls register_strategy via the tool (writes a real store draft);
    turn 2 calls run_backtest with strategy_ref=<registered name>. The draft must
    resolve through the hydrated StrategyManager — a fresh manager would KeyError."""
    from api.agent.executor import BacktestExecutor
    from api.agent.store import StrategyStore

    store = StrategyStore(db_path=str(tmp_path / "t.db"))

    captured = {}

    def fake_run_backtest(strategy, universe=None, freq="daily", start="2020-01-01",
                          end="2024-12-31", adjust="qfq", initial_cash=100_000.0,
                          strategy_manager=None, data_layer=None):
        # strategy_ref must be the name registered by the tool in turn 1; a
        # non-hydrated manager would raise KeyError here.
        func, name = strategy_manager.resolve(strategy)
        captured["resolved"] = name
        return {"success": True, "universe": ["510300"], "symbol": "510300", "symbol_name": "测试ETF", "freq": freq,
                "metrics": {"total_return": 0.2, "annual_return": 0.12, "max_drawdown": -0.10},
                "equity_curve": [], "trades": [], "strategy": name}

    import api.agent.executor as exmod
    monkeypatch.setattr(exmod, "run_backtest", fake_run_backtest)

    provider = FakeProvider([
        LLMResponse(text=None, tool_uses=[
            ToolCall(id="1", name="register_strategy",
                     input={"name": "ma", "source": "def handle_data(ctx):\n    pass"}),
        ]),
        LLMResponse(text=None, tool_uses=[
            ToolCall(id="2", name="run_backtest", input={"strategy_ref": "ma"}),
        ]),
        LLMResponse(text="完成", tool_uses=[]),
    ])
    ex = BacktestExecutor()
    agent = LLMAgent(provider=provider, store=store, executor=ex,
                     max_turns=5, max_tools_per_turn=5)
    bus = FakeBus()
    try:
        agent.run("s1", "注册 ma 并回测", bus=bus)
    finally:
        ex.shutdown()
    # the tool wrote the draft to the real store...
    assert store.get_source("ma") is not None
    # ...and turn 2's run_backtest resolved that draft by name.
    assert captured.get("resolved") == "ma"


def test_same_turn_register_then_backtest_resolves(tmp_path):
    """register_strategy + run_backtest in the SAME turn: the draft written by the
    tool must resolve when its backtest runs. Hydration happens after tool
    execution and before wait_all, so the manager is populated in time."""
    from api.agent.store import StrategyStore

    store = StrategyStore(db_path=str(tmp_path / "t.db"))
    captured = {}

    class SyncExecutor:
        """Resolves strategy_ref in wait_all (main thread, after hydration) exactly
        like the executor worker threads do in production."""
        def __init__(self):
            self._strategy_manager = None
            self.jobs = []
        def submit(self, strategy_ref, universe=None, freq="daily", start="2020-01-01",
                   end="2024-12-31", adjust="qfq"):
            self.jobs.append({"strategy_ref": strategy_ref, "universe": universe})
            return len(self.jobs)
        def wait_all(self, timeout=300):
            results = []
            for j in self.jobs:
                func, name = self._strategy_manager.resolve(j["strategy_ref"])
                captured["resolved"] = name
                results.append({"job_id": 1, "strategy": j["strategy_ref"], "universe": j["universe"],
                                "status": "done",
                                "result": {"metrics": {"annual_return": 0.12, "max_drawdown": -0.10},
                                           "symbol": "510300", "symbol_name": "测试ETF"},
                                "error": None})
            self.jobs = []
            return results
        def reset_batch(self):
            self.jobs = []
        def shutdown(self):
            pass

    provider = FakeProvider([
        LLMResponse(text=None, tool_uses=[
            ToolCall(id="1", name="register_strategy",
                     input={"name": "ma", "source": "def handle_data(ctx):\n    pass"}),
            ToolCall(id="2", name="run_backtest", input={"strategy_ref": "ma"}),
        ]),
        LLMResponse(text="完成", tool_uses=[]),
    ])
    ex = SyncExecutor()
    agent = LLMAgent(provider=provider, store=store, executor=ex,
                     max_turns=5, max_tools_per_turn=5)
    bus = FakeBus()
    agent.run("s1", "注册 ma 并回测", bus=bus)
    # the tool wrote the draft to the real store in this same turn...
    assert store.get_source("ma") is not None
    # ...and the same turn's run_backtest resolved it by name.
    assert captured.get("resolved") == "ma"


def test_agent_loop_openai_wire_format(monkeypatch):
    """Drive the loop through the real OpenAICompatProvider (mocked SDK) and assert
    the messages the SDK receives are OpenAI-valid: assistant tool_calls with
    function.arguments JSON, and role:'tool' result messages."""
    from api.agent.provider import OpenAICompatProvider

    responses = [
        {"choices": [{"message": {
            "content": None,
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "check_goal", "arguments": '{"metrics": {"annual_return": 0.12}, "constraints": {"annual_return": 0.10}}'},
            }],
        }}]},
        {"choices": [{"message": {"content": "目标达成", "tool_calls": None}}]},
    ]
    captured = []

    class FakeCompletions:
        def create(self, **kwargs):
            captured.append(kwargs)
            return responses.pop(0)

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr("api.agent.provider.OpenAI", FakeClient)
    provider = OpenAICompatProvider(api_key="k", base_url="http://x", model="m1")
    agent = LLMAgent(provider=provider, store=FakeStore(), executor=FakeExecutor(),
                     max_turns=5, max_tools_per_turn=5)
    bus = FakeBus()
    report = agent.run("s1", "做年化10%", goal="年化>=10%", bus=bus)
    assert "目标达成" in report["report"]
    # turn 1: system + initial user message only
    assert captured[0]["messages"][0]["role"] == "system"
    assert captured[0]["messages"][1] == {"role": "user", "content": "做年化10%"}
    # turn 2: the loop has appended assistant tool_calls + tool results
    msgs = captured[1]["messages"]
    assert msgs[0]["role"] == "system"
    assert msgs[1] == {"role": "user", "content": "做年化10%"}
    # assistant: OpenAI function-call wire shape
    asst = msgs[2]
    assert asst["role"] == "assistant"
    assert asst["content"] is None
    assert asst["tool_calls"][0]["id"] == "call_1"
    assert asst["tool_calls"][0]["type"] == "function"
    assert asst["tool_calls"][0]["function"]["name"] == "check_goal"
    assert json.loads(asst["tool_calls"][0]["function"]["arguments"])["constraints"]["annual_return"] == 0.10
    # tool result: role "tool" with matching tool_call_id
    tool_msg = msgs[3]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "call_1"
    assert "met" in tool_msg["content"]


def test_agent_loop_anthropic_wire_format(monkeypatch):
    """Drive the loop through the real AnthropicProvider (mocked SDK) and assert the
    messages the SDK receives are Anthropic-valid: assistant content blocks of type
    tool_use, user content blocks of type tool_result, system at the top level."""
    from api.agent.provider import AnthropicProvider

    responses = [
        SimpleNamespace(content=[
            SimpleNamespace(type="tool_use", id="toolu_1", name="check_goal",
                            input={"metrics": {"annual_return": 0.12},
                                   "constraints": {"annual_return": 0.10}}),
        ]),
        SimpleNamespace(content=[SimpleNamespace(type="text", text="目标达成")]),
    ]
    captured = []

    class FakeMessages:
        def create(self, **kwargs):
            captured.append(kwargs)
            return responses.pop(0)

    class FakeAnthropic:
        def __init__(self, **kwargs):
            self.messages = FakeMessages()

    monkeypatch.setattr("api.agent.provider.anthropic.Anthropic", FakeAnthropic)
    provider = AnthropicProvider(api_key="k", model="claude-test")
    agent = LLMAgent(provider=provider, store=FakeStore(), executor=FakeExecutor(),
                     max_turns=5, max_tools_per_turn=5)
    bus = FakeBus()
    report = agent.run("s1", "做年化10%", goal="年化>=10%", bus=bus)
    assert "目标达成" in report["report"]
    # turn 1: system + initial user message only
    assert captured[0]["system"] == build_system_prompt("年化>=10%")
    assert captured[0]["messages"] == [{"role": "user", "content": "做年化10%"}]
    # turn 2: the loop has appended assistant tool_calls + tool results
    kwargs = captured[1]
    assert kwargs["system"] == build_system_prompt("年化>=10%")
    msgs = kwargs["messages"]
    assert msgs[0] == {"role": "user", "content": "做年化10%"}
    asst = msgs[1]
    assert asst["role"] == "assistant"
    assert asst["content"][0]["type"] == "tool_use"
    assert asst["content"][0]["id"] == "toolu_1"
    assert asst["content"][0]["name"] == "check_goal"
    assert asst["content"][0]["input"]["constraints"]["annual_return"] == 0.10
    usr = msgs[2]
    assert usr["role"] == "user"
    assert usr["content"][0]["type"] == "tool_result"
    assert usr["content"][0]["tool_use_id"] == "toolu_1"
    assert usr["content"][0]["is_error"] is False


def test_provider_error_publishes_error_event_and_returns_report():
    """C2: provider.complete raising must not propagate — LLMAgent.run publishes
    an SSE error event and returns a report containing the message."""
    class BoomProvider:
        def complete(self, *, system, messages, tools, model=None, max_tokens=4096):
            raise RuntimeError("provider boom")

    agent = LLMAgent(provider=BoomProvider(), store=FakeStore(), executor=FakeExecutor(),
                     max_turns=5, max_tools_per_turn=5)
    bus = FakeBus()
    report = agent.run("s1", "hi", bus=bus)
    assert "provider boom" in report["report"]
    assert report.get("error") == "provider boom"
    assert any(ev.get("type") == "error" and "provider boom" in ev.get("error", "") for ev in bus.events)


def test_wait_all_error_publishes_error_event_and_returns_report():
    """C2: executor.wait_all raising must not propagate — publish error event,
    return a report rather than crashing the daemon thread."""
    class BoomExecutor:
        def submit(self, *a, **k):
            return 1
        def wait_all(self, timeout=300):
            raise RuntimeError("executor boom")
        def reset_batch(self):
            pass

    provider = FakeProvider([
        LLMResponse(text=None, tool_uses=[
            ToolCall(id="1", name="run_backtest", input={"strategy_ref": "ma"}),
        ]),
    ])
    agent = LLMAgent(provider=provider, store=FakeStore(), executor=BoomExecutor(),
                     max_turns=5, max_tools_per_turn=5)
    bus = FakeBus()
    report = agent.run("s1", "hi", bus=bus)
    assert "executor boom" in report["report"]
    assert report.get("error") == "executor boom"
    assert any(ev.get("type") == "error" and "executor boom" in ev.get("error", "") for ev in bus.events)


def test_agent_records_tool_calls():
    provider = FakeProvider([
        LLMResponse(text=None, tool_uses=[
            ToolCall(id="1", name="register_strategy",
                     input={"name": "ma", "source": "def handle_data(ctx):\n    pass"}),
            ToolCall(id="2", name="run_backtest", input={"strategy_ref": "ma"}),
        ]),
        LLMResponse(text="完成", tool_uses=[]),
    ])
    chat = FakeChatStore()
    bus = FakeBus()
    agent = LLMAgent(provider=provider, store=FakeStore(), executor=FakeExecutor(),
                     chat_store=chat, max_turns=5, max_tools_per_turn=5)
    agent.run("s1", "做年化10%", message_id=7, bus=bus)
    calls = chat.list_tool_calls("s1")
    assert len(calls) == 2
    assert calls[0]["name"] == "register_strategy"
    assert calls[0]["message_id"] == 7
    assert calls[0]["turn"] == 0
    assert calls[0]["input"]["name"] == "ma"
    assert calls[0]["is_error"] == 0
    assert calls[1]["name"] == "run_backtest"
    # SSE 的 tool 事件携带完整 input，前端可实时展开
    tool_evs = [e for e in bus.events if e.get("type") == "tool"]
    assert tool_evs[0]["name"] == "register_strategy"
    assert tool_evs[0]["input"]["name"] == "ma"


def test_agent_records_tool_error():
    provider = FakeProvider([
        LLMResponse(text=None, tool_uses=[
            ToolCall(id="1", name="publish_strategy", input={"name": "ma", "goal_met": False}),
        ]),
        LLMResponse(text="完成", tool_uses=[]),
    ])
    chat = FakeChatStore()
    agent = LLMAgent(provider=provider, store=FakeStore(), executor=FakeExecutor(),
                     chat_store=chat, max_turns=5, max_tools_per_turn=5)
    agent.run("s1", "做年化10%", message_id=7, bus=FakeBus())
    calls = chat.list_tool_calls("s1")
    assert len(calls) == 1
    assert calls[0]["name"] == "publish_strategy"
    assert calls[0]["is_error"] == 1
    assert "goal not met" in calls[0]["output"]


def test_agent_links_strategy_to_session(tmp_path):
    from api.agent.store import StrategyStore
    store = StrategyStore(db_path=str(tmp_path / "t.db"))
    chat = FakeChatStore()
    provider = FakeProvider([
        LLMResponse(text=None, tool_uses=[
            ToolCall(id="1", name="register_strategy",
                     input={"name": "ma", "source": "def handle_data(ctx):\n    pass"}),
        ]),
        LLMResponse(text="完成", tool_uses=[]),
    ])
    agent = LLMAgent(provider=provider, store=store, executor=FakeExecutor(),
                     chat_store=chat, max_turns=5, max_tools_per_turn=5)
    agent.run("s1", "注册 ma", message_id=3, bus=FakeBus())
    linked = store.list_session_strategies("s1")
    assert len(linked) == 1
    assert linked[0]["name"] == "ma"
    assert linked[0]["version"] == 1


def test_system_prompt_training_only_and_validation_hidden():
    goal = {"constraints": {"annual_return": 0.10},
            "period": {"start": "2020-01-01", "end": "2024-12-31"},
            "validation_periods": [{"start": "2025-01-01", "end": "2025-12-31"}],
            "universe": ["沪深300"]}
    prompt = build_system_prompt(goal)
    assert "训练段" in prompt
    assert "验证段" in prompt
    assert "2025-01-01" not in prompt  # 验证段日期对 LLM 隐藏


def test_agent_run_sets_ctx_goal_and_training_period():
    from api.agent.tools import AgentToolContext
    from api.agent.agent import LLMAgent

    provider = FakeProvider([])
    agent = LLMAgent(provider=provider, store=FakeStore(), executor=FakeExecutor())
    goal = {"constraints": {"annual_return": 0.10},
            "period": {"start": "2020-01-01", "end": "2024-12-31"},
            "validation_periods": [{"start": "2025-01-01", "end": "2025-12-31"}]}
    agent.run("s1", "在沪深300做到年化10%", goal=goal)
    assert agent._ctx.goal == goal
    assert agent._ctx.training_period == {"start": "2020-01-01", "end": "2024-12-31"}


def test_validation_dates_never_reach_llm_context():
    from api.agent.agent import LLMAgent
    from api.agent.provider import ToolCall

    goal = {"constraints": {"annual_return": 0.10},
            "period": {"start": "2020-01-01", "end": "2024-12-31"},
            "validation_periods": [{"start": "2025-01-01", "end": "2025-12-31"}]}
    # turn 1: run_backtest; turn 2: publish (no more tools)
    provider = FakeProvider([
        LLMResponse(text="先回测", tool_uses=[ToolCall(id="c1", name="run_backtest",
                                                      input={"strategy_ref": "ma"})]),
        LLMResponse(text="完成", tool_uses=[]),
    ])
    agent = LLMAgent(provider=provider, store=FakeStore(), executor=FakeExecutor())
    agent.run("s1", "在沪深300做到年化10%", goal=goal)
    for call in provider.calls:
        for m in call["messages"]:
            content = json.dumps(m, ensure_ascii=False)
            assert "2025-01-01" not in content, content
            assert "2025-12-31" not in content, content
