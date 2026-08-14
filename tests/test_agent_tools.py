"""Agent tools tests (fake ctx, no network)."""
from __future__ import annotations

import json

import pytest

from api.agent.tools import (
    AgentToolContext,
    TOOLS,
    check_goal,
    diagnose_backtest,
    list_industries,
    list_symbols,
    publish_strategy,
    query_sector_perf,
    register_strategy,
    run_backtest,
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
        {"name": "ma", "source": "def handle_data(ctx):\n    pass", "description": "d"},
        c,
    ))
    assert out["status"] == "draft"
    assert out["version"] == 1
    # invalid source rejected via AST sandbox
    with pytest.raises(Exception):
        register_strategy({"name": "bad", "source": "import os\ndef handle_data(ctx): pass"}, c)


def test_register_strategy_requires_handle_data(tmp_path):
    # the strategy interface is now handle_data(ctx); an old-style
    # strategy(ctx, params) draft must be rejected at registration.
    c = _ctx(tmp_path)
    with pytest.raises(Exception):
        register_strategy({"name": "old", "source": "def strategy(ctx, p):\n    pass"}, c)


def test_run_backtest_forwards_universe_no_symbol(tmp_path):
    c = _ctx(tmp_path)
    out = json.loads(run_backtest(
        {"strategy_ref": "ma", "universe": {"symbols": ["600519"]}}, c,
    ))
    assert out["status"] == "running"
    assert out["strategy"] == "ma"
    assert len(c.executor.submitted) == 1
    _args, kwargs = c.executor.submitted[0]
    assert kwargs["strategy_ref"] == "ma"
    assert kwargs["universe"] == {"symbols": ["600519"]}
    assert "symbol" not in kwargs
    assert "params" not in kwargs


def test_run_backtest_defaults_strategy_and_universe(tmp_path):
    c = _ctx(tmp_path)
    run_backtest({}, c)
    _args, kwargs = c.executor.submitted[0]
    assert kwargs["strategy_ref"] == "buy_and_hold"
    assert kwargs["universe"] is None


def test_run_backtest_tool_schema_no_symbol():
    t = next(x for x in TOOLS if x["name"] == "run_backtest")
    props = t["parameters"]["properties"]
    assert "symbol" not in props
    assert "params" not in props
    assert "universe" in props
    assert "symbol" not in t["parameters"].get("required", [])


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
    register_strategy({"name": "ma", "source": "def handle_data(ctx):\n    pass"}, c)
    with pytest.raises(Exception):
        publish_strategy({"name": "ma", "goal_met": False}, c)
    out = json.loads(publish_strategy(
        {"name": "ma", "goal_met": True, "metrics": {"annual_return": 0.12}, "goal": "年化>=10%"},
        c,
    ))
    assert out["status"] == "published"


def test_run_backtest_rejects_outside_training_period(tmp_path):
    c = _ctx(tmp_path)
    c.training_period = {"start": "2020-01-01", "end": "2024-12-31"}
    with pytest.raises(Exception):
        run_backtest({"strategy_ref": "ma", "start": "2025-01-01", "end": "2025-12-31"}, c)
    # 训练段内正常提交
    run_backtest({"strategy_ref": "ma", "start": "2021-01-01", "end": "2023-12-31"}, c)
    assert len(c.executor.submitted) == 1


def test_list_symbols_with_index(monkeypatch, tmp_path):
    import api.agent.tools as T
    monkeypatch.setattr(T, "index_constituents", lambda code: [
        {"code": "600519", "name": "贵州茅台", "weight": 5.0},
        {"code": "000001", "name": "平安银行", "weight": 3.0},
    ])
    c = _ctx(tmp_path)
    out = json.loads(T.list_symbols({"index": "000300"}, c))
    assert [s["code"] for s in out["symbols"]] == ["600519", "000001"]
    assert out["symbols"][0]["name"] == "贵州茅台"


def test_list_industries_tool(monkeypatch, tmp_path):
    import api.agent.tools as T
    monkeypatch.setattr(T, "list_sw_industries", lambda: [
        {"code": "801080.SI", "name": "电子", "n_stocks": 495},
    ])
    c = _ctx(tmp_path)
    out = json.loads(T.list_industries({}, c))
    assert out["industries"][0]["name"] == "电子"


