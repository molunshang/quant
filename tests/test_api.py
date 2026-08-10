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


class _FakeDataLayer:
    """Synthetic bars for any symbol — no network. Mirrors engine test fakes."""
    def symbol_info(self, symbol):
        from data.sources import SymbolInfo
        return SymbolInfo(symbol, symbol, "stock", "sh")
    def get_bars(self, info, freq="daily", start="", end="", adjust="qfq"):
        return make_bars()


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
    assert "buy_and_hold" in names
    assert "momentum_rotation" in names


def test_register_and_use_custom_strategy():
    src = """
def handle_data(ctx):
    if ctx.bar_index == 0:
        for s in ctx.universe:
            ctx.buy(s, 1.0 / len(ctx.universe))
"""
    r = client.post("/api/strategies", json={"name": "test_allin", "source": src, "description": "t"})
    assert r.status_code == 200
    assert r.json()["strategy"]["name"] == "test_allin"

    # invalid strategy rejected
    r2 = client.post("/api/strategies", json={"name": "bad", "source": "import os\ndef strategy(ctx,p):\n pass"})
    assert r2.status_code == 400


def test_backtest_runner_universe_no_symbol(tmp_path, monkeypatch):
    """Portfolio runner: universe spec instead of a single symbol, no params."""
    import pandas as pd
    from api.runner import run_backtest
    from strategies.builtin import buy_and_hold
    from engine.universe import metadata_calendar

    bars = make_bars()
    class _DL:
        def symbol_info(self, symbol):
            from data.sources import SymbolInfo
            return SymbolInfo(symbol, symbol, "stock", "sh")
        def get_bars(self, info, freq="daily", start="", end="", adjust="qfq"):
            return make_bars()
    # isolate from the real cache dir so the calendar comes from the fake DL
    monkeypatch.setattr("engine.universe.CACHE_DIR", str(tmp_path))
    res = run_backtest(
        strategy=buy_and_hold,
        universe={"symbols": ["600519"]},
        freq="daily", start="2023-01-01", end="2024-12-31",
        data_layer=_DL(),
    )
    assert res["success"] is True
    assert "total_return" in res["metrics"]
    assert res["symbol"] == "600519"


def test_backtest_endpoint_with_mock(tmp_path, monkeypatch):
    """End-to-end via runner with synthetic bars (no network)."""
    from api.runner import run_backtest
    from strategies.builtin import buy_and_hold
    monkeypatch.setattr("engine.universe.CACHE_DIR", str(tmp_path))
    res = run_backtest(
        strategy=buy_and_hold,
        universe={"symbols": ["600519"]},
        freq="daily", start="2023-01-01", end="2023-06-30",
        data_layer=_FakeDataLayer(),
    )
    m = res["metrics"]
    assert res["success"] is True
    assert m["total_return"] > 0
    # day 0: bought all-in + commission -> equity slightly below initial cash
    first = res["equity_curve"][0]["equity"]
    assert first < 100_000
    assert first > 99_000


def test_bars_endpoint(monkeypatch):
    """GET /api/bars/{symbol} returns OHLCV bars (fake data layer, no network)."""
    import data.sources as ds
    def fake_get_bars(self, info, freq="daily", start="", end="", adjust="qfq"):
        return make_bars()
    monkeypatch.setattr(ds.DataLayer, "get_bars", fake_get_bars)
    r = client.get("/api/bars/600519")
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "600519"
    assert len(body["bars"]) > 0
    first = body["bars"][0]
    assert {"date", "open", "high", "low", "close", "volume"} <= set(first)


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
        "/": ["<title>A股回测系统</title>"],
        "/chat": ["<title>AI 目标优化", 'id="chatHistory"', 'id="chatDetail"'],
        "/data": ["<title>数据预缓存"],
        "/settings": ["<title>LLM 设置"],
    }
    for path, markers in expected.items():
        r = client.get(path)
        assert r.status_code == 200, path
        assert "text/html" in r.headers["content-type"], path
        for marker in markers:
            assert marker in r.text, (path, marker)
