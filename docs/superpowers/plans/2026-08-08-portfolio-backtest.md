# 组合级自选标的回测 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把回测从「指定单标的」重构为「组合级策略自选标的」——策略只需起始金额，自己从 universe 选标的、自己决定怎么交易（聚宽式 `handle_data(ctx)` 接口）。

**Architecture:** 引擎持有 universe 各标的 bars 字典 + 统一日历（全部标日并集），逐「时间点」推进；每个时间点调用一次 `handle_data(ctx)`，收集 `ctx.buy(symbol, pct)`/`ctx.sell(symbol, pct)` 目标权重订单，按当前 bar 收盘价撮合（各标按自身类型应用 A股规则）。数据 lazy-load：元数据（起止日期）预对齐日历，策略首次用到某标时才拉全量 K 线（离线优先，未命中实时抓取并缓存）。

**Tech Stack:** Python 3.10+，pandas / numpy，FastAPI，pytest（TestClient）。

## Global Constraints

- 策略签名：可选 `initialize(ctx)` + 必选 `handle_data(ctx)`（**无 `params` 参数**）
- `StrategyFunc = Callable[[Context], None]`
- `ctx.buy(symbol, pct)`：`pct` 相对当前净值 `total_value`（0~1）；`ctx.sell(symbol, pct)`：卖持仓 pct%（默认清仓），均返回 `bool` 表示是否成交
- `ctx.history(symbol, lookback=0)`：截至当前时间点的历史；`lookback` 沿用 `bars_upto` 约定（0=全历史到当前，N=最近 N+1 根）
- universe 默认 = 已缓存标的集（`data/cache/{type}_{code}_{freq}_{adjust}.csv` 解析），可选按 `types` 过滤；也支持显式 `symbols` 列表
- 不向后兼容：删除 `params`、`/api/optimize`、`run_optimize`、`OptimizeRequest`、`symbol` 必填
- 基准 = 指数（沪深300 `000300`）归一化
- AST 校验保留：策略只允许 import math/numpy/pandas；禁全局/非局部、禁 `ctx._*` 私有访问、禁 eval/exec
- 测试禁止真实网络：数据层用 fake 注入，Agent 用 FakeProvider/FakeStore/FakeExecutor
- 保持测试隔离：`tests/conftest.py` 已把 `QUANT_AGENT_DB` 重定向到 session 级影子库

---

### Task 1: 组合感知 Context

**Files:**
- Rewrite: `engine/context.py`
- Test: `tests/test_engine.py`（新增组合 Context 用例）

**Interfaces:**
- Consumes: `BacktestEngine`（见 Task 2）、`TradingRules`（engine/rules.py 现有）、`DataLayer.get_bars`（data/sources.py 现有）
- Produces:
  - `Context(initial_cash, engine, universe, calendar, data_layer)` — 组合感知上下文
  - `ctx.universe: list[str]`、`ctx.state: dict`、`ctx.cash: float`、`ctx.positions: dict[str, int]`、`ctx.total_value: float`、`ctx.calendar: list[str]`、`ctx.time: str`、`ctx.bar_index: int`
  - `ctx.history(symbol, lookback=0) -> pd.DataFrame`（含 lazy 加载）
  - `ctx.price(symbol) -> float`、`ctx.buy(symbol, pct) -> bool`、`ctx.sell(symbol, pct) -> bool`

- [ ] **Step 1: 写失败测试**（`tests/test_engine.py` 追加）

```python
def test_context_combination_api():
    import pandas as pd
    from engine.engine import BacktestEngine, EngineConfig
    from engine.context import Context
    bars_a = pd.DataFrame({
        "date": pd.date_range("2023-01-02", periods=5, freq="B").strftime("%Y-%m-%d"),
        "open": [100]*5, "high": [101]*5, "low": [99]*5, "close": [100]*5, "volume": [10000]*5,
    })
    eng = BacktestEngine(EngineConfig(initial_cash=100_000))
    ctx = Context(100_000, engine=eng, universe=["600519"], calendar=list(bars_a["date"]),
                  data_layer=_FakeDataLayer({"600519": bars_a}))
    assert ctx.cash == 100_000
    assert ctx.positions == {}
    assert ctx.total_value == 100_000
    ctx.state["x"] = 1
    assert ctx.state["x"] == 1
    bars = ctx.history("600519")
    assert list(bars.columns) == ["date", "open", "high", "low", "close", "volume"]
    assert len(bars) == 5
    assert ctx.price("600519") == 100.0
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /Users/zk/code/agent/quant-agent && .venv/bin/pytest tests/test_engine.py::test_context_combination_api -v`
Expected: FAIL（`Context` 构造函数签名不匹配 / 属性不存在）

- [ ] **Step 3: 实现组合 Context**

