"""Tests for strategy loading, validation, and the manager."""
from __future__ import annotations

import pytest

from strategies.base import load_strategy_from_source, validate_strategy_source
from strategies.manager import StrategyManager


GOOD_SRC = """
def strategy(ctx, params):
    short = params.get('short', 20)
    bars = ctx.bars_upto()
    if len(bars) < short:
        return
    ma = bars['close'].astype(float).rolling(short).mean().iloc[-1]
    if ctx.price < ma and ctx.shares == 0:
        ctx.buy()
"""


def test_load_good_strategy():
    f = load_strategy_from_source(GOOD_SRC)
    assert callable(f)
    assert f.__name__ == "strategy"


def test_reject_import():
    with pytest.raises(ValueError, match="math, numpy, pandas"):
        validate_strategy_source("import os\ndef strategy(ctx, params):\n    pass")


def test_reject_from_import():
    with pytest.raises(ValueError, match="math, numpy, pandas"):
        validate_strategy_source("from os import path\ndef strategy(ctx, params):\n    pass")


def test_allow_math_import():
    src = "import math\ndef strategy(ctx, params):\n    return math.sqrt(2)"
    assert callable(load_strategy_from_source(src))


def test_allow_math_from_import():
    src = "from math import sqrt\ndef strategy(ctx, params):\n    return sqrt(2)"
    assert callable(load_strategy_from_source(src))


def test_allow_numpy_pandas_import():
    src = ("import numpy as np\nimport pandas as pd\n"
           "def strategy(ctx, params):\n"
           "    return np.mean(ctx.bars_upto()['close'])")
    assert callable(load_strategy_from_source(src))


def test_reject_import_from_engine():
    with pytest.raises(ValueError, match="math, numpy, pandas"):
        validate_strategy_source("from engine.context import Context\ndef strategy(ctx, params):\n    pass")


def test_injected_helpers_run_in_engine():
    """User source can call pre-injected sma/ema/rsi/macd and import math/numpy,
    and still run end-to-end through the engine."""
    src = (
        "import math\n"
        "import numpy as np\n"
        "def strategy(ctx, params):\n"
        "    closes = ctx.bars_upto()['close'].astype(float)\n"
        "    ma = sma(closes, params.get('short', 20))\n"
        "    r = rsi(closes, 14)\n"
        "    if len(closes) < 30:\n"
        "        return\n"
        "    size = int(math.floor(ctx.cash / ctx.price / 100)) * 100\n"
        "    if ctx.shares == 0 and np.isfinite(ma.iloc[-1]) and size > 0:\n"
        "        ctx.buy(size)\n"
        "    elif r.iloc[-1] > 90 and ctx.shares > 0:\n"
        "        ctx.sell()\n"
    )
    func = load_strategy_from_source(src)
    import pandas as pd
    bars = pd.DataFrame({
        "date": pd.date_range("2023-01-02", periods=80, freq="B").strftime("%Y-%m-%d"),
        "open": [100.0] * 80, "high": [101.0] * 80, "low": [99.0] * 80,
        "close": [100.0] * 80, "volume": [10000] * 80,
    })
    from engine.engine import BacktestEngine, EngineConfig
    result = BacktestEngine(EngineConfig(initial_cash=100_000)).run(func, bars, params={"short": 20})
    assert result.metrics["n_trades"] >= 1


def test_reject_missing_strategy_func():
    with pytest.raises(ValueError, match="strategy"):
        load_strategy_from_source("def foo(ctx, params):\n    pass")


def test_reject_private_access():
    with pytest.raises(ValueError, match="private"):
        validate_strategy_source("def strategy(ctx, params):\n    ctx._engine.buy(ctx)\n")


def test_manager_registers_and_lists():
    m = StrategyManager()
    m.register("my_strat", GOOD_SRC, "测试策略")
    names = [s["name"] for s in m.list()]
    assert "my_strat" in names
    assert "sma_cross" in names  # built-in
    f = m.get_func("my_strat")
    assert callable(f)


def test_manager_resolve_name_and_source():
    m = StrategyManager()
    f1, n1 = m.resolve("sma_cross")
    assert n1 == "sma_cross"
    f2, n2 = m.resolve({"name": "inline", "source": GOOD_SRC})
    assert n2 == "inline"
    assert callable(f2)


def test_unknown_strategy_raises():
    m = StrategyManager()
    with pytest.raises(KeyError):
        m.get_func("does_not_exist")
