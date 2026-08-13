# 防过拟合 + 深度诊断 + 数据丰富度 设计

日期：2026-08-13

## 背景与问题

LLM 驱动策略优化的核心风险是**过拟合**：Agent 反复在固定区间调代码直到达标，等于在回测区间上背题，策略在未见过的数据（未来）上往往失效。引擎已保证 `ctx.history` 只能看过去（防"代码偷看未来"），但**多次试验择优偏差**（试几十种策略挑历史最好看的）无法用"看不看数据"解决，需要用**分段验收**来对抗。

同时，Agent 现在只拿到 7 个汇总指标，不知道"为什么没达标"，迭代效率低；数据层只能按代码/名称查标的，无法落地「沪深300成分」「白酒行业」这类目标。

本次三个方向：
1. **防过拟合**：训练段调优 + 多验证段自动"期末考"，验证段对 Agent 隐藏。
2. **深度诊断**：扩默认指标集 + 新增 `diagnose_backtest` 按需工具。
3. **数据丰富度**：指数成分股 + 行业分类 + 指数行情，让目标可落地。

## 设计决策（已与用户确认）

### 方向 ① 防过拟合 —— 训练/验证分段 + 期末考

**核心思想**：LLM 调优只能在训练段反复试，验证段（多个时间段）对 Agent 隐藏、无法跑回测。发布时自动期末考——同一份代码在全部验证段上达标才允许发布。

#### 分段推导规则（gate）

- 目标区间 `period: {start, end}` 为训练段。
- 默认切出多个验证段：每个验证段为训练段之后的**连续一年**，最多 2 段。例：`train 2020-01-01~2024-12-31` → `val1 2025-01-01~2025-12-31`、`val2 2026-01-01~2026-08-13`（到当前为止）。
- 验证段需要有数据（当前日期前）。若目标区间已到最近，则验证段空 → 提示用户提供更早的目标区间，或仅用可得的验证段。
- `GoalExtraction` 增加 `validation_periods: list[dict]` 字段（gate 提取时推导，或确认时可改）。

#### 训练/验证可见性

- `run_backtest` 工具：**只能提交训练段区间**（工具描述明确；执行层校验 `start/end` 必须在训练段内，越界报错）。这是防过拟合的第一道闸：Agent 永远无法在验证段上反复调参。
- **不提供** `run_validation` / 验证段报告工具：验证段对 Agent **完全隐藏**——没有工具能跑验证段，也没有工具能看验证段结果。连"返回占位"的工具也不加，避免给 Agent 传递"存在验证段"之外的任何信号。真正的验证段回测在 `publish_strategy` 时由系统自动跑。
- 目的：验证段对 Agent 零反馈（既不能跑，也不能看结果），杜绝"背题"。Agent 唯一的验证段信息是确认单上展示的验证段区间本身（这不是可优化反馈）。

#### 发布闸门

- `publish_strategy` 校验：训练段达标 **且** 全部验证段达标，才允许发布。
- 未达标时返回详细反馈（哪个验证段不达标、指标差距），Agent 可回训练段继续调优。
- `metrics` 快照记录：训练段指标 + 各验证段指标（`validation_metrics: [{period, metrics}]`），存入 `strategy_versions.metrics_json`。
- `store.publish_version` 签名扩展：接受 `validation_metrics`。

#### 系统提示（`agent.py`）

- `build_system_prompt` 明确：只能在训练段（目标区间）上写策略和回测；验证段由系统自动验收，Agent 不应尝试运行验证段。

#### 引擎

- 引擎**无需改动**——它就是通用回测引擎，验证段只是用不同 `start/end` 再跑一次。改动集中在 gate/tools/store/agent 提示。

### 方向 ② 深度诊断 —— 扩指标集 + 按需诊断工具

#### 默认指标扩充（`_result_to_text` 自动带上）

在现有 `total_return / annual_return / max_drawdown / sharpe / volatility / win_rate / n_trades` 基础上，追加：

- `excess_return`：超额收益（策略总收益 − 沪深300同期总收益）
- `calmar`：年化收益 / |最大回撤|
- `sortino`：下行波动修正的夏普
- `turnover`：年化换手率（平均每日成交额 / 平均组合净值）
- `avg_holdings`：平均持仓数
- `max_concentration`：最大单标的价值占比
- `monthly_win_rate`：正收益月占比

#### 新增 `diagnose_backtest` 工具（按需调用，默认上下文精简）

```
输入: {job_id}
输出:
  - monthly_returns: 月度收益矩阵（年×月，含 NaN 表示停牌/未上市）
  - drawdown_analysis: 最大回撤起止日期、持续天数、最长回撤区间
  - symbol_attribution: 每标的 {盈亏贡献, 交易次数, 最大单笔亏损, 持有天数}
  - holdings_history: 每日持仓数序列
  - benchmark_comparison: 策略 vs 沪深300 的年度收益对比
```

#### 实现

- `engine/engine.py`：`compute_metrics` 扩展新指标。
- 新增 `engine/diagnose.py`：`diagnose(equity_curve, trades, benchmark)` 生成上述分解。
- `api/agent/tools.py`：新增 `diagnose_backtest` 工具，从 executor 拿 job 结果，调用 diagnose。
- `api/agent/agent.py`：系统提示提及"可用 diagnose_backtest 深挖为什么没达标"。

### 方向 ③ 数据丰富度 —— 指数成分 + 行业分类 + 指数行情

#### 数据层新增

- `data/indices.py`：**指数成分查询**
  - 用 `index_stock_cons_weight_csindex(symbol)`（已验证：沪深300 返回 300 只，含权重、成分名）。
  - 维护主流指数名录：沪深300 `000300`、中证500 `000905`、上证50 `000016`、创业板指 `399006`、中证1000 `000852`、中证红利 `000922` 等。
  - 成分缓存到 `data/cache/index_{code}.json`，定期刷新。
  - 返回 `[{code, name, weight}]`。