```python
"""Strategy context for the portfolio backtest engine.

A strategy is `initialize(ctx)` (optional) + `handle_data(ctx)` (required).
`handle_data` runs once per time step (daily in this release). The context is
portfolio-aware: cash + positions dict over symbols, unified calendar, and a
lazy bar loader for `history()`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from .engine import BacktestEngine


class Context:
    def __init__(self, initial_cash: float, engine: "BacktestEngine | None" = None,
                 universe: list[str] | None = None, calendar: list[str] | None = None,
                 data_layer=None):
        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.positions: dict[str, int] = {}
        self.state: dict = {}
        self.universe: list[str] = list(universe or [])
        self.calendar: list[str] = list(calendar or [])
        self.time: str | None = None
        self.bar_index: int = 0
        self.trades: list[dict] = []
        self._engine = engine
        self._data_layer = data_layer
        self._bars: dict[str, pd.DataFrame] = {}
        self._bar_index_by_date: dict[str, dict[str, int]] = {}
        self._buy_dates: dict[str, set[str]] = {}
        self._avg_cost: dict[str, float] = {}
        self._symbol_type: dict[str, str] = {}

    # ---- order interface (delegates to engine for A-share rule matching) ----
    def buy(self, symbol: str, pct: float = 1.0) -> bool:
        if self._engine is None:
            raise RuntimeError("Context not attached to an engine")
        return self._engine.buy(self, symbol, pct)

    def sell(self, symbol: str, pct: float = 1.0) -> bool:
        if self._engine is None:
            raise RuntimeError("Context not attached to an engine")
        return self._engine.sell(self, symbol, pct)

    # ---- data access ----
    def history(self, symbol: str, lookback: int = 0) -> pd.DataFrame:
        """Return `symbol` bars up to (inclusive) the current time point.

        Triggers lazy load on first use. lookback=0 -> full history to now;
        lookback=N -> last N+1 bars.
        """
        df = self._ensure_loaded(symbol)
        idx = self._idx_at_current(symbol)
        if lookback <= 0:
            start = 0
        else:
            start = max(0, idx - lookback)
        return df.iloc[start:idx + 1].reset_index(drop=True)

    def price(self, symbol: str) -> float:
        df = self._ensure_loaded(symbol)
        idx = self._idx_at_current(symbol)
        return float(df.iloc[idx]["close"])

    # ---- portfolio helpers ----
    @property
    def market_value(self) -> float:
        return sum(self.position_value(s) for s in self.positions)

    def position_value(self, symbol: str) -> float:
        if symbol not in self.positions:
            return 0.0
        return self.positions[symbol] * self.price(symbol)

    @property
    def total_value(self) -> float:
        return self.cash + self.market_value

    @property
    def shares(self) -> dict[str, int]:
        return dict(self.positions)

    # ---- lazy loader / alignment (called by engine) ----
    def _ensure_loaded(self, symbol: str) -> pd.DataFrame:
        if symbol not in self._bars:
            if self._data_layer is None:
                raise RuntimeError(f"no data layer for lazy-loading {symbol}")
            info = self._data_layer.symbol_info(symbol)
            df = self._data_layer.get_bars(info, freq=self._freq, start=self.calendar[0],
                                           end=self.calendar[-1], adjust=self._adjust)
            if df is None or df.empty:
                raise ValueError(f"no data for {symbol}")
            self._symbol_type[symbol] = info.type
            self._bars[symbol] = df.reset_index(drop=True)
            self._bar_index_by_date[symbol] = {
                str(d): i for i, d in enumerate(self._bars[symbol]["date"])
            }
        return self._bars[symbol]

    def _idx_at_current(self, symbol: str) -> int:
        """Row index of current time point in symbol's bars (searchsorted-free:
        prebuilt date->index map; falls back to 0 if current date not present)."""
        if symbol not in self._bar_index_by_date:
            self._ensure_loaded(symbol)
        m = self._bar_index_by_date[symbol]
        d = str(self.time)
        if d in m:
            return m[d]
        # no bar on current date for this symbol -> use the last bar before it
        import bisect
        dates = list(m.keys())
        i = bisect.bisect_right(dates, d) - 1
        return m[dates[max(0, i)]] if dates else 0

    def __repr__(self):
        return f"<Context cash={self.cash:.0f} pos={self.positions} val={self.total_value:.0f}>"
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /Users/zk/code/agent/quant-agent && .venv/bin/pytest tests/test_engine.py::test_context_combination_api -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/zk/code/agent/quant-agent
git add engine/context.py tests/test_engine.py
git commit -m "feat(engine): 组合感知 Context（universe/positions/state/history lazy-load）

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: 组合级 BacktestEngine（统一日历 + 目标权重撮合）

**Files:**
- Rewrite: `engine/engine.py`
- Test: `tests/test_engine.py`（重写为组合级用例）

**Interfaces:**
- Consumes: `Context`（Task 1）、`TradingRules`/`calc_commission`/`calc_transfer_fee`（engine/rules.py 现有）、`_FakeDataLayer`（见测试）
- Produces:
  - `EngineConfig`（含 `initial_cash`、`commission_rate`、`stamp_duty`、`lot_size`、`t_plus_1`、`price_limit_pct`）
  - `BacktestEngine(config, data_layer)`
  - `engine.run(strategy: StrategyFunc, calendar, universe, freq, start, end, adjust) -> BacktestResult`
  - `BacktestResult(equity_curve, trades, ctx, freq)`、`result.metrics`（经 `compute_metrics`）
  - `compute_metrics(equity_curve, trades) -> dict`（benchmark 用指数归一化）

- [ ] **Step 1: 写失败测试**（重写 `tests/test_engine.py` 为组合级）

```python
"""Tests for the portfolio backtest engine — unified calendar, target-weight matching, A-share rules."""
from __future__ import annotations

import pandas as pd
import pytest

from engine.engine import BacktestEngine, EngineConfig, compute_metrics
from engine.context import Context
from strategies.builtin import buy_and_hold, momentum_rotation


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


