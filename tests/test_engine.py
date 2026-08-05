"""Tests for the backtest engine — A-share rules and metrics."""
from __future__ import annotations

import pandas as pd
import pytest

from engine.engine import BacktestEngine, EngineConfig, compute_metrics
from strategies.builtin import buy_and_hold, sma_cross


def make_bars(n=100, start_price=100.0, seed=42):
    """Synthetic OHLCV bars with a gentle uptrend + noise."""
    rng = pd.Series(range(n))
    noise = pd.Series([i % 5 for i in range(n)]) * 0.3
    close = start_price * (1 + 0.002 * rng) + noise
    dates = pd.date_range("2023-01-02", periods=n, freq="B")
    df = pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "open": close.shift(1).fillna(close.iloc[0]) * 0.999,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": [10000] * n,
    })
    return df


def test_buy_and_hold_profits_in_uptrend():
    bars = make_bars(120)
    engine = BacktestEngine(EngineConfig(initial_cash=100_000))
    result = engine.run(buy_and_hold, bars)
    m = result.metrics
    assert m["total_return"] > 0.05
    assert m["n_trades"] >= 1  # buy on day 0
    assert m["equity_curve"]
    assert m["equity_curve"][0]["equity"] < 100_000.0  # bought + commission on day 0
    assert m["equity_curve"][0]["equity"] > 99_000.0


def test_t_plus_1_blocks_same_day_sell():
    # Buy and try to sell on the same bar — must be rejected.
    bars = make_bars(5)
    called = {"sold": False}

    def strategy(ctx, params):
        if ctx.bar_index == 0:
            ctx.buy()
        if ctx.bar_index == 0:  # same day
            called["sold"] = ctx.sell()

    engine = BacktestEngine(EngineConfig(initial_cash=100_000))
    engine.run(strategy, bars)
    assert called["sold"] is False


def test_stamp_duty_on_sell_not_buy():
    bars = make_bars(10)
    captured = {}

    def strategy(ctx, params):
        if ctx.bar_index == 0:
            ctx.buy()
        if ctx.bar_index == 5:
            captured["sell"] = ctx.sell()

    engine = BacktestEngine(EngineConfig(initial_cash=100_000, symbol_type="stock"))
    engine.run(strategy, bars)
    sell = captured["sell"]
    # sell trade record must have stamp_duty > 0
    sell_trades = [t for t in engine.run(strategy, bars).trades if t["side"] == "sell"]
    # re-run to capture trades
    result = engine.run(strategy, bars)
    sell_trade = [t for t in result.trades if t["side"] == "sell"][0]
    assert sell_trade["stamp_duty"] > 0
    buy_trade = [t for t in result.trades if t["side"] == "buy"][0]
    assert buy_trade["stamp_duty"] == 0.0


def test_etf_no_stamp_duty():
    bars = make_bars(10)
    def strategy(ctx, params):
        if ctx.bar_index == 0:
            ctx.buy()
        if ctx.bar_index == 5:
            ctx.sell()
    engine = BacktestEngine(EngineConfig(initial_cash=100_000, symbol_type="etf"))
    result = engine.run(strategy, bars)
    sell_trade = [t for t in result.trades if t["side"] == "sell"][0]
    assert sell_trade["stamp_duty"] == 0.0


def test_lot_size_rounding():
    bars = make_bars(5)
    captured = {}

    def strategy(ctx, params):
        if ctx.bar_index == 0:
            captured["filled"] = ctx.buy(shares=137)  # not multiple of 100
            captured["position"] = ctx.position

    engine = BacktestEngine(EngineConfig(initial_cash=100_000))
    engine.run(strategy, bars)
    assert captured["position"] % 100 == 0


def test_sma_cross_generates_trades():
    # up -> down -> up -> down: forces golden cross (buy) then death cross (sell)
    n = 320
    def trend(i):
        if i < 80:
            return 100 + i * 0.5               # up
        if i < 160:
            return 100 + 80*0.5 - (i-80) * 0.4  # down
        if i < 240:
            return 100 + 80*0.5 - 80*0.4 + (i-160) * 0.5  # up
        return 100 + 80*0.5 - 80*0.4 + 80*0.5 - (i-240) * 0.4  # down again
    close = pd.Series(range(n)).apply(trend)
    dates = pd.date_range("2022-01-02", periods=n, freq="B")
    bars = pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "open": close * 0.999, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": 10000,
    })
    engine = BacktestEngine(EngineConfig(initial_cash=100_000))
    result = engine.run(sma_cross, bars, params={"short": 10, "long": 30})
    assert len([t for t in result.trades if t["side"] == "buy"]) > 0
    assert len([t for t in result.trades if t["side"] == "sell"]) > 0


def test_corporate_action_split_adjusts_position():
    bars = make_bars(10)
    bars["factor"] = 1.0
    bars.loc[bars["date"] == bars["date"].iloc[5], "factor"] = 2.0  # 2:1 split on day 5
    captured = {}

    def strategy(ctx, params):
        if ctx.bar_index == 0:
            ctx.buy(shares=100)
        if ctx.bar_index == 5:
            captured["position"] = ctx.position
            captured["avg_cost"] = ctx.avg_cost

    engine = BacktestEngine(EngineConfig(initial_cash=100_000))
    engine.run(strategy, bars)
    assert captured["position"] == 200      # doubled by split
    assert captured["avg_cost"] < 60        # cost basis roughly halved (slightly >50 due to commission)


def test_compute_metrics_basic():
    eq = pd.DataFrame({
        "date": pd.date_range("2023-01-01", periods=50, freq="B").strftime("%Y-%m-%d"),
        "equity": [100 + i for i in range(50)],
        "close": [100 + i for i in range(50)],
        "benchmark": [100 + i for i in range(50)],
    })
    m = compute_metrics(eq, [])
    assert m["total_return"] > 0
    assert m["final_equity"] == 149
    assert m["max_drawdown"] <= 0  # strictly increasing -> no drawdown
    assert m["n_trades"] == 0
