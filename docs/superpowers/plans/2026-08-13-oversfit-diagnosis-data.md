# 防过拟合 + 深度诊断 + 数据丰富度 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 LLM 策略优化加防过拟合（训练/验证分段 + 发布自动期末考）、回测深度诊断、以及指数成分/行业分类数据能力。

**Architecture:** 引擎层扩展指标并新增 `engine/diagnose.py` 诊断模块；数据层新增 `data/indices.py`（指数成分）、`data/industry.py`（申万行业）；Agent 层在 `tools.py` 加查询/诊断工具、`run_backtest` 加训练段校验、`publish_strategy` 加验证段闸门，`gate.py` 推导验证段，`store.py` 记录验证段指标。验证段对 Agent 完全隐藏（无工具可运行/查看）。

**Tech Stack:** Python 3, pandas, numpy, akshare, fastapi, sqlite3。日期一律用 `"YYYY-MM-DD"` 字符串。

## Global Constraints

- 测试**不得联网**：akshare / DataLayer / run_backtest 一律用 monkeypatch 或 fake 注入。
- 日期格式统一 `"YYYY-MM-DD"` 字符串，比较用字符串字典序即可（ISO 格式天然有序）。
- 验证段日期**不得**出现在任何传给 LLM 的文本（系统提示 / `format_goal_text` / 工具描述）里——Agent 只知道"存在验证段"，不知道具体日期。
- 回撤是亏损指标：约束比较用 `|val| <= |threshold|`（正值阈值等价于负值）。
- 新增指标键（excess_return / calmar / sortino / turnover / avg_holdings / max_concentration / monthly_win_rate）必须与 spec 完全一致。
- 现有测试全部保持通过（`pytest -q` 绿色基线）。
- 每个任务结尾：跑相关测试全绿 + git commit。

---

### Task 1: 引擎指标扩展 + 持仓/集中度记录

**Files:**
- Modify: `engine/engine.py:81-93`（equity_rows 追加列）、`engine/engine.py:225-285`（compute_metrics 扩展）
- Test: `tests/test_engine.py`（新增用例）

**Interfaces:**
- Consumes: `engine/engine.py` 现有 `compute_metrics(equity_curve, trades) -> dict`。
- Produces: `equity_curve` DataFrame 新增 `n_positions`、`max_concentration` 两列；`compute_metrics` 返回 dict 新增键 `excess_return`、`calmar`、`sortino`、`turnover`、`avg_holdings`、`max_concentration`、`monthly_win_rate`。Task 9 的 `_result_to_text` 会读取这些键。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_engine.py` 末尾）

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_engine.py::test_extended_metrics_present -q`
Expected: FAIL（`assert 'excess_return' in m` 失败，key 不存在）

- [ ] **Step 3: 实现 engine.run 追加列**

在 `engine/engine.py` 的 `run()` 循环内、`equity_rows.append({...})` 之前插入持仓统计：

```python
        for i, day in enumerate(calendar):
            ctx.time = str(day)
            ctx.bar_index = i
            # corporate-action factor adjustment (per symbol, before strategy sees data)
            self._apply_corporate_actions(ctx)
            # strategy decides target weights; ctx.buy/sell fill IMMEDIATELY at
            # the current bar's close (eager matching) under A-share rules.
            # Built-ins sell-then-buy so cash from sells is available to buys.
            strategy(ctx)
            holdings = list(ctx.positions)
            n_positions = len(holdings)
            max_conc = 0.0
            if n_positions and ctx.total_value > 0:
                vals = [ctx.positions[s] * ctx.price(s) for s in holdings]
                max_conc = max(vals) / ctx.total_value
            equity_rows.append({
                "date": str(day),
                "cash": ctx.cash,
                "position": sum(ctx.positions.values()),
                "market_value": ctx.market_value,
                "equity": ctx.total_value,
                "benchmark": benchmark.get(str(day)),
                "n_positions": n_positions,
                "max_concentration": max_conc,
            })
```

- [ ] **Step 4: 实现 compute_metrics 扩展**

在 `compute_metrics` 内、`return {...}` 之前追加计算；把新键加进 return dict：

```python
    # ---- extended diagnosis metrics ----
    benchmark = eq["benchmark"].astype(float) if "benchmark" in eq.columns else None
    excess_return = 0.0
    if benchmark is not None and benchmark.notna().any():
        b = benchmark.dropna()
        if len(b) >= 2 and b.iloc[0] > 0:
            excess_return = total_return - (b.iloc[-1] / b.iloc[0] - 1)
    calmar = annual_return / abs(max_drawdown) if max_drawdown < 0 else 0.0
    sortino = 0.0
    if n > 2:
        rets = np.diff(equity) / equity[:-1]
        rets = rets[np.isfinite(rets)]
        downside = rets[rets < 0]
        if len(downside) > 1:
            dstd = np.std(downside, ddof=1)
            if dstd > 0:
                sortino = float(np.mean(rets) / dstd * np.sqrt(annual_factor))
    turnover = 0.0
    if trades and n:
        total_amount = sum(abs(float(t.get("amount", 0))) for t in trades)
        avg_equity = float(np.mean(equity))
        if avg_equity > 0:
            turnover = total_amount / len(equity) * 252.0 / avg_equity
    if "n_positions" in eq.columns and eq["n_positions"].notna().any():
        avg_holdings = float(np.mean(eq["n_positions"]))
    else:
        avg_holdings = 0.0
    if "max_concentration" in eq.columns and eq["max_concentration"].notna().any():
        max_concentration = float(eq["max_concentration"].max())
    else:
        max_concentration = 0.0
    monthly_win_rate = 0.0
    if n > 1:
        _df = eq.copy()
        _df["ym"] = pd.to_datetime(_df["date"]).dt.to_period("M")
        _monthly_end = _df.groupby("ym")["equity"].last()
        _monthly_ret = _monthly_end.pct_change().dropna()
        if len(_monthly_ret):
            monthly_win_rate = float((_monthly_ret > 0).mean())
    return {
        "total_return": total_return, "annual_return": annual_return,
        "max_drawdown": max_drawdown, "volatility": vol, "sharpe": sharpe,
        "n_trades": len(trades),
        "n_buys": sum(1 for t in trades if t["side"] == "buy"),
        "n_sells": sum(1 for t in trades if t["side"] == "sell"),
        "win_rate": win_rate, "final_equity": final, "initial_equity": initial,
        "excess_return": excess_return, "calmar": calmar, "sortino": sortino,
        "turnover": turnover, "avg_holdings": avg_holdings,
        "max_concentration": max_concentration, "monthly_win_rate": monthly_win_rate,
        "equity_curve": eq.to_dict("records"), "trades": trades,
    }
```

> 用 `_df` 避免遮蔽外层 `df`（compute_metrics 里没有 df，但保守起见不污染作用域）。

- [ ] **Step 5: 跑全量引擎测试确认通过**

Run: `pytest tests/test_engine.py -q`
Expected: 全部 PASS（含新增用例）

- [ ] **Step 6: Commit**

```bash
git add engine/engine.py tests/test_engine.py
git commit -m "feat(engine): 扩展指标集(excess/calmar/sortino/换手/持仓/月度胜率)+持仓列
```

---

### Task 2: 深度诊断模块 engine/diagnose.py

**Files:**
- Create: `engine/diagnose.py`
- Test: `tests/test_diagnose.py`（新建）

**Interfaces:**
- Produces: `engine.diagnose.diagnose(equity_curve, trades) -> dict`，键：`monthly_returns`（`{year: {month: return_pct}}`）、`drawdown_analysis`（`{max_drawdown, peak_date, trough_date, longest_drawdown_days}`）、`symbol_attribution`（`[{symbol, pnl, n_trades, max_single_loss, held_days}]`）、`holdings_history`（`[{date, n_positions}]`）、`benchmark_comparison`（`{year: {strategy_return, benchmark_return?}}`）。`equity_curve` 接受 DataFrame 或 `[{...}]` 记录列表。Task 7 的 `diagnose_backtest` 工具消费它。