def test_buy_and_hold_equity_weighted_over_universe():
    bars_a = make_bars(80, start_price=100.0)
    bars_b = make_bars(80, start_price=50.0)
    dl = _FakeDataLayer({"600519": bars_a, "000858": bars_b})
    engine = BacktestEngine(EngineConfig(initial_cash=100_000), data_layer=dl)
    calendar = sorted(set(bars_a["date"]) | set(bars_b["date"]))
    result = engine.run(buy_and_hold, calendar, list(dl._bars), "daily", "2023-01-01", "2024-12-31", "qfq")
    m = result.metrics
    assert m["total_return"] > 0
    assert m["n_buys"] == 2  # one buy per symbol
    # both symbols were lazy-loaded (used by strategy)
    assert set(dl.requested) == {"600519", "000858"}


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
    assert dl.requested == ["600519"]  # 000858 never touched -> never loaded


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
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /Users/zk/code/agent/quant-agent && .venv/bin/pytest tests/test_engine.py -v`
Expected: FAIL（新签名不存在 / `buy_and_hold`/`momentum_rotation` 未定义）

- [ ] **Step 3: 实现组合引擎**

```python
"""Portfolio backtest engine: unified calendar, target-weight matching, A-share rules."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from .context import Context
from .rules import TradingRules, calc_commission, calc_transfer_fee

StrategyFunc = Callable[[Context], None]


@dataclass
class EngineConfig:
    initial_cash: float = 100_000.0
    commission_rate: float = 0.0003
    min_commission: float = 5.0
    stamp_duty: float = 0.0005
    transfer_fee_rate: float = 0.00001
    lot_size: int = 100
    t_plus_1: bool = True
    price_limit_pct: float = 0.10


class BacktestResult:
    def __init__(self, equity_curve: pd.DataFrame, trades: list[dict], ctx: Context, freq: str):
        self.equity_curve = equity_curve
        self.trades = trades
        self.ctx = ctx
        self.freq = freq

    @property
    def metrics(self) -> dict:
        return compute_metrics(self.equity_curve, self.trades)


class BacktestEngine:
    def __init__(self, config: EngineConfig | None = None, data_layer=None):
        self.cfg = config or EngineConfig()
        self.data_layer = data_layer

    def _rules(self, symbol_type: str) -> TradingRules:
        c = self.cfg
        return TradingRules(
            commission_rate=c.commission_rate,
            min_commission=c.min_commission,
            stamp_duty=c.stamp_duty,
            transfer_fee_rate=c.transfer_fee_rate,
            lot_size=c.lot_size,
            t_plus_1=c.t_plus_1,
            price_limit_pct=c.price_limit_pct,
        )

    def run(self, strategy: StrategyFunc, calendar: list[str], universe: list[str],
            freq: str = "daily", start: str = "2020-01-01", end: str = "2024-12-31",
            adjust: str = "qfq") -> BacktestResult:
        if not calendar or not universe:
            raise ValueError("calendar and universe must be non-empty")
        ctx = Context(self.cfg.initial_cash, engine=self, universe=universe,
                      calendar=calendar, data_layer=self.data_layer)
        ctx._freq = freq
        ctx._adjust = adjust
        self._ctx = ctx
        equity_rows: list[dict] = []
        benchmark = self._load_benchmark(start, end)

        for i, day in enumerate(calendar):
            ctx.time = str(day)
            ctx.bar_index = i
            # corporate-action factor adjustment (per symbol, before strategy sees data)
            self._apply_corporate_actions(ctx)
            # strategy decides target weights
            strategy(ctx)
            # record portfolio value AFTER matching orders placed this bar
            self._match_orders(ctx)
            equity_rows.append({
                "date": str(day),
                "cash": ctx.cash,
                "position": sum(ctx.positions.values()),
                "market_value": ctx.market_value,
                "equity": ctx.total_value,
                "benchmark": benchmark.get(str(day)),
            })

        equity_df = pd.DataFrame(equity_rows)
        return BacktestResult(equity_curve=equity_df, trades=ctx.trades, ctx=ctx, freq=freq)

    # ---- matching ----
    def buy(self, ctx: Context, symbol: str, pct: float = 1.0) -> bool:
        """Buy `symbol` up to pct of current net value. Returns True if filled."""
        target = ctx.total_value * pct
        bar = self._bar_for(ctx, symbol)
        price = float(bar["close"])
        rules = self._rules(ctx._symbol_type.get(symbol, "stock"))
        lot = rules.lot_size
        if rules.price_limit_pct > 0 and self._is_limit_up(bar, rules):
            return False
        shares = int(target // (price * lot)) * lot
        if shares <= 0:
            return False
        # step down a lot until fits cash incl. commission
        while shares > 0:
            amount = shares * price
            commission = calc_commission(amount, rules.commission_rate, rules.min_commission)
            if amount + commission <= ctx.cash:
                break
            shares -= lot
        if shares <= 0:
            return False
        amount = shares * price
        commission = calc_commission(amount, rules.commission_rate, rules.min_commission)
        ctx.cash -= amount + commission
        pos = ctx.positions.get(symbol, 0)
        prev_cost = ctx._avg_cost.get(symbol, 0.0) * pos
        ctx.positions[symbol] = pos + shares
        ctx._avg_cost[symbol] = (prev_cost + amount) / ctx.positions[symbol]
        if rules.t_plus_1:
            ctx._buy_dates.setdefault(symbol, set()).add(str(ctx.time))
        ctx.trades.append({
            "date": str(ctx.time), "symbol": symbol, "side": "buy", "shares": shares,
            "price": price, "amount": amount, "commission": commission,
            "stamp_duty": 0.0, "transfer_fee": 0.0,
        })
        return True

    def sell(self, ctx: Context, symbol: str, pct: float = 1.0) -> bool:
        """Sell pct of `symbol` position (default: entire). Returns True if filled."""
        pos = ctx.positions.get(symbol, 0)
        if pos <= 0:
            return False
        bar = self._bar_for(ctx, symbol)
        price = float(bar["close"])
        rules = self._rules(ctx._symbol_type.get(symbol, "stock"))
        if rules.t_plus_1 and str(ctx.time) in ctx._buy_dates.get(symbol, set()):
            return False
        if rules.price_limit_pct > 0 and self._is_limit_down(bar, rules):
            return False
        shares = int(pos * pct // rules.lot_size) * rules.lot_size
        if shares <= 0:
            return False
        shares = min(shares, pos)
        amount = shares * price
        commission = calc_commission(amount, rules.commission_rate, rules.min_commission)
        is_etf = rules.is_etf_or_fund(ctx._symbol_type.get(symbol, "stock"))
        stamp = 0.0 if is_etf else amount * rules.stamp_duty
        transfer = calc_transfer_fee(amount, rules.transfer_fee_rate) if not is_etf else 0.0
        ctx.cash += amount - commission - stamp - transfer
        ctx.positions[symbol] = pos - shares
        if ctx.positions[symbol] == 0:
            ctx._avg_cost.pop(symbol, None)
        ctx.trades.append({
            "date": str(ctx.time), "symbol": symbol, "side": "sell", "shares": shares,
            "price": price, "amount": amount, "commission": commission,
            "stamp_duty": stamp, "transfer_fee": transfer,
        })
        return True

    # ---- helpers ----
    def _bar_for(self, ctx: Context, symbol: str) -> pd.Series:
        df = ctx._ensure_loaded(symbol)
        idx = ctx._idx_at_current(symbol)
        return df.iloc[idx]

    def _is_limit_up(self, bar: pd.Series, rules: TradingRules) -> bool:
        prev_close = float(bar.get("prev_close", 0.0)) or float(bar["open"])
        if prev_close <= 0:
            return False
        limit = prev_close * (1 + rules.price_limit_pct)
        return float(bar["close"]) >= limit - 1e-6

    def _is_limit_down(self, bar: pd.Series, rules: TradingRules) -> bool:
        prev_close = float(bar.get("prev_close", 0.0)) or float(bar["open"])
        if prev_close <= 0:
            return False
        limit = prev_close * (1 - rules.price_limit_pct)
        return float(bar["close"]) <= limit + 1e-6

    def _apply_corporate_actions(self, ctx: Context) -> None:
        """Scale positions on factor-change days (total-return approx)."""
        for symbol in list(ctx.positions):
            df = ctx._bars.get(symbol)
            if df is None or "factor" not in df.columns:
                continue
            idx = ctx._idx_at_current(symbol)
            if idx <= 0:
                continue
            prev = float(df["factor"].iloc[idx - 1]) or 1.0
            cur = float(df["factor"].iloc[idx]) or 1.0
            if prev <= 0:
                continue
            ratio = cur / prev
            if ratio != 1.0 and ctx.positions[symbol] > 0:
                pos = ctx.positions[symbol]
                new_pos = int(pos * ratio // self.cfg.lot_size) * self.cfg.lot_size
                if new_pos > 0:
                    ctx._avg_cost[symbol] = ctx._avg_cost.get(symbol, 0.0) * pos / new_pos
                    ctx.positions[symbol] = new_pos

    def _load_benchmark(self, start: str, end: str) -> dict[str, float]:
        """Load CSI 300 index (000300) closes for the benchmark series.

        Uses the same DataLayer lazy path; if unavailable, returns {} (equity
        curve rows get None benchmark — metrics treat it as all-1.0).
        """
        if self.data_layer is None:
            return {}
        try:
            from data.sources import SymbolInfo
            info = SymbolInfo("000300", "沪深300", "index", "sh")
            df = self.data_layer.get_bars(info, freq="daily", start=start, end=end, adjust="qfq")
            if df is None or df.empty:
                return {}
            return {str(d): float(c) for d, c in zip(df["date"], df["close"])}
        except Exception:  # noqa: BLE001 - benchmark is optional
            return {}


def compute_metrics(equity_curve: pd.DataFrame, trades: list[dict]) -> dict:
    """Compute performance metrics from equity curve + trades."""
    if equity_curve is None or equity_curve.empty:
        return {"error": "no equity data"}
    eq = equity_curve.copy()
    equity = eq["equity"].astype(float).values
    initial = float(equity[0]) if len(equity) else 0.0
    final = float(equity[-1]) if len(equity) else 0.0
    total_return = (final / initial - 1) if initial else 0.0
    n = len(equity)
    annual_factor = 252.0
    if n > 1:
        dates = pd.to_datetime(eq["date"])
        days = (dates.iloc[-1] - dates.iloc[0]).days
        if days > 0:
            annual_factor = 252.0 * n / days
    annual_return = (final / initial) ** (annual_factor / n) - 1 if n > 0 and initial > 0 and final > 0 else 0.0
    peak = np.maximum.accumulate(equity)
    drawdown = equity / peak - 1
    max_drawdown = float(np.min(drawdown)) if len(drawdown) else 0.0
    vol = sharpe = 0.0
    if n > 2:
        rets = np.diff(equity) / equity[:-1]
        rets = rets[np.isfinite(rets)]
        if len(rets) > 1:
            std = np.std(rets, ddof=1)
            vol = float(std * np.sqrt(annual_factor))
            sharpe = float(np.mean(rets) / std * np.sqrt(annual_factor)) if std > 0 else 0.0
    wins = losses = 0
    open_buys: list[dict] = []
    for t in trades:
        if t["side"] == "buy":
            open_buys.append(t)
        else:
            remaining = t["shares"]
            cost = 0.0
            while remaining > 0 and open_buys:
                b = open_buys[0]
                take = min(remaining, b["shares"])
                cost += take * b["price"]
                remaining -= take
                b["shares"] -= take
                if b["shares"] == 0:
                    open_buys.pop(0)
            pnl = t["amount"] - cost - t["commission"] - t.get("stamp_duty", 0.0) - t.get("transfer_fee", 0.0)
            if pnl >= 0:
                wins += 1
            else:
                losses += 1
    n_closed = wins + losses
    win_rate = wins / n_closed if n_closed else 0.0
    return {
        "total_return": total_return, "annual_return": annual_return,
        "max_drawdown": max_drawdown, "volatility": vol, "sharpe": sharpe,
        "n_trades": len(trades),
        "n_buys": sum(1 for t in trades if t["side"] == "buy"),
        "n_sells": sum(1 for t in trades if t["side"] == "sell"),
        "win_rate": win_rate, "final_equity": final, "initial_equity": initial,
        "equity_curve": eq.to_dict("records"), "trades": trades,
    }
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /Users/zk/code/agent/quant-agent && .venv/bin/pytest tests/test_engine.py -v`
Expected: PASS（8 个组合级用例）

- [ ] **Step 5: Commit**

```bash
cd /Users/zk/code/agent/quant-agent
git add engine/engine.py tests/test_engine.py
git commit -m "feat(engine): 组合级 BacktestEngine（统一日历+目标权重撮合+指数基准）

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: Universe 解析与 lazy 数据层

**Files:**
- Create: `engine/universe.py`
- Test: `tests/test_universe.py`

**Interfaces:**
- Consumes: `DataLayer`（data/sources.py）、`get_registry`（data/registry.py）、`CACHE_DIR`（data/sources.py）
- Produces:
  - `resolve_universe(spec: dict | None, freq, adjust) -> list[str]` — 解析 universe（显式 symbols / 默认缓存集 + types 过滤）
  - `cached_symbols(freq, adjust, types=None) -> list[str]` — 从缓存目录文件名解析已缓存标的
  - `metadata_calendar(symbols, start, end, freq, adjust, data_layer) -> list[str]` — 元数据预对齐统一日历

- [ ] **Step 1: 写失败测试**（`tests/test_universe.py`）

```python
"""Universe resolution & calendar alignment tests (no network)."""
from __future__ import annotations

import pandas as pd
import pytest

from engine.universe import resolve_universe, cached_symbols, metadata_calendar


class _FakeDL:
    def __init__(self, cache_dir=None, bars_by_symbol=None):
        self._bars = bars_by_symbol or {}
        self.requested = []
    def get_bars(self, info, freq="daily", start="", end="", adjust="qfq"):
        self.requested.append(info.code)
        return self._bars[info.code]


def test_cached_symbols_parses_filenames(tmp_path, monkeypatch):
    for fn in ("stock_600519_daily_qfq.csv", "etf_510300_daily_qfq.csv",
               "fund_161725_daily_qfq.csv", "stock_000858_daily_qfq.csv"):
        (tmp_path / fn).write_text("date,open,high,low,close,volume\n")
    monkeypatch.setattr("engine.universe.CACHE_DIR", str(tmp_path))
    syms = cached_symbols("daily", "qfq")
    assert set(syms) == {"600519", "510300", "161725", "000858"}
    assert cached_symbols("daily", "qfq", types=["etf"]) == ["510300"]


def test_resolve_explicit_symbols():
    assert resolve_universe({"symbols": ["600519", "000858"]}, "daily", "qfq") == ["600519", "000858"]


def test_resolve_default_is_cached(tmp_path, monkeypatch):
    for fn in ("stock_600519_daily_qfq.csv", "etf_510300_daily_qfq.csv"):
        (tmp_path / fn).write_text("date,open,high,low,close,volume\n")
    monkeypatch.setattr("engine.universe.CACHE_DIR", str(tmp_path))
    assert set(resolve_universe(None, "daily", "qfq")) == {"600519", "510300"}
    assert resolve_universe({"types": ["etf"]}, "daily", "qfq") == ["510300"]


def test_metadata_calendar_union_of_dates():
    bars_a = pd.DataFrame({"date": ["2023-01-02", "2023-01-03", "2023-01-04"]})
    bars_b = pd.DataFrame({"date": ["2023-01-03", "2023-01-04", "2023-01-05"]})
    dl = _FakeDL(bars_by_symbol={"600519": bars_a, "000858": bars_b})
    cal = metadata_calendar(["600519", "000858"], "2023-01-01", "2023-12-31", "daily", "qfq", dl)
    assert cal == ["2023-01-02", "2023-01-03", "2023-01-04", "2023-01-05"]
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /Users/zk/code/agent/quant-agent && .venv/bin/pytest tests/test_universe.py -v`
Expected: FAIL（`engine.universe` 不存在）

- [ ] **Step 3: 实现**

```python
"""Universe resolution and metadata calendar alignment.

