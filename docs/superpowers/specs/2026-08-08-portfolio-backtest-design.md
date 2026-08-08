# 组合级自选标的回测：策略只需起始金额，自己选标的

日期：2026-08-08

## 背景与问题

当前回测系统是**单标的**架构：`run_backtest(symbol, ...)` 必须传一个标的，引擎一次只加载该标的的 K 线，策略（`strategy(ctx, params)`）只能在单个标的里买卖。真实的量化策略不是这样——**只需要起始金额，由策略自己选择标的、自己决定怎么交易**（聚宽式 `handle_data` 组合接口）。

需求：把回测从「指定单标的」改为「组合级策略自选标的」。策略是 portfolio 级函数，每 bar 看全 universe，自行决定持仓；`run_backtest` 只接收「策略 + 起始金额（+ universe/回测设置）」。

## 设计决策（已与用户确认）

1. **组合级策略接口（聚宽式）**：策略是 `handle_data(ctx)` 组合级函数，**每个时间点调用一次**（本期实现为每个交易日；时间点频率由引擎 `freq` 决定，为分钟级预留），可分析 universe 内全部标的历史，再决定买卖。策略可定义可选 `initialize(ctx)` 一次初始化跨 bar 状态。
2. **统一日历逐日推进**：universe 全部标日的并集作为时间轴，一个账户一个钟，各标的按日期对齐。跨标的资金分配/轮动才能真实。
3. **目标权重 + 收盘撮合**：策略通过 `ctx.buy(symbol, pct)` / `ctx.sell(symbol, pct)` 下目标权重订单（`pct` 相对当前组合净值），引擎按当前 bar 收盘价撮合，执行 A股规则。
4. **仅历史到当前 bar（防前视）**：策略只能看到每个标的截至当前 bar 的历史，禁止未来数据偷看。
5. **去掉 `params`**：策略签名简化为 `initialize(ctx)`（可选）+ `handle_data(ctx)`，需要可调的由策略源码自己定义默认值，外部不调参。删除 `/api/optimize`（参数网格搜索随之失去意义）。
5b. **跨 bar 状态 = `ctx.state`**：策略在 `initialize` 里把常量写进 `ctx.state`（一个跨 bar 持久 dict，可序列化），在 `handle_data` 里读。相比闭包更直觉、可序列化，也拿回类式策略「跨 bar 状态」的全部价值。
6. **universe 默认 = 已缓存标的集**：不传 universe 时，从 `data/cache/{type}_{code}_{freq}_{adjust}.csv` 解析出全部有缓存的标的作为候选池（"缓存内即全市场"），可选按 `types` 过滤；也支持显式传 `symbols` 列表。
7. **lazy-load 数据**：回测前用**元数据**（每个标的起止日期）预对齐统一日历；只有策略实际用到的标的才拉全量 K 线——先查本地缓存，未命中实时抓取并回写缓存。
8. **指数基准**：权益曲线 benchmark 用指数（沪深300 `000300`）归一化，多标的下不用单一标的收盘价。
9. **不需要向后兼容**：旧单标的内置策略、`symbol` 必填 API、Web 表单、已注册的用户策略全部按新方式实现，旧代码清理掉。

## 架构选型

沿用现有分层（data → engine → strategies → api → web），但 engine/context 从单资产重写为组合资产。策略接口取**聚宽式**（`initialize(ctx)` + `handle_data(ctx)` + `ctx.state`），而非 vnpy 式逐标的事件类（`on_symbol_bar`/`on_fill`）——聚宽/zipline 的「每个时间点一次组合回调 + 按需 `history(symbol)`」天然支持 lazy-load 与横截面选股；逐标的事件流要求 universe 预加载、且对日线+收盘撮合大半是 YAGNI。`params` 从整个链路删除。

## 1. Universe 与数据模型

**请求形状**（`POST /api/backtest`）：

```json
{
  "universe": {
    "symbols": ["600519", "000858"],
    "types": ["stock", "etf"]
  },
  "strategy": "my_strategy",
  "initial_cash": 100000,
  "freq": "daily",
  "start": "2022-01-01",
  "end": "2024-12-31",
  "adjust": "qfq"
}
```