- [ ] **Step 1: 写失败测试**（新建 `tests/test_diagnose.py`）

```python
"""Deep diagnosis module tests (pure pandas, no network)."""
from __future__ import annotations

import pandas as pd
import pytest

from engine.diagnose import diagnose


def _eq():
    return pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=40, freq="B").strftime("%Y-%m-%d"),
        "equity": [100.0] * 10 + [90.0] * 10 + [120.0] * 20,
        "benchmark": [100.0] * 40,
        "n_positions": [1] * 40,
        "max_concentration": [1.0] * 40,
    })


def _trades():
    return [
        {"date": "2025-01-02", "symbol": "600519", "side": "buy", "shares": 100,
         "price": 100.0, "amount": 10000.0, "commission": 5.0,
         "stamp_duty": 0.0, "transfer_fee": 0.0},
        {"date": "2025-02-10", "symbol": "600519", "side": "sell", "shares": 100,
         "price": 120.0, "amount": 12000.0, "commission": 5.0,
         "stamp_duty": 6.0, "transfer_fee": 0.1},
    ]


def test_diagnose_keys_present():
    d = diagnose(_eq(), _trades())
    assert set(d) == {"monthly_returns", "drawdown_analysis",
                      "symbol_attribution", "holdings_history", "benchmark_comparison"}


def test_diagnose_drawdown_and_attribution():
    d = diagnose(_eq(), _trades())
    assert d["drawdown_analysis"]["max_drawdown"] < 0
    assert d["drawdown_analysis"]["peak_date"] < d["drawdown_analysis"]["trough_date"]
    sym = next(s for s in d["symbol_attribution"] if s["symbol"] == "600519")
    assert sym["n_trades"] == 2
    assert sym["pnl"] == pytest.approx(12000 - 5 - 6 - 0.1 - (10000 + 5), abs=0.01)
    assert sym["held_days"] == 1


def test_diagnose_accepts_record_list():
    rows = _eq().to_dict("records")
    d = diagnose(rows, _trades())
    assert "monthly_returns" in d


def test_diagnose_empty_equity():
    d = diagnose([], [])
    assert d == {"error": "no equity data"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_diagnose.py -q`
Expected: FAIL（`ModuleNotFoundError: engine.diagnose`）

- [ ] **Step 3: 创建 `engine/diagnose.py`**

```python
"""Deep backtest diagnosis: monthly returns, drawdown, symbol attribution.

`diagnose()` produces the breakdown a strategy agent needs to understand WHY a
backtest missed its goal — fed by `diagnose_backtest` (api/agent/tools.py).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _as_frame(equity_curve) -> pd.DataFrame:
    if isinstance(equity_curve, pd.DataFrame):
        return equity_curve.copy()
    return pd.DataFrame(equity_curve or [])


def _monthly_returns(eq: pd.DataFrame) -> dict:
    df = eq.copy()
    df["ym"] = pd.to_datetime(df["date"]).dt.to_period("M")
    ends = df.groupby("ym")["equity"].last()
    rets = ends.pct_change().dropna()
    out: dict = {}
    for ym, r in rets.items():
        out.setdefault(str(ym.year), {})[int(ym.month)] = round(float(r), 6)
    return out


def _drawdown_analysis(eq: pd.DataFrame) -> dict:
    equity = eq["equity"].astype(float).values
    dates = eq["date"].tolist()
    peak = np.maximum.accumulate(equity)
    dd = equity / peak - 1
    max_i = int(np.argmin(dd))
    peak_i = int(np.argmax(equity[:max_i + 1]))
    longest = cur = 0
    for v in dd:
        if v < 0:
            cur += 1
            longest = max(longest, cur)
        else:
            cur = 0
    return {
        "max_drawdown": round(float(dd[max_i]), 6),
        "peak_date": dates[peak_i],
        "trough_date": dates[max_i],
        "longest_drawdown_days": longest,
    }


def _symbol_attribution(trades: list[dict]) -> list[dict]:
    per_sym: dict[str, dict] = {}
    for t in trades:
        sym = t.get("symbol", "?")
        d = per_sym.setdefault(sym, {"buys": [], "pnl": 0.0, "n_trades": 0,
                                     "max_single_loss": 0.0, "held_days": set()})
        d["n_trades"] += 1
        if t.get("side") == "buy":
            d["buys"].append({"shares": int(t["shares"]), "price": float(t["price"])})
            d["held_days"].add(str(t.get("date", "")))
        else:
            remaining = int(t["shares"])
            cost = 0.0
            while remaining > 0 and d["buys"]:
                b = d["buys"][0]
                take = min(remaining, b["shares"])
                cost += take * b["price"]
                remaining -= take
                b["shares"] -= take
                if b["shares"] == 0:
                    d["buys"].pop(0)
            fees = (float(t.get("commission", 0)) + float(t.get("stamp_duty", 0))
                    + float(t.get("transfer_fee", 0)))
            pnl = float(t.get("amount", 0)) - cost - fees
            d["pnl"] += pnl
            if pnl < d["max_single_loss"]:
                d["max_single_loss"] = pnl
    return [
        {"symbol": s, "pnl": round(v["pnl"], 2), "n_trades": v["n_trades"],
         "max_single_loss": round(v["max_single_loss"], 2),
         "held_days": len(v["held_days"])}
        for s, v in sorted(per_sym.items())
    ]


def _holdings_history(eq: pd.DataFrame) -> list[dict]:
    if "n_positions" not in eq.columns:
        return []
    return [{"date": str(d), "n_positions": int(n)}
            for d, n in zip(eq["date"], eq["n_positions"]) if pd.notna(n)]


def _benchmark_comparison(eq: pd.DataFrame) -> dict:
    df = eq.copy()
    df["year"] = pd.to_datetime(df["date"]).dt.year
    out: dict = {}
    for year, g in df.groupby("year"):
        strat = float(g["equity"].iloc[-1]) / float(g["equity"].iloc[0]) - 1
        row = {"strategy_return": round(strat, 6)}
        if "benchmark" in g.columns:
            b = g["benchmark"].astype(float)
            b = b[b.notna()]
            if len(b) >= 2 and b.iloc[0] > 0:
                row["benchmark_return"] = round(float(b.iloc[-1] / b.iloc[0] - 1), 6)
        out[int(year)] = row
    return out


def diagnose(equity_curve, trades: list[dict] | None = None) -> dict:
    """Deep diagnosis of a backtest result.

    `equity_curve` may be a DataFrame or a list of record dicts (as returned in
    a backtest response). Returns {} style dict with monthly returns, drawdown
    analysis, per-symbol attribution, holdings history and benchmark comparison.
    """
    eq = _as_frame(equity_curve)
    if eq.empty:
        return {"error": "no equity data"}
    trades = trades or []
    return {
        "monthly_returns": _monthly_returns(eq),
        "drawdown_analysis": _drawdown_analysis(eq),
        "symbol_attribution": _symbol_attribution(trades),
        "holdings_history": _holdings_history(eq),
        "benchmark_comparison": _benchmark_comparison(eq),
    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_diagnose.py -q`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add engine/diagnose.py tests/test_diagnose.py
git commit -m "feat(engine): 深度诊断模块 diagnose(月度收益/回撤/标的归因/持仓/基准对比)
```

---

### Task 3: 指数成分数据层 data/indices.py

**Files:**
- Create: `data/indices.py`
- Test: `tests/test_data.py`（追加用例）

**Interfaces:**
- Produces: `data.indices.INDEX_NAMES`（`{code: name}`）、`data.indices.INDEX_ALIASES`（`{中文名: code}`）、`data.indices.resolve_index(name_or_code) -> str | None`、`data.indices.list_indices() -> [{"code", "name"}]`、`data.indices.index_constituents(code, force=False) -> [{"code", "name", "weight"}]`（结果缓存到 `data/cache/index_{code}.json`）。Task 7 的 `list_symbols` / `query_sector_perf` 消费。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_data.py` 末尾）

