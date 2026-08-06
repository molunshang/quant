"""Goal gate tests (pure logic + state machine, fake provider, no network)."""
from __future__ import annotations

from api.agent.store import AgentSessionStore


def test_agent_session_store_roundtrip(tmp_path):
    store = AgentSessionStore(str(tmp_path / "t.db"))
    assert store.get("s1") is None  # idle = no row

    store.set("s1", "pending_confirm", goal_json='{"universe": ["沪深300"]}')
    row = store.get("s1")
    assert row["status"] == "pending_confirm"
    assert "沪深300" in row["goal_json"]

    # set without goal_json must preserve the stored value
    store.set("s1", "running")
    row = store.get("s1")
    assert row["status"] == "running"
    assert "沪深300" in row["goal_json"]  # preserved


def test_agent_session_store_status_transition(tmp_path):
    store = AgentSessionStore(str(tmp_path / "t.db"))
    store.set("s1", "pending_clarify", questions_json='["问题A"]')
    store.set("s1", "pending_confirm", confirm_summary_json='{"universe": ["沪深300"]}')
    row = store.get("s1")
    assert row["status"] == "pending_confirm"
    assert row["questions_json"] == '["问题A"]'  # preserved — set() only updates passed fields
    assert "沪深300" in row["confirm_summary_json"]


import json

from api.agent.gate import (
    DEFAULT_BENCHMARK, DEFAULT_PERIOD, GoalExtraction,
    build_confirmation_summary, build_questions, format_confirmation_text,
    format_goal_text, gate_step, is_confirmed, missing_fields,
)


def test_missing_fields_complete_goal():
    ex = GoalExtraction(universe=["沪深300"], constraints={"annual_return": 0.10})
    assert missing_fields(ex) == []


def test_missing_fields_partial_goal():
    ex = GoalExtraction(universe=["沪深300"], constraints=None)
    assert missing_fields(ex) == ["constraints"]
    ex2 = GoalExtraction(universe=None, constraints={"annual_return": 0.10})
    assert missing_fields(ex2) == ["universe"]


def test_missing_fields_does_not_flag_period_benchmark():
    # period/benchmark 有默认值，缺失不触发澄清
    ex = GoalExtraction(universe=["沪深300"], constraints={"annual_return": 0.10})
    assert missing_fields(ex) == []


def test_build_questions_for_missing_constraints():
    ex = GoalExtraction(universe=["沪深300"], constraints=None)
    qs = build_questions(["constraints"], ex)
    assert len(qs) == 1
    assert "量化目标" in qs[0]


def test_build_questions_includes_followup():
    ex = GoalExtraction(universe=["沪深300"], constraints={"annual_return": 0.10},
                        followup_question="基准是相对沪深300还是绝对收益？")
    qs = build_questions([], ex)
    assert qs == ["基准是相对沪深300还是绝对收益？"]


def test_confirmation_summary_defaults():
    ex = GoalExtraction(universe=["沪深300"], constraints={"annual_return": 0.10})
    s = build_confirmation_summary(ex)
    assert s["period"] == DEFAULT_PERIOD
    assert s["benchmark"] == DEFAULT_BENCHMARK
    assert s["defaults_noted"] == {"period": True, "benchmark": True}


def test_confirmation_summary_explicit_values():
    ex = GoalExtraction(universe=["沪深300"], constraints={"annual_return": 0.10},
                        period={"start": "2018-01-01", "end": "2022-12-31"},
                        benchmark="中证500 超额收益")
    s = build_confirmation_summary(ex)
    assert s["period"]["start"] == "2018-01-01"
    assert s["benchmark"] == "中证500 超额收益"
    assert s["defaults_noted"] == {"period": False, "benchmark": False}


def test_confirmation_summary_marks_default_period():
    s = build_confirmation_summary(GoalExtraction(universe=["x"], constraints={}))
    assert s["period"] == DEFAULT_PERIOD
    assert s["defaults_noted"]["period"] is True


def test_is_confirmed_words():
    for w in ("确认", "没问题", "可以", "开始", "好", "OK", "对", "是的", "就这样", "同意", "行", "开始吧", "确认了"):
        assert is_confirmed(w), w


def test_is_confirmed_rejects_modification():
    for w in ("回撤改成20%", "标的换成白酒", "时间区间改为2019年", ""):
        assert not is_confirmed(w), w


def test_format_confirmation_text_contains_default_marks():
    s = build_confirmation_summary(GoalExtraction(universe=["沪深300"], constraints={"annual_return": 0.10}))
    text = format_confirmation_text(s)
    assert "沪深300" in text
    assert "2020-01-01" in text
    assert "默认，可修改" in text
    assert "确认" in text


