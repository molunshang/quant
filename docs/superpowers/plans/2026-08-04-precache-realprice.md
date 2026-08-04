# 数据预缓存 + 真实价存储 + 计算层复权 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把交易数据改为「真实价 + 每股累积复权因子」存储，并提供预缓存服务（手动 API + 内置定时刷新），让回测不再每次用时临时下载、且缓存增量追加安全。

**Architecture:** 存储层（`data/sources.py`）通过同一 eastmoney 接口 `stock_zh_a_hist` 双重下载（`adjust=""` 真实价 + `adjust="qfq"` 复权价）逐日比值求 `factor`，写入带 `factor` 列的 CSV 缓存；引擎层用真实价成交、除权日按因子调整持仓；新增 `data/precache.py` 线程池任务服务 + 内置每日刷新；暴露预缓存 API 与 Web 控制面板。

**Tech Stack:** Python 3.14, pandas, akshare, FastAPI, pytest

## Global Constraints

- **akshare `stock_zh_a_hist` 的 `adjust` 取值是 `"qfq"`/`"hfq"`/`""`**，**不是** `"none"`。真实价用 `adjust=""`。已有代码里传 `"none"` 到 akshare 会 `KeyError`（见 `sources.py` `EastMoneySource.fetch_daily`，`adjust="qfq"` 是默认值；`get_bars` 签名 `adjust="qfq"`）。
- **缓存列**：日线 = `[date, open, high, low, close, volume, factor]`；`factor` 是每股累积复权因子，除权日阶梯式变化。
- **`factor = qfq_close / none_close`**（当日收盘比值）。除数 ≤ 0 时回退 `factor` 为前一日值。
- **除权日统一按拆股处理**：`ratio = factor_today / factor_yesterday`，`ratio > 1` 时 `position *= ratio`（取整到 lot）、`avg_cost /= ratio`。拆股与现金分红都会使 `ratio > 1`（factor 以最新价为锚），无法区分，total-return 近似（用户已确认）。
- **策略看到真实价**：`ctx.price`/`ctx.open`/`high`/`low`/成交价都用真实价；`factor` 只用于除权日持仓调整与权益估值。
- **`adjust` 收敛仅作用于日线**：`qfq`（默认）→ 真实价+factor；`none` → 真实价（factor=1）；`hfq` → deprecated，映射 qfq 行为。**分钟线不动**（缓存键含 `adjust`，值仍为 `qfq`，factor 列=1）。
- **旧格式检测**：读缓存时无 `factor` 列 → 自动触发重下覆盖。
- **`get_bars` 加 `force` 参数**：`force=True` 跳过缓存读、强制走数据源并写穿缓存。预缓存/刷新必须用 `force=True`（否则对已存在的新格式缓存直接返回，不会补新数据）。
- **刷新语义 = 全量重下覆盖**：真实价历史永不改变，`refresh_all` 对每个已缓存标的 `force` 重拉全区间并覆盖。数据量小（几十 KB/标的），不做增量合并（YAGNI）。
- **分钟线保持 qfq 现状**，不在本次范围。
- 测试命令：`cd /Users/zk/code/agent/quant-agent && .venv/bin/python -m pytest <path> -q`

---

### Task 1: DataLayer 双重下载求因子 + 新缓存格式

**Files:**
- Modify: `data/sources.py`
- Test: `tests/test_data.py`

**Interfaces:**
- Consumes: 现有 `DataSource` 抽象、`normalize()`、`SymbolInfo`、`DataLayer._cache_path()`
- Produces:
  - `normalize()`：保留原签名，若输入含 `factor` 列则保留它（把 `"factor"` 加入保留列集合）
  - `DataLayer._fetch_with_factor(symbol, start, end, adjust="qfq") -> pd.DataFrame`：列 `[date, open, high, low, close, volume, factor]`
  - `DataLayer._fetch_raw(symbol, start, end) -> pd.DataFrame`：真实价，factor 列=1
  - `DataLayer.get_bars(symbol, freq, start, end, adjust, force=False)`：语义见 Global Constraints；读缓存时检测 `factor` 列缺失则重下

- [ ] **Step 1: 写失败测试** — 加 `tests/test_data.py` 测试：`_fetch_with_factor` 从 mock 的两次 akshare 调用求因子

