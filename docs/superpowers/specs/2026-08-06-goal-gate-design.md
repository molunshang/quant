# 目标门卫（Goal Gate）：提交目标后先澄清/确认再执行

日期：2026-08-06

## 背景与问题

当前 LLM Agent 在 `POST /api/chat` 提交目标后立即启动工具循环（选标的 → 写策略 → 回测 → 校验）。若目标模糊（如"在沪深300上做到年化10%"缺少回撤约束、或"跑赢大盘"未定义基准），Agent 只能自行猜测推进，可能跑偏方向、浪费大量回测轮次，且用户没有机会在开跑前纠正目标理解。

需求：目标提交后**不能直接开跑**，须先确保目标完全明确（缺失/冲突信息向用户澄清），并**始终先确认再执行**；确认无误后才进入现有工具循环。

## 设计决策（已与用户确认）

1. **明确性判断 = 结构化提取 + LLM 兜底**：先做结构化字段提取；字段缺失/冲突时生成针对性提问；语义模糊但字段齐全的场景（如"跑赢大盘"无基准），LLM 生成一个开放问题兜底。
2. **暂停/恢复 = 挂起会话 + 下一条消息恢复**：SSE 推送问题/确认单后本轮结束；用户在**同一会话**再发一条消息即视为答复/确认，后端带历史恢复。
3. **确认门槛 = 始终先确认再执行**：任何目标开跑前都先出「目标理解确认单」，用户确认或修改后才执行。
4. **会话状态 = 持久化到 SQLite**：服务重启后挂起会话仍可恢复，刷新页面可继续。
5. **回复方式 = 同一输入框自由回复**：前端无快捷选项按钮，用户直接回复文本。

## 架构选型：方案 A — 执行前门卫

确认逻辑做成独立「门卫」模块，置于现有 `LLMAgent.run()` 之前；确认后把结构化目标注入 system prompt，原循环照跑。不引入执行中 `ask_user` 工具（方案 B，本需求不要求）。

## 1. 会话状态机

每个会话有且仅有一个状态，持久化在 SQLite 新表 `agent_sessions`。`idle` 不在表中——新会话无行即 `idle`，会话行创建时从 `pending_clarify` 或 `pending_confirm` 开始：

```
idle(无行) → pending_clarify ⇄ pending_confirm → running → done
```

| 状态 | 含义 | 用户发消息时后端行为 |
|------|------|------|
| `idle` | 新会话 / 上次完成 | 启动门卫 |
| `pending_clarify` | 目标字段缺失/冲突，已抛出问题 | 视为答复 → 重新提取 |
| `pending_confirm` | 已出确认单，等用户拍板 | 识别确认词→开跑；否则按修改意见重提 |
| `running` | 工具循环执行中 | 拒绝："正在运行中" |
| `done` | 已出最终汇报 | 启动新门卫 |

**`agent_sessions` 表**：

```sql
CREATE TABLE agent_sessions (
  session_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,                 -- pending_clarify/pending_confirm/running/done（idle=无行）
  goal_json TEXT,                       -- 累积的结构化目标 {universe, constraints, period, benchmark}
  questions_json TEXT,                  -- 待澄清问题（重连后前端可复显）
  confirm_summary_json TEXT,            -- 确认单
  updated_at TEXT
);
```

## 2. 目标门卫模块（新文件 `api/agent/gate.py`）

纯逻辑、可单测，不碰现有 agent 循环。五个函数：

- **`extract(message, history, provider) -> GoalExtraction`** — 一次结构化 LLM 调用，输出 JSON：`universe`（标的范围）、`constraints`（`{annual_return, max_drawdown, sharpe, ...}`）、`period`（`{start, end}`）、`benchmark`（基准与超额定义）。数值字段做正则/数字解析兜底，防 LLM 乱编。
- **`missing_fields(extraction) -> list[str]`** — 哪些**关键字段**缺失/冲突。关键字段 = `universe`（标的范围）+ `constraints`（量化约束）。`period`/`benchmark` 有默认值，缺失**不**触发澄清，但在确认单中展示默认值并标注"（默认，可修改）"，用户可在确认时修改。
- **`build_questions(missing, extraction) -> list[str]`** — 对缺失的关键字段生成针对性问题；语义模糊但字段齐全的场景（如"跑赢大盘"但 benchmark 是默认的沪深300，与用户本意可能不符）由 LLM 兜底生成一个开放问题。
- **`build_confirmation_summary(extraction) -> dict`** — 输出确认单，**始终含四项**：标的范围、量化约束、时间区间（缺省填 2020-2024）、基准与超额定义（缺省"沪深300 绝对收益"）。缺省值明确标注"（默认，可修改）"。
- **`is_confirmed(text) -> bool`** — 确认词匹配（确认/没问题/可以/开始/OK/对）。

