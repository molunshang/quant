# LLM 目标优化 Agent 设计文档

日期：2026-08-03
状态：已确认待实现

## 1. 目标

在现有 A股回测系统上新增 **LLM 驱动的目标优化 Agent**：用户在 Web 聊天框用自然语言设定目标（如「在沪深300上做到年化收益10%、超额收益10%」），LLM 自主选标的、写/改策略、运行回测、校验是否达标，直到达成目标或放弃。

## 2. 需求清单（已与用户逐条确认）

1. **Web 聊天输入框**：通过对话设置目标，聊天里实时看进度和结果
2. **LLM 全自动**：用户只给目标（年化/超额/回撤等），LLM 自己选标的、定策略参数，**无人工确认**
3. **LLM 可写策略源码**：复用现有 AST 沙箱校验（拒绝 import/私有访问）
4. **草稿→发布 + 版本管理**：迭代的策略默认**草稿**，达标才**发布**，发布记录指标快照、可回滚
5. **多 LLM 后端**：不限定 Anthropic SDK，用**多 Provider 适配器**（Anthropic / OpenAI 兼容 / DeepSeek / Qwen）
6. **回测可能长时间运行**（日线+分钟线），且**可能并行**跑多个策略（单任务内 + 多任务并发）
7. **聊天实时流式进度**（SSE）
8. **架构**：手动工具调用循环（代码层自写，非 SDK Tool Runner）

## 3. 技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| LLM 客户端 | 多 Provider 适配器（`LLMProvider` 抽象） | 支持 Anthropic / OpenAI 兼容多后端 |
| 回测并发 | `ThreadPoolExecutor` 后台执行器 | 并行 + 异步跑回测，支持长任务 |
| 策略存储 | SQLite | 草稿/发布/版本/指标快照持久化 |
| 聊天历史 | SQLite | 用户消息 + AI 最终汇报持久化 |
| 进度事件 | 内存队列（读后清除） | 单进程、退出即失效，无需断点恢复 |
| 实时推送 | SSE + 心跳 | 长连接保活 + 进度流式 |
| 前端 | 现有 web/index.html 扩展 | 加聊天面板 |

## 4. 架构

```
web/index.html (聊天 UI)
   │  用户输入目标 / 后续消息
   ▼
POST /api/chat  ──►  LLMAgent  (手动工具调用循环, 后台线程)
   │                    │
   │                    ├── LLMProvider 抽象 ── AnthropicProvider / OpenAICompatProvider
   │                    │        └─ complete(system, messages, tools) → text + tool_uses
   │                    │
   │                    ├── BacktestExecutor (线程池)
   │                    │        ├─ run_backtest (复用现有 engine/runner)
   │                    │        └─ 并行跑多个回测, 每个实例 running/done/error
   │                    │
   │                    ├── StrategyStore (SQLite)
   │                    │        ├─ 草稿 draft / 发布 published
   │                    │        └─ 版本历史 + 指标快照
   │                    │
   │                    └── tools: list_symbols / run_backtest / register_strategy /
   │                               list_strategies / publish_strategy / check_goal
   │
   ▼
SSE  /api/chat/events?session_id=X  ◄── 事件流: 进度/回测结果/草稿更新/发布/最终汇报
```

## 5. 核心设计决策

### 5.1 状态机驱动的工具循环（非多轮对话累积）

关键决策：**每轮 LLM 只看「当前状态快照」，不保留历史迭代对话**。

```
状态 State = { goal, current_draft, last_backtest_results, published_strategies }

循环 (每轮一次 LLM 调用):
1. 构造 prompt = system(角色) + State 快照(目标/当前草稿/最近指标/已发布)
2. LLM 返回: 动作列表(可并行) 或 最终汇报
     - run_backtest / register_strategy / publish_strategy / check_goal / list_symbols / list_strategies
3. agent 层执行动作:
     - 回测: 批量提交线程池 → 等全部完成 → 精简结果回填
     - 其他: 即时执行
4. 更新 State (草稿变了/回测结果/发布)
5. 检查退出:
     - check_goal 判定「达成」→ 汇报, 停
     - 轮次 ≥ max_turns 或 LLM 说「放弃」→ 汇报, 停
     - 否则 → 回步骤 1 (新状态快照)
```

**状态快照内容**（不保留每轮迭代历史）：
- `goal`：固定
- `current_draft`：最新草稿源码（覆盖式更新）
- `last_backtest_results`：最近一轮回测指标（精简）
- `published`：已发布版本列表（供参考）

### 5.2 回测并发：异步提交 + 批量等待