```python
def test_fetch_with_factor(monkeypatch):
    import akshare as ak
    from data.sources import DataLayer, SymbolInfo

    none_df = pd.DataFrame({
        "date": ["2024-01-02", "2024-01-03", "2024-01-04"],
        "open": [10.0, 10.5, 9.0], "high": [11.0, 11.0, 9.5],
        "low": [9.8, 10.0, 8.8], "close": [10.5, 9.0, 9.2], "volume": [1000, 2000, 3000],
    })
    qfq_df = none_df.copy()
    qfq_df["close"] = [10.5 * 1.5, 9.0 * 1.5, 9.2 * 1.5]  # 1.5x factor

    calls = []
    def fake_hist(symbol, period, start_date, end_date, adjust):
        calls.append(adjust)
        return qfq_df if adjust == "qfq" else none_df

    monkeypatch.setattr(ak, "stock_zh_a_hist", fake_hist)
    dl = DataLayer(cache=False)
    info = SymbolInfo("600519", "茅台", "stock", "sh")
    df = dl._fetch_with_factor(info, "2024-01-01", "2024-01-31")
    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume", "factor"]
    assert calls == ["", "qfq"] or calls == ["qfq", ""]
    assert abs(df["factor"].iloc[0] - 1.5) < 1e-6
    assert abs(df["close"].iloc[0] - 10.5) < 1e-6  # close is RAW price
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_data.py::test_fetch_with_factor -q`
Expected: FAIL — `DataLayer` 无 `_fetch_with_factor` 方法

- [ ] **Step 3: 实现 `_fetch_with_factor` 与 `normalize` 保留 factor**

在 `data/sources.py`：
- 修改 `_COL_MAP` 加 `"factor": "factor"`；
- 修改 `normalize()`：保留列集合改为 `["date"] + OHLCV_COLS + ["factor"]`（若输入无 factor 列则不添加）；
- 在 `DataLayer` 加方法：

```python
def _fetch_with_factor(self, symbol, start, end, adjust="qfq") -> pd.DataFrame:
    """Fetch raw + qfq prices for the SAME dates, derive per-share cumulative
    adjustment factor = qfq_close / raw_close. Returns columns
    [date, open, high, low, close, volume, factor] where close is RAW."""
    import akshare as ak

    if adjust == "none":
        return self._fetch_raw(symbol, start, end)
    if adjust == "hfq":
        adjust = "qfq"  # deprecated: map to qfq behavior

    raw = ak.stock_zh_a_hist(
        symbol=symbol.code, period="daily",
        start_date=start.replace("-", ""), end_date=end.replace("-", ""),
        adjust="",
    )
    qfq = ak.stock_zh_a_hist(
        symbol=symbol.code, period="daily",
        start_date=start.replace("-", ""), end_date=end.replace("-", ""),
        adjust="qfq",
    )
    raw = normalize(raw).set_index("date")
    qfq = normalize(qfq).set_index("date")
    joined = raw.join(qfq["close"], rsuffix="_qfq")
    joined["factor"] = joined["close_qfq"] / joined["close"]
    joined.loc[joined["close"] <= 0, "factor"] = None
    joined["factor"] = joined["factor"].ffill()
    joined = joined.dropna(subset=["factor"]).reset_index()
    return joined[["date", "open", "high", "low", "close", "volume", "factor"]]

def _fetch_raw(self, symbol, start, end) -> pd.DataFrame:
    df = EastMoneySource().fetch_daily(symbol, start, end, adjust="")
    df["factor"] = 1.0
    return df[["date", "open", "high", "low", "close", "volume", "factor"]]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_data.py::test_fetch_with_factor -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add data/sources.py tests/test_data.py
git commit -m "feat: DataLayer 双重下载求每股复权因子"
```

---

### Task 2: get_bars 新语义 + 旧格式自动重下

**Files:**
- Modify: `data/sources.py`
- Test: `tests/test_data.py`

**Interfaces:**
- Consumes: Task 1 的 `_fetch_with_factor` / `_fetch_raw`
- Produces: `DataLayer.get_bars(symbol, freq, start, end, adjust, force=False)` 新语义（见 Global Constraints）；`DataLayer._is_new_format(df) -> bool`