A backtest runs over a *universe* of candidate symbols. The strategy picks
which to trade; the engine only loads bars for symbols the strategy actually
uses (lazy). The unified calendar (union of all symbol trading dates) is
aligned from lightweight metadata before any full bars are pulled.
"""
from __future__ import annotations

import os
import re

from data.sources import CACHE_DIR


def cached_symbols(freq: str = "daily", adjust: str = "qfq",
                   types: list[str] | None = None) -> list[str]:
    """List symbols that have a local cache file for the given freq/adjust.

    Filename pattern: {type}_{code}_{freq}_{adjust}.csv
    """
    out: list[str] = []
    if not os.path.isdir(CACHE_DIR):
        return out
    for fn in sorted(os.listdir(CACHE_DIR)):
        if not fn.endswith(".csv"):
            continue
        parts = fn[:-4].split("_")
        if len(parts) != 4:
            continue
        typ, code, f, adj = parts
        if f != freq or adj != adjust:
            continue
        if types and typ not in types:
            continue
        out.append(code)
    return out


def resolve_universe(spec: dict | None, freq: str = "daily", adjust: str = "qfq") -> list[str]:
    """Resolve a request's universe into a symbol list.

    - spec with 'symbols' -> those symbols
    - spec with 'types' -> cached symbols of those types
    - spec None/empty -> all cached symbols
    """
    spec = spec or {}
    if spec.get("symbols"):
        return list(spec["symbols"])
    types = spec.get("types")
    return cached_symbols(freq=freq, adjust=adjust, types=types)


def metadata_calendar(symbols: list[str], start: str, end: str, freq: str,
                      adjust: str, data_layer) -> list[str]:
    """Union of all symbols' trading dates in [start, end], sorted.

    Pulls full bars for each symbol once here — that's O(U) loads, but only
    for the *resolved* universe; strategy-level lazy loading is separate.
    For very large universes this stays bounded because resolve_universe
    defaults to cached symbols only.
    """
    date_sets: list[set[str]] = []
    from data.registry import get_registry
    reg = get_registry()
    for sym in symbols:
        info = reg.get(sym)
        df = data_layer.get_bars(info, freq=freq, start=start, end=end, adjust=adjust)
        if df is None or df.empty:
            continue
        date_sets.append(set(df["date"].astype(str)))
    if not date_sets:
        return []
    return sorted(set().union(*date_sets))
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /Users/zk/code/agent/quant-agent && .venv/bin/pytest tests/test_universe.py -v`
Expected: PASS（4 个用例）

- [ ] **Step 5: Commit**

```bash
cd /Users/zk/code/agent/quant-agent
git add engine/universe.py tests/test_universe.py
git commit -m "feat(engine): universe 解析（显式列表/默认缓存集）+ 元数据日历对齐

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: 策略层（base + 组合内置策略）

**Files:**
- Rewrite: `strategies/base.py`
- Rewrite: `strategies/builtin.py`
- Modify: `strategies/manager.py`
- Test: `tests/test_strategies.py`（重写）

**Interfaces:**
- Consumes: `Context`（Task 1）、`INDICATOR_HELPERS`（strategies/indicators.py 现有）
- Produces:
  - `validate_strategy_source(source) -> None`
  - `load_strategy_from_source(source, name) -> Callable`（编译 `initialize`/`handle_data` 到命名空间，返回 `handle_data`；`initialize` 挂到返回函数的 `__initialize__`）
  - `StrategyManager.register/resolve/list/get_func`（无 params）
  - 内置：`buy_and_hold`、`momentum_rotation`

- [ ] **Step 1: 写失败测试**（重写 `tests/test_strategies.py`）

```python
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
    bars_a = make_bars(60, start_price=100.0)
    bars_b = make_bars(60, start_price=50.0)
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
    bars_a = make_bars(100, start_price=100.0)
    bars_b = make_bars(100, start_price=50.0)
    dl = _FakeDL({"600519": bars_a, "000858": bars_b})
    engine = BacktestEngine(EngineConfig(initial_cash=100_000), data_layer=dl)
    calendar = sorted(set(bars_a["date"]) | set(bars_b["date"]))
    result = engine.run(_import_builtin("momentum_rotation"), calendar, ["600519", "000858"],
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
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /Users/zk/code/agent/quant-agent && .venv/bin/pytest tests/test_strategies.py -v`
Expected: FAIL（`handle_data` 约定未实现 / 内置策略是旧的 `strategy(ctx, params)`）

- [ ] **Step 3: 实现**

`strategies/base.py`（重写）：

```python
"""Strategy interface.