**Universe 解析规则**（新增 `resolve_universe(spec)`）：
- 传了 `symbols` → 用这几个标的（显式池）
- 没传 `symbols` → 默认 = 已缓存标的集：扫描 `data/cache/{type}_{code}_{freq}_{adjust}.csv` 文件名解析出全部有缓存的标的，可选按 `types` 过滤
- 满足"缓存内即全市场"：预缓存做多大，全市场就有多大

**元数据预对齐**：对 universe 每个标的读取**起止日期**（缓存 CSV 首尾行，或未缓存时一次轻量请求），求全部标日的并集作为统一日历。此时**不拉全量 K 线**。

**lazy bar 加载器**：引擎持有 `{symbol: DataFrame}` 字典，策略第一次 `ctx.history(symbol)` 时才触发 `dl.get_bars()` 加载该标的全量 bars（离线优先：缓存命中直接用；未命中实时抓取并回写缓存）。加载后按统一日历做 searchsorted 预对齐，供 `ctx.history` 按需切片。

## 2. 引擎（engine/）

### `engine/engine.py` — 重写 `BacktestEngine`

- 持有 universe bars 字典、统一时间轴、组合账户（`cash` + `positions: dict[symbol, shares]`）
- **按时间点（time step）推进**：本期统一时间轴 = 全部标日的并集（`daily`）；每个时间点设置 `ctx.time`、按需切片暴露各标的历史 → 调用 `handle_data(ctx)` → 收集策略下的订单 → 按当前 bar 收盘价撮合（各标按自身类型应用 A股规则）→ 记录组合净值。引擎的时间推进抽象为「下一个时间点」，与 bar 频率解耦，为分钟级预留
- `buy(ctx, symbol, pct)`：`pct` 相对当前净值 `total_value` 的目标比例；换算成股数（百股整手、按现金约束降档），按该标的价格/涨跌停/费用撮合
- `sell(ctx, symbol, pct)`：卖持仓 pct%（默认清仓），同样受 T+1/涨跌停约束
- 公司行为（`factor` 复权拆分）沿用现有逻辑，按标的分列应用
- `compute_metrics`：benchmark 改指数（沪深300 `000300`）归一化

**分钟级扩展预留**：`ctx.time`、`ctx.history(s)`、`ctx.price(s)`、撮合价都基于「当前时间点」切分，不感知频率单位。将来把统一时间轴换成分钟 bar 并集、撮合用分钟收盘价即可，策略源码（`handle_data` 内逻辑）无需改动。

### `engine/context.py` — 重写 `Context`

组合感知：

**策略接口**：

```python
def initialize(ctx):        # 可选，每回测只跑一次；把常量写进 ctx.state
    ctx.state["top_n"] = 3

def handle_data(ctx):       # 每个时间点调用一次（本期=每个交易日）
    top = ctx.state["top_n"]
    ...
```

**Context API**：

```python
ctx.universe              # 候选标的代码列表（策略自选范围，可能含未加载数据的标的）
ctx.state                 # 跨 bar 持久 dict（initialize 写入，handle_data 读写）
ctx.cash                  # 组合现金
ctx.positions             # dict: symbol -> shares
ctx.total_value           # 组合净值 = cash + Σ(shares × price)
ctx.calendar              # 统一时间轴（时间点列表；本期=日）
ctx.time                  # 当前时间点（本期为日期字符串 YYYY-MM-DD）
ctx.bar_index             # 当前时间点在统一时间轴中的索引（0 起）

ctx.history(symbol, lookback=0)  # 该标的截至当前时间点的历史（预对齐+按需切片）
                                 # lookback 沿用现有 bars_upto 约定：0=全历史到当前 bar，
                                 # lookback=N=最近 (N+1) 根 bar
ctx.price(symbol)                # 该标的最新收盘价
ctx.buy(symbol, pct)             # 用当前净值 pct% 买入，返回 bool
ctx.sell(symbol, pct)            # 卖该标的持仓 pct%（默认清仓），返回 bool
```

> lazy-load：`ctx.history(s)` 首次调用该标的时才触发加载（离线优先，未命中实时抓取并缓存）。策略遍历 `ctx.universe` 只触发它实际分析的标的加载。

> 分钟级扩展：`ctx.time` / `ctx.history(s)` / `ctx.price(s)` / 撮合价都基于「当前时间点」切分，不感知频率单位。将来统一时间轴换成分钟 bar 并集即可，`handle_data` 内逻辑无需改动。