```python
def test_resolve_index_alias_and_code():
    from data.indices import resolve_index
    assert resolve_index("沪深300") == "000300"
    assert resolve_index("000300") == "000300"
    assert resolve_index("不存在的指数") is None


def test_index_constituents_parses_and_caches(monkeypatch, tmp_path):
    import data.indices as idx_mod
    monkeypatch.setattr(idx_mod, "INDEX_DIR", str(tmp_path))
    import pandas as pd
    df = pd.DataFrame({
        "日期": ["2026-08-13"], "指数代码": ["000300"], "指数名称": ["沪深300"],
        "成分券代码": ["600519", "000001"], "成分券名称": ["贵州茅台", "平安银行"],
        "权重": [5.0, 3.2],
    })
    calls = {"n": 0}

    def fake_cons(symbol):
        calls["n"] += 1
        assert symbol == "000300"
        return df

    monkeypatch.setattr("akshare.index_stock_cons_weight_csindex", fake_cons)
    out = idx_mod.index_constituents("000300")
    assert [c["code"] for c in out] == ["600519", "000001"]
    assert out[0]["name"] == "贵州茅台"
    assert out[0]["weight"] == 5.0
    # 二次调用命中缓存，不重复抓取
    idx_mod.index_constituents("000300")
    assert calls["n"] == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_data.py::test_index_constituents_parses_and_caches -q`
Expected: FAIL（`ModuleNotFoundError: data.indices`）

- [ ] **Step 3: 创建 `data/indices.py`**

```python
"""Index constituents + cache. Uses the csindex (中证指数) endpoint.

Index names in goals ("沪深300成分") resolve to a canonical index code; the
agent expands an index to its constituent stock list via list_symbols(index=...).
"""
from __future__ import annotations

import json
import os

INDEX_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "cache")

INDEX_NAMES: dict[str, str] = {
    "000300": "沪深300",
    "000905": "中证500",
    "000016": "上证50",
    "399006": "创业板指",
    "000852": "中证1000",
    "000922": "中证红利",
}

INDEX_ALIASES: dict[str, str] = {
    "沪深300": "000300",
    "沪深300成分": "000300",
    "中证500": "000905",
    "中证500成分": "000905",
    "上证50": "000016",
    "创业板指": "399006",
    "创业板": "399006",
    "中证1000": "000852",
    "中证红利": "000922",
}


def resolve_index(name_or_code: str) -> str | None:
    """Map an index name (沪深300) or code (000300) to a canonical code."""
    if name_or_code in INDEX_ALIASES:
        return INDEX_ALIASES[name_or_code]
    if name_or_code in INDEX_NAMES:
        return name_or_code
    return None


def list_indices() -> list[dict]:
    return [{"code": c, "name": n} for c, n in INDEX_NAMES.items()]


def index_constituents(code: str, force: bool = False) -> list[dict]:
    """Constituent stocks of an index: [{code, name, weight}]. Cached to JSON."""
    if code not in INDEX_NAMES:
        raise ValueError(f"unknown index: {code}")
    path = os.path.join(INDEX_DIR, f"index_{code}.json")
    if not force and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001 - stale/broken cache -> refetch
            pass
    import akshare as ak

    df = ak.index_stock_cons_weight_csindex(symbol=code)
    out = []
    for _, r in df.iterrows():
        out.append({
            "code": str(r["成分券代码"]),
            "name": str(r["成分券名称"]),
            "weight": float(r["权重"]) if r["权重"] else 0.0,
        })
    try:
        os.makedirs(INDEX_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
    except Exception:  # noqa: BLE001 - cache write is best-effort
        pass
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_data.py -q`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add data/indices.py tests/test_data.py
git commit -m "feat(data): 指数成分数据层(中证指数+缓存+别名解析)
```

---

### Task 4: 行业分类数据层 data/industry.py

**Files:**
- Create: `data/industry.py`
- Test: `tests/test_data.py`（追加用例）

**Interfaces:**
- Produces: `data.industry.list_industries(force=False) -> [{"code", "name", "n_stocks"}]`（缓存到 `data/cache/sw_industries.json`）、`data.industry.industry_constituents(industry_code) -> [{"code", "name"}]`（best-effort，失败返回 `[]`）。Task 7 的 `list_industries` 工具 / `list_symbols(industry=...)` 消费。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_data.py` 末尾）

```python
def test_list_industries_parses_and_caches(monkeypatch, tmp_path):
    import data.industry as ind_mod
    monkeypatch.setattr(ind_mod, "INDUSTRY_DIR", str(tmp_path))
    import pandas as pd
    df = pd.DataFrame({"行业代码": ["801080.SI", "801150.SI"],
                       "行业名称": ["电子", "食品饮料"], "成份个数": [495, 120]})
    calls = {"n": 0}

    def fake_info():
        calls["n"] += 1
        return df

    monkeypatch.setattr("akshare.sw_index_first_info", fake_info)
    out = ind_mod.list_industries()
    assert out[0]["name"] == "电子"
    assert out[0]["n_stocks"] == 495
    ind_mod.list_industries()
    assert calls["n"] == 1


def test_industry_constituents_best_effort(monkeypatch):
    import data.industry as ind_mod
    import pandas as pd
    df = pd.DataFrame({"股票代码": ["600519"], "股票简称": ["贵州茅台"]})
    monkeypatch.setattr("akshare.sw_index_third_cons", lambda symbol: df)
    assert ind_mod.industry_constituents("801150") == [{"code": "600519", "name": "贵州茅台"}]

    def boom(symbol):
        raise RuntimeError("network down")

    monkeypatch.setattr("akshare.sw_index_third_cons", boom)
    assert ind_mod.industry_constituents("801150") == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_data.py::test_list_industries_parses_and_caches -q`
Expected: FAIL（`ModuleNotFoundError: data.industry`）

- [ ] **Step 3: 创建 `data/industry.py`**

```python
"""SW (申万) industry classification, best-effort.

`list_industries` reads the SW first-level list (31 sectors). Per-industry
constituents (`industry_constituents`) depend on an endpoint that may be
unavailable on flaky networks — it degrades to [] and the agent falls back to
`list_symbols(type=...)`.
"""
from __future__ import annotations

import json
import os

INDUSTRY_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "cache")
_INDUSTRY_FILE = os.path.join(INDUSTRY_DIR, "sw_industries.json")


def list_industries(force: bool = False) -> list[dict]:
    """SW first-level industries: [{code, name, n_stocks}]. Cached to JSON."""
    if not force and os.path.exists(_INDUSTRY_FILE):
        try:
            with open(_INDUSTRY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001 - stale cache -> refetch
            pass
    import akshare as ak

    df = ak.sw_index_first_info()
    out = [{"code": str(r["行业代码"]), "name": str(r["行业名称"]),
            "n_stocks": int(r["成份个数"])}
           for _, r in df.iterrows()]
    try:
        os.makedirs(INDUSTRY_DIR, exist_ok=True)
        with open(_INDUSTRY_FILE, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
    except Exception:  # noqa: BLE001 - cache write is best-effort
        pass
    return out


def industry_constituents(industry_code: str) -> list[dict]:
    """Constituent stocks of a SW industry: [{code, name}]. Best-effort —
    returns [] when the underlying endpoint is unavailable."""
    try:
        import akshare as ak

        df = ak.sw_index_third_cons(symbol=industry_code)
        if df is None or df.empty:
            return []
        return [{"code": str(r["股票代码"]), "name": str(r["股票简称"])}
                for _, r in df.iterrows()]
    except Exception:  # noqa: BLE001 - best-effort, degrade to []
        return []
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_data.py -q`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add data/industry.py tests/test_data.py
git commit -m "feat(data): 申万行业分类数据层(列表缓存+成分best-effort)
```

---

### Task 5: executor 保留已完成结果供诊断

**Files:**
- Modify: `api/agent/executor.py:27-31`（加 `_completed`）、`api/agent/executor.py:52-81`（wait_all 存结果、加 get_job）
- Test: `tests/test_agent_executor.py`（追加用例）

**Interfaces:**
- Produces: `BacktestExecutor.get_job(job_id) -> dict | None`（返回 wait_all 保留的 `{"job_id", "strategy", "universe", "status", "result", "error"}` 字典）。Task 7 的 `diagnose_backtest` 消费。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_agent_executor.py` 末尾）