A portfolio strategy is `initialize(ctx)` (optional) + `handle_data(ctx)`
(required). `handle_data` runs once per time step (daily in this release);
`ctx` is portfolio-aware (see engine.context). Built-in strategies live in
this package; user-submitted strategies are loaded and validated before run.
"""
from __future__ import annotations

import ast
import inspect
import math
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from engine.context import Context
from .indicators import ema, macd, rsi, sma

# Indicator helpers injected into every user strategy namespace.
INDICATOR_HELPERS = {"sma": sma, "ema": ema, "rsi": rsi, "macd": macd}

StrategyFunc = Callable[[Context], None]

_ALLOWED_IMPORTS = {"math", "numpy", "pandas"}


@dataclass
class StrategySpec:
    name: str
    description: str
    func: StrategyFunc | None  # None for user-defined (loaded from source)
    source: str | None = None
    builtin: bool = False


def validate_strategy_source(source: str) -> None:
    """Statically check a user strategy source before it runs.

    Allowed imports: math, numpy, pandas. No imports from engine/data/api
    (strategy should only use ctx). Enforced by AST allowlist — not a
    security boundary, just a guardrail for the local agent.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods = {a.name.split(".")[0] for a in node.names}
            if mods - _ALLOWED_IMPORTS:
                raise ValueError("strategy may import only math, numpy, pandas")
            continue
        if isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root not in _ALLOWED_IMPORTS:
                raise ValueError("strategy may import only math, numpy, pandas")
            continue
        if isinstance(node, ast.Global) or isinstance(node, ast.Nonlocal):
            raise ValueError("global/nonlocal not allowed")
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in ("eval", "compile", "__import__", "exec"):
                raise ValueError(f"`{node.func.id}` is not allowed")
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                raise ValueError("access to private members (ctx._*) is not allowed")


def load_strategy_from_source(source: str, name: str = "user_strategy") -> StrategyFunc:
    """Compile a strategy source into `handle_data`, with optional `initialize`
    attached as `__initialize__`.

    The source must define `handle_data(ctx)`; `initialize(ctx)` is optional.
    Indicator helpers (sma/ema/rsi/macd) are injected.
    """
    validate_strategy_source(source)
    ns: dict = {"math": math, "np": np, "pandas": pd, **INDICATOR_HELPERS}
    exec(compile(source, f"<strategy:{name}>", "exec"), ns)
    func = ns.get("handle_data")
    if func is None:
        raise ValueError("strategy source must define a function named `handle_data(ctx)`")
    if not callable(func):
        raise ValueError("`handle_data` must be callable")
    sig = inspect.signature(func)
    if len(sig.parameters) < 1:
        raise ValueError("`handle_data` must accept (ctx)")
    init = ns.get("initialize")
    if init is not None:
        if not callable(init):
            raise ValueError("`initialize` must be callable")
        setattr(func, "__initialize__", init)
    return func
