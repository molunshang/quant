# A股回测系统设计文档

日期：2026-08-03
状态：已实现并验证

## 1. 目标

搭建一个中国A股回测系统，支持：
- **自定义策略**：用户/Agent 通过源码或内置策略定义交易逻辑
- **Web 可视化**：权益曲线、回撤、K线买卖点标记
- **Agent API**：编程调用，自动迭代寻找最优策略
- **多标的**：股票、基金、ETF

## 2. 技术栈

| 组件 | 选型 | 理由 |
|------|------|------|
| 语言 | Python 3.14 | 数据分析生态成熟 |
| Web框架 | FastAPI | 类型安全、自动文档、异步 |
| 数据处理 | pandas + numpy | 回测标准工具 |
| 数据源 | akshare（免费） | 覆盖 A股/基金/ETF |
| 前端 | 原生 HTML + ECharts | 零依赖、图表能力强 |
| 测试 | pytest | 标准测试框架 |

## 3. 架构

```
quant-agent/
├── data/           # 数据层
│   ├── sources.py  #   DataSource抽象 + EastMoney/Fund/Sina实现 + CSV缓存
│   └── registry.py #   标的名录（JSON缓存 + 默认兜底）
├── engine/         # 回测引擎
│   ├── engine.py   #   事件驱动逐bar循环 + A股规则
│   ├── context.py  #   策略上下文（ctx.buy/ctx.sell/指标）
│   └── rules.py    #   交易规则（T+1/涨跌停/印花税/手）
├── strategies/     # 策略层
│   ├── base.py     #   策略接口 + 源码校验
│   ├── builtin.py  #   内置策略（买入持有/均线/RSI）
│   ├── indicators.py#  指标库（SMA/EMA/RSI/MACD）
│   └── manager.py  #   策略注册/解析
├── api/            # FastAPI接口
│   ├── main.py     #   路由
│   ├── schemas.py  #   请求/响应模型
│   └── runner.py   #   数据→引擎→策略编排
├── viz/            # 图表数据生成（ECharts JSON）
├── web/            # Web前端（单页应用）
└── tests/          # 单元测试（25个通过）
```

## 4. 核心设计决策

### 4.1 数据层多源容错
- `DataSource` 抽象基类，`DataLayer` 按序尝试：EastMoney → Fund → Sina
- 股票用 eastmoney；ETF/指数用 sina `stock_zh_index_daily`；场外基金用 `fund_open_fund_info_em`（仅净值）
- CSV 缓存避免重复下载；日期范围在 `get_bars` 统一过滤
- **注册表 JSON 缓存**：首次构建标的名录（~1min）落盘，之后 0.3s 加载

### 4.2 自研事件驱动引擎
- 逐 bar 调用 `strategy(ctx, params)`，日线/分钟线统一
- 内置 A股规则：
  - **T+1**：当日买入不可卖出（`ctx._buy_dates`）
  - **涨跌停**：涨停不可买、跌停不可卖（用 `prev_close` 或 `open` 兜底）
  - **印花税**：股票卖出 0.05%，ETF/基金免征
  - **佣金**：万3，最低5元
  - **一手100股**：整数手，现金不足时逐手下调
- 策略通过 `ctx.buy()/ctx.sell()` 下单，`ctx` 暴露 `price/open/high/low/volume/bars_upto/shares/total_value`

### 4.3 策略即函数
- 策略 = `def strategy(ctx, params)` 纯函数
- 内置：buy_and_hold / sma_cross / rsi_reversal
- 自定义：注册源码（AST校验禁 import/私有访问），或 inline 提交
- Agent 通过 `/api/optimize` 做参数网格扫描

### 4.4 分钟级支持
- bar 频率抽象为参数（daily/1/5/15/30/60）
- 同一策略代码在不同频率直接可用
- 数据层预留分钟接口（eastmoney `stock_zh_a_hist_min_em`）

## 5. API 设计

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/backtest` | 跑回测，返回指标+权益曲线+交易 |
| POST | `/api/strategies` | 注册/更新策略（源码） |
| GET  | `/api/strategies` | 列出策略 |
| POST | `/api/optimize` | 参数网格扫描，按指标排序 |
| GET  | `/api/symbols` | 标的名录（类型/关键词过滤） |
| GET  | `/api/meta` | 系统元数据 |
| GET  | `/api/health` | 健康检查 |

## 6. 可视化

- 权益曲线 vs 基准（归一化百分比）
- 回撤曲线（面积图）
- K线 + 买卖点标记（ECharts candlestick + scatter）
- 前端单页应用，控制面板配置，3个图表 Tab，交易明细表

## 7. 测试

25 个 pytest 用例，覆盖：
- 引擎：T+1、涨跌停、印花税、ETF免征、一手100股、交易生成
- 数据：列名归一化、排序、类型推断、多源
- 策略：源码校验（拒绝import/私有）、注册、解析
- API：健康检查、策略列表/注册、自定义策略

## 8. 验证结果（真实数据）

| 标的 | 类型 | 策略 | 总收益 | 最大回撤 | 交易数 |
|------|------|------|--------|---------|--------|
| 600519 茅台 | 股票 | 均线 | —（一手需15万，默认10万买不起） | | |
| 000001 平安 | 股票 | 均线 | -12.5% | | 24 |
| 510300 沪深300ETF | ETF | 均线 | +0.8% | -13.2% | 21 |
| 161725 白酒 | 基金 | 动量 | +8.1% | | 18 |

## 9. 已知限制 / 未来工作

- 分钟级数据源仅 eastmoney（当前被限流，需代理或换源）
- 场外基金仅净值（无OHLC、无分钟）
- 策略沙箱是 AST 校验，非安全边界（本地单用户工具可接受）
- 茅台等高价股默认10万买不起1手 — 需提高现金或选低价标的
- 东财数据源被限流时降级新浪，但新浪无分钟数据