```python
def test_get_job_returns_completed_result(monkeypatch):
    import api.agent.executor as mod
    monkeypatch.setattr(mod, "run_backtest", _dummy_backtest)
    ex = BacktestExecutor()
    try:
        j1 = ex.submit("buy_and_hold", universe={"symbols": ["600519"]})
        ex.wait_all(timeout=30)
        job = ex.get_job(j1)
        assert job is not None
        assert job["result"]["success"] is True
        assert ex.get_job(999999) is None
    finally:
        ex.shutdown()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_agent_executor.py::test_get_job_returns_completed_result -q`
Expected: FAIL（`AttributeError: 'BacktestExecutor' object has no attribute 'get_job'`）

- [ ] **Step 3: 实现**

在 `BacktestExecutor.__init__` 里加 `self._completed: dict[int, dict] = {}`：

```python
    def __init__(self, initial_cash: float = 100_000.0, max_workers: int = 4, strategy_manager=None):
        self.initial_cash = initial_cash
        self._strategy_manager = strategy_manager
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        self._jobs: dict[int, BacktestJob] = {}
        self._futures: list[concurrent.futures.Future] = []
        self._order: list[int] = []
        self._completed: dict[int, dict] = {}
```

在 `wait_all` 返回前把结果存进 `_completed` 并加容量上限：

```python
        results = [self._jobs[job_id] for job_id in self._order]
        self._futures = []
        self._order = []
        self._jobs = {}
        out = [
            {
                "job_id": j.id,
                "strategy": j.strategy,
                "universe": j.universe,
                "status": j.status,
                "result": j.result,
                "error": j.error,
            }
            for j in results
        ]
        # retain for diagnose_backtest; cap size so a long-running server
        # doesn't accumulate unboundedly.
        for r in out:
            self._completed[r["job_id"]] = r
        if len(self._completed) > 50:
            for k in sorted(self._completed)[:-50]:
                del self._completed[k]
        return out
```

新增方法：

```python
    def get_job(self, job_id: int) -> dict | None:
        """Return a previously completed job's result dict, or None."""
        return self._completed.get(job_id)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_agent_executor.py -q`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add api/agent/executor.py tests/test_agent_executor.py
git commit -m "feat(agent): executor 保留已完成 job 结果(get_job 供诊断工具)
```

---

### Task 6: gate 验证段推导 + 确认单展示

**Files:**
- Modify: `api/agent/gate.py`（`GoalExtraction` 加字段、新增 `derive_validation_periods`、`build_confirmation_summary`/`format_confirmation_text` 展示验证段）
- Modify: `api/agent/api.py:65-79`（`_extract_and_advance` 提取后推导验证段）
- Test: `tests/test_agent_gate.py`（追加用例）

**Interfaces:**
- Produces: `GoalExtraction.validation_periods: list[dict] | None`（`[{"start", "end"}]`）；`gate.derive_validation_periods(period, today=None) -> list[dict]`（训练段之后每年一段，最多 2 段，最后一段截断到 today；训练段已到 today → `[]`）。`build_confirmation_summary` 返回 dict 含 `validation_periods` 键；`format_confirmation_text` 输出含"验证段"行。Task 8 的 publish 闸门从 `ctx.goal["validation_periods"]` 读取。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_agent_gate.py` 末尾）

```python
from api.agent.gate import derive_validation_periods


def test_derive_validation_periods_two_years():
    vp = derive_validation_periods({"start": "2020-01-01", "end": "2024-12-31"}, today="2026-08-13")
    assert vp == [
        {"start": "2025-01-01", "end": "2025-12-31"},
        {"start": "2026-01-01", "end": "2026-08-13"},
    ]


def test_derive_validation_periods_training_at_today_empty():
    assert derive_validation_periods({"start": "2020-01-01", "end": "2026-12-31"}, today="2026-08-13") == []


def test_derive_validation_periods_missing_period_empty():
    assert derive_validation_periods(None, today="2026-08-13") == []


def test_confirmation_summary_has_validation_periods():
    ex = GoalExtraction(universe=["沪深300"], constraints={"annual_return": 0.10},
                        validation_periods=[{"start": "2025-01-01", "end": "2025-12-31"}])
    s = build_confirmation_summary(ex)
    assert s["validation_periods"] == [{"start": "2025-01-01", "end": "2025-12-31"}]


def test_format_confirmation_text_mentions_validation():
    ex = GoalExtraction(universe=["沪深300"], constraints={"annual_return": 0.10},
                        validation_periods=[{"start": "2025-01-01", "end": "2025-12-31"}])
    text = format_confirmation_text(build_confirmation_summary(ex))
    assert "验证段" in text
    assert "2025-01-01" in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_agent_gate.py -q`
Expected: FAIL（`ImportError: cannot import name 'derive_validation_periods'`）

- [ ] **Step 3: 实现 gate.py**

`GoalExtraction` 加字段（`dataclass`，放在 `benchmark` 之后）：

```python
@dataclass
class GoalExtraction:
    universe: list[str] | None = None
    constraints: dict[str, float] | None = None
    period: dict[str, str] | None = None
    benchmark: str | None = None
    validation_periods: list[dict] | None = None
    followup_question: str | None = None
```

新增推导函数（放在 `DEFAULT_BENCHMARK` 定义之后）：

```python
def derive_validation_periods(period: dict | None, today: str | None = None) -> list[dict]:
    """Derive validation periods after the training period (one per year, max 2).

    A validation period is a full calendar year after the training end; the
    last one is truncated to `today`. Returns [] when the training period ends
    at or after today (no unseen data yet) or when `period` is missing.
    """
    if not period:
        return []
    try:
        end_d = date.fromisoformat(period.get("end", ""))
    except (TypeError, ValueError):
        return []
    today_d = date.fromisoformat(today or date.today().isoformat())
    out: list[dict] = []
    year = end_d.year + 1
    while len(out) < 2 and year <= today_d.year:
        ys = date(year, 1, 1)
        ye = date(year, 12, 31)
        if ys > today_d:
            break
        if ye > today_d:
            ye = today_d
        if ys <= ye:
            out.append({"start": ys.isoformat(), "end": ye.isoformat()})
        year += 1
    return out
```

`build_confirmation_summary` 的 return dict 加一行：

```python
        "validation_periods": extraction.validation_periods or [],
```

`format_confirmation_text` 在"基准"行之后、确认提示之前加验证段行：

```python
    vp = summary.get("validation_periods") or []
    if vp:
        segs = "、".join(f"{v['start']}~{v['end']}" for v in vp)
        lines.append(f"• 验证段（期末考，Agent 不可见，发布时自动验收）：{segs}")
    else:
        lines.append("• 验证段：无（目标区间已到当前，无未见过的数据；建议选更早的目标区间以启用防过拟合期末考）")
    lines.append("回复「确认」开始执行，或告诉我需要修改的地方。")
```

- [ ] **Step 4: 实现 api.py 推导接线**

在 `api/agent/api.py` 顶部 import 加 `derive_validation_periods`：

```python
from .gate import (
    derive_validation_periods, format_confirmation_text, format_goal_text,
    gate_extract, gate_step, is_confirmed,
)
```

在 `_extract_and_advance` 的 `gate_extract(...)` 之后插入推导：

```python
def _extract_and_advance(session_id, message, history, goal, provider, bus, session_store) -> dict:
    extraction = gate_extract(message, history, provider, goal=goal)
    if extraction.period:
        extraction.validation_periods = derive_validation_periods(extraction.period)
    step_name, payload = gate_step(extraction)
```