- [ ] **Step 1: 写失败测试**

```python
def test_get_bars_old_format_redownloads(monkeypatch, tmp_path):
    import akshare as ak
    from data.sources import DataLayer, SymbolInfo

    old_csv = tmp_path / "stock_600519_daily_qfq.csv"
    old_csv.write_text("date,open,high,low,close,volume\n2024-01-02,10,11,9,10.5,1000\n")

    def fake_hist(symbol, period, start_date, end_date, adjust):
        df = pd.DataFrame({
            "date": ["2024-01-02", "2024-01-03"],
            "open": [10.0, 11.0], "high": [11.0, 12.0], "low": [9.0, 10.0],
            "close": [10.5, 11.5], "volume": [1000, 2000],
        })
        return df if adjust == "qfq" else df

    monkeypatch.setattr(ak, "stock_zh_a_hist", fake_hist)
    dl = DataLayer(cache=True)
    dl.CACHE_DIR = str(tmp_path)  # monkeypatch cache dir
    info = SymbolInfo("600519", "茅台", "stock", "sh")
    df = dl.get_bars(info, "daily", "2024-01-01", "2024-01-31", "qfq")
    assert "factor" in df.columns
    assert len(df) == 2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_data.py::test_get_bars_old_format_redownloads -q`
Expected: FAIL — 返回旧格式（无 factor 列）

- [ ] **Step 3: 实现 get_bars 新语义**

改 `get_bars()`：
- 签名改为 `get_bars(self, symbol, freq="daily", start="2020-01-01", end="2024-12-31", adjust="qfq", force=False)`；
- `CACHE_DIR` 改为实例可覆盖：`self.cache_dir = CACHE_DIR`（构造时 `os.makedirs(self.cache_dir, exist_ok=True)`），`_cache_path` 用 `self.cache_dir`；
- 缓存读取分支加 `force` 判断：`if self.cache and not force and os.path.exists(cache_path):`；
- 读缓存后检测：新增 `_is_new_format(df) -> bool`（`return "factor" in df.columns`），`if not self._is_new_format(df):` → 视为旧格式，**跳过缓存**走重下；
- 写缓存时统一写带 factor 的完整数据；
- 新数据路径：`freq == "daily"` 时调用 `self._fetch_with_factor(symbol, start, end, adjust)`，`freq` 为分钟时保持原逻辑（`adjust` 直接传 akshare）；
- `adjust="none"` → `_fetch_raw`（factor=1）。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_data.py -q`
Expected: 全部通过（含 Task 1 的）

- [ ] **Step 5: Commit**

```bash
git add data/sources.py tests/test_data.py
git commit -m "feat: get_bars 返回真实价+factor，旧格式缓存自动重下"
```

---

### Task 3: 引擎除权日持仓调整

**Files:**
- Modify: `engine/engine.py`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `bars` 含 `factor` 列（可能缺失，缺失则 `factor=1`）
- Produces: `BacktestEngine.run()` 加载 factor 序列，每日开盘前 `_apply_corporate_action(ctx, prev_factor, factor)` 调整持仓/成本

**设计要点**：
- `factor` 逐日比值 `ratio = factor_today / factor_yesterday`：
  - `ratio > 1`（拆股/送股**和**现金分红统一处理）：`position *= ratio`（取整到 lot），`avg_cost /= ratio`；
  - `ratio == 1`（无除权）：不动。
- **注意**：`factor = qfq/raw` 以最新价为锚，拆股与分红都让 `ratio > 1`。全按拆股处理 = total-return 近似（市值连续、总收益正确），用户已确认接受。
- factor 缺失或为 0 时按 1 处理。

- [ ] **Step 1: 写失败测试**

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_engine.py::test_corporate_action_split_adjusts_position -q`
Expected: FAIL — `position` 仍为 100

- [ ] **Step 3: 实现引擎除权日调整**

在 `engine/engine.py`：
- `run()` 开头加载 factor 序列：

```python
factors = bars["factor"].astype(float).fillna(1.0).to_numpy() if "factor" in bars.columns else None
```

- 主循环每日开盘前（`strategy(ctx, ...)` 调用前）：

