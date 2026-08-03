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
    with pytest.raises(ValueError, match="import"):
        validate_strategy_source("import os\ndef strategy(ctx, params):\n    pass")


def test_reject_from_import():
    with pytest.raises(ValueError, match="import"):
        validate_strategy_source("from os import path\ndef strategy(ctx, params):\n    pass")


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