> `extraction.to_dict()`（`asdict` + 过滤 None）会把 `validation_periods` 一并写进 `goal_json`，供 Task 8 的 publish 闸门读取。

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_agent_gate.py -q`
Expected: 全部 PASS（含新增 5 条）

- [ ] **Step 6: Commit**

```bash
git add api/agent/gate.py api/agent/api.py tests/test_agent_gate.py
git commit -m "feat(agent): gate 推导验证段并展示在确认单(对 Agent 隐藏具体日期)
```

---

### Task 7: tools 查询/诊断工具 + 训练段校验

**Files:**
- Modify: `api/agent/tools.py`（`AgentToolContext` 加 `training_period`；`list_symbols` 加 index/industry；新增 `list_industries`、`query_sector_perf`、`diagnose_backtest`；`run_backtest` 加训练段校验；`TOOLS` 更新）
- Test: `tests/test_agent_tools.py`（追加用例）

**Interfaces:**
- Consumes: `data.indices.{resolve_index, index_constituents}`、`data.industry.{list_industries as list_sw_industries, industry_constituents}`、`engine.diagnose.diagnose`、`BacktestExecutor.get_job`。
- Produces: `AgentToolContext.training_period: dict | None`（`{"start", "end"}`，Task 8 由 agent.run 设置）；工具函数 `list_industries(input_, ctx)`、`query_sector_perf(input_, ctx)`、`diagnose_backtest(input_, ctx)`；`run_backtest` 越界抛 `ValueError`。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_agent_tools.py` 末尾）

```python
def test_run_backtest_rejects_outside_training_period(tmp_path):
    c = _ctx(tmp_path)
    c.training_period = {"start": "2020-01-01", "end": "2024-12-31"}
    with pytest.raises(Exception):
        run_backtest({"strategy_ref": "ma", "start": "2025-01-01", "end": "2025-12-31"}, c)
    # 训练段内正常提交
    run_backtest({"strategy_ref": "ma", "start": "2021-01-01", "end": "2023-12-31"}, c)
    assert len(c.executor.submitted) == 1


def test_list_symbols_with_index(monkeypatch, tmp_path):
    import api.agent.tools as T
    monkeypatch.setattr(T, "index_constituents", lambda code: [
        {"code": "600519", "name": "贵州茅台", "weight": 5.0},
        {"code": "000001", "name": "平安银行", "weight": 3.0},
    ])
    c = _ctx(tmp_path)
    out = json.loads(T.list_symbols({"index": "000300"}, c))
    assert [s["code"] for s in out["symbols"]] == ["600519", "000001"]
    assert out["symbols"][0]["name"] == "贵州茅台"


def test_list_industries_tool(monkeypatch, tmp_path):
    import api.agent.tools as T
    monkeypatch.setattr(T, "list_sw_industries", lambda: [
        {"code": "801080.SI", "name": "电子", "n_stocks": 495},
    ])
    c = _ctx(tmp_path)
    out = json.loads(T.list_industries({}, c))
    assert out["industries"][0]["name"] == "电子"


def test_query_sector_perf(monkeypatch, tmp_path):
    import akshare as ak
    import pandas as pd
    df = pd.DataFrame({
        "date": ["2026-06-01", "2026-08-01"],
        "open": [1.0, 1.1], "high": [1.1, 1.2], "low": [0.9, 1.0],
        "close": [100.0, 110.0], "volume": [1, 1],
    })
    monkeypatch.setattr(ak, "stock_zh_index_daily", lambda symbol: df)
    c = _ctx(tmp_path)
    out = json.loads(list_industries if False else __import__("api.agent.tools", fromlist=["query_sector_perf"]).query_sector_perf({"code": "000300", "days": 60}, c))
    assert out["return_pct"] == pytest.approx(0.10)


def test_diagnose_backtest_tool(tmp_path):
    from api.agent.tools import diagnose_backtest

    class Ex:
        def __init__(self):
            self.jobs = {7: {"job_id": 7, "result": {
                "equity_curve": [{"date": "2025-01-02", "equity": 100.0, "n_positions": 1,
                                  "max_concentration": 1.0},
                                 {"date": "2025-01-03", "equity": 110.0, "n_positions": 1,
                                  "max_concentration": 1.0}],
                "trades": [],
            }}}
        def get_job(self, job_id):
            return self.jobs.get(job_id)

    c = AgentToolContext(store=None, executor=Ex())
    out = json.loads(diagnose_backtest({"job_id": 7}, c))
    assert "monthly_returns" in out
    assert "drawdown_analysis" in out
    assert "symbol_attribution" in out
```

> 上面的 `test_query_sector_perf` 写法别扭，实际实现时应先 `from api.agent.tools import query_sector_perf` 到模块顶部 import（见 Step 1 说明：该测试文件顶部已有 `from api.agent.tools import (...)`），把 `query_sector_perf` 加进那个 import 块即可，然后直接调用 `query_sector_perf(...)`。以下第 4 步给出最终形态。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_agent_tools.py -q`
Expected: 至少 FAIL（`query_sector_perf` / `diagnose_backtest` 未定义、`list_symbols` 无 index 参数）

- [ ] **Step 3: 实现 tools.py 顶部 import 与 AgentToolContext**

`api/agent/tools.py` 顶部追加 import：

```python
from data.indices import index_constituents, resolve_index
from data.industry import industry_constituents, list_industries as list_sw_industries
from data.registry import _exchange, get_registry
from data.sources import SymbolInfo
```

> 现在 `from data.registry import get_registry` 已在文件里，把 `_exchange` 加进同一行即可。

`AgentToolContext.__init__` 加 `training_period` 字段：

```python
class AgentToolContext:
    def __init__(self, store, executor, data_layer=None, strategy_manager=None,
                 session_id=None, training_period=None):
        self.store = store
        self.executor = executor
        self.data_layer = data_layer
        self.strategy_manager = strategy_manager or StrategyManager()
        self.session_id = session_id
        self.training_period = training_period
```

- [ ] **Step 4: 实现工具函数**

重写 `run_backtest`（在 submit 前校验训练段）：

```python
def run_backtest(input_: dict, ctx: AgentToolContext) -> str:
    strategy_ref = input_.get("strategy_ref", input_.get("strategy", "buy_and_hold"))
    start = input_.get("start", "2020-01-01")
    end = input_.get("end", "2024-12-31")
    tp = getattr(ctx, "training_period", None)
    if tp and not (tp.get("start", "") <= start and end <= tp.get("end", "")):
        raise ValueError(
            f"run_backtest 只能在训练段 {tp.get('start')}~{tp.get('end')} 内回测"
            "（防过拟合：验证段由系统在发布时自动验收，禁止直接运行）")
    job_id = ctx.executor.submit(
        strategy_ref=strategy_ref,
        universe=input_.get("universe"),
        freq=input_.get("freq", "daily"),
        start=start,
        end=end,
        adjust=input_.get("adjust", "qfq"),
    )
    return _json({"job_id": job_id, "status": "running", "strategy": strategy_ref})
```

重写 `list_symbols`（支持 index / industry）：

```python
def list_symbols(input_: dict, ctx: AgentToolContext) -> str:
    typ = input_.get("type")
    keyword = input_.get("keyword")
    index_code = input_.get("index")
    industry = input_.get("industry")
    items = None
    if index_code:
        code = resolve_index(index_code) or index_code
        items = [SymbolInfo(c["code"], c["name"], "stock", _exchange(c["code"]))
                 for c in index_constituents(code)]
    elif industry:
        items = [SymbolInfo(c["code"], c["name"], "stock", _exchange(c["code"]))
                 for c in industry_constituents(industry)]
    else:
        items = get_registry().list(typ)
    if keyword:
        kw = keyword.strip().lower()
        items = [s for s in items if kw in s.code.lower() or kw in s.name.lower()]
    items = items[:20]
    return _json({"symbols": [{"code": s.code, "name": s.name, "type": s.type} for s in items]})
