"""Tests for strategy loading, validation, and the manager (portfolio interface)."""
from __future__ import annotations

import pandas as pd
import pytest

from strategies.base import load_strategy_from_source, validate_strategy_source
from strategies.manager import StrategyManager
from engine.engine import BacktestEngine, EngineConfig
from engine.universe import metadata_calendar


class _FakeDL:
    def __init__(self, bars):
        self._bars = bars
    def symbol_info(self, symbol):
        from data.sources import SymbolInfo
        return SymbolInfo(symbol, symbol, "stock", "sh")
    def get_bars(self, info, freq="daily", start="", end="", adjust="qfq"):
        return self._bars[info.code]


def make_bars(n=80, start_price=100.0):
    close = pd.Series(range(n)).apply(lambda i: start_price * (1 + 0.005 * i))
    dates = pd.date_range("2023-01-02", periods=n, freq="B")
    return pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "open": close * 0.999, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": [10000] * n,
    })


def make_bars_dip(n=60, start_price=100.0, dip_at=45, drop=0.85):
    """Rising series that drops to `drop`x at `dip_at` and stays flat after —
    price falls below its SMA, so mean-reversion strategies fire."""
    close = pd.Series(range(n)).apply(lambda i: start_price * (1 + 0.005 * i))
    close = close.copy()
    close.loc[dip_at:] = close.loc[dip_at] * drop
    dates = pd.date_range("2023-01-02", periods=n, freq="B")
    return pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "open": close * 0.999, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": [10000] * n,
    })


def make_bars_flip(n=100, start_price=100.0, fast_bars=55, fast=0.01, crash=-0.02):
    """Rises fast early, crashes late. Its 60-day momentum is the leader at
    bar 60 (close[60]/close[0]) but the laggard by bar 80 (close[80]/close[20]),
    so top-N rotation actually flips between rebalances."""
    closes = [
        start_price * (1 + fast) ** min(i, fast_bars) * (1 + crash) ** max(0, i - fast_bars)
        for i in range(n)
    ]
    close = pd.Series(closes)
    dates = pd.date_range("2023-01-02", periods=n, freq="B")
    return pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "open": close * 0.999, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": [10000] * n,
    })


GOOD_SRC = """
def initialize(ctx):
    ctx.state['short'] = 20

def handle_data(ctx):
    for s in ctx.universe:
        bars = ctx.history(s)
        if len(bars) < ctx.state['short']:
            continue
        ma = bars['close'].astype(float).rolling(ctx.state['short']).mean().iloc[-1]
        if ctx.price(s) < ma and s not in ctx.positions:
            ctx.buy(s, 0.5)
"""


def test_load_good_strategy():
    f = load_strategy_from_source(GOOD_SRC)
    assert callable(f)
    assert f.__name__ == "handle_data"
    assert callable(getattr(f, "__initialize__", lambda ctx: None))


def test_reject_import():
    with pytest.raises(ValueError, match="math, numpy, pandas"):
        validate_strategy_source("import os\ndef handle_data(ctx):\n    pass")


def test_reject_private_access():
    with pytest.raises(ValueError, match="private"):
        validate_strategy_source("def handle_data(ctx):\n    ctx._engine.buy(ctx)\n")


def test_reject_missing_handle_data():
    with pytest.raises(ValueError, match="handle_data"):
        load_strategy_from_source("def foo(ctx):\n    pass")


def test_injected_helpers_run_in_engine():
    src = (
        "def handle_data(ctx):\n"
        "    for s in ctx.universe:\n"
        "        bars = ctx.history(s)\n"
        "        if len(bars) < 30:\n"
        "            continue\n"
        "        ma = sma(bars['close'].astype(float), 20)\n"
        "        if ctx.price(s) < ma.iloc[-1] and s not in ctx.positions:\n"
        "            ctx.buy(s, 0.5)\n"
    )
    func = load_strategy_from_source(src)
    bars_a = make_bars_dip(60, start_price=100.0)
    bars_b = make_bars_dip(60, start_price=50.0)
    dl = _FakeDL({"600519": bars_a, "000858": bars_b})
    engine = BacktestEngine(EngineConfig(initial_cash=100_000), data_layer=dl)
    calendar = sorted(set(bars_a["date"]) | set(bars_b["date"]))
    result = engine.run(func, calendar, ["600519", "000858"], "daily", "2023-01-01", "2024-12-31", "qfq")
    assert result.metrics["n_trades"] >= 1


def test_buy_and_hold_buys_all_universe():
    bars_a = make_bars(60, start_price=100.0)
    bars_b = make_bars(60, start_price=50.0)
    dl = _FakeDL({"600519": bars_a, "000858": bars_b})
    engine = BacktestEngine(EngineConfig(initial_cash=100_000), data_layer=dl)
    calendar = sorted(set(bars_a["date"]) | set(bars_b["date"]))
    result = engine.run(_import_builtin("buy_and_hold"), calendar, ["600519", "000858"],
                        "daily", "2023-01-01", "2024-12-31", "qfq")
    assert result.metrics["n_buys"] == 2


def _import_builtin(name):
    from strategies import builtin
    return getattr(builtin, name)


def test_momentum_rotation_trades():
    func = _import_builtin("momentum_rotation")

    def _init(ctx):
        ctx.state["top_n"] = 1  # 2-symbol universe: top-1 forces rotation

    setattr(func, "__initialize__", _init)
    bars_a = make_bars(100, start_price=100.0)
    bars_b = make_bars_flip(100, start_price=50.0)
    dl = _FakeDL({"600519": bars_a, "000858": bars_b})
    engine = BacktestEngine(EngineConfig(initial_cash=100_000), data_layer=dl)
    calendar = sorted(set(bars_a["date"]) | set(bars_b["date"]))
    result = engine.run(func, calendar, ["600519", "000858"],
                        "daily", "2023-01-01", "2024-12-31", "qfq")
    assert result.metrics["n_buys"] >= 1
    assert result.metrics["n_sells"] >= 1


def test_manager_registers_and_resolves():
    m = StrategyManager()
    m.register("my_strat", GOOD_SRC, "测试")
    names = [s["name"] for s in m.list()]
    assert "my_strat" in names
    assert "buy_and_hold" in names
    f = m.get_func("my_strat")
    assert callable(f)
    f2, n2 = m.resolve({"name": "inline", "source": GOOD_SRC})
    assert n2 == "inline"
