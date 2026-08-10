"""BacktestExecutor tests (synthetic bars, no network)."""
from __future__ import annotations

import pandas as pd
import pytest

from api.agent.executor import BacktestExecutor


def make_bars(n=60, start_price=100.0):
    close = pd.Series(range(n)).apply(lambda i: start_price * (1 + 0.005 * i))
    dates = pd.date_range("2023-01-02", periods=n, freq="B")
    return pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "open": close * 0.999, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": [10000] * n,
    })


def _dummy_backtest(strategy, universe=None, freq="daily", start="", end="",
                    adjust="qfq", initial_cash=100_000.0, strategy_manager=None, data_layer=None):
    from api.runner import run_backtest as real
    # Reuse real run_backtest but with a fake data_layer that returns make_bars().
    class FakeDataLayer:
        def symbol_info(self, symbol):
            from data.sources import SymbolInfo
            return SymbolInfo(symbol, symbol, "stock", "sh")
        def get_bars(self, info, freq="daily", start="", end="", adjust="qfq"):
            return make_bars()
    return real(
        strategy=strategy, universe=universe, freq=freq,
        start=start, end=end, adjust=adjust, initial_cash=initial_cash,
        strategy_manager=strategy_manager, data_layer=FakeDataLayer(),
    )


def test_submit_and_wait_all(monkeypatch):
    import api.agent.executor as mod
    monkeypatch.setattr(mod, "run_backtest", _dummy_backtest)
    ex = BacktestExecutor()
    try:
        j1 = ex.submit("buy_and_hold", universe={"symbols": ["600519"]})
        j2 = ex.submit("momentum_rotation", universe={"symbols": ["600519"]})
        results = ex.wait_all(timeout=30)
        assert len(results) == 2
        assert results[0]["status"] == "done"
        assert results[0]["result"]["success"] is True
        assert "total_return" in results[0]["result"]["metrics"]
        ex.reset_batch()
        j3 = ex.submit("buy_and_hold", universe={"symbols": ["600519"]})
        r3 = ex.wait_all(timeout=30)
        assert len(r3) == 1
        assert r3[0]["result"]["success"] is True
    finally:
        ex.shutdown()


def test_submit_forwards_strategy_manager(monkeypatch):
    """BacktestExecutor constructed with a strategy_manager threads it into run_backtest."""
    import api.agent.executor as mod
    from strategies.manager import StrategyManager

    captured = {}

    def fake_run_backtest(strategy, universe=None, freq="daily", start="", end="",
                          adjust="qfq", initial_cash=100_000.0, strategy_manager=None, data_layer=None):
        captured["strategy_manager"] = strategy_manager
        captured["universe"] = universe
        return {"success": True, "universe": ["600519"], "symbol": "600519", "symbol_name": "x", "freq": freq,
                "metrics": {"total_return": 0.1}, "equity_curve": [], "trades": [],
                "strategy": strategy}

    monkeypatch.setattr(mod, "run_backtest", fake_run_backtest)
    sm = StrategyManager()
    ex = BacktestExecutor(strategy_manager=sm)
    try:
        ex.submit("buy_and_hold", universe={"symbols": ["600519"]})
        results = ex.wait_all(timeout=30)
    finally:
        ex.shutdown()
    assert captured["strategy_manager"] is sm
    assert captured["universe"] == {"symbols": ["600519"]}
    assert results[0]["status"] == "done"


def test_submit_universe_forwarded(monkeypatch):
    import api.agent.executor as mod
    captured = {}

    def fake_run_backtest(strategy, universe=None, freq="daily", start="2020-01-01",
                          end="2024-12-31", adjust="qfq", initial_cash=100_000.0,
                          commission_rate=0.0003, stamp_duty=0.0005, lot_size=100,
                          strategy_manager=None, data_layer=None):
        captured["universe"] = universe
        captured["strategy"] = strategy
        return {"success": True, "universe": ["600519"], "symbol": "600519", "freq": freq,
                "metrics": {"total_return": 0.1}, "equity_curve": [], "trades": [],
                "strategy": "buy_and_hold"}
    monkeypatch.setattr(mod, "run_backtest", fake_run_backtest)
    ex = BacktestExecutor()
    try:
        j1 = ex.submit("buy_and_hold", universe={"symbols": ["600519"]})
        results = ex.wait_all(timeout=30)
    finally:
        ex.shutdown()
    assert results[0]["status"] == "done"
    assert captured["universe"] == {"symbols": ["600519"]}
    assert captured["strategy"] == "buy_and_hold"