```

新增三个工具函数（放在 `list_strategies` 之后）：

```python
def list_industries(input_: dict, ctx: AgentToolContext) -> str:
    return _json({"industries": list_sw_industries()})


def query_sector_perf(input_: dict, ctx: AgentToolContext) -> str:
    """近 N 日涨跌幅 for an index or SW industry. Best-effort."""
    code = input_.get("code")
    days = int(input_.get("days", 60))
    if not code:
        raise ValueError("code required (index code like 000300, or SW industry like 801080.SI)")
    index_code = resolve_index(code) or str(code).replace(".SI", "").replace(".si", "")
    prefix = "sz" if index_code.startswith("39") else "sh"
    import akshare as ak

    df = ak.stock_zh_index_daily(symbol=f"{prefix}{index_code}")
    df = df.tail(days)
    if df.empty:
        return _json({"code": code, "days": days, "return_pct": None})
    ret = float(df["close"].iloc[-1]) / float(df["close"].iloc[0]) - 1
    return _json({
        "code": code, "days": days,
        "return_pct": round(ret, 6),
        "start": str(df["date"].iloc[0]), "end": str(df["date"].iloc[-1]),
    })


def diagnose_backtest(input_: dict, ctx: AgentToolContext) -> str:
    job_id = int(input_.get("job_id"))
    job = ctx.executor.get_job(job_id)
    if job is None:
        raise KeyError(f"job {job_id} 不在已完成结果中（先 run_backtest 并等它完成）")
    result = job.get("result")
    if not result:
        raise ValueError(f"job {job_id} 无可用结果")
    from engine.diagnose import diagnose

    return _json(diagnose(result.get("equity_curve"), result.get("trades")))
```

- [ ] **Step 5: 更新 TOOLS 列表**

`run_backtest` 的 description 追加训练段约束；`list_symbols` 的 `properties` 加 `index`/`industry`；`publish_strategy` 的 `properties` 加 `universe`（description 注明验证闸门，见 Task 8，此处只加 schema）；`list_strategies` 之后插入三个新工具：

```python
    {
        "name": "list_industries",
        "description": "List SW (申万) first-level industries, each {code, name, n_stocks}. Use to pick a sector as the backtest universe.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "query_sector_perf",
        "description": "Return the trailing N-day return (%) of an index or SW industry (code: index code like 000300, or SW industry like 801080.SI). Use to see which sectors have been strong recently.",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Index code (000300) or SW industry code (801080.SI)."},
                "days": {"type": "integer", "description": "Trailing days, default 60."},
            },
            "required": ["code"],
        },
    },
    {
        "name": "diagnose_backtest",
        "description": "Deep-diagnose a completed backtest by job_id: monthly returns, drawdown peak/trough, per-symbol profit attribution, holdings history, benchmark comparison. Call when a backtest misses its goal to understand WHY before revising the strategy.",
        "parameters": {
            "type": "object",
            "properties": {
                "job_id": {"type": "integer", "description": "The job_id returned by run_backtest."},
            },
            "required": ["job_id"],
        },
    },
```

> `publish_strategy` 的 universe schema 在 Task 8 一并处理；此处只需新增上述三个工具条目，并把 `_tool_name_to_fn`（在 `api/agent/agent.py`）在 Task 9 补上这三个名字的映射。

- [ ] **Step 6: 修 query_sector_perf 测试为最终形态**

把 Step 1 里那个别扭的 `test_query_sector_perf` 替换为（顶部 import 加 `query_sector_perf`）：

```python
def test_query_sector_perf(monkeypatch, tmp_path):
    import akshare as ak
    import pandas as pd
    df = pd.DataFrame({
        "date": ["2026-06-01", "2026-08-01"],
        "open": [1.0, 1.1], "high": [1.1, 1.2], "low": [0.9, 1.0],
        "close": [100.0, 110.0], "volume": [1, 1],
    })
    monkeypatch.setattr(ak, "stock_zh_index_daily", lambda symbol: df)
    c = _ctx(tmp_path)
    out = json.loads(query_sector_perf({"code": "000300", "days": 60}, c))
    assert out["return_pct"] == pytest.approx(0.10)
    assert out["start"] == "2026-06-01"
```

并把 `from api.agent.tools import (...)` 顶部 import 块加入 `list_industries, query_sector_perf, diagnose_backtest`。

- [ ] **Step 7: 跑测试确认通过**

Run: `pytest tests/test_agent_tools.py -q`
Expected: 全部 PASS（含新增）

- [ ] **Step 8: Commit**

```bash
git add api/agent/tools.py tests/test_agent_tools.py
git commit -m "feat(agent): 查询/诊断工具(index成分/行业/涨跌/深度诊断)+run_backtest训练段校验
```

---

### Task 8: publish 验证闸门 + store 记录验证指标

**Files:**
- Modify: `api/agent/store.py`（`publish_version` 加 `validation_metrics`）
- Modify: `api/agent/tools.py`（`AgentToolContext` 加 `goal`/`validation_runner`；抽出 `evaluate_constraints`；`check_goal` 复用；新增 `validate_strategy_on_periods`、`make_validation_runner`；`publish_strategy` 加验证闸门）
- Test: `tests/test_agent_store.py`、`tests/test_agent_tools.py`（追加用例）

**Interfaces:**
- Consumes: `GoalExtraction.validation_periods`（在 `ctx.goal["validation_periods"]`）。
- Produces: `StrategyStore.publish_version(name, version, metrics, goal, validation_metrics=None)`（`validation_metrics` 并入 `metrics_json`，返回 `{"name", "version", "status", "metrics"}`）；`tools.evaluate_constraints(metrics, constraints) -> list[str]`；`tools.validate_strategy_on_periods(name, source, constraints, validation_periods, runner, universe=None) -> (validation_metrics, failures)`；`tools.make_validation_runner(ctx, name, source) -> runner(period, universe) -> metrics dict`。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_agent_store.py` 末尾：

```python
def test_publish_version_with_validation_metrics(tmp_path):
    s = StrategyStore(db_path=str(tmp_path / "t.db"))
    s.register_draft("ma", "def handle_data(ctx):\n    pass", "sma")
    pub = s.publish_version(
        "ma", 1, {"total_return": 0.2}, "年化>=10%",
        validation_metrics=[{"period": {"start": "2025-01-01", "end": "2025-12-31"},
                             "metrics": {"total_return": 0.1}}],
    )
    assert pub["status"] == "published"
    assert pub["metrics"]["validation_metrics"][0]["metrics"]["total_return"] == 0.1
    g = s.get_strategy("ma")
    assert g["versions"][0]["metrics"]["validation_metrics"][0]["period"]["start"] == "2025-01-01"
    s.close()
```

追加到 `tests/test_agent_tools.py` 末尾：

```python
def test_publish_validation_gate_rejects(tmp_path):
    c = _ctx(tmp_path)
    register_strategy({"name": "ma", "source": "def handle_data(ctx):\n    pass"}, c)
    c.goal = {"constraints": {"annual_return": 0.10},
              "validation_periods": [{"start": "2025-01-01", "end": "2025-12-31"}]}
    c.validation_runner = lambda period, universe: {"annual_return": 0.03}
    with pytest.raises(Exception) as e:
        publish_strategy({"name": "ma", "goal_met": True}, c)
    assert "验证段" in str(e.value)


def test_publish_validation_gate_passes(tmp_path):
    c = _ctx(tmp_path)
    register_strategy({"name": "ma", "source": "def handle_data(ctx):\n    pass"}, c)
    c.goal = {"constraints": {"annual_return": 0.10},
              "validation_periods": [{"start": "2025-01-01", "end": "2025-12-31"}]}
    c.validation_runner = lambda period, universe: {"annual_return": 0.12}
    out = json.loads(publish_strategy(
        {"name": "ma", "goal_met": True, "metrics": {"annual_return": 0.12}}, c))
    assert out["status"] == "published"
    assert out["metrics"]["validation_metrics"][0]["period"]["start"] == "2025-01-01"


def test_validate_strategy_on_periods_pure():
    from api.agent.tools import validate_strategy_on_periods
    vp = [{"start": "2025-01-01", "end": "2025-12-31"}]
    vm, fail = validate_strategy_on_periods(
        "ma", "def handle_data(ctx): pass", {"annual_return": 0.10}, vp,
        runner=lambda period, universe: {"annual_return": 0.05})
    assert fail == [{"period": vp[0], "unmet": ["annual_return: 0.05 < 0.1"]}]
    assert vm[0]["metrics"]["annual_return"] == 0.05
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_agent_store.py tests/test_agent_tools.py -q`
Expected: FAIL（`publish_version` 不接受 `validation_metrics`；`AgentToolContext` 无 `goal`/`validation_runner`；`publish_strategy` 无验证闸门）

