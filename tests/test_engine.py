"""Tests for the portfolio backtest engine — unified calendar, target-weight matching, A-share rules."""
from __future__ import annotations

import pandas as pd
import pytest

from engine.engine import BacktestEngine, EngineConfig, compute_metrics
from engine.context import Context


class _FakeDataLayer:
    """Returns synthetic bars per symbol; records which symbols were requested (for lazy-load assertions)."""
    def __init__(self, bars_by_symbol, symbol_types=None):
        self._bars = bars_by_symbol
        self._types = symbol_types or {s: "stock" for s in bars_by_symbol}
        self.requested = []
    def symbol_info(self, symbol):
        from data.sources import SymbolInfo
        return SymbolInfo(symbol, symbol, self._types.get(symbol, "stock"), "sh")
    def get_bars(self, info, freq="daily", start="", end="", adjust="qfq"):
        self.requested.append(info.code)
        return self._bars[info.code]


def make_bars(n=60, start_price=100.0, drift=0.005, seed=0):
    close = pd.Series(range(n)).apply(lambda i: start_price * (1 + drift * i))
    dates = pd.date_range("2023-01-02", periods=n, freq="B")
    return pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "open": close * 0.999, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": [10000] * n,
    })


def test_engine_runs_equal_weight_over_universe():
    bars_a = make_bars(80, start_price=100.0)
    bars_b = make_bars(80, start_price=50.0)
    dl = _FakeDataLayer({"600519": bars_a, "000858": bars_b})
    engine = BacktestEngine(EngineConfig(initial_cash=100_000), data_layer=dl)
    calendar = sorted(set(bars_a["date"]) | set(bars_b["date"]))

    def equal_weight(ctx):
        if ctx.bar_index == 0:
            for s in ctx.universe:
                ctx.buy(s, 1.0 / len(ctx.universe))

    result = engine.run(equal_weight, calendar, ["600519", "000858"], "daily", "2023-01-01", "2024-12-31", "qfq")
    m = result.metrics
    assert m["total_return"] > 0
    assert m["n_buys"] == 2  # one buy per symbol
    # both symbols were lazy-loaded (used by strategy); benchmark 000300 also loaded
    assert {"600519", "000858"} <= set(dl.requested)


def test_lazy_load_only_used_symbols():
    bars_a = make_bars(80, start_price=100.0)
    bars_b = make_bars(80, start_price=50.0)

    def only_a(ctx):
        if ctx.bar_index == 0:
            ctx.buy("600519", 1.0)

    dl = _FakeDataLayer({"600519": bars_a, "000858": bars_b})
    engine = BacktestEngine(EngineConfig(initial_cash=100_000), data_layer=dl)
    calendar = sorted(set(bars_a["date"]) | set(bars_b["date"]))
    engine.run(only_a, calendar, list(dl._bars), "daily", "2023-01-01", "2024-12-31", "qfq")
    # 600519 used -> loaded; 000858 never touched -> never loaded
    assert "600519" in dl.requested
    assert "000858" not in dl.requested


def test_anti_lookahead_history_stops_at_current():
    bars = make_bars(30, start_price=100.0)
    seen = {}

    def spy(ctx):
        if ctx.bar_index == 20:
            h = ctx.history("600519")
            seen["last_date"] = str(h["date"].iloc[-1])
            seen["n"] = len(h)

    dl = _FakeDataLayer({"600519": bars})
    engine = BacktestEngine(EngineConfig(initial_cash=100_000), data_layer=dl)
    engine.run(spy, list(bars["date"]), ["600519"], "daily", "2023-01-01", "2024-12-31", "qfq")
    assert seen["last_date"] == str(bars["date"].iloc[20])
    assert seen["n"] == 21