### `engine/rules.py` — 保留

A股规则（T+1 / 涨跌停 / 百股整手 / 佣金 / 印花税）保留，按**每个标的的类型**分别应用。

## 3. 策略（strategies/）

- `strategies/base.py`：策略由 `initialize(ctx)`（可选）+ `handle_data(ctx)` 两个函数组成；`StrategyFunc = Callable[[Context], None]`；去掉 `params`；AST 校验保留
- `strategies/builtin.py`：重写为组合版
  - `buy_and_hold`：首日等权买入 universe 全部标的，持有到结束
  - `momentum_rotation`（新增示例）：按历史动量排名选前 N 个标的轮动，卖旧买新（N 与动量窗口由策略源码内 `initialize` 写进 `ctx.state` 的默认值定义，外部不可调参）
- `strategies/manager.py`：去 `params_schema`

## 3.1 策略编写示例

策略由可选 `initialize(ctx)` + 必选 `handle_data(ctx)` 组成，`handle_data` 每个时间点（本期=每个交易日）调用一次。典型写法：`initialize` 写常量到 `ctx.state` → `handle_data` 分析各标的历史 → 决定目标持仓 → 用 `ctx.buy`/`ctx.sell` 下达订单。

### 示例 1：买入持有（等权买入全部候选标的，只调仓一次）

```python
def handle_data(ctx):
    if ctx.bar_index > 0:
        return
    n = len(ctx.universe)
    for s in ctx.universe:
        ctx.buy(s, 1.0 / n)
```

### 示例 2：动量轮动（选最近 N 日涨幅最高的 K 个标的持有，定期轮换）

```python
def initialize(ctx):
    ctx.state["window"] = 60
    ctx.state["top_n"] = 3
    ctx.state["rebalance_every"] = 20

def handle_data(ctx):
    # 每 rebalance_every 个交易日才做一次调仓决策
    if ctx.bar_index % ctx.state["rebalance_every"] != 0:
        return

    momentum = {}
    for s in ctx.universe:
        bars = ctx.history(s)          # 首次访问触发该标的 lazy 加载
        if len(bars) < ctx.state["window"]:
            continue
        ret = bars["close"].iloc[-1] / bars["close"].iloc[-ctx.state["window"]] - 1
        momentum[s] = ret

    if not momentum:
        return
    top = sorted(momentum, key=momentum.get, reverse=True)[:ctx.state["top_n"]]

    # 卖出不在 top 的持仓
    for s in list(ctx.positions):
        if s not in top:
            ctx.sell(s)
    # 买入 top 中尚未持仓的标的，等权
    for s in top:
        if s not in ctx.positions:
            ctx.buy(s, 1.0 / len(top))
```

> 同一时间点内的 `ctx.sell` + `ctx.buy` 订单会被引擎**统一收集、按收盘价一并撮合**，目标权重都相对同一时刻的净值计算，无需关心顺序。

### 示例 3：多标的均线策略（金叉买、死叉卖，按标的独立判断）

```python
def handle_data(ctx):
    for s in ctx.universe:
        bars = ctx.history(s)
        if len(bars) < 60:
            continue
        close = bars["close"].astype(float)
        ma_short = close.rolling(20).mean()
        ma_long = close.rolling(60).mean()
        cur = ma_short.iloc[-1] > ma_long.iloc[-1]
        prev = ma_short.iloc[-2] > ma_long.iloc[-2] if len(bars) >= 2 else False

        if cur and not prev and s not in ctx.positions:
            ctx.buy(s, 0.5)            # 金叉：买入，最多占净值 50%
        elif not cur and prev and s in ctx.positions:
            ctx.sell(s)                # 死叉：清仓
```

### 通用约定

- `ctx.history(s)` 返回的是**截至当前时间点**的 DataFrame（含 `date/open/high/low/close/volume`），天然防前视
- `ctx.buy(s, pct)` 的 `pct` 相对当前净值（0~1）；`ctx.sell(s, pct)` 卖持仓的 pct%，缺省为清仓
- 成交受 A股规则约束（T+1 / 涨跌停 / 百股整手 / 费用），撮合不一定完全成交，`buy`/`sell` 返回 `bool` 表示是否成交；策略不应假设下单必成
- 需要可调数值（窗口、权重、阈值）时**在 `initialize` 里写进 `ctx.state` 作为默认值**，外部不传参

