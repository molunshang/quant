# AI 优化历史查询 + 工具调用详情 + 策略查看 设计文档

日期：2026-08-07
状态：已确认待实现

## 1. 背景与需求

现有 `/chat`（AI 目标优化）页面为单栏：用户输入目标 → agent 选标的/写策略/回测/校验 → SSE 流式回显。存在三个缺口：

1. **无法查询历史优化会话**：会话记录在 SQLite 里（`chat_messages`、`agent_sessions`），但没有页面入口浏览历史；刷新即丢失上下文。
2. **agent 调用 function 时看不到具体信息**：工具调用事件只在内存 EventBus 里（读后即弃），且前端只显示「调用 X」，看不到传入参数与返回结果。
3. **优化产出的策略不可查看**：`strategies`/`strategy_versions` 已持久化源码/指标/目标，但页面无入口查看源码。

## 2. 需求清单（已与用户逐条确认）

1. **历史会话列表 + 详情（主从布局）**：页面左侧为历史优化会话列表，右侧为选中会话的详情；右侧内容随左侧选择变化。
2. **可续聊**：历史会话详情页底部输入框可继续向该会话发消息；goal-gate 状态已持久化，服务端支持续聊。新优化用「新建会话」。
3. **工具调用详情可查**：agent 调用 function 时记录参数与结果，历史与实时都可查看（刷新后仍在）。
4. **策略可查看**：会话产出的策略（草稿/发布）可点开查看源码、指标快照、目标。
5. **历史列表粒度为「优化会话」**：一条会话 = 一次对话/一个目标，可含多个策略版本。

## 3. 技术选型

沿用现有技术栈，不引入新依赖：

| 组件 | 选型 | 理由 |
|------|------|------|
| 持久化 | SQLite（现有 agent.db） | 与 `chat_messages`/`strategies` 同库，`CREATE TABLE IF NOT EXISTS` 免迁移 |
| 工具调用存储 | 新表 `tool_calls` | 关联到具体用户消息，刷新/重启后可查 |
| 会话-策略关联 | 新表 `session_strategies` | 关联到具体 `strategy_id` + `version` |
| 前端 | 重做 `web/chat.html` 为主从布局 | 复用 `common.css`/`common.js` |
| 实时推送 | 现有 SSE + EventBus | 不变 |

## 4. 数据模型（`api/agent/store.py`）

### 4.1 新增表

```sql
-- 工具调用持久化（需求 3 的核心）：关联到触发它的用户消息
CREATE TABLE IF NOT EXISTS tool_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  message_id INTEGER NOT NULL REFERENCES chat_messages(id),
  turn INTEGER NOT NULL,
  name TEXT NOT NULL,          -- list_symbols / run_backtest / register_strategy ...
  input_json TEXT NOT NULL,    -- 传入参数
  output_json TEXT,            -- 返回结果（错误时为 ERROR: ...）
  is_error INTEGER NOT NULL DEFAULT 0,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_message ON tool_calls(message_id);

-- 会话 → 策略版本 关联（需求 4）：精确定位该会话产出的某一版策略
CREATE TABLE IF NOT EXISTS session_strategies (
  session_id TEXT NOT NULL,
  strategy_id INTEGER NOT NULL REFERENCES strategies(id),
  version INTEGER NOT NULL,
  created_at TEXT,
  PRIMARY KEY (session_id, strategy_id, version)
);
```

### 4.2 store 方法新增

**ChatStore**：
- `add_tool_call(session_id, message_id, turn, name, input, output, is_error) -> int`
- `list_tool_calls(session_id) -> list[dict]`（按 message_id 分组后由 API 层组织）

**StrategyStore**：
- `link_session_strategy(session_id, name, version)` — 内部解析 `name` → `strategy_id`，INSERT 关联
- `list_session_strategies(session_id) -> list[dict]` — 返回 `[{name, version, status, source, metrics, goal}]`（JOIN strategies + strategy_versions）

**StrategyStore.get_versions**：SQL 已 SELECT `source` 但未放入返回 dict，补上 `source` 字段（需求 4 前端展示源码）。

**StrategyStore.register_draft**：返回值补 `strategy_id`，供 `link_session_strategy` 使用。

## 5. Agent 循环改动（`api/agent/agent.py` + `api/agent/api.py`）

### 5.1 `LLMAgent`

- 构造时新增 `chat_store` 依赖
- `run()` 新增 `message_id` 参数（触发本轮的 user 消息 id）
- 工具执行处（`tool_results` 循环内）同步记录，成功与出错都记：

```python
# 成功分支
chat_store.add_tool_call(session_id, message_id, turn, tc.name, tc.input, out, is_error=False)
# except 分支
chat_store.add_tool_call(session_id, message_id, turn, tc.name, tc.input, str(e), is_error=True)
```

- `register_strategy` 工具成功后，调用 `store.link_session_strategy(session_id, name, version)`。实现方式：`AgentToolContext` 增加 `session_id` 字段；`tools.register_strategy` 内 `if ctx.session_id: ctx.store.link_session_strategy(...)`。

### 5.2 `api.py`

- `/api/chat`：`chat_store.add_message` 拿返回值 `msg_id`，透传 `handle_chat(..., message_id=msg_id)` → `agent.run(message_id=msg_id)`
- `AgentToolContext(store, executor, session_id=...)` 传入 session_id