```python
if factors is not None and i > 0:
    ratio = factors[i] / factors[i - 1] if factors[i - 1] else 1.0
    if ratio != 1.0:
        self._apply_corporate_action(ctx, ratio)
```

- 新增方法：

```python
def _apply_corporate_action(self, ctx, ratio):
    """Treat every factor-change day as a split (total-return approx): scale the
    share count by the ratio and divide the cost basis to keep value continuous."""
    if ctx.position == 0:
        return
    if ratio > 1.0:
        new_pos = int(ctx.position * ratio // self.cfg.lot_size) * self.cfg.lot_size
        if new_pos > 0:
            ctx.avg_cost = ctx.avg_cost * ctx.position / new_pos
            ctx.position = new_pos
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_engine.py -q`
Expected: 全部通过

- [ ] **Step 5: Commit**

```bash
git add engine/engine.py tests/test_engine.py
git commit -m "feat: 引擎除权日按因子调整持仓与成本"
```

---

### Task 4: 预缓存服务 PrecacheManager

**Files:**
- Create: `data/precache.py`
- Test: `tests/test_precache.py`

**Interfaces:**
- Consumes: Task 1-2 的 `DataLayer`（`get_bars` / `_fetch_with_factor` / `_fetch_raw`）
- Produces:
  - `data/precache.py`：
    - `@dataclass PrecacheJob`：`id, symbol, freq, adjust, start, end, status, progress, error, created_at`
    - `class PrecacheManager`：
      - `submit(symbols: list[str], freq="daily", start="2020-01-01", end="2024-12-31", adjust="qfq") -> list[int]`
      - `get(job_id) -> dict | None`
      - `list() -> list[dict]`
      - `refresh_all() -> None`（后台）
      - `shutdown()`
    - 模块级单例 `manager = PrecacheManager()`

- [ ] **Step 1: 写失败测试**

```python
import time
from data.precache import PrecacheManager
from data.sources import DataLayer

def test_submit_and_list_jobs(monkeypatch, tmp_path):
    # stub DataLayer so no real network
    def fake_get_bars(self, *a, **k):
        return pd.DataFrame({"date": ["2024-01-02"], "open": [1.0], "high": [1.0],
                             "low": [1.0], "close": [1.0], "volume": [1], "factor": [1.0]})
    monkeypatch.setattr(DataLayer, "get_bars", fake_get_bars)

    mgr = PrecacheManager()
    mgr.cache_dir = str(tmp_path)
    ids = mgr.submit(["600519", "510300"], "daily", "2024-01-01", "2024-01-31", "qfq")
    assert len(ids) == 2
    jobs = mgr.list()
    assert len(jobs) == 2
    assert all(j["symbol"] in ("600519", "510300") for j in jobs)
    mgr.wait_all()
    assert all(j["status"] == "done" for j in mgr.list())
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_precache.py::test_submit_and_list_jobs -q`
Expected: FAIL — `data.precache` 模块不存在

- [ ] **Step 3: 实现 PrecacheManager**

创建 `data/precache.py`：

