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
