"""Agent tools tests (fake ctx, no network)."""
from __future__ import annotations

import json

import pytest

from api.agent.tools import (
    AgentToolContext,
    TOOLS,
    list_symbols,
    register_strategy,
    check_goal,
    publish_strategy,
)


class FakeExecutor:
    def __init__(self):
        self.submitted = []
    def submit(self, *a, **k):
        self.submitted.append((a, k))
        return 1


def _ctx(tmp_path):
    from api.agent.store import StrategyStore
    store = StrategyStore(db_path=str(tmp_path / "t.db"))
    return AgentToolContext(store=store, executor=FakeExecutor())


def test_tools_schema_complete():
    names = {t["name"] for t in TOOLS}
    assert {"list_symbols", "run_backtest", "register_strategy",
            "list_strategies", "publish_strategy", "check_goal"} <= names


def test_register_strategy_is_draft(tmp_path):
    c = _ctx(tmp_path)
    out = json.loads(register_strategy(
        {"name": "ma", "source": "def strategy(ctx, params):\n    pass", "description": "d"},
        c,
    ))
    assert out["status"] == "draft"
    assert out["version"] == 1
    # invalid source rejected via AST sandbox
    with pytest.raises(Exception):
        register_strategy({"name": "bad", "source": "import os\ndef strategy(ctx,p): pass"}, c)


def test_check_goal_met():
    out = json.loads(check_goal(
        {"metrics": {"annual_return": 0.12, "max_drawdown": -0.10},
         "constraints": {"annual_return": 0.10, "max_drawdown": -0.15}},
        None,
    ))
    assert out["met"] is True


def test_check_goal_not_met():
    out = json.loads(check_goal(
        {"metrics": {"annual_return": 0.05, "max_drawdown": -0.10},
         "constraints": {"annual_return": 0.10, "max_drawdown": -0.15}},
        None,
    ))
    assert out["met"] is False


def test_check_goal_drawdown_exceeds_limit():
    # I3: max_drawdown compared by magnitude — |val| > |threshold| means NOT met.
    out = json.loads(check_goal(
        {"metrics": {"max_drawdown": -0.20},
         "constraints": {"max_drawdown": -0.15}},
        None,
    ))
    assert out["met"] is False
    assert any("max_drawdown" in u for u in out["unmet"])


def test_check_goal_drawdown_within_limit():
    # I3: |val| <= |threshold| means met.
    out = json.loads(check_goal(
        {"metrics": {"max_drawdown": -0.10},
         "constraints": {"max_drawdown": -0.15}},
        None,
    ))
    assert out["met"] is True


def test_check_goal_drawdown_positive_threshold_deterministic():
    # I3: a positive threshold (e.g. 0.15) means the same limit as -0.15:
    # met iff |max_drawdown| <= 0.15.
    met = json.loads(check_goal(
        {"metrics": {"max_drawdown": -0.10},
         "constraints": {"max_drawdown": 0.15}},
        None,
    ))
    assert met["met"] is True
    not_met = json.loads(check_goal(
        {"metrics": {"max_drawdown": -0.20},
         "constraints": {"max_drawdown": 0.15}},
        None,
    ))
    assert not_met["met"] is False


def test_publish_requires_goal_met(tmp_path):
    c = _ctx(tmp_path)
    register_strategy({"name": "ma", "source": "def strategy(ctx, p):\n    pass"}, c)
    with pytest.raises(Exception):
        publish_strategy({"name": "ma", "goal_met": False}, c)
    out = json.loads(publish_strategy(
        {"name": "ma", "goal_met": True, "metrics": {"annual_return": 0.12}, "goal": "年化>=10%"},
        c,
    ))
    assert out["status"] == "published"