- [ ] **Step 3: 实现 store.publish_version**

```python
    @_synchronized
    def publish_version(self, name: str, version: int, metrics: dict, goal: str,
                        validation_metrics: list[dict] | None = None) -> dict:
        sid_row = self.conn.execute(
            "SELECT id FROM strategies WHERE name = ?", (name,)
        ).fetchone()
        if sid_row is None:
            raise KeyError(f"strategy {name!r} has no version {version}")
        sid = sid_row["id"]
        row = self.conn.execute(
            "SELECT id FROM strategy_versions WHERE strategy_id = ? AND version = ?",
            (sid, version),
        ).fetchone()
        if row is None:
            raise KeyError(f"strategy {name!r} has no version {version}")
        store_metrics = dict(metrics)
        if validation_metrics:
            store_metrics["validation_metrics"] = validation_metrics
        now = _now()
        self.conn.execute(
            "UPDATE strategy_versions SET status='published', metrics_json=?, goal=?, published_at=? WHERE id=?",
            (json.dumps(store_metrics), goal, now, row["id"]),
        )
        self.conn.execute(
            "UPDATE strategies SET status='published', updated_at=? WHERE id=?",
            (now, sid),
        )
        self.conn.commit()
        return {"name": name, "version": version, "status": "published", "metrics": store_metrics}
```

- [ ] **Step 4: 实现 tools 的 constraints 抽取与验证闸门**

在 `api/agent/tools.py`，把 `check_goal` 里的约束判断抽成共享函数（`_DRAWDOWN_KEYS` 保留在文件里）：

```python
_DRAWDOWN_KEYS = ("max_drawdown",)


def evaluate_constraints(metrics: dict, constraints: dict) -> list[str]:
    """Return list of unmet constraint descriptions. Empty means all met."""
    unmet = []
    for key, threshold in constraints.items():
        val = metrics.get(key)
        if val is None:
            unmet.append(f"{key}: missing")
            continue
        if not isinstance(threshold, (int, float)):
            unmet.append(f"{key}: bad threshold")
            continue
        if key in _DRAWDOWN_KEYS:
            # max_drawdown is a loss metric: meet when |val| <= |threshold|
            if not abs(float(val)) <= abs(float(threshold)):
                unmet.append(f"{key}: |{val}| > |{threshold}|")
        else:
            if not float(val) >= float(threshold):
                unmet.append(f"{key}: {val} < {threshold}")
    return unmet


def check_goal(input_: dict, ctx: AgentToolContext) -> str:
    """LLM supplies metrics + constraints; code verifies each constraint."""
    metrics = input_.get("metrics", {})
    constraints = input_.get("constraints", {})
    unmet = evaluate_constraints(metrics, constraints)
    return _json({"met": not unmet, "unmet": unmet, "metrics": metrics})
```

新增纯函数与 runner 构造（放在 `check_goal` 之后）：

```python
def validate_strategy_on_periods(strategy_name, source, constraints, validation_periods,
                                 runner, universe=None):
    """Run the strategy on each validation period, check constraints.

    runner(period: dict, universe) -> backtest metrics dict.
    Returns (validation_metrics, failures):
      - validation_metrics: [{period, metrics}] for every period that ran
      - failures: [{period, unmet|error}] for every period that failed
    """
    validation_metrics = []
    failures = []
    for vp in validation_periods:
        try:
            metrics = runner(vp, universe)
        except Exception as e:  # noqa: BLE001 - surface per-period error
            failures.append({"period": vp, "error": str(e)})
            continue
        unmet = evaluate_constraints(metrics, constraints)
        validation_metrics.append({"period": vp, "metrics": metrics})
        if unmet:
            failures.append({"period": vp, "unmet": unmet})
    return validation_metrics, failures


def make_validation_runner(ctx, name, source):
    """Real validation runner: registers the strategy source and runs a backtest
    over a period via api.runner. Network-backed — tests inject ctx.validation_runner."""
    def runner(period, universe):
        ctx.strategy_manager.register(name, source)
        from api.runner import run_backtest

        res = run_backtest(
            strategy=name, universe=universe, freq="daily",
            start=period["start"], end=period["end"], adjust="qfq",
            initial_cash=ctx.executor.initial_cash,
            strategy_manager=ctx.strategy_manager,
        )
        return res["metrics"]
    return runner
```

`AgentToolContext.__init__` 加 `goal` / `validation_runner`（保持既有参数顺序，追加到末尾）：

```python
    def __init__(self, store, executor, data_layer=None, strategy_manager=None,
                 session_id=None, training_period=None, goal=None, validation_runner=None):
        self.store = store
        self.executor = executor
        self.data_layer = data_layer
        self.strategy_manager = strategy_manager or StrategyManager()
        self.session_id = session_id
        self.training_period = training_period
        self.goal = goal
        self.validation_runner = validation_runner
```

重写 `publish_strategy` 加验证闸门：

```python
def publish_strategy(input_: dict, ctx: AgentToolContext) -> str:
    if not input_.get("goal_met"):
        raise ValueError("cannot publish: goal not met")
    name = input_["name"]
    version = input_.get("version")
    g = ctx.store.get_strategy(name)
    if g is None:
        raise KeyError(f"unknown strategy: {name}")
    if version is None:
        version = g["current_version"]
    goal = getattr(ctx, "goal", None) or {}
    constraints = goal.get("constraints") or {}
    validation_periods = goal.get("validation_periods") or []
    source = ctx.store.get_source(name, int(version))
    universe = input_.get("universe")
    runner = getattr(ctx, "validation_runner", None) or make_validation_runner(ctx, name, source)
    validation_metrics, failures = validate_strategy_on_periods(
        name, source, constraints, validation_periods,
        runner=runner, universe=universe,
    )
    if failures:
        raise ValueError(
            "验证段不达标，拒绝发布（防过拟合）："
            + json.dumps(failures, ensure_ascii=False)
            + " 请回到训练段继续调优后再试")
    rec = ctx.store.publish_version(
        name, int(version),
        metrics=input_.get("metrics", {}),
        goal=input_.get("goal", ""),
        validation_metrics=validation_metrics,
    )
    return _json(rec)
```

更新 `publish_strategy` 的 TOOLS 描述与 universe 参数（原条目基础上改）：

```python
    {
        "name": "publish_strategy",
        "description": "Publish a strategy version ONLY when the goal is met AND all hidden validation periods pass. Requires goal_met=true. On publish the system automatically runs the strategy on every validation period (unseen by the agent); if any validation period misses the goal constraints, publish is rejected with the shortfalls. Pass the same universe you used for the winning training backtest.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Registered strategy name (the draft to publish)"},
                "version": {"type": "integer", "description": "Optional; defaults to current draft version"},
                "goal_met": {"type": "boolean", "description": "Must be true to publish"},
                "universe": {"type": "object", "description": "Optional; the universe the winning training backtest used, e.g. {\"symbols\": [...]}. Validation runs on this pool."},
                "metrics": {"type": "object", "description": "Metrics snapshot at publish time, e.g. the backtest metrics that met the goal"},
                "goal": {"type": "string", "description": "The user goal this version satisfies (recorded for the report)"},
            },
            "required": ["name", "goal_met"],
        },
    },
```

