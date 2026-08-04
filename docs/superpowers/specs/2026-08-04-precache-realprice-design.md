# 数据预缓存 + 真实价存储 + 计算层复权 设计

日期：2026-08-04
状态：已确认

## 背景与动机

### 预缓存

当前 `DataLayer.get_bars()` 的缓存是**惰性 + 一次性**的：首次访问某标的才下载并写入 CSV，之后**永不刷新**。LLM Agent 迭代时每选中一个新标的都要在循环中途支付首次下载的网络成本，且 `BacktestExecutor` 4 线程并发下多个回测可能同时卡在下载上。

需要一个**主动预缓存**层：提前把一批标的的数据下载到本地，之后回测直接读本地，不走网络。

### 真实价存储（方案 B 的核心）

当前系统把**前复权（qfq）价烘焙进了存储**：`EastMoneySource.fetch_daily(adjust="qfq")` 直接调用 akshare 的 `stock_zh_a_hist(adjust="qfq")`，缓存 CSV 里存的是加工过的 qfq 价，不是当天真实成交价。

这带来一个**架构问题**：qfq 价以「最新价」为锚，每次发生分红除权，**整段历史被重新缩放**。于是：

1. 已缓存的历史段会随新分红**漂移**，缓存失效需要全量重下；
2. 增量追加会把新旧两种基准在拼接处拼出**假跳空**，污染收益率/Sharpe。

用户指出（且正确）：**分红不改变真实历史价格**，复权是「后续计算」层关心的问题，不该下沉到存储层。因此采用方案 B：

- **存储层只存真实成交价**（`none` 价），历史永不改变，增量追加天然安全；
- **计算层**通过每股累积复权因子，在需要时把真实价换算回 qfq 视角。

## 决策汇总

| # | 决策 | 内容 |
|---|------|------|
| 1 | 存储 | 日线缓存存**真实成交价 + `factor`（每股累积复权因子）列** |
| 2 | 除权日 | 引擎在除权日**按因子调整持仓**（送股/拆股调股数，分红调成本），等价真实资金流 |
| 3 | 因子来源 | **真实价/qfq 双重下载**（同一 eastmoney 接口 `stock_zh_a_hist` 调两次 `adjust="none"` + `adjust="qfq"`），逐日比值求因子 |
| 4 | 分钟线 | **本次不做**，保持 qfq 现状，记录 TODO |
| 5 | 旧缓存 | **检测旧格式（无 `factor` 列）自动重下覆盖** |
| 6 | 触发方式 | 手动 API + 应用内置定时（新增标的立即下载、已缓存标的收盘后刷新） |
| 7 | 进度展示 | Web 控制面板，轮询查看后台任务进度 |

## 语义确认（已与用户确认）

1. **策略看到真实价**：`ctx.price` / `ctx.open` / `high` / `low` 和成交价都用**真实成交价**（与实盘一致）。`factor` 只用于：
   - 跨除权日的持仓成本/股数调整；
   - 权益曲线的正确估值。
2. **`adjust` 参数收敛**：
   - `adjust="qfq"`（默认）：返回**真实价 + `factor`**，上层可自行换算成 qfq 视角（默认行为保持前复权视角）；
   - `adjust="none"`：返回真实价（`factor=1`）；
   - `adjust="hfq"`：**标记 deprecated**，映射到 qfq 行为并告警。
3. **factor 时机**：factor 取**当日收盘**比值 `qfq_close / none_close`。除权日 qfq 价连续无跳空、真实价跳低，故 factor 在除权日表现为阶梯式上升，反映当日股本变动。

## 架构

```
data/sources.py         存储层：真实价 + factor 列，双重下载求因子，旧格式自动重下
data/precache.py        (新增) 预缓存服务：任务注册表 + 线程池 + 每日刷新
engine/context.py       策略上下文：暴露真实价
engine/engine.py        引擎：除权日持仓调整 + 权益估值用 factor
api/main.py             预缓存 API 端点
web/index.html          数据预缓存控制面板
tests/                  因子计算 / 旧格式重下 / 除权日调整 / 增量刷新 / API
```

## 组件设计

### 1. 存储层（`data/sources.py`）

**新列约定**：日线缓存 CSV 列 = `[date, open, high, low, close, volume, factor]`。

**新方法 `_fetch_with_factor(symbol, start, end)`**：
- 同一标的调用 `stock_zh_a_hist` 两次：`adjust="none"` 得真实价、`adjust="qfq"` 得 qfq 价；
- 按日期对齐（外连接，两边都保留），逐日 `factor = qfq_close / none_close`；
- 输出标准化 DataFrame（含 `factor` 列），仅对 **eastmoney 源的 stock/etf** 生效（见「约束」）。

**缓存版本检测**：
- 读缓存时检查是否有 `factor` 列；
- 无 `factor` 列 = 旧格式（qfq 价）→ 自动触发重下并覆盖。

**新增方法**：
- `precache(symbol, freq, start, end)`：强制走数据源写穿缓存（不读旧缓存）；
- `refresh(symbol)`：增量补新交易日（真实价历史不变，只拉 `[最后缓存日期+1 .. 今天]` 并与现有数据合并去重）。