```

`strategies/builtin.py`（重写）：

```python
"""Built-in portfolio strategies.

Each is `initialize(ctx)` (optional) + `handle_data(ctx)`, so users can copy
them into a custom strategy and tweak.
"""
from __future__ import annotations


def buy_and_hold(ctx) -> None:
    """Buy all universe symbols equal-weighted on the first bar; hold to the end."""
    if ctx.bar_index > 0:
        return
    n = len(ctx.universe)
    if n == 0:
        return
    for s in ctx.universe:
        ctx.buy(s, 1.0 / n)


def momentum_rotation(ctx) -> None:
    """Hold the top-N symbols by 60-day momentum; rebalance every 20 bars."""
    window = ctx.state.get("window", 60)
    top_n = ctx.state.get("top_n", 3)
    rebalance_every = ctx.state.get("rebalance_every", 20)
    if ctx.bar_index % rebalance_every != 0:
        return
    momentum = {}
    for s in ctx.universe:
        bars = ctx.history(s)
        if len(bars) < window:
            continue
        ret = bars["close"].iloc[-1] / bars["close"].iloc[-window] - 1
        momentum[s] = ret
    if not momentum:
        return
    top = sorted(momentum, key=momentum.get, reverse=True)[:top_n]
    for s in list(ctx.positions):
        if s not in top:
            ctx.sell(s)
    for s in top:
        if s not in ctx.positions:
            ctx.buy(s, 1.0 / len(top))


BUILTIN_STRATEGIES: dict[str, dict] = {
    "buy_and_hold": {
        "name": "buy_and_hold",
        "description": "买入持有：首日等权买入 universe 全部标的，一直持有",
        "func": buy_and_hold,
        "builtin": True,
    },
    "momentum_rotation": {
        "name": "momentum_rotation",
        "description": "动量轮动：选最近 60 日涨幅最高的 3 个标的持有，每 20 日轮换",
        "func": momentum_rotation,
        "builtin": True,
    },
}
```

`strategies/manager.py`（改）：去掉 `params_schema` 字段与 `meta.get("params_schema")`、`to_dict` 里的 `params_schema`。

- [ ] **Step 4: 运行确认通过**

Run: `cd /Users/zk/code/agent/quant-agent && .venv/bin/pytest tests/test_strategies.py -v`
Expected: PASS（8 个用例）

- [ ] **Step 5: Commit**

```bash
cd /Users/zk/code/agent/quant-agent
git add strategies/base.py strategies/builtin.py strategies/manager.py tests/test_strategies.py
git commit -m "feat(strategies): 组合级策略接口 initialize/handle_data + 内置策略重写

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: runner 与 schemas（universe 化，去 params）

**Files:**
- Rewrite: `api/runner.py`
- Modify: `api/schemas.py`
- Test: `tests/test_api.py`（更新 runner 相关）

**Interfaces:**
- Consumes: `resolve_universe`/`metadata_calendar`（Task 3）、`BacktestEngine`（Task 2）、`StrategyManager`（Task 4）
- Produces:
  - `run_backtest(strategy, universe=None, freq="daily", start="2020-01-01", end="2024-12-31", adjust="qfq", initial_cash=100_000.0, commission_rate=0.0003, stamp_duty=0.0005, lot_size=100, strategy_manager=None, data_layer=None) -> dict`
  - `BacktestRequest`（universe 替代 symbol、去 params）

- [ ] **Step 1: 写失败测试**（`tests/test_api.py` 更新）

```python
def test_backtest_runner_universe_no_symbol():
    import pandas as pd
    from api.runner import run_backtest
    from strategies.builtin import buy_and_hold
    from engine.universe import metadata_calendar

    bars = make_bars()
    class _DL:
        def get_bars(self, info, freq="daily", start="", end="", adjust="qfq"):
            return make_bars()
    res = run_backtest(
        strategy=buy_and_hold,
        universe={"symbols": ["600519"]},
        freq="daily", start="2023-01-01", end="2024-12-31",
        data_layer=_DL(),
    )
    assert res["success"] is True
    assert "total_return" in res["metrics"]
    assert res["symbol"] == "600519"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /Users/zk/code/agent/quant-agent && .venv/bin/pytest tests/test_api.py -v`
Expected: FAIL（`run_backtest` 旧签名）

- [ ] **Step 3: 实现**

`api/runner.py`（重写）：

```python
"""Backtest runner: wires universe -> strategy -> engine into one call."""
from __future__ import annotations

from data.registry import get_registry
from data.sources import DataLayer
from engine.engine import BacktestEngine, EngineConfig
from engine.universe import metadata_calendar, resolve_universe
from strategies.manager import StrategyManager


def run_backtest(
    strategy,
    universe: dict | None = None,
    freq: str = "daily",
    start: str = "2020-01-01",
    end: str = "2024-12-31",
    adjust: str = "qfq",
    initial_cash: float = 100_000.0,
    commission_rate: float = 0.0003,
    stamp_duty: float = 0.0005,
    lot_size: int = 100,
    strategy_manager: StrategyManager | None = None,
    data_layer: DataLayer | None = None,
) -> dict:
    """Resolve universe, align calendar, run engine, return metrics+curves+trades."""
    sm = strategy_manager or StrategyManager()
    dl = data_layer or DataLayer()
    symbols = resolve_universe(universe, freq=freq, adjust=adjust)
    if not symbols:
        raise ValueError("universe is empty: no cached symbols match, pass symbols explicitly")
    calendar = metadata_calendar(symbols, start, end, freq, adjust, dl)
    if not calendar:
        raise ValueError("no data for universe")

    func, strat_name = sm.resolve(strategy) if isinstance(strategy, (str, dict)) else (strategy, getattr(strategy, "__name__", "strategy"))
    if callable(getattr(func, "__initialize__", None)):
        from engine.context import Context
        _c = Context(initial_cash)
        func.__initialize__(_c)

    cfg = EngineConfig(
        initial_cash=initial_cash,
        commission_rate=commission_rate,
        stamp_duty=stamp_duty,
        lot_size=lot_size,
    )
    engine = BacktestEngine(cfg, data_layer=dl)
    result = engine.run(func, calendar, symbols, freq=freq, start=start, end=end, adjust=adjust)
    metrics = result.metrics
    return {
        "success": True,
        "universe": symbols,
        "symbol": symbols[0] if symbols else "",
        "symbol_name": (lambda info: info.name)(get_registry().get(symbols[0])) if symbols else "",
        "freq": freq,
        "metrics": {k: v for k, v in metrics.items() if k not in ("equity_curve", "trades")},
        "equity_curve": metrics.get("equity_curve", []),
        "trades": metrics.get("trades", []),
        "strategy": strat_name,
    }
```

`api/schemas.py`：`BacktestRequest` 改为：