> 现在 `api/agent/tools.py` 顶部需要有 `import json`（`_json` 用了它，已有）；`publish_strategy` 里用 `json.dumps`，确认顶部 `import json` 存在。

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_agent_store.py tests/test_agent_tools.py tests/test_agent_agent.py -q`
Expected: 全部 PASS（含新增 4 条；既有 `test_publish_requires_goal_met` 不设 goal → 无验证段 → 照常发布）

- [ ] **Step 6: Commit**

```bash
git add api/agent/store.py api/agent/tools.py tests/test_agent_store.py tests/test_agent_tools.py
git commit -m "feat(agent): publish 验证段闸门+store 记录验证指标+constraints 抽取复用
```

---

### Task 9: 系统提示 + _result_to_text 扩指标 + 工具映射

**Files:**
- Modify: `api/agent/agent.py`（`build_system_prompt` 训练段约束/验证闸门/新工具；`_result_to_text` 扩指标；`_tool_name_to_fn` 加新工具；`run()` 设置 `ctx.goal`/`ctx.training_period`）
- Test: `tests/test_agent_agent.py`（追加用例）

**Interfaces:**
- Consumes: Task 7 的工具函数名、Task 1 的新指标键、Task 6 的 `goal.validation_periods`。
- Produces: `LLMAgent.run` 运行前 `self._ctx.goal = goal or {}`、`self._ctx.training_period = (goal or {}).get("period")`。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_agent_agent.py` 末尾）

```python
def test_system_prompt_training_only_and_validation_hidden():
    goal = {"constraints": {"annual_return": 0.10},
            "period": {"start": "2020-01-01", "end": "2024-12-31"},
            "validation_periods": [{"start": "2025-01-01", "end": "2025-12-31"}],
            "universe": ["沪深300"]}
    prompt = build_system_prompt(goal)
    assert "训练段" in prompt
    assert "验证段" in prompt
    assert "2025-01-01" not in prompt  # 验证段日期对 LLM 隐藏


def test_agent_run_sets_ctx_goal_and_training_period():
    from api.agent.tools import AgentToolContext
    from api.agent.agent import LLMAgent

    provider = FakeProvider([])
    agent = LLMAgent(provider=provider, store=FakeStore(), executor=FakeExecutor())
    goal = {"constraints": {"annual_return": 0.10},
            "period": {"start": "2020-01-01", "end": "2024-12-31"},
            "validation_periods": [{"start": "2025-01-01", "end": "2025-12-31"}]}
    agent.run("s1", "在沪深300做到年化10%", goal=goal)
    assert agent._ctx.goal == goal
    assert agent._ctx.training_period == {"start": "2020-01-01", "end": "2024-12-31"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_agent_agent.py -q`
Expected: FAIL（`build_system_prompt` 无"训练段/验证段"字样；`agent._ctx.training_period` 不存在）

- [ ] **Step 3: 实现 build_system_prompt**

把 `build_system_prompt` 里 workflow 的 2/3/4 行替换为：

```python
        "2. 用 run_backtest 提交组合回测（strategy_ref 用当前草稿名），可传 universe 限制标的池（缺省=已缓存标的集），可一次提交多个并行。回测只能跑训练段区间（即目标区间的 start~end）；验证段由系统在发布时自动验收，禁止（也无法）直接回测验证段。",
        "3. 查看回测指标，用 check_goal 校验是否达标；未达标可先用 diagnose_backtest(job_id) 深挖原因（月度收益、回撤起止、标的盈亏归因），再修改草稿重跑。",
        "4. 达标后必须用 publish_strategy（goal_met=true）发布。发布时系统自动在未见过的验证段上做期末考，任一验证段不达标都会拒绝发布并反馈差距，Agent 回到训练段继续调优。",
```

在 universe 那行（现有 `if goal.get("universe"):` 分支）的文本里追加指数/行业展开提示：

```python
            if goal.get("universe"):
                lines.append(f"标的池范围（策略在 universe 内自选标的）：{'、'.join(goal['universe'])}"
                             "（指数/行业名可用 list_symbols(index=...) 或 list_symbols(industry=...) 展开成具体标的；list_industries 可查行业）")
```

- [ ] **Step 4: 实现 _result_to_text 扩指标**

```python
def _result_to_text(res: dict) -> str:
    """Compact backtest result for LLM context."""
    r = res.get("result")
    if r is None:
        return f"回测 job #{res.get('job_id')} 失败: {res.get('error')}"
    m = r.get("metrics", {})
    keep = {k: m.get(k) for k in
            ("total_return", "annual_return", "max_drawdown", "sharpe", "volatility",
             "win_rate", "n_trades", "excess_return", "calmar", "sortino", "turnover",
             "avg_holdings", "max_concentration", "monthly_win_rate")}
    return json.dumps({
        "job_id": res.get("job_id"),
        "symbol": r.get("symbol"),
        "symbol_name": r.get("symbol_name"),
        "metrics": keep,
    }, ensure_ascii=False)
```

- [ ] **Step 5: 实现 _tool_name_to_fn 映射 + run() 设置 ctx**

```python
    def _tool_name_to_fn(self, name: str):
        import api.agent.tools as T
        return {
            "list_symbols": T.list_symbols,
            "run_backtest": T.run_backtest,
            "register_strategy": T.register_strategy,
            "list_strategies": T.list_strategies,
            "publish_strategy": T.publish_strategy,
            "check_goal": T.check_goal,
            "list_industries": T.list_industries,
            "query_sector_perf": T.query_sector_perf,
            "diagnose_backtest": T.diagnose_backtest,
        }[name]
```

在 `run()` 开头（`self._ctx.session_id = session_id` 之后）设置 goal 与训练段：

```python
        self._ctx.session_id = session_id
        self._ctx.goal = goal or {}
        self._ctx.training_period = (goal or {}).get("period")
```

- [ ] **Step 6: 跑测试确认通过**

Run: `pytest tests/test_agent_agent.py tests/test_agent_tools.py -q`
Expected: 全部 PASS

- [ ] **Step 7: Commit**

```bash
git add api/agent/agent.py tests/test_agent_agent.py
git commit -m "feat(agent): 系统提示训练段约束+验证闸门+新工具映射+回测指标扩充
```

---

## Self-Review

**Spec coverage:**
- 方向①防过拟合：训练段校验（Task 7 run_backtest）、验证段推导（Task 6 gate）、发布自动期末考 + 反馈（Task 8 publish 闸门）、验证段对 Agent 隐藏（Task 9 系统提示不暴露日期；无 run_validation 工具）。
- 方向②诊断：默认指标扩充（Task 1 compute_metrics + Task 9 _result_to_text）、`diagnose_backtest` 按需工具（Task 2 + Task 5 get_job + Task 7 工具）。
- 方向③数据：指数成分（Task 3 + Task 7 list_symbols/query_sector_perf）、行业分类（Task 4 + Task 7 list_industries）、gate universe 指数/行业（Task 6 确认单展示原文，Task 9 提示 Agent 用工具展开）。
- store 记录验证段指标（Task 8）。

**Placeholder scan:** 无 TBD/TODO；每个代码步骤含完整实现。

**Type consistency:** `validate_strategy_on_periods(name, source, constraints, validation_periods, runner, universe)` 在 Task 8 定义与测试一致；`publish_version(name, version, metrics, goal, validation_metrics)` 在 store 与 tools 调用一致；`get_job(job_id)` 在 executor 与 `diagnose_backtest` 一致；`evaluate_constraints` 被 `check_goal` 与 `validate_strategy_on_periods` 复用，签名一致。

**验证段日期隐藏检查:** `format_goal_text`（未改）不读 `validation_periods`；`build_system_prompt` 只读 period（训练段），不读验证段——Task 9 测试显式断言 `"2025-01-01" not in prompt`。