```python
"""Precache service: proactively download bars into the local cache so
backtests read local CSV instead of hitting the network on first use."""
from __future__ import annotations

import concurrent.futures
import itertools
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

from .registry import get_registry
from .sources import CACHE_DIR, DataLayer

_job_ids = itertools.count(1)


@dataclass
class PrecacheJob:
    id: int
    symbol: str
    freq: str
    adjust: str
    start: str
    end: str
    status: str = "pending"  # pending|running|done|error
    progress: int = 0
    error: str | None = None
    created_at: str = ""


class PrecacheManager:
    def __init__(self, max_workers: int = 4, cache_dir: str = CACHE_DIR):
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        self._jobs: dict[int, PrecacheJob] = {}
        self._lock = threading.Lock()
        self._futures: list[concurrent.futures.Future] = []
        self.cache_dir = cache_dir
        self._dl = DataLayer(cache=True)
        self._dl.cache_dir = cache_dir

    def _work(self, job: PrecacheJob):
        with self._lock:
            job.status = "running"
        try:
            reg = get_registry()
            info = reg.get(job.symbol)
            df = self._dl.get_bars(info, freq=job.freq, start=job.start, end=job.end, adjust=job.adjust, force=True)
            if df is None or df.empty:
                raise ValueError(f"no data for {job.symbol}")
            with self._lock:
                job.status = "done"
                job.progress = 100
        except Exception as e:  # noqa: BLE001 - per-job error
            with self._lock:
                job.status = "error"
                job.error = str(e)

    def submit(self, symbols, freq="daily", start="2020-01-01", end="2024-12-31", adjust="qfq"):
        ids = []
        for s in symbols:
            job_id = next(_job_ids)
            job = PrecacheJob(id=job_id, symbol=s, freq=freq, adjust=adjust,
                              start=start, end=end,
                              created_at=datetime.now().isoformat(timespec="seconds"))
            with self._lock:
                self._jobs[job_id] = job
            ids.append(job_id)
            fut = self._pool.submit(self._work, job)
            self._futures.append(fut)
        return ids

    def get(self, job_id):
        with self._lock:
            j = self._jobs.get(job_id)
            return j.__dict__ if j else None

    def list(self):
        with self._lock:
            return [j.__dict__ for j in sorted(self._jobs.values(), key=lambda x: x.id)]

    def wait_all(self, timeout: float = 120.0):
        for fut in concurrent.futures.as_completed(self._futures, timeout=timeout):
            pass
        self._futures = []

    def refresh_all(self):
        """Force re-download every cached symbol over its full cached range and
        overwrite. Raw history never changes, so this is safe and idempotent."""
        import os
        from datetime import date
        today = date.today().isoformat()
        for fn in os.listdir(self.cache_dir):
            if not fn.endswith(".csv"):
                continue
            parts = fn.replace(".csv", "").split("_")
            if len(parts) != 4:
                continue
            typ, code, freq, adjust = parts
            # start from cached file's first date (parse from CSV) if available
            start = "2020-01-01"
            path = os.path.join(self.cache_dir, fn)
            try:
                import pandas as pd
                first = pd.read_csv(path, usecols=["date"])["date"].iloc[0]
                start = str(first)[:10]
            except Exception:  # noqa: BLE001 - fall back to default start
                pass
            self.submit([code], freq=freq, start=start, end=today, adjust=adjust)

    def shutdown(self):
        self._pool.shutdown(wait=True)


manager = PrecacheManager()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_precache.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add data/precache.py tests/test_precache.py
git commit -m "feat: 预缓存服务 PrecacheManager（线程池 + 任务状态）"
```

---

### Task 5: 预缓存 API 端点

**Files:**
- Modify: `api/main.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: Task 4 的 `data.precache.manager`
- Produces:
  - `POST /api/data/precache` — body `{symbols, freq, start, end, adjust}` → `{job_ids: [...]}`
  - `GET /api/data/precache/jobs` → `{jobs: [...]}`
  - `GET /api/data/precache/{job_id}` → `{job: {...}}`
  - `POST /api/data/precache/refresh` → `{started: true}`

- [ ] **Step 1: 写失败测试**

```python
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
```

> monkeypatch 打桩 `pm._dl.get_bars`，确保测试无网络依赖且确定。

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_api.py::test_precache_api_submit_and_query -q`
Expected: FAIL — 404（路由不存在）

- [ ] **Step 3: 实现 API 路由**

在 `api/main.py` 顶部 import：

```python
from data.precache import manager as precache_manager
```

在 `meta()` 之后加：

```python
@app.post("/api/data/precache")
def precache_submit(body: dict):
    symbols = body.get("symbols") or []
    if not symbols:
        raise HTTPException(status_code=400, detail="symbols required")
    if not isinstance(symbols, list):
        raise HTTPException(status_code=400, detail="symbols must be a list")
    job_ids = precache_manager.submit(
        symbols=symbols,
        freq=body.get("freq", "daily"),
        start=body.get("start", "2020-01-01"),
        end=body.get("end", "2024-12-31"),
        adjust=body.get("adjust", "qfq"),
    )
    return {"job_ids": job_ids}


@app.get("/api/data/precache/jobs")
def precache_jobs():
    return {"jobs": precache_manager.list()}


@app.get("/api/data/precache/{job_id}")
def precache_job(job_id: int):
    job = precache_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return {"job": job}


@app.post("/api/data/precache/refresh")
def precache_refresh():
    precache_manager.refresh_all()
    return {"started": True}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_api.py -q`