**流程**：`extract` → `missing_fields`（关键字段）非空 → `build_questions` → 挂起 `pending_clarify`；关键字段齐 → 直接 `build_confirmation_summary` → 挂起 `pending_confirm`。最小路径（目标完整）= 仅一次确认暂停；模糊路径 = 澄清→确认两次。

**`extract` 的输入**：`POST /api/chat` 的 `message` 为主；若 body 同时携带显式 `goal` 字段（现有 API 支持），合并进提取上下文作为用户目标的补充说明。

## 3. 后端流程（`api.py` + `agent.py`）

`POST /api/chat` 根据会话状态分派（不再是"每次开新 run"）：

- `idle`（无会话行）/`done` → 启动门卫线程 → SSE 推 `clarify` 或 `confirm` 事件
- `pending_clarify` → 用户答复追加进历史 → 重新 `extract` → 仍有缺失推新问题，字段齐则转 `pending_confirm`
- `pending_confirm` → `is_confirmed` 命中 → 状态转 `running`，把确认后的结构化目标注入 system prompt 后启动**现有** `LLMAgent.run()`；未命中（用户要改）→ 按修改意见重新提取/重出确认单
- `running` → 返回 `{"error": "正在运行中"}`

**`build_system_prompt` 扩展**：注入确认后的 `universe` / `constraints` / `period` / `benchmark`，约束 LLM 选标的范围、回测区间，并在调 `check_goal` 时用确认的阈值。

**新增 SSE 事件**：

- `{"type": "clarify", "questions": [...]}` — 进入 `pending_clarify`
- `{"type": "confirm", "summary": {...}, "text": "..."}` — 进入 `pending_confirm`
- `{"type": "running"}` — 确认后开跑
- `{"type": "error", "error": "..."}` — 复用，含"正在运行中"

现有 `turn/tool/backtest_results/done` 不变。

## 4. 前端改动（`web/index.html`）

- **事件处理扩展**（`chatConnect` 的 `onmessage`）：
  - `clarify` → 显示「🤔 需要确认：Q1 / Q2 / ...」，`chatStatus` 显示「请回复以澄清」，输入框**保持可用**
  - `confirm` → 显示确认单（`text` 全文），`chatStatus` 显示「请确认或修改后回复」，输入框可用
  - `running` → `chatStatus` 显示「已确认，开始执行…」
  - 现有 `turn/tool/backtest_results/done/error` 不变
- **输入框不再自动禁用**：改为澄清/确认阶段保持可用，`running` 时才禁用

无新组件、无新 API 调用。

## 5. 测试方案

新增 `tests/test_agent_gate.py`，覆盖门卫纯逻辑（可单测、无网络）：

| 用例 | 断言 |
|------|------|
| 完整目标 → `missing_fields` 为空 | 只走 `confirm`，无 `clarify` |
| 缺失约束 → `missing_fields` 列出缺失项 | 走 `clarify`，问题含对应字段 |
| `is_confirmed` 确认词识别 | 确认/可以/没问题/OK/对 → True |
| 用户要求修改 → 非确认词 | False，触发重新提取 |
| 数值提取兜底（"10%"→0.10，"15%"→-0.15） | 解析正确 |
| 缺省值注入（无时间区间→2020-2024；无基准→沪深300绝对收益） | 确认单含默认值 |

状态机分派用 `FakeProvider` 走 `POST /api/chat`，沿用现有 `test_agent_api.py` 的 testclient 模式。门卫本身的 `extract` 是 LLM 调用——用 `FakeProvider` 返回固定 JSON 模拟，不触发真实网络。

## 6. 不做的事（YAGNI）

- 执行中 `ask_user` 工具 / 断点续跑（方案 B，本需求不要求）
- 前端快捷选项按钮（已定：同一输入框自由回复）
- 多轮自由对话 / 持久化完整对话树（确认单足够，不建对话系统）
- 修改现有 `LLMAgent.run()` 循环内部逻辑（只注入目标，不改循环）

## 涉及文件

| 文件 | 改动 |
|------|------|
| `api/agent/gate.py` | **新增**：门卫模块 |
| `api/agent/store.py` | 新增 `AgentSessionStore`（`agent_sessions` 表 + CRUD） |
| `api/agent/agent.py` | `build_system_prompt` 注入结构化目标 |
| `api/agent/api.py` | `POST /api/chat` 状态机分派 + 门卫线程 |
| `web/index.html` | SSE 事件处理 + 输入框禁用策略 |
| `tests/test_agent_gate.py` | **新增**：门卫逻辑 + 状态机分派测试 |
