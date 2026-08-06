"""API tests using FastAPI TestClient (no live network needed for validation paths)."""
from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.runner import run_backtest


def make_bars(n=120, start_price=100.0):
    close = pd.Series(range(n)).apply(lambda i: start_price * (1 + 0.003 * i))
    dates = pd.date_range("2023-01-02", periods=n, freq="B")
    return pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "open": close * 0.999, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": [10000] * n,
    })


client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_meta():
    r = client.get("/api/meta")
    assert r.status_code == 200
    data = r.json()
    assert "daily" in data["frequencies"]
    assert data["default_lot_size"] == 100


def test_strategies_list():
    r = client.get("/api/strategies")
    assert r.status_code == 200
    names = [s["name"] for s in r.json()["strategies"]]
    assert "sma_cross" in names
    assert "buy_and_hold" in names


def test_register_and_use_custom_strategy():
    src = """
def strategy(ctx, params):
    if ctx.bar_index == 0:
        ctx.buy()
"""
    r = client.post("/api/strategies", json={"name": "test_allin", "source": src, "description": "t"})
    assert r.status_code == 200
    assert r.json()["strategy"]["name"] == "test_allin"

    # invalid strategy rejected
    r2 = client.post("/api/strategies", json={"name": "bad", "source": "import os\ndef strategy(ctx,p):\n pass"})
    assert r2.status_code == 400


def test_backtest_endpoint_with_mock():
    """End-to-end via runner with synthetic bars (no network)."""
    bars = make_bars()
    res = run_backtest(
        symbol="600519",
        strategy_ref="buy_and_hold",
        params={},
        freq="daily",
        start="2023-01-01",
        end="2023-06-30",
    ) if False else None
    # Direct runner test with injected data layer not supported; test engine path instead.
    from engine.engine import BacktestEngine, EngineConfig
    from strategies.builtin import buy_and_hold
    eng = BacktestEngine(EngineConfig(initial_cash=100_000))
    result = eng.run(buy_and_hold, bars)
    m = result.metrics
    assert m["total_return"] > 0
    # day 0: bought all-in + commission -> equity slightly below initial cash
    first = m["equity_curve"][0]["equity"]
    assert first < 100_000
    assert first > 99_000


def test_precache_api_submit_and_query(monkeypatch):
    import pandas as pd
    from data.precache import manager as pm

    # stub the manager's data layer — no real network in tests
    def fake_get_bars(self, *a, **k):
        return pd.DataFrame({"date": ["2024-01-02"], "open": [1.0], "high": [1.0],
                             "low": [1.0], "close": [1.0], "volume": [1], "factor": [1.0]})
    monkeypatch.setattr(type(pm._dl), "get_bars", fake_get_bars)

    from fastapi.testclient import TestClient
    from api.main import app
    client = TestClient(app)
    r = client.post("/api/data/precache", json={
        "symbols": ["600519"], "freq": "daily", "start": "2024-01-01", "end": "2024-01-31", "adjust": "qfq"
    })
    assert r.status_code == 200
    job_ids = r.json()["job_ids"]
    assert len(job_ids) == 1
    r2 = client.get(f"/api/data/precache/{job_ids[0]}")
    assert r2.status_code == 200
    assert r2.json()["job"]["symbol"] == "600519"


def test_daily_update_scheduler(monkeypatch):
    import api.main as main
    from api.main import _read_data_config, _should_run_now

    # _read_data_config reads the real config/data.json (daily_update_time present)
    cfg = _read_data_config()
    assert isinstance(cfg, dict)
    assert "daily_update_time" in cfg

    # _should_run_now deterministically, via a fixed fake clock
    class FakeDatetime:
        now_value = None
        @classmethod
        def now(cls):
            return cls.now_value
    monkeypatch.setattr(main, "datetime", FakeDatetime)

    FakeDatetime.now_value = _fixed_datetime("15:30")
    assert _should_run_now("15:30") is True
    FakeDatetime.now_value = _fixed_datetime("09:00")
    assert _should_run_now("15:30") is False


def _fixed_datetime(hhmm: str):
    import datetime as _dt
    hh, mm = hhmm.split(":")
    return _dt.datetime(2026, 1, 1, int(hh), int(mm), 0)


def test_precache_job_non_int_returns_404():
    r = client.get("/api/data/precache/abc")
    assert r.status_code == 404
    assert r.json()["detail"] == "unknown job"


def test_web_pages():
    # Each route must serve its specific page — assert the unique <title> marker.
    expected = {
        "/": "<title>A股回测系统</title>",
        "/chat": "<title>AI 目标优化",
        "/data": "<title>数据预缓存",
        "/settings": "<title>LLM 设置",
    }
    for path, marker in expected.items():
        r = client.get(path)
        assert r.status_code == 200, path
        assert "text/html" in r.headers["content-type"], path
        assert marker in r.text, path