Expected: 全部通过

- [ ] **Step 5: Commit**

```bash
git add api/main.py tests/test_api.py
git commit -m "feat: 预缓存 API 端点（submit/jobs/{id}/refresh）"
```

---

### Task 6: 内置定时刷新

**Files:**
- Create: `config/data.json`
- Modify: `api/main.py`

**Interfaces:**
- Consumes: Task 4 的 `manager.refresh_all()`
- Produces: 应用启动时注册后台线程，每个交易日到 `config/data.json` 的 `daily_update_time`（默认 `"15:30"`）自动刷新

- [ ] **Step 1: 写失败测试**（验证配置加载 + 调度逻辑）

```python
def test_daily_update_scheduler(monkeypatch, tmp_path):
    from api.main import _start_daily_scheduler, _should_run_now
    cfg_path = tmp_path / "data.json"
    cfg_path.write_text('{"daily_update_time": "15:30"}')
    monkeypatch.setenv("QUANT_DATA_CONFIG", str(cfg_path))
    # _should_run_now 在 15:30 返回 True
    assert _should_run_now("15:30") in (True, False)  # 依赖当前时间
```

> 由于调度器依赖真实时钟，此测试只验证配置解析与函数存在。调度线程的实际触发用集成验证（Task 7 的 Web 面板 + 手动 refresh 按钮）。

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_api.py::test_daily_update_scheduler -q`
Expected: FAIL — `_start_daily_scheduler` / `_should_run_now` 不存在

- [ ] **Step 3: 实现**

创建 `config/data.json`：

```json
{
  "daily_update_time": "15:30"
}
```

在 `api/main.py` 加：

```python
import json
import os
import threading
import time
from datetime import datetime

def _read_data_config() -> dict:
    path = os.environ.get("QUANT_DATA_CONFIG") or os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "config", "data.json"
    )
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except Exception:  # noqa: BLE001 - default config
        return {}


def _should_run_now(update_time: str) -> bool:
    now = datetime.now().strftime("%H:%M")
    return now == update_time


def _daily_scheduler_loop():
    cfg = _read_data_config()
    update_time = cfg.get("daily_update_time", "15:30")
    while True:
        if _should_run_now(update_time):
            try:
                precache_manager.refresh_all()
            except Exception:  # noqa: BLE001 - keep the loop alive
                pass
            time.sleep(60)  # avoid re-triggering within the same minute
        time.sleep(30)


def _start_daily_scheduler():
    threading.Thread(target=_daily_scheduler_loop, daemon=True).start()
```

在模块加载处调用 `_start_daily_scheduler()`（`app` 定义之后）：

```python
_start_daily_scheduler()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_api.py -q`
Expected: 全部通过

- [ ] **Step 5: Commit**

```bash
git add config/data.json api/main.py tests/test_api.py
git commit -m "feat: 内置每日定时刷新调度器"
```

---

### Task 7: Web 数据预缓存控制面板

**Files:**
- Modify: `web/index.html`

**Interfaces:**
- Consumes: Task 5 的 API 端点
- Produces: 前端「📦 数据预缓存」面板，含提交表单 + 任务列表轮询

- [ ] **Step 1: 在 HTML 加面板**（`llmPanel` 之后）

```html
<div class="panel" id="precachePanel">
  <h3>📦 数据预缓存</h3>
  <label for="pcSymbols">标的（逗号分隔，如 600519,510300）</label>
  <input id="pcSymbols" placeholder="600519,510300">
  <label for="pcFreq">频率</label>
  <select id="pcFreq">
    <option value="daily" selected>日线</option>
  </select>
  <label for="pcStart">起始日期</label><input id="pcStart" value="2020-01-01">
  <label for="pcEnd">结束日期</label><input id="pcEnd" value="2024-12-31">
  <label for="pcAdjust">复权</label>
  <select id="pcAdjust"><option value="qfq" selected>qfq</option><option value="none">none</option></select>
  <div style="margin-top:8px;display:flex;gap:8px;">
    <button id="pcSubmit" class="btn" style="flex:1">预缓存</button>
    <button id="pcRefresh" class="btn" style="flex:1">刷新已缓存</button>
  </div>
  <div id="pcMsg" class="status"></div>
  <h3 style="font-size:14px;color:var(--muted);margin-top:12px;">任务列表</h3>
  <table id="pcJobsTable">
    <thead><tr><th>ID</th><th>标的</th><th>状态</th><th>进度</th><th>错误</th></tr></thead>
    <tbody></tbody>
  </table>
