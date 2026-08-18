# quant-agent

中国A股回测系统 — 支持自定义策略、Web 可视化、LLM Agent 自动迭代目标优化。

> 本仓库为**双项目结构**：
> - **quant-agent**（根目录）— 自研回测引擎 + LLM Agent 优化系统，本文档介绍。
> - **jq/** — 基于[掘金量化](https://www.myquant.cn)平台的策略集合（`gm.api` SDK），跑掘金终端回测/实盘。见 [jq/README.md](jq/README.md)。
>
> 两个项目相互独立、互不依赖。掘金策略需要 `pip install -r jq/requirements.txt`（`gm` 包）才能运行。

## 特性

- **自定义策略**：组合级策略 `initialize(ctx)`（可选）+ `handle_data(ctx)`，策略从 universe 自选标的、自定买卖，通过 API 提交源码或引用内置策略（AST 沙箱校验，拒绝 import / 私有访问）
- **Web 可视化**：权益曲线、回撤曲线、K线买卖点标记（ECharts）
- **AI 目标优化 Agent**：用自然语言设定目标（年化收益 / 超额 / 最大回撤），LLM 自主选标的、写策略、跑回测、校验是否达标并发布；目标不明确时先澄清、执行前先确认（目标门卫）；Web 聊天面板实时流式进度（SSE）
- **多 LLM 后端**：Anthropic + OpenAI 兼容多 Provider（可接 DeepSeek / Qwen 等），Web 面板在线配置、测试连接、设默认
- **策略草稿 → 发布 + 版本管理**：迭代中的策略为草稿，达标才发布，记录指标快照，SQLite 持久化
- **多标的**：股票、基金、ETF
- **多数据源容错**：东方财富（首选）→ 新浪（降级），CSV 缓存
- **数据预缓存**：真实价 + 每股复权因子存储，手动 API 或每日定时预下载，回测不再临时联网
- **A股交易规则**：T+1、涨跌停、印花税（股票卖出 0.05%，ETF/基金免征）、佣金、一手 100 股
- **日线 + 分钟线**：bar 频率可配置

## 快速开始

```bash
# 1. 创建虚拟环境并安装依赖
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. 启动 Web 服务
.venv/bin/uvicorn api.main:app --reload

# 3. 打开 http://127.0.0.1:8000
```

### LLM Provider 配置

使用「AI 目标优化」前需配置 LLM。编辑 `config/llm.json`，或在 Web 面板「⚙️ LLM 设置」中增删改查、测试连接、设置默认：

```json
{
  "default": "anthropic-local",
  "providers": [
    {"name": "anthropic-local", "type": "anthropic",
     "base_url": "http://127.0.0.1:3456", "model": "opengo/deepseek-v4-flash",
     "api_key": "env:ANTHROPIC_API_KEY"}
  ]
}
```

- `type`：`anthropic`（base_url 可选，默认官方地址）或 `openai_compat`（base_url 必填，须以 `http://` / `https://` 开头）
- `api_key` 支持 `env:VARNAME` 引用环境变量
- 可通过环境变量 `QUANT_LLM_CONFIG` 指定配置文件路径

## API 概览

### 回测核心

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/backtest` | 运行组合回测（策略自选标的），返回指标+权益曲线+交易记录 |
| POST | `/api/strategies` | 注册/更新策略 |
| GET  | `/api/strategies` | 列出策略 |
| GET  | `/api/symbols` | 可交易标的名录 |
| GET  | `/api/bars/{symbol}` | 单标的 OHLCV K线（图表用） |
| GET  | `/api/meta` | bar 频率、佣金默认值、可用指标 |
| POST | `/api/chart/equity` | 权益曲线 ECharts 配置 |

### 数据预缓存

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/data/precache` | 异步提交预缓存任务，返回 job_ids |
| GET  | `/api/data/precache/jobs` | 任务列表（含进度） |
| GET  | `/api/data/precache/{job_id}` | 单任务进度 |
| POST | `/api/data/precache/refresh` | 手动触发刷新已缓存标的 |

### AI Agent / LLM 配置

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/chat` | 提交目标 → 澄清/确认后启动 Agent 循环（目标门卫） |
| GET  | `/api/chat/events` | SSE 实时进度流 |
| GET  | `/api/chat/sessions` | 会话列表 |
| GET  | `/api/providers` | 可用 provider 名列表 |
| GET  | `/api/llm/providers/list` | LLM provider 配置列表 |
| POST | `/api/llm/providers/add` | 新增 provider |
| POST | `/api/llm/providers/update` | 更新 provider |
| POST | `/api/llm/providers/delete` | 删除 provider |
| POST | `/api/llm/providers/test` | 测试连接 |
| POST | `/api/llm/default/set` | 设置默认 provider |
| GET  | `/api/strategies/published` | 已发布策略 |
| GET  | `/api/strategies/{name}/versions` | 策略版本历史 |

## 目录结构

```
quant-agent/
├── data/           # 数据层：DataSource 抽象 + 多源实现 + CSV 缓存 + 标的名录
├── engine/         # 回测引擎：事件驱动 + A股规则
├── strategies/     # 策略层：内置策略 + 源码校验
├── api/            # FastAPI 接口
│   └── agent/      # LLM Agent：工具循环、Provider 抽象、回测执行器、SQLite 存储
├── config/         # LLM provider 配置 (llm.json)
├── web/            # Web 前端（单页）
├── viz/            # 图表数据生成
└── tests/          # 单元测试
```