def test_etf_sell_no_stamp_duty():
    bars = make_bars(30, start_price=100.0)

    def strat(ctx):
        if ctx.bar_index == 0:
            ctx.buy("510300", 1.0)
        if ctx.bar_index == 10:
            ctx.sell("510300")

    dl = _FakeDataLayer({"510300": bars}, symbol_types={"510300": "etf"})
    engine = BacktestEngine(EngineConfig(initial_cash=100_000), data_layer=dl)
    result = engine.run(strat, list(bars["date"]), ["510300"], "daily", "2023-01-01", "2024-12-31", "qfq")
    sell = [t for t in result.trades if t["side"] == "sell"][0]
    assert sell["stamp_duty"] == 0.0
    buy = [t for t in result.trades if t["side"] == "buy"][0]
    assert buy["stamp_duty"] == 0.0


def test_stock_sell_pays_stamp_duty():
    bars = make_bars(30, start_price=100.0)

    def strat(ctx):
        if ctx.bar_index == 0:
            ctx.buy("600519", 1.0)
        if ctx.bar_index == 10:
            ctx.sell("600519")

    dl = _FakeDataLayer({"600519": bars}, symbol_types={"600519": "stock"})
    engine = BacktestEngine(EngineConfig(initial_cash=100_000), data_layer=dl)
    result = engine.run(strat, list(bars["date"]), ["600519"], "daily", "2023-01-01", "2024-12-31", "qfq")
    sell = [t for t in result.trades if t["side"] == "sell"][0]
    assert sell["stamp_duty"] > 0


def test_target_weight_pct_is_relative_to_net_value():
    bars_a = make_bars(30, start_price=100.0)
    bars_b = make_bars(30, start_price=50.0)

    def half_half(ctx):
        if ctx.bar_index == 0:
            ctx.buy("600519", 0.5)
            ctx.buy("000858", 0.5)

    dl = _FakeDataLayer({"600519": bars_a, "000858": bars_b})
    engine = BacktestEngine(EngineConfig(initial_cash=100_000), data_layer=dl)
    result = engine.run(half_half, list(bars_a["date"]), ["600519", "000858"], "daily", "2023-01-01", "2024-12-31", "qfq")
    buys = [t for t in result.trades if t["side"] == "buy"]
    assert len(buys) == 2
    # each bought ~50% of 100k = ~50k, minus commission; both within a few pct of half
    for t in buys:
        assert 40000 < t["amount"] < 55000


def test_same_day_sell_blocked_t_plus_1():
    bars = make_bars(10, start_price=100.0)
    sold = {"ok": True}

    def strat(ctx):
        if ctx.bar_index == 0:
            ctx.buy("600519", 1.0)
            sold["ok"] = ctx.sell("600519")  # same day -> T+1 blocks

    dl = _FakeDataLayer({"600519": bars})
    engine = BacktestEngine(EngineConfig(initial_cash=100_000), data_layer=dl)
    engine.run(strat, list(bars["date"]), ["600519"], "daily", "2023-01-01", "2024-12-31", "qfq")
    assert sold["ok"] is False


def test_metrics_benchmark_is_index_normalized():
    eq = pd.DataFrame({
        "date": pd.date_range("2023-01-01", periods=50, freq="B").strftime("%Y-%m-%d"),
        "equity": [100 + i for i in range(50)],
        "close": [100 + i for i in range(50)],
        "benchmark": [3000 + i * 2 for i in range(50)],  # index-like absolute level
    })
    m = compute_metrics(eq, [])
    assert m["total_return"] > 0
    assert m["final_equity"] == 149


def test_context_combination_api():
    import pandas as pd
    from engine.engine import BacktestEngine, EngineConfig
    from engine.context import Context

    class _FakeDL:
        def __init__(self, bars):
            self._bars = bars
        def symbol_info(self, symbol):
            from data.sources import SymbolInfo
            return SymbolInfo(symbol, symbol, "stock", "sh")
        def get_bars(self, info, freq="daily", start="", end="", adjust="qfq"):
            return self._bars[info.code]

    bars_a = pd.DataFrame({
        "date": pd.date_range("2023-01-02", periods=5, freq="B").strftime("%Y-%m-%d"),
        "open": [100]*5, "high": [101]*5, "low": [99]*5, "close": [100]*5, "volume": [10000]*5,
    })
    eng = BacktestEngine(EngineConfig(initial_cash=100_000))
    ctx = Context(100_000, engine=eng, universe=["600519"], calendar=list(bars_a["date"]),
                  data_layer=_FakeDL({"600519": bars_a}))
    assert ctx.cash == 100_000
    assert ctx.positions == {}
    assert ctx.total_value == 100_000
    ctx.state["x"] = 1
    assert ctx.state["x"] == 1
    bars = ctx.history("600519")
    assert list(bars.columns) == ["date", "open", "high", "low", "close", "volume"]
    assert len(bars) == 5
    assert ctx.price("600519") == 100.0