## 4. API 层

### `api/runner.py`

`run_backtest(strategy, universe=None, initial_cash, freq, start, end, adjust)`：
1. 解析 universe（显式列表 / 默认缓存集 + types 过滤）
2. 元数据预对齐统一日历
3. 建 `BacktestEngine`，传入 lazy 数据加载器
4. 运行，返回指标 + 权益曲线 + 交易记录（含标的字段）

去掉 `symbol`、`params`；删除 `run_optimize`。

### `api/schemas.py`

- `BacktestRequest`：用 `universe` 替代 `symbol`、去掉 `params`
- 删除 `OptimizeRequest`

### `api/main.py`

- 更新 `/api/backtest`
- 删除 `/api/optimize`
- `/api/meta` 相应调整

### `api/agent/`（LLM Agent）

- `executor.py`：`submit` 去掉 `symbol`、`params`，改为 `submit(strategy_ref, universe=None, ...)`
- `tools.py`：`run_backtest` 工具去掉 `symbol` 必填、加 `universe` 可选；Agent 只需「策略 + 投资金额」
- `store.py`：若存策略指标快照，去掉 params 关联
- 工具的 `description` 更新为组合语义（`ctx.history(symbol)` / `ctx.buy(symbol, pct)`）

## 5. Web 前端（web/index.html）

- 去掉"交易标的"必填输入，改为 **universe 设置**（可选：类型过滤 / 标的列表 / 默认缓存集）
- 策略编辑去 `paramsJson`；策略源码占位符更新为 `initialize(ctx)` + `handle_data(ctx)` 组合版示例
- 结果区：基准改指数；K线买卖点标记多标的下按标的聚合/切换展示；交易记录表加标的列

## 6. 测试

重写为组合级用例：

| 文件 | 覆盖 |
|------|------|
| `tests/test_engine.py` | 组合引擎：多标的统一日历、目标权重撮合、lazy-load 触发、防前视、各标的不同规则（ETF 无印花税）、指数基准 |
| `tests/test_api.py` | `run_backtest` 新签名（universe 解析、默认缓存集）、无 `params` |
| `tests/test_agent_executor.py` | `submit` 无 symbol、universe 传递 |
| `tests/test_agent_tools.py` | `run_backtest` 工具无 symbol 必填、有 universe |
| `tests/test_strategies.py` | 组合版内置策略（等权持有、动量轮动）、`initialize`/`handle_data` 校验 |

## 7. 不做的事（YAGNI）

- 保留 `params` / `/api/optimize`（已定删除）
- 向后兼容旧单标的内置策略 / `symbol` 必填 API（已定不兼容）
- 全市场 2 万标的实时拉数（已定：默认已缓存集）
- 空头 / 杠杆 / 融资融券（本期只做多组合）
- 完全 lazy 日历（已定：元数据预对齐 + 数据 lazy）
- **本期不实现分钟级回测**，但已按「时间点」抽象预留接口契约（`ctx.time` / `handle_data` 每时间点一次），将来换分钟时间轴即可，策略源码无需改动

## 涉及文件

| 文件 | 改动 |
|------|------|
| `engine/engine.py` | **重写**：组合级 BacktestEngine |
| `engine/context.py` | **重写**：组合感知 Context |
| `engine/rules.py` | 保留，按标的应用 |
| `strategies/base.py` | `StrategyFunc = Callable[[Context], None]`，去 params |
| `strategies/builtin.py` | **重写**：组合版（buy_and_hold 等权、momentum_rotation） |
| `strategies/manager.py` | 去 params_schema |
| `api/runner.py` | `run_backtest` 新签名，删 run_optimize |
| `api/schemas.py` | `BacktestRequest` 用 universe，删 OptimizeRequest |
| `api/main.py` | 更新 `/api/backtest`，删 `/api/optimize` |
| `api/agent/executor.py` | `submit` 去 symbol/params，加 universe |
| `api/agent/tools.py` | `run_backtest` 工具去 symbol 必填、加 universe |
| `api/agent/store.py` | 去 params 关联（如涉及） |
| `web/index.html` | universe 设置、去 paramsJson、指数基准、交易表加标的 |
| `tests/*` | 重写为组合级用例 |