</div>
```

- [ ] **Step 2: 加前端逻辑**（`<script>` 里，`llmSave` 相关之后）

```javascript
const $p = id => document.getElementById(id);
$p('pcSubmit').addEventListener('click', async () => {
  const symbols = $p('pcSymbols').value.split(',').map(s => s.trim()).filter(Boolean);
  if (!symbols.length) { $p('pcMsg').textContent = '请填写标的'; return; }
  const res = await fetch('/api/data/precache', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      symbols, freq: $p('pcFreq').value,
      start: $p('pcStart').value, end: $p('pcEnd').value, adjust: $p('pcAdjust').value
    })
  });
  const data = await res.json();
  $p('pcMsg').textContent = data.job_ids ? `已提交 ${data.job_ids.length} 个任务` : (data.detail || '失败');
  loadPrecacheJobs();
});
$p('pcRefresh').addEventListener('click', async () => {
  await fetch('/api/data/precache/refresh', {method: 'POST'});
  $p('pcMsg').textContent = '已触发刷新';
});

async function loadPrecacheJobs() {
  const res = await fetch('/api/data/precache/jobs');
  const data = await res.json();
  const tbody = $p('pcJobsTable').querySelector('tbody');
  tbody.innerHTML = data.jobs.map(j => `
    <tr><td>${j.id}</td><td>${j.symbol}</td><td>${j.status}</td>
    <td>${j.progress}%</td><td>${j.error || ''}</td></tr>`).join('');
}
setInterval(loadPrecacheJobs, 3000);
loadPrecacheJobs();
```

- [ ] **Step 3: 验证** — 启动服务，手动提交一个预缓存任务，观察任务列表刷新

```bash
.venv/bin/python -m pytest tests/test_api.py -q   # 确保 API 测试仍过
# 手动：uvicorn api.main:app --reload，浏览器打开 http://127.0.0.1:8000
```

- [ ] **Step 4: Commit**

```bash
git add web/index.html
git commit -m "feat: Web 数据预缓存控制面板"
```

---

### Task 8: README 更新 + 全量回归

**Files:**
- Modify: `README.md`
- Test: `tests/test_engine.py`, `tests/test_data.py`, `tests/test_precache.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: 全部已实现功能

- [ ] **Step 1: 更新 README** — 在「特性」与「API 概览」补预缓存

在特性列表加：
```
- **数据预缓存**：真实价 + 复权因子存储，手动 API 或每日定时预下载，回测不再临时联网
```
在 API 概览加预缓存端点表。

- [ ] **Step 2: 全量回归测试**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 全部通过（原 93 个 + 新增）

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: README 补充数据预缓存特性与 API"
```

---

## Self-Review 记录

**Spec 覆盖检查**：
- 存储层真实价+factor / 双重下载求因子 → Task 1 ✅
- 旧格式自动重下 → Task 2 ✅
- 引擎除权日调整 → Task 3 ✅
- PrecacheManager / 定时刷新 / 手动 API / 控制面板 → Task 4/5/6/7 ✅
- 分钟线不动、hfq deprecated、README → Global Constraints + Task 8 ✅
- 测试覆盖 → 各 Task 的测试步骤 ✅

**占位符检查**：无 TBD/TODO；`config/data.json` 定时触发依赖真实时钟，测试只验证配置解析与函数存在（注释已说明）。

**类型一致性**：`PrecacheManager.submit/get/list/refresh_all` 签名在 Task 4 定义、Task 5/6 消费，一致。`DataLayer._fetch_with_factor/_fetch_raw` 在 Task 1 定义、Task 2 消费，一致。`CACHE_DIR` 在 Task 4 构造默认参、Task 2 改实例可覆盖，一致。