def test_query_sector_perf(monkeypatch, tmp_path):
    import akshare as ak
    import pandas as pd
    df = pd.DataFrame({
        "date": ["2026-06-01", "2026-08-01"],
        "open": [1.0, 1.1], "high": [1.1, 1.2], "low": [0.9, 1.0],
        "close": [100.0, 110.0], "volume": [1, 1],
    })
    monkeypatch.setattr(ak, "stock_zh_index_daily", lambda symbol: df)
    c = _ctx(tmp_path)
    out = json.loads(query_sector_perf({"code": "000300", "days": 60}, c))
    assert out["return_pct"] == pytest.approx(0.10)
    assert out["start"] == "2026-06-01"


def test_query_sector_perf_capped_at_training_end(monkeypatch, tmp_path):
    import akshare as ak
    import pandas as pd
    # rows after the training end (2025/2026) must never reach the agent
    df = pd.DataFrame({
        "date": ["2024-11-01", "2024-12-31", "2025-01-01", "2025-06-01", "2026-01-01"],
        "open": [1.0] * 5, "high": [1.1] * 5, "low": [0.9] * 5,
        "close": [100.0, 110.0, 121.0, 133.1, 146.41], "volume": [1] * 5,
    })
    monkeypatch.setattr(ak, "stock_zh_index_daily", lambda symbol: df)
    c = _ctx(tmp_path)
    c.training_period = {"start": "2020-01-01", "end": "2024-12-31"}
    out = json.loads(query_sector_perf({"code": "000300", "days": 60}, c))
    assert out["end"] <= "2024-12-31"
    assert out["start"] == "2024-11-01"
    # return computed only over rows up to training end: 110/100 - 1
    assert out["return_pct"] == pytest.approx(0.10)


def test_diagnose_backtest_tool(tmp_path):
    class Ex:
        def __init__(self):
            self.jobs = {7: {"job_id": 7, "result": {
                "equity_curve": [{"date": "2025-01-02", "equity": 100.0, "n_positions": 1,
                                  "max_concentration": 1.0},
                                 {"date": "2025-01-03", "equity": 110.0, "n_positions": 1,
                                  "max_concentration": 1.0}],
                "trades": [],
            }}}
        def get_job(self, job_id):
            return self.jobs.get(job_id)

    c = AgentToolContext(store=None, executor=Ex())
    out = json.loads(diagnose_backtest({"job_id": 7}, c))
    assert "monthly_returns" in out
    assert "drawdown_analysis" in out
    assert "symbol_attribution" in out


def test_publish_validation_gate_rejects(tmp_path):
    c = _ctx(tmp_path)
    register_strategy({"name": "ma", "source": "def handle_data(ctx):\n    pass"}, c)
    c.goal = {"constraints": {"annual_return": 0.10},
              "validation_periods": [{"start": "2025-01-01", "end": "2025-12-31"}]}
    c.validation_runner = lambda period, universe: {"annual_return": 0.03}
    with pytest.raises(Exception) as e:
        publish_strategy({"name": "ma", "goal_met": True}, c)
    assert "验证段" in str(e.value)
    assert "2025-01-01" not in str(e.value)


def test_publish_validation_gate_passes(tmp_path):
    c = _ctx(tmp_path)
    register_strategy({"name": "ma", "source": "def handle_data(ctx):\n    pass"}, c)
    c.goal = {"constraints": {"annual_return": 0.10},
              "validation_periods": [{"start": "2025-01-01", "end": "2025-12-31"}]}
    c.validation_runner = lambda period, universe: {"annual_return": 0.12}
    out = json.loads(publish_strategy(
        {"name": "ma", "goal_met": True, "metrics": {"annual_return": 0.12}}, c))
    assert out["status"] == "published"
    assert out["metrics"]["validation_metrics"][0]["period"]["start"] == "2025-01-01"


def test_validate_strategy_on_periods_pure():
    from api.agent.tools import validate_strategy_on_periods
    vp = [{"start": "2025-01-01", "end": "2025-12-31"}]
    vm, fail = validate_strategy_on_periods(
        "ma", "def handle_data(ctx): pass", {"annual_return": 0.10}, vp,
        runner=lambda period, universe: {"annual_return": 0.05})
    assert fail == [{"period": vp[0], "unmet": ["annual_return: 0.05 < 0.1"]}]
    assert vm[0]["metrics"]["annual_return"] == 0.05