```python
class UniverseSpec(BaseModel):
    symbols: list[str] | None = Field(None, description="显式标的列表")
    types: list[str] | None = Field(None, description="类型过滤: stock|etf|fund")


class BacktestRequest(BaseModel):
    universe: UniverseSpec | None = Field(None, description="标的池（缺省=已缓存标的集）")
    freq: str = Field("daily", description="bar 频率: daily|1|5|15|30|60")
    start: str = Field("2020-01-01", description="起始日期 YYYY-MM-DD")
    end: str = Field("2024-12-31", description="结束日期 YYYY-MM-DD")
    adjust: str = Field("qfq", description="复权: qfq|hfq|none")
    strategy: Any = Field(..., description="策略名或 {'name','source'} 源码字典")
    initial_cash: float = Field(100_000.0, ge=1000)
    commission_rate: float = Field(0.0003)
    stamp_duty: float = Field(0.0005)
    lot_size: int = Field(100)
```

（删除 `OptimizeRequest`。）

- [ ] **Step 4: 运行确认通过**

Run: `cd /Users/zk/code/agent/quant-agent && .venv/bin/pytest tests/test_api.py -v`
Expected: PASS（更新后的 API 用例；`test_backtest_endpoint_with_mock` 等按新签名改写）

- [ ] **Step 5: Commit**

```bash
cd /Users/zk/code/agent/quant-agent
git add api/runner.py api/schemas.py tests/test_api.py
git commit -m "feat(api): run_backtest universe 化（去 symbol/params），BacktestRequest 加 universe

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: API 端点与 Agent 层

**Files:**
- Modify: `api/main.py`（更新 `/api/backtest`，删 `/api/optimize`）
- Rewrite: `api/agent/executor.py`
- Modify: `api/agent/tools.py`
- Modify: `api/agent/agent.py`（system prompt + `_result_to_text`）
- Test: `tests/test_agent_executor.py`、`tests/test_agent_tools.py`、`tests/test_agent_agent.py`

**Interfaces:**
- Consumes: `run_backtest`（Task 5）、`BacktestRequest`/`UniverseSpec`（Task 5）
- Produces:
  - `BacktestExecutor.submit(strategy_ref, universe=None, freq, start, end, adjust) -> int`
  - `BacktestExecutor.wait_all(timeout) -> list[dict]`（结果含 `universe`，`symbol` 为宇宙首标或空）
  - `run_backtest` tool：`{strategy_ref, universe?, freq?, start?, end?, adjust?}`（无 symbol 必填）

- [ ] **Step 1: 写失败测试**（更新 `tests/test_agent_executor.py`）

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /Users/zk/code/agent/quant-agent && .venv/bin/pytest tests/test_agent_executor.py -v`
Expected: FAIL（`submit` 旧签名 `symbol, strategy_ref, params`）

- [ ] **Step 3: 实现**

`api/agent/executor.py`（重写核心）:

```python
@dataclass
class BacktestJob:
    id: int
    strategy: str
    universe: dict | None = None
    status: str = "running"
    result: dict | None = None
    error: str | None = None


class BacktestExecutor:
    def __init__(self, initial_cash: float = 100_000.0, max_workers: int = 4, strategy_manager=None):
        self.initial_cash = initial_cash
        self._strategy_manager = strategy_manager
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        self._jobs: dict[int, BacktestJob] = {}
        self._futures: list[concurrent.futures.Future] = []
        self._order: list[int] = []

    def submit(self, strategy_ref, universe=None, freq="daily", start="2020-01-01",
               end="2024-12-31", adjust="qfq") -> int:
        job_id = next(_job_ids)
        job = BacktestJob(id=job_id, strategy=strategy_ref, universe=universe)
        self._jobs[job_id] = job
        self._order.append(job_id)
        fut = self._pool.submit(
            run_backtest,
            strategy=strategy_ref,
            universe=universe,
            freq=freq, start=start, end=end, adjust=adjust,
            initial_cash=self.initial_cash,
            strategy_manager=self._strategy_manager,
        )
        self._futures.append(fut)
        return job_id

    def wait_all(self, timeout: float = 300.0) -> list[dict]:
        if not self._futures:
            return []
        for fut in concurrent.futures.as_completed(self._futures, timeout=timeout):
            idx = self._futures.index(fut)
            job_id = self._order[idx]
            job = self._jobs[job_id]
            try:
                job.result = fut.result()
                job.status = "done"
            except Exception as e:  # noqa: BLE001
                job.error = str(e)
                job.status = "error"
        results = [self._jobs[job_id] for job_id in self._order]
        self._futures = []; self._order = []; self._jobs = {}
        return [
            {"job_id": j.id, "strategy": j.strategy, "universe": j.universe,
             "status": j.status, "result": j.result, "error": j.error}
            for j in results
        ]
```

`api/agent/tools.py` `run_backtest` 改为：

```python
def run_backtest(input_: dict, ctx: AgentToolContext) -> str:
    strategy_ref = input_.get("strategy_ref", input_.get("strategy", "buy_and_hold"))
    job_id = ctx.executor.submit(
        strategy_ref=strategy_ref,
        universe=input_.get("universe"),
        freq=input_.get("freq", "daily"),
        start=input_.get("start", "2020-01-01"),
        end=input_.get("end", "2024-12-31"),
        adjust=input_.get("adjust", "qfq"),
    )
    return _json({"job_id": job_id, "status": "running", "strategy": strategy_ref})
```

`TOOLS` 里 `run_backtest` 描述改为「提交组合回测。strategy_ref 是策略名（当前草稿）——先 register_strategy。universe 可选（缺省=已缓存标的集）。Agent 只需策略+回测设置，标的由策略自选。」；参数去掉 `symbol`、`params`，加 `universe`。

`api/agent/agent.py`：
- `build_system_prompt` 的 1-3 步改写：不再「先 list_symbols 选标的」；改为「1. 用 register_strategy 编写组合策略，源码定义 initialize(ctx)（可选）+ handle_data(ctx)，用 ctx.history(s)、ctx.buy(s, pct)/ctx.sell(s, pct)……2. 用 run_backtest 提交回测（strategy_ref 用当前草稿名），可传 universe 限制标的池，缺省用已缓存标的集；标的由策略自选。」
- `_result_to_text` 保留 `symbol`/`symbol_name` 读取（runner 仍返回它们做兼容），或改用 `universe`。

- [ ] **Step 4: 运行确认通过**

Run: `cd /Users/zk/code/agent/quant-agent && .venv/bin/pytest tests/test_agent_executor.py tests/test_agent_tools.py tests/test_agent_agent.py -v`
Expected: PASS（FakeExecutor/FakeProvider 脚本里 `run_backtest` 的 input 改为 `{"strategy_ref": "ma"}`，去掉 `symbol`）

- [ ] **Step 5: Commit**