def test_format_goal_text():
    text = format_goal_text({"universe": ["沪深300"],
                             "constraints": {"annual_return": 0.10},
                             "period": {"start": "2020-01-01", "end": "2024-12-31"},
                             "benchmark": "沪深300 绝对收益"})
    assert "沪深300" in text
    assert "annual_return" in text
    assert "2020-01-01" in text


def test_gate_step_complete_goal_confirms():
    ex = GoalExtraction(universe=["沪深300"], constraints={"annual_return": 0.10})
    name, payload = gate_step(ex)
    assert name == "confirm"
    assert "universe" in payload


def test_gate_step_missing_goal_clarifies():
    ex = GoalExtraction(universe=["沪深300"], constraints=None)
    name, payload = gate_step(ex)
    assert name == "clarify"
    assert payload  # non-empty questions


def test_gate_step_followup_clarifies_even_when_fields_ok():
    ex = GoalExtraction(universe=["沪深300"], constraints={"annual_return": 0.10},
                        followup_question="跑赢大盘指哪个基准？")
    name, payload = gate_step(ex)
    assert name == "clarify"
    assert "跑赢大盘" in payload[0]


from api.agent.gate import gate_extract


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


def test_gate_extract_parses_full_goal():
    from api.agent.provider import LLMResponse
    provider = FakeGateProvider([LLMResponse(
        text='{"universe": ["沪深300"], "constraints": {"annual_return": 0.10, "max_drawdown": -0.15},'
             ' "period": {"start": "2020-01-01", "end": "2024-12-31"}, "benchmark": "沪深300 绝对收益"}',
        tool_uses=[])])
    ex = gate_extract("在沪深300做到年化10%回撤15%", [], provider)
    assert ex.universe == ["沪深300"]
    assert ex.constraints["annual_return"] == 0.10
    assert ex.constraints["max_drawdown"] == -0.15
    assert ex.period == {"start": "2020-01-01", "end": "2024-12-31"}
    assert ex.benchmark == "沪深300 绝对收益"
    assert provider.calls[0]["tools"] == []  # gate is a plain-text LLM call


def test_gate_extract_handles_fenced_json():
    from api.agent.provider import LLMResponse
    provider = FakeGateProvider([LLMResponse(
        text='```json\n{"universe": ["510300"]}\n```', tool_uses=[])])
    ex = gate_extract("做510300", [], provider)
    assert ex.universe == ["510300"]


def test_gate_extract_malformed_json_degrades_to_empty():
    from api.agent.provider import LLMResponse
    provider = FakeGateProvider([LLMResponse(text="抱歉我不懂", tool_uses=[])])
    ex = gate_extract("随便", [], provider)
    assert ex.universe is None
    assert ex.constraints is None


def test_gate_extract_string_percent_coerced():
    from api.agent.provider import LLMResponse
    # LLM 输出字符串百分比时兜底转小数；回撤取负值（spec 全局约束）
    provider = FakeGateProvider([LLMResponse(
        text='{"constraints": {"annual_return": "10%", "max_drawdown": "15%"}}', tool_uses=[])])
    ex = gate_extract("年化10%回撤15%", [], provider)
    assert ex.constraints["annual_return"] == 0.10
    assert ex.constraints["max_drawdown"] == -0.15


def test_gate_extract_merges_goal_and_history():
    from api.agent.provider import LLMResponse
    provider = FakeGateProvider([LLMResponse(text='{"universe": ["沪深300"]}', tool_uses=[])])
    gate_extract("在沪深300成分内", ["之前说年化10%"], provider, goal="额外目标")
    content = provider.calls[0]["messages"][0]["content"]
    assert "额外目标" in content
    assert "之前说年化10%" in content
    assert "在沪深300成分内" in content


from api.agent.api import handle_chat
from api.agent.provider import LLMResponse
from api.agent.store import AgentSessionStore, ChatStore


class FakeBus:
    def __init__(self):
        self.events = []
    def publish(self, session_id, event):
        self.events.append(event)


class FakeStore:
    """Minimal store for LLMAgent (report path only, no tools used)."""
    def list_strategies(self, include_drafts=True):
        return []
    def get_strategy(self, name):
        return None
    def get_source(self, name, version=None):
        return None


class FakeExecutor:
    def submit(self, *a, **k):
        return 1
    def wait_all(self, timeout=300):
        return []
    def reset_batch(self):
        pass


def _chat(tmp_path):
    return ChatStore(str(tmp_path / "chat.db"))


def _sess(tmp_path):
    return AgentSessionStore(str(tmp_path / "sess.db"))