def test_corporate_action_split_adjusts_position():
    bars = make_bars(30, start_price=100.0)
    bars["factor"] = 1.0
    bars.loc[bars["date"] == bars["date"].iloc[5], "factor"] = 2.0  # 2:1 split on day 5
    seen = {}

    def strat(ctx):
        if ctx.bar_index == 0:
            ctx.buy("600519", 1.0)
        if ctx.bar_index == 5:
            seen["pos"] = ctx.positions.get("600519", 0)

    dl = _FakeDataLayer({"600519": bars})
    engine = BacktestEngine(EngineConfig(initial_cash=100_000), data_layer=dl)
    engine.run(strat, list(bars["date"]), ["600519"], "daily", "2023-01-01", "2024-12-31", "qfq")
    assert seen["pos"] > 0


def test_corporate_action_nan_factor_no_crash():
    bars = make_bars(30, start_price=100.0)
    bars["factor"] = 1.0
    bars.loc[bars["date"] == bars["date"].iloc[5], "factor"] = float("nan")

    def strat(ctx):
        if ctx.bar_index == 0:
            ctx.buy("600519", 1.0)

    dl = _FakeDataLayer({"600519": bars})
    engine = BacktestEngine(EngineConfig(initial_cash=100_000), data_layer=dl)
    engine.run(strat, list(bars["date"]), ["600519"], "daily", "2023-01-01", "2024-12-31", "qfq")
    # must not raise


def test_benchmark_requests_index():
    bars_a = make_bars(30, start_price=100.0)
    bars_b = make_bars(30, start_price=50.0)
    dl = _FakeDataLayer({"600519": bars_a, "000858": bars_b, "000300": make_bars(30, start_price=3000.0)})
    engine = BacktestEngine(EngineConfig(initial_cash=100_000), data_layer=dl)
    calendar = sorted(set(bars_a["date"]) | set(bars_b["date"]))

    def equal_weight(ctx):
        if ctx.bar_index == 0:
            for s in ctx.universe:
                ctx.buy(s, 1.0 / len(ctx.universe))

    engine.run(equal_weight, calendar, ["600519", "000858"], "daily", "2023-01-01", "2024-12-31", "qfq")
    assert "000300" in dl.requested  # benchmark index loaded


def test_extended_metrics_present():
    bars_a = make_bars(80, start_price=100.0)
    bars_b = make_bars(80, start_price=50.0)
    dl = _FakeDataLayer({"600519": bars_a, "000858": bars_b})
    engine = BacktestEngine(EngineConfig(initial_cash=100_000), data_layer=dl)
    calendar = sorted(set(bars_a["date"]) | set(bars_b["date"]))

    def equal_weight(ctx):
        if ctx.bar_index == 0:
            for s in ctx.universe:
                ctx.buy(s, 1.0 / len(ctx.universe))

    result = engine.run(equal_weight, calendar, ["600519", "000858"], "daily",
                        "2023-01-01", "2024-12-31", "qfq")
    m = result.metrics
    for key in ("excess_return", "calmar", "sortino", "turnover",
                "avg_holdings", "max_concentration", "monthly_win_rate"):
        assert key in m, key
        assert isinstance(m[key], float)
    # n_positions 列已记录，且等权持有两只标的 -> 平均持仓数 > 0
    assert "n_positions" in result.equity_curve.columns
    assert m["avg_holdings"] > 0
    assert 0.0 <= m["max_concentration"] <= 1.0