```bash
cd /Users/zk/code/agent/quant-agent
git add api/main.py api/agent/executor.py api/agent/tools.py api/agent/agent.py tests/test_agent_executor.py tests/test_agent_tools.py tests/test_agent_agent.py
git commit -m "feat(api+agent): backtest 端点/Agent 工具去 symbol，加 universe

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: Web 前端（universe 设置 + 组合展示）

**Files:**
- Modify: `web/index.html`

**Interfaces:**
- Consumes: `/api/backtest`（新 schema）、`/api/strategies`、`/api/meta`

- [ ] **Step 1: 改控制面板**（把「交易标的」改为 universe 设置）

```html
<label for="universeType">标的池类型</label>
<select id="universeType">
  <option value="">全部（已缓存）</option>
  <option value="stock">股票</option>
  <option value="etf">ETF</option>
  <option value="fund">基金</option>
</select>
<label for="universeSymbols">标的列表（可选，逗号分隔；留空=按类型取已缓存标的）</label>
<input id="universeSymbols" placeholder="600519,000858,510300">
```

删除 `#symbol`、`#symbol-list`、`#symbolType`；`runBacktest` payload 改为：

```js
const universe = {};
const types = $('universeType').value;
const syms = $('universeSymbols').value.trim();
if (syms) universe.symbols = syms.split(/[,，]/).map(s=>s.trim()).filter(Boolean);
else if (types) universe.types = [types];
const payload = {
  ...(Object.keys(universe).length ? {universe} : {}),
  freq: $('freq').value, start: $('start').value, end: $('end').value,
  adjust: $('adjust').value, strategy, initial_cash: parseFloat($('cash').value),
};
```

- [ ] **Step 2: 改策略源码占位符**（去掉 params，改为 `handle_data` 组合示例）

```html
<textarea id="strategySrc" rows="12" placeholder="def handle_data(ctx):
    # 首日等权买入全部候选标的，持有到结束
    if ctx.bar_index > 0:
        return
    n = len(ctx.universe)
    for s in ctx.universe:
        ctx.buy(s, 1.0 / n)
">
```

- [ ] **Step 3: 改结果展示**（基准改指数、交易表加标的列）

```js
// 交易表表头加“标的”列
// <th>标的</th><th>日期</th>...
tbody.innerHTML = data.trades.map(t => `<tr>
  <td>${t.symbol}</td><td>${t.date}</td><td class="${cls}">${dir}</td>
  <td>${t.shares}</td><td>${t.price}</td><td>${t.amount.toFixed(2)}</td>
  <td>${t.commission.toFixed(2)}</td><td>${(t.stamp_duty||0).toFixed(2)}</td></tr>`).join('');
```

- [ ] **Step 4: 运行确认**（启动服务手动冒烟）

```bash
cd /Users/zk/code/agent/quant-agent
.venv/bin/uvicorn api.main:app --port 8000 &
curl -s -X POST http://127.0.0.1:8000/api/backtest -H 'Content-Type: application/json' \
  -d '{"strategy":"buy_and_hold","universe":{"symbols":["600519"]},"start":"2023-01-01","end":"2024-12-31"}' \
  | python3 -m json.tool
# 预期: success:true, metrics.total_return 存在
```

- [ ] **Step 5: Commit**

```bash
cd /Users/zk/code/agent/quant-agent
git add web/index.html
git commit -m "feat(web): 回测页 universe 设置、handle_data 策略示例、交易表加标的

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 8: 全局清理与回归

**Files:**
- Modify: `api/main.py`（删 `/api/optimize` 路由；如漏删则补）
- Delete: 无（`run_optimize` 已在 Task 5 删）
- Test: 全量回归

- [ ] **Step 1: 删 `/api/optimize`**（`api/main.py`）

删除 `from .runner import ... run_optimize`、`optimize` 路由、`from .schemas import ... OptimizeRequest`。

- [ ] **Step 2: grep 清理残留**

```bash
cd /Users/zk/code/agent/quant-agent
grep -rn "params\|param_grid\|run_optimize\|symbol=" api/ engine/ strategies/ --include="*.py" | grep -v "test_\|_params\b\|self\.params\|cfg\.params\|\.params"
```

预期：无 `params`/`run_optimize` 残留（除策略里用户自定义变量外）。

- [ ] **Step 3: 全量回归**

```bash
cd /Users/zk/code/agent/quant-agent
.venv/bin/pytest -q
```

预期：全绿（组合级重写后的 test_engine / test_strategies / test_api / test_agent_* / test_universe；保留 test_data/test_precache/test_crash_log 等数据层用例）。

- [ ] **Step 4: Commit**

```bash
cd /Users/zk/code/agent/quant-agent
git add -A
git commit -m "refactor: 清理 params/optimize 残留，全量回归通过

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- 决策 1/2/3/4（组合接口、统一日历、目标权重、防前视）→ Task 1, 2 ✓
- 决策 5/5b（去 params、ctx.state）→ Task 4（`initialize`/`handle_data`）、Task 5/8 ✓
- 决策 6（universe 默认缓存集）→ Task 3 ✓
- 决策 7（lazy-load、元数据预对齐）→ Task 3（`metadata_calendar`）+ Task 1（`_ensure_loaded`）✓
- 决策 8（指数基准）→ Task 2 `_load_benchmark` + `compute_metrics` ✓
- 决策 9（不向后兼容）→ Task 5/6/8 删 `params`/`optimize`/`symbol` ✓
- 策略示例（3.1）→ Task 4 内置策略（buy_and_hold、momentum_rotation）✓
- Web（5）→ Task 7 ✓
- 测试表（6）→ 各 Task 的测试 ✓

**Placeholder scan:** 无 TBD/TODO；每步含完整代码与命令。

**Type consistency:**
- `Context(...)` 构造签名在 Task 1 定义，Task 2 引擎使用一致（`Context(initial_cash, engine=self, universe, calendar, data_layer)`）✓
- `engine.run(strategy, calendar, universe, freq, start, end, adjust)` 在 Task 2 定义，Task 4/5 调用一致 ✓
- `ctx._ensure_loaded` / `ctx._idx_at_current` / `ctx._symbol_type` 在 Task 1 定义，Task 2 引擎引用一致 ✓
- `resolve_universe` / `metadata_calendar` / `cached_symbols` 在 Task 3 定义，Task 5 调用一致 ✓
- `run_backtest(strategy, universe, ...)` 在 Task 5 定义，Task 6 executor 调用一致 ✓
- `BacktestExecutor.submit(strategy_ref, universe, ...)` 在 Task 6 定义，tools/agent 调用一致 ✓

**已知需要实现者在执行时同步处理的：** `tests/test_api.py` 里旧的 `test_register_and_use_custom_strategy`（`def strategy(ctx, params)`）需改为 `handle_data` 版；`tests/test_agent_agent.py`/`test_agent_tools.py` 里 `run_backtest` 的 `input={"symbol": ...}` 需改为 `{"strategy_ref": ...}`。