- `run_backtest` **异步**：提交后立即返回 `job_id`
- LLM 一轮内可**并行提交多个回测**（不同策略×标的）
- agent 层**等这一轮所有 job 完成**（或部分失败/超时），把结果**一次性**回填给 LLM
- LLM 不用自己管理 job_id 轮询节奏 —— agent 层替它等；**不提供** `get_backtest_result` 给 LLM 自轮询

### 5.3 多 Provider 适配器

```python
class LLMProvider(ABC):
    @abstractmethod
    def complete(self, *, system, messages, tools, model=None, max_tokens=4096) -> LLMResponse:
        """一次 LLM 调用, 返回文本 + tool_use 列表。"""

@dataclass
class LLMResponse:
    text: str | None
    tool_uses: list[ToolCall]

@dataclass
class ToolCall:
    id: str
    name: str
    input: dict
```

- **AnthropicProvider**：`anthropic` SDK，`base_url`/`api_key`/`model` 可配
- **OpenAICompatProvider**：OpenAI SDK 兼容（`base_url`+`api_key`+`model`），覆盖 DeepSeek/Qwen/vLLM
- **provider_registry**：从 `config/llm.json` 加载，多 provider 并存，前端可下拉选
- **统一工具格式**：`complete()` 内部把 OpenAI/Anthropic 工具 schema 互转

**配置 `config/llm.json`**：
```json
{
  "providers": [
    {"name": "anthropic-local", "type": "anthropic",
     "base_url": "http://127.0.0.1:3456", "model": "opengo/deepseek-v4-flash", "api_key": "env:ANTHROPIC_API_KEY"},
    {"name": "deepseek", "type": "openai_compat",
     "base_url": "https://api.deepseek.com", "model": "deepseek-chat", "api_key": "env:DEEPSEEK_API_KEY"}
  ]
}
```

### 5.4 草稿→发布 + 版本管理（SQLite）

**核心资产必须持久化**。Schema：

```sql
CREATE TABLE strategies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',   -- 'draft' | 'published'
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE strategy_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id INTEGER NOT NULL REFERENCES strategies(id),
    version INTEGER NOT NULL,
    source TEXT NOT NULL,          -- Python 源码
    description TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',   -- 版本级: draft | published
    metrics_json TEXT,             -- 达标发布时的指标快照
    goal TEXT,                     -- 达标时的目标
    published_at TEXT,
    created_at TEXT,
    UNIQUE(strategy_id, version)
);

CREATE TABLE chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,            -- 'user' | 'assistant' | 'system'
    content TEXT NOT NULL,
    created_at TEXT
);
```

**语义**：
- `register_strategy` → 新草稿版本，`status='draft'`，版本号递增
- `publish_strategy` → 某草稿版本标记 `status='published'` + 记录指标快照 + 发布时间
- 策略级 `status='published'` = 存在 ≥1 个已发布版本；当前发布版本 = 最新已发布版
- 版本历史可查（`GET /api/strategies/{name}/versions`），可回滚（发布旧版本）

### 5.5 SSE 超时处理（无事件持久化）

**核心认知：SSE 连接 ≠ 等待回测完成。** 单进程、退出即失效，因此**不做事件落盘**，用内存事件队列。

```
POST /api/chat            → 起 agent 循环, 事件追加到 session 内存队列
GET /api/chat/events?session_id=X
   ├─ 先排空队列里未消费事件 (读取后清除)
   ├─ 再阻塞等待新事件 (条件变量), 超时(~15s)发心跳 `: ping`
   └─ 连接断重连 → 重新排空队列（未读事件仍在内存）→ 补齐已错过进度
```

- **heartbeat**：SSE 每 ~15s 推 `: ping` 注释行，维持连接不被中间代理掐断
- **回测等待时持续推送**：提交回测后推「🔄 回测进行中 job #3」，完成推「✅ 结果」—— 让浏览器知道连接活着、在等什么
- **前端自动重连**：`EventSource` 自带重连 + 事件补齐
- **不保留**：中间进度事件（读取后清除，进程退出丢失）

### 5.6 存储分层

| 数据 | 存储 | 生命周期 |
|---|---|---|
| 策略（源码/版本/发布状态/指标快照） | SQLite | 持久化，退出不丢 |
| 聊天历史（用户消息 + AI 最终汇报） | SQLite | 持久化，退出不丢 |
| 中间进度事件 | 内存队列 | 读取后清除，退出丢弃 |

## 6. LLM 工具集