- `data/industry.py`：**行业分类**
  - 用 `sw_index_first_info()` 获取申万一级行业列表（已验证：31 个行业，含成分个数）。
  - 每只股票打行业标签：`sw_index_third_cons`（三级行业成分）当前返回空，标注为 **best-effort**——若该函数可用则用，否则行业标签按需从东财（`stock_board_industry_cons_em`，当前被代理拦截）或申万一级成分补全。实现时优先申万，失败静默降级。
  - 缓存 `industry_map.json`（code → 行业名）。
- 指数行情：日线 `stock_zh_index_daily` 复用（已用于基准）；新增"行业/指数涨跌榜"——用近 N 日行情排序，供 Agent 看"哪个行业最近强"。

#### Agent 工具扩展

- `list_symbols` 扩展：支持 `index` 参数（"沪深300成分"）→ 返回成分股列表（复用 `data/indices.py`）。
- 新增 `list_industries`：列出申万一级行业及成分个数。
- 新增 `query_sector_perf`：查询指定行业/指数近 N 日涨跌幅。实现：行业用申万行业指数日线行情（`stock_zh_index_daily` 拉 `sh801080` 这类行业指数代码近 N 日），指数直接用 `stock_zh_index_daily`；`sw_index_first_info` 只有实时估值（市盈率/市净率）无涨跌幅，不作为涨跌来源。
- 行业成分股查询：`list_symbols(type='stock', industry='白酒')` → 返回该行业成分（若行业映射可用）。

#### gate 扩展

- `universe` 字段支持指数名/行业名（如"沪深300成分"、"白酒行业"），gate 提取后由 `resolve_universe` 展开成具体标的代码列表。
- `build_system_prompt` 展示 universe 时，若用户说的是指数/行业，Agent 知道可用 `list_symbols(index=...)` / `list_industries` 展开。

## 数据流（端到端）

```
目标输入
  → gate 提取 {universe(可含指数/行业), constraints, period=训练段, validation_periods}
  → 确认单展示训练段 + 各验证段
  → LLM 在训练段: list_symbols/list_industries 选标的
                 → register_strategy 写策略
                 → run_backtest 调优（只能跑训练段）
                 → check_goal 校验训练段达标
                 → 达标后 publish_strategy
  → publish_strategy 触发自动期末考（跑全部验证段）
  → 全达标 → 发布（metrics 快照含验证段指标）
  → 任一验证段不达标 → 拒绝 + 反馈报告 → Agent 回训练段继续调优
```

## 涉及文件

| 层 | 文件 | 改动 |
|----|------|------|
| 数据 | `data/indices.py` | **新增**：指数成分查询 + 缓存 + 名录 |
| 数据 | `data/industry.py` | **新增**：行业分类 + 缓存 |
| 数据 | `data/registry.py` | `get`/`list` 支持按行业/指数解析 |
| 引擎 | `engine/engine.py` | `compute_metrics` 扩展新指标 |
| 引擎 | `engine/diagnose.py` | **新增**：深度诊断分解 |
| Agent | `api/agent/gate.py` | `GoalExtraction.validation_periods`、universe 支持指数/行业、确认单展示验证段 |
| Agent | `api/agent/tools.py` | `run_backtest` 训练段校验、`diagnose_backtest`、`list_industries`、`query_sector_perf`、`list_symbols` 加 index/industry |
| Agent | `api/agent/agent.py` | 系统提示：训练段约束、验证段自动验收、诊断工具 |
| Agent | `api/agent/store.py` | `publish_version` 接受 `validation_metrics` |
| Agent | `api/agent/executor.py` | job 结果供 diagnose 访问 |
| Web | `web/index.html` | 确认单展示验证段（可选，聊天页优先） |
| Web | `web/chat.html` | 确认单展示验证段 |
| 测试 | `tests/test_agent_gate.py` | 验证段推导、universe 指数/行业展开 |
| 测试 | `tests/test_agent_tools.py` | 训练段越界校验、diagnose 工具、行业/指数工具 |
| 测试 | `tests/test_agent_agent.py` | 期末考发布闸门、验证段反馈 |
| 测试 | `tests/test_engine.py` | 扩展指标、diagnose 分解 |
| 测试 | `tests/test_data.py` | indices/industry 数据层 |

## 不做的事（YAGNI）

- **不做 walk-forward 滚动重优化**：真·walk-forward 是每窗口一套参数，但 Agent 优化的是代码而非参数，无法每窗口换参数。改为"训练段调优一次 + 多验证段验收"，拿到跨行情验证的好处而不付出滚动成本。
- **不做分钟级验证段**：验证段沿用与训练段相同的 `freq`。
- **不做基本面/财报数据**：本期只做指数成分、行业分类、指数行情。
- **不做实盘/paper trading**：本期专注回测可信度。
- **不做前端完整诊断可视化**：诊断工具返回结构化数据，前端仅确认单展示验证段；热力图/归因可视化留作后续。

## 风险与备注

- **网络环境**：东财端点（`stock_board_industry_cons_em`、`stock_individual_info_em`）在当前机器被代理拦截，`sw_index_third_cons` 返回空。实现时以**申万一级行业 + 中证指数**为可靠底座，行业成分 best-effort、失败静默降级（行业标签缺失时 Agent 仍可用 `list_symbols(type=...)` 兜底）。
- **验证段数据可得性**：验证段区间必须 <= 当前日期且有数据，否则跳过该段并提示。
- **发布闸门是硬约束**：验证段不达标 = 拒绝发布，不允许 Agent 绕过（工具层没有绕过通道）。