### 5.3 时序

用户消息(msg_id) → agent.run(turn 1..N) → 每 turn 工具调用记一条 `tool_calls` → 完成后 assistant 汇报入库。右侧时间线按 `message_id` 分组，一条消息一簇工具调用。

## 6. API 端点（`api/agent/api.py`）

### 6.1 新增（只读）

```python
@app.get("/api/chat/history")
# → {"sessions": [
#     {"session_id", "title", "created_at", "updated_at", "message_count",
#      "strategy_names", "status"}]}
# 按 updated_at 降序，最新在前；title = 首条 user 消息前 30 字（截断加省略号）
# status 取 agent_sessions 最新值；无行时视为 "done"

@app.get("/api/chat/sessions/{sid}")
# → {"session_id",
#     "messages": [{"id", "role", "content", "created_at",
#                   "tool_calls": [{"name", "input", "output", "is_error", "turn"}]}],
#     "strategies": [{"name", "version", "status", "source", "metrics", "goal"}]}
# 未知 sid → 404
```

### 6.2 复用/补字段

- `get_versions` 补 `source`（见 4.2）
- `/api/strategies/{name}/versions` 已存在，无需新端点

## 7. 前端（`web/chat.html` 重做为主从布局）

### 7.1 布局

```
┌────────────────────────────────────────────────┐
│  顶部共享导航（common.css 提供）                  │
├──────────────┬─────────────────────────────────┤
│ 左栏 历史会话 │  右栏 会话详情                     │
│ ───────────  │  ┌────────────────────────────┐ │
│ [+ 新建会话]  │  │ 消息时间线                   │ │
│ 会话1 (时间)  │  │  用户: 在沪深300做到年化10%  │ │
│ 会话2 (时间)  │  │  ▸ 调用 list_symbols  (可展开)│ │
│ 会话3 (时间)  │  │  ▸ 调用 register_strategy   │ │
│              │  │  AI: 完成，年化11.2%         │ │
│              │  ├────────────────────────────┤ │
│              │  │ 📋 产出策略                  │ │
│              │  │  [ma_v3 已发布] [ma_v2 草稿]  │ │
│              │  ├────────────────────────────┤ │
│              │  │ [输入框] [发送]               │ │
│              │  └────────────────────────────┘ │
└──────────────┴─────────────────────────────────┘
```

### 7.2 交互细节

- **左栏历史列表**：`/api/chat/history` 渲染；点击某项 → `/api/chat/sessions/{sid}` 渲染右栏
- **工具调用块**：默认折叠，显示 `调用 register_strategy`；点击展开显示**完整**输入 JSON + 输出 JSON（`is_error` 标红）
- **产出策略**：显示状态徽标（已发布/草稿）；点击在下方展示该版本源码（`<pre>`）、指标快照（`metrics`）、目标（`goal`）
- **续聊**：选中会话后底部输入框直接发消息（`session_id` 复用）；goal-gate 状态从 SQLite 恢复
- **新建会话**：重置 `session_id`，清空右栏时间线
- **实时 vs 历史**：选中会话发新消息后，SSE 事件流追加到当前右栏，同时记录入库
- 页面加载自动选中第一个会话
- **选中会话状态**：`running` 禁用输入框显示「运行中…」；`pending_clarify/confirm` 显示待澄清/待确认提示并可继续输入

## 8. 错误处理与边界

- 无历史/空会话：`/api/chat/history` 返回 `{sessions: []}`，左栏「暂无历史」，右栏空态引导
- 续聊未完成会话：见 7.2 状态处理
- 老库升级：`CREATE TABLE IF NOT EXISTS` 自动建表，无迁移脚本
- 工具调用入参出参**完整保留，不截断**：`run_backtest` 出参、`register_strategy` 源码输入都可能很长，但完整保留对复现/审计有价值。SQLite 适合存大 JSON blob；若后续膨胀再考虑分页/归档
- 并发写：沿用 `_synchronized`（per-instance RLock）保护新方法

## 9. 测试

沿用现有风格（`tmp_path` 隔离 DB / TestClient）：

**store 层（test_agent_store.py）**
- `add_tool_call` / `list_tool_calls` 按 message_id 过滤、含 is_error
- `link_session_strategy` + `list_session_strategies` 返回 `(strategy_id, version)`
- `get_versions` 返回 `source`

**agent 层（test_agent_agent.py）**
- 假 provider 触发工具调用 → 断言 `chat_store.list_tool_calls` 记录成功/错误两条，message_id 正确

**API 层（test_agent_api.py）**
- `/api/chat/history` 空库返回 `{sessions: []}`
- 写入一条消息 + 工具调用后，history 含该会话、detail 含消息与 tool_calls
- `/api/chat/sessions/{sid}` 未知 sid → 404

**前端（test_api.py::test_web_pages）**
- 断言 `/chat` 页面包含主从布局标记（如 `chatHistory` / `chatDetail` id）

## 10. 不做的事（YAGNI）

- 不做会话内「策略对比」/「指标可视化」
- 不做会话删除/归档
- 不做多会话合并/导出
- 不做工具调用的分页（当前会话规模内全量返回即可）