| 工具 | 作用 | 关键参数 | 说明 |
|---|---|---|---|
| `list_symbols` | 选标的 | `type` / `keyword` | 复用现有 registry，返回精简列表 |
| `run_backtest` | 跑回测 | `symbol`, `strategy_ref`, `params`, `freq`, `start/end` | **异步**：提交后返回 `job_id`，不阻塞 |
| `register_strategy` | 提交/改策略 | `name`, `source`, `description` | 源码过 AST 沙箱；存为草稿 |
| `list_strategies` | 看当前策略 | — | 草稿 + 已发布 + 版本 |
| `publish_strategy` | 发布达标策略 | `name`, `version?` | 只发布达标版本；记录指标快照 |
| `check_goal` | 校验是否达标 | 指标 + 目标约束 | **LLM 自己判断**是否达成 |

**关键点**：
- `run_backtest` 的 `strategy_ref` **引用当前草稿**：LLM 先 `register_strategy` 存草稿，回测时用策略名引用「当前草稿版本」。草稿更新 → 下次回测自动用新草稿。回测结果与草稿版本强关联
- `check_goal` 由 LLM 自己调用：读指标后填入目标约束，代码层校验
- **上下文精简**：回测结果只返回指标 + 最近 N 笔交易 + 权益曲线尾部，避免撑爆上下文
- 每次工具完成 → 推 SSE 事件到聊天

## 7. 循环控制参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `max_turns` | 10 | 最大 LLM 工具调用轮次 |
| `max_tools_per_turn` | 5 | 每轮最多并发工具数（并行回测上限） |
| `max_backtest_jobs` | 20 | 会话内最多回测任务数 |
| `job_timeout` | 300s | 单个回测超时（分钟线更久） |
| `max_context_chars` | ~50k | 状态快照内容上限 |
| 重试 | 3次指数退避 | LLM API 调用失败重试 |

**错误处理**：
- 工具执行失败 → `is_error: true` 的 tool_result（LLM 读到错误后调整）
- LLM API 失败 → 重试 N 次，超过放弃并汇报
- 循环超 max_turns → 停止，LLM 收到「已到上限」，出最终总结
- 用户新消息打断旧循环 → 旧 agent 线程置取消标记，新循环接管

## 8. API 设计（新增）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/chat` | 发消息（目标或后续指令），返回 session_id；后台起 agent 循环 |
| GET | `/api/chat/events?session_id=X` | SSE 事件流（内存队列 + 心跳） |
| GET | `/api/chat/sessions` | 历史会话列表 |
| GET | `/api/strategies/published` | 已发布策略列表 |
| GET | `/api/strategies/{name}/versions` | 策略版本历史 |
| POST | `/api/strategies/{name}/publish` | 手动发布（可选，供 Web 端按钮） |
| GET | `/api/providers` | 列出已配置的 LLM Provider |

## 9. 聊天 UI

web/index.html 新增聊天面板：

```
┌─────────────────────────────────────┐
│  🤖 AI 目标优化                      │
│  用户: 在沪深300上做年化收益10%       │
│  目标: 年化≥10%, 回撤≤15%            │
│  系统: 🔄 正在选标的...               │
│  系统: ✅ 回测 510300 均线 完成       │
│        年化 8.2% 回撤 -12% 未达标     │
│  系统: 🔄 修改策略 → 新草稿 v3        │
│  系统: ✅ 回测 510300 RSI 完成        │
│        年化 12.4% 回撤 -14% 🎉 达标   │
│  系统: 📌 发布 v3 (指标快照已存)      │
│  AI: 目标达成！已发布策略...           │
│  [ 输入目标或指令... ]  [发送]        │
└─────────────────────────────────────┘
```

## 10. 新增模块结构

```
api/agent/
├── __init__.py
├── provider.py    # LLMProvider 抽象 + Anthropic/OpenAICompat 实现 + registry
├── executor.py    # BacktestExecutor 线程池
├── store.py       # StrategyStore + ChatStore (SQLite)
├── agent.py       # LLMAgent 工具循环 + 状态机 + 事件广播
└── tools.py       # 工具实现 (list_symbols/run_backtest/...)
config/
└── llm.json       # Provider 配置
```

## 11. 测试

- Provider：Anthropic/OpenAICompat 的 complete() 调用（mock）
- 工具：run_backtest 异步提交/批量等待、register_strategy 存草稿、publish_strategy 只发布达标、check_goal 判定
- 循环：状态机推进、max_turns 停止、错误处理、打断
- 存储：SQLite 增删改查、版本递增、发布快照
- API：/api/chat、SSE 事件、providers、published

## 12. 已知限制 / 非目标

- 单进程、无断点恢复：中间进度事件不落盘（已确认）
- LLM 上下文只保留状态快照，不做完整迭代历史回放
- 不接入实盘下单，仅回测
- AST 沙箱校验非安全边界（沿用现有策略）