**`get_bars()` 语义变更**：
- `adjust="qfq"`（默认）→ 返回真实价 + `factor`（上层按需换算）；
- `adjust="none"` → 返回真实价（`factor` 列=1）；
- `adjust="hfq"` → 映射 qfq 行为（deprecated）。

**Sina 源 / 基金源**：`fetch_daily` 保留现状（返回不带 `factor` 的真实/原始价），作为**降级路径**。仅 eastmoney 源产生带 `factor` 的缓存；降级成功时写入无 factor 的缓存（同样带版本标记，后续重下可升级）。

### 2. 引擎层（`engine/`）

**`Context`**：
- 暴露真实价属性（`price`/`open`/`high`/`low` 直接读 bar 的真实价）；
- 新增 `factor` 序列访问（供引擎内部用）。

**`BacktestEngine.run()`**：
- 加载 `bars["factor"]`，识别除权日（`factor` 逐日比值 ≠ 1 的日子）；
- 每日开盘前：若当日是除权日，按因子调整持仓——
  - `factor` 比值 > 1（送股/拆股）：`position *= 比值`，`avg_cost /= 比值`；
  - `factor` 比值 < 1（分红/配股）：等价现金资金流，调整成本（具体公式见实现计划）；
- 成交用真实价；涨跌停判断 `_is_limit_up/down` 用真实价（比 qfq 价更准）；
- 权益曲线 `equity = cash + position * close`（真实价 × 股数 = 真实市值）。

### 3. 预缓存服务（新增 `data/precache.py`）

**`PrecacheManager`**：
- 线程池（`max_workers` 可配，默认 4，与 `BacktestExecutor` 一致）；
- 任务注册表：`job_id → {symbol, freq, adjust, start, end, status, progress, error, created_at}`；
- `submit(symbols, freq, start, end, adjust)`：批量入队，返回 job 列表；
- `get(id)` / `list()`：查任务状态；
- `refresh_all()`：扫描 `data/cache/` 已有文件，逐个增量补新交易日。

**内置定时**：
- 配置 `config/data.json`：`{"daily_update_time": "15:30"}`；
- 应用启动时注册后台线程，每个交易日到点自动 `refresh_all()`（非交易日无新数据，幂等）；
- 新增标的：手动 API 提交时**立即下载**。

### 4. API（`api/main.py`）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/data/precache` | 异步提交预缓存任务，立即返回 `job_id` 列表 |
| GET | `/api/data/precache/jobs` | 任务列表（含进度） |
| GET | `/api/data/precache/{id}` | 单任务进度 |
| POST | `/api/data/precache/refresh` | 手动触发刷新已缓存标的 |

请求体（`POST /api/data/precache`）：
```json
{"symbols": ["600519", "510300"], "freq": "daily", "start": "2020-01-01", "end": "2024-12-31", "adjust": "qfq"}
```

### 5. Web 控制面板（`web/index.html`）

- 新增「📦 数据预缓存」面板（在 LLM 设置面板之后）：
  - 输入：标的列表（逗号分隔）、频率（日线）、日期范围、复权；
  - 按钮：提交预缓存 / 刷新已缓存；
  - 任务列表表格：job_id、标的、状态（pending/running/done/error）、进度、错误，`setInterval` 轮询刷新。

### 6. 测试

- `tests/test_data.py` 扩展：
  - `_fetch_with_factor` 因子计算正确（mock 返回 none/qfq 两套，验证逐日比值）；
  - 旧格式（无 factor 列）缓存自动重下；
  - `adjust` 语义（qfq 返回真实价+factor，none 返回 factor=1）；
- 新增 `tests/test_precache.py`：
  - `PrecacheManager.submit/get/list` 状态流转；
  - 增量刷新只补新交易日（去重合并正确）；
  - API 端点（submit/query/refresh）状态与响应；
  - 除权日持仓调整（送股/拆股/分红三种情形）。

## 约束与风险

1. **因子仅 eastmoney 源可求**：`stock_zh_a_hist` 支持 `adjust="none"|"qfq"`，是唯一可双重下载的入口。Sina 源（`stock_zh_index_daily`/`stock_zh_a_daily`）与基金源（`fund_open_fund_info_em`）无 `adjust="none"` 分支，**降级时无因子**，返回原始价（不带 factor 列，带版本标记）。
2. **预缓存仅对 eastmoney 可达的标的有意义**：降级路径下缓存仍可用（真实价），但无因子，引擎以 `factor=1` 处理（等价于 `none` 模式）。
3. **分钟线**：保持 qfq 现状，不在本次范围。**`adjust` 收敛仅作用于日线**——分钟线缓存键仍含 `adjust="qfq"`，直接存 eastmoney 返回的 qfq 价（`factor` 列恒为 1，引擎按无除权处理）。TODO：分钟线因子获取（数据源不提供，需另想办法）后统一。
4. **`hfq` deprecated**：现有调用方传 `hfq` 会得到 qfq 行为。需在文档/API 层标明，避免误解。
5. **factor 数值稳定性**：`qfq_close / none_close` 在成交量接近 0 或价格为 0 的日子可能不稳定，需加保护（如除数为 0 时用前值）。