def _events_of_type(bus, typ):
    return [e for e in bus.events if e.get("type") == typ]


def test_handle_chat_complete_goal_confirms(tmp_path):
    provider = FakeGateProvider([LLMResponse(text='{"universe": ["沪深300"], "constraints": {"annual_return": 0.10}}', tool_uses=[])])
    bus = FakeBus()
    out = handle_chat("s1", "在沪深300做到年化10%", None, provider, bus,
                      _sess(tmp_path), _chat(tmp_path), FakeStore(), FakeExecutor())
    assert out["outcome"] == "confirm"
    assert _events_of_type(bus, "confirm")
    assert not _events_of_type(bus, "clarify")


def test_handle_chat_missing_constraints_clarifies(tmp_path):
    provider = FakeGateProvider([LLMResponse(text='{"universe": ["沪深300"]}', tool_uses=[])])
    bus = FakeBus()
    out = handle_chat("s1", "在沪深300选个好策略", None, provider, bus,
                      _sess(tmp_path), _chat(tmp_path), FakeStore(), FakeExecutor())
    assert out["outcome"] == "clarify"
    ev = _events_of_type(bus, "clarify")[0]
    assert any("量化目标" in q for q in ev["questions"])


def test_handle_chat_answer_then_confirm(tmp_path):
    sess, chat = _sess(tmp_path), _chat(tmp_path)
    # handle_chat 不写 chat_store（生产环境由端点写入）；测试手动模拟
    chat.add_message("s1", "user", "在沪深300")
    # 第一次：只给了标的 -> clarify
    p1 = FakeGateProvider([LLMResponse(text='{"universe": ["沪深300"]}', tool_uses=[])])
    handle_chat("s1", "在沪深300", None, p1, FakeBus(), sess, chat, FakeStore(), FakeExecutor())
    assert sess.get("s1")["status"] == "pending_clarify"
    # 第二次：补充约束 -> confirm（history 应含上一条"在沪深300"）
    chat.add_message("s1", "user", "年化10%")
    p2 = FakeGateProvider([LLMResponse(text='{"universe": ["沪深300"], "constraints": {"annual_return": 0.10}}', tool_uses=[])])
    bus2 = FakeBus()
    out = handle_chat("s1", "年化10%", None, p2, bus2, sess, chat, FakeStore(), FakeExecutor())
    assert out["outcome"] == "confirm"
    assert sess.get("s1")["status"] == "pending_confirm"
    # 提取调用应携带历史（message 不重复）
    content = p2.calls[0]["messages"][0]["content"]
    assert content.count("在沪深300") == 1
    assert "年化10%" in content


def test_handle_chat_confirm_runs_agent(tmp_path):
    sess, chat = _sess(tmp_path), _chat(tmp_path)
    sess.set("s1", "pending_confirm",
             goal_json='{"universe": ["沪深300"], "constraints": {"annual_return": 0.10}}')
    provider = FakeGateProvider([LLMResponse(text="目标达成，已发布", tool_uses=[])])
    bus = FakeBus()
    out = handle_chat("s1", "确认", None, provider, bus, sess, chat, FakeStore(), FakeExecutor())
    assert out["outcome"] == "running"
    assert _events_of_type(bus, "running")
    assert sess.get("s1")["status"] == "done"
    assert chat.list_messages("s1")[-1]["content"] == "目标达成，已发布"


def test_handle_chat_confirm_modification_reclarifies(tmp_path):
    sess, chat = _sess(tmp_path), _chat(tmp_path)
    sess.set("s1", "pending_confirm",
             goal_json='{"universe": ["沪深300"], "constraints": {"annual_return": 0.10}}')
    # 用户说"回撤改成20%" -> 视为修改意见，重新提取
    provider = FakeGateProvider([LLMResponse(text='{"universe": ["沪深300"], "constraints": {"annual_return": 0.10, "max_drawdown": -0.20}}', tool_uses=[])])
    bus = FakeBus()
    out = handle_chat("s1", "回撤改成20%", None, provider, bus, sess, chat, FakeStore(), FakeExecutor())
    assert out["outcome"] == "confirm"
    assert not _events_of_type(bus, "running")


def test_handle_chat_rejects_when_running(tmp_path):
    sess, chat = _sess(tmp_path), _chat(tmp_path)
    sess.set("s1", "running")
    bus = FakeBus()
    out = handle_chat("s1", "再来一个", None, FakeGateProvider([]), bus, sess, chat, FakeStore(), FakeExecutor())
    assert out["outcome"] == "error"
    assert out["reason"] == "running"
    assert any("正在运行中" in e.get("error", "") for e in _events_of_type(bus, "error"))
