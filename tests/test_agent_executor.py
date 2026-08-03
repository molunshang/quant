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


def _dummy_backtest(symbol, strategy_ref, params=None, freq="daily", start="", end="",
                    adjust="qfq", initial_cash=100_000.0, strategy_manager=None, data_layer=None):
    from api.runner import run_backtest as real
    # Reuse real run_backtest but with a fake data_layer that returns make_bars().
    class FakeDataLayer:
        def get_bars(self, info, freq="daily", start="", end="", adjust="qfq"):
            return make_bars()
    return real(
        symbol=symbol, strategy_ref=strategy_ref, params=params, freq=freq,
        start=start, end=end, adjust=adjust, initial_cash=initial_cash,
        strategy_manager=strategy_manager, data_layer=FakeDataLayer(),
    )


def test_submit_and_wait_all(monkeypatch):
    import api.agent.executor as mod
    monkeypatch.setattr(mod, "run_backtest", _dummy_backtest)
    ex = BacktestExecutor()
    try:
        j1 = ex.submit("600519", "buy_and_hold", {})
        j2 = ex.submit("600519", "sma_cross", {"short": 5, "long": 20})
        results = ex.wait_all(timeout=30)
        assert len(results) == 2
        assert results[0]["status"] == "done"
        assert results[0]["result"]["success"] is True
        assert "total_return" in results[0]["result"]["metrics"]
        ex.reset_batch()
        j3 = ex.submit("600519", "buy_and_hold", {})
        r3 = ex.wait_all(timeout=30)
        assert len(r3) == 1
        assert r3[0]["result"]["success"] is True
    finally:
        ex.shutdown()
