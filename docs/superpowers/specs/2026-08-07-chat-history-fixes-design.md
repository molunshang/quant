# 聊天历史侧栏修复 + 测试隔离 + 事实库清理 设计文档

日期：2026-08-07
状态：已确认待实现
上游：2026-08-07-ai-optim-history-design.md（历史会话/工具调用详情/策略查看 基础能力）

## 1. 背景与需求

`/chat` 页的历史侧栏与测试体系存在四个问题：

1. **历史排序不一致**：`/api/chat/history` 在 Python 侧按 `updated_at` 降序排序，但同秒并列（177 会话中有 30 个共享秒级时间戳）时顺序退化为 `list_sessions()` 的 `ORDER BY session_id`（字母序，store.py:334），表现为"最新会话没排最前"。
2. **测试污染事实库**：`tests/test_agent_gate.py` 与 `tests/test_llm_config.py` 调用 `register_agent_routes(app)` 时**未注入 store**，默认落到 `data/agent.db`；`test_agent_api.py::test_chat_returns_session` 还会**真实调用本地 LLM**（`anthropic-local` → localhost:3456）并在后台线程写库。事实库已积累 177 个测试会话。
3. **侧栏页面过长**：历史列表一次性全量渲染，无滚动容器、无增量加载。
4. **事实库有垃圾数据**：175 个 `hi` / `做年化10%` / `smoke` / `x` 测试会话，以及 `smoke_strat` 发布策略。

## 2. 需求清单（已与用户逐条确认）

1. **历史记录按时间倒排**（最新在前），同秒并列也要确定顺序 —— 依赖确定的排序键。
2. **清除事实库测试垃圾**，保留两个真实会话与真实策略（见 §6）；同时**修改现有单测，禁止直接写事实库**——mock 或影子库。
3. **左侧历史列表可滚动 + 增量加载**，避免一次性加载全部数据。

## 3. 技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| 排序/分页 | SQL `GROUP BY` + `ORDER BY updated_at DESC, MAX(id) DESC, session_id ASC` + `LIMIT/OFFSET` | 一条 SQL 产出有序摘要；`MAX(id)` 作为同秒并列 tie-breaker（id 单调，反映写入次序） |
| 增量加载 | 后端 offset 分页 + 前端 `IntersectionObserver` 哨兵 | 前端滚到底自动追加；避免游标分页对并列时间戳的复杂处理 |
| 测试隔离 | 影子库（`QUANT_AGENT_DB` 环境变量）+ 逐测试 `tmp_path` 注入 + 假 provider | 双保险：兜底影子库保证默认路径永不碰事实库，显式注入保证隔离性 |
| 事实库清理 | 一次性脚本（手工执行） | 保留真实数据，删除测试垃圾 |

## 4. 后端改动（`api/agent/store.py` + `api/agent/api.py`）

### 4.1 影子库路径（store.py）

`DB_PATH` 支持环境变量覆盖，测试可整体重定向：

```python
DB_PATH = os.environ.get("QUANT_AGENT_DB") or os.path.join(DB_DIR, "agent.db")
```

### 4.2 `ChatStore.session_summaries(offset, limit)`

单条 SQL 聚合，替代现有 `chat_history` 的 N+1 循环（每会话 `list_messages` + `list_session_strategies`）：

```python
def session_summaries(self, offset, limit) -> list[dict]:
    rows = self.conn.execute("""
        SELECT session_id,
               MAX(id) AS last_id,
               MIN(created_at) AS created_at,
               MAX(created_at) AS updated_at,
               COUNT(*) AS message_count,
               (SELECT content FROM chat_messages u
                 WHERE u.session_id = m.session_id AND u.role = 'user'
                 ORDER BY u.id LIMIT 1) AS first_user
        FROM chat_messages m
        GROUP BY session_id
        ORDER BY MAX(created_at) DESC, MAX(id) DESC, session_id ASC
        LIMIT ? OFFSET ?
    """, (limit, offset)).fetchall()
    return [{
        "session_id": r["session_id"],
        "title": (r["first_user"] or "")[:30] + ("…" if len(r["first_user"] or "") > 30 else ""),
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
        "message_count": r["message_count"],
    } for r in rows]
```

排序键：`updated_at DESC`（秒级），同秒并列时 `MAX(id) DESC`（同秒内后写入的更大 id 排前），最后 `session_id ASC` 兜底——完全确定顺序。

### 4.3 批量状态查询

- `StrategyStore.list_session_strategy_names(session_ids: list[str]) -> dict[str, list[str]]`：一条 `WHERE session_id IN (...)` 查询，返回 `{session_id: [strategy_names]}`。
- `AgentSessionStore.get_many(session_ids: list[str]) -> dict[str, dict]`：一条 `WHERE session_id IN (...)` 查询，返回 `{session_id: row}`。

### 4.4 `/api/chat/history` 端点（api.py）

```python
@app.get("/api/chat/history")
def chat_history(offset: int = 0, limit: int = 30):
    limit = max(1, min(limit, 100))      # clamp
    offset = max(0, offset)
    summaries = chat_store.session_summaries(offset, limit)
    ids = [s["session_id"] for s in summaries]
    names = store.list_session_strategy_names(ids) if ids else {}
    rows = session_store.get_many(ids) if ids else {}
    sessions = [{
        **s,
        "strategy_names": names.get(s["session_id"], []),
        "status": rows.get(s["session_id"], {}).get("status") or "done",
    } for s in summaries]
    return {"sessions": sessions, "has_more": len(sessions) == limit}
```

响应新增 `has_more`（前端据此判断是否继续加载）；默认 30 条/页。

## 5. 前端改动（`web/chat.html`）

### 5.1 滚动容器

`#chatHistory` 固定视口高度、独立滚动（不再撑长页面）：

```css
.layout { height: calc(100vh - 140px); align-items: stretch; }
#chatHistory {
  flex: 0 0 280px;
  display: flex; flex-direction: column;
  min-height: 0;
  border-right: 1px solid var(--border);
  padding-right: 8px;
}
#historyList { flex: 1 1 auto; overflow-y: auto; min-height: 0; }
```

### 5.2 增量加载

- 状态：`historyPage` / `historyDone` / `historyLoading`。
- `loadHistory(reset=true)`：reset 时清空列表、`historyPage=0`、`historyDone=false`；`fetch('/api/chat/history?offset=' + historyPage*30 + '&limit=30')`，追加渲染；`historyPage++`。
- 底部哨兵 `<div id="historySentinel">`；`IntersectionObserver` 进入视口且 `!historyDone && !historyLoading` 时加载下一页。加载失败停止（`historyDone=true`）。
- 事件时机：
  - 页面加载、`/done` SSE、`/error` SSE 后：`loadHistory(true)` 刷新并重置分页。
  - 新建会话：清空、重置分页、自动选中已加载的最新会话。
  - `selectSession` 逻辑不变（在已加载数据中取最新）。

## 6. 事实库清理（一次性，手工执行）

执行前先备份 `data/agent.db`。清理策略（Option B）：

- **删除**：除 `474dee0be1a6`、`43eff297c199` 外的 175 个会话及其 `chat_messages` / `tool_calls` / `session_strategies` / `agent_sessions` 行。
- **保留**：`474dee0be1a6`（11 条消息、26 工具调用、4 个策略关联）、`43eff297c199`（14 条消息）。
- **删除策略**：`smoke_strat`（`strategies` + `strategy_versions`）。
- **保留策略**：`etf_ma_cross`、`etf_trend`（真实使用）。
- **状态修正**：`43eff297c199` 卡死的 `agent_sessions.status='running'` 置为 `done`。

脚本执行后校验行数：`chat_messages` 应为 2 个会话的 25 条，`tool_calls` 26 条，`session_strategies` 4 条，`agent_sessions` 2 行，`strategies` 2 个。

## 7. 测试改造（禁止直接写事实库）

### 7.1 影子库兜底（`tests/conftest.py`）

新增 `tests/conftest.py`，在导入测试模块前把默认 DB 路径整体重定向到会话级临时目录，并自动清理：

```python
import os, tempfile, pytest

@pytest.fixture(scope="session", autouse=True)
def _shadow_db():
    tmp = tempfile.mkdtemp(prefix="quant-test-db-")
    os.environ["QUANT_AGENT_DB"] = os.path.join(tmp, "agent.db")
    yield
    del os.environ["QUANT_AGENT_DB"]
```

效果：即使某测试忘记注入 store，默认 `StrategyStore()`/`ChatStore()`/`AgentSessionStore()` 也落到影子库，**永远不写 `data/agent.db`**。

### 7.2 逐测试隔离

- **`tests/test_agent_api.py`**：
  - `test_chat_returns_session`：当前会真实调用本地 LLM 并在后台线程写库。改为注入假 config（`ProviderConfigStore` 指向含一个 provider 的临时 llm.json）+ 假 provider（复用 `FakeGateProvider` 的 `complete()` 契约）+ 隔离 store；等待后台线程落库后断言 `session_id`。消除真实网络与事实库写入。
  - `test_providers_endpoint` / `test_published_strategies_endpoint`：改用 `_make_client` 隔离客户端。
- **`tests/test_agent_gate.py:389`**（`test_chat_endpoint_still_returns_session`）：`register_agent_routes(app)` 未注入 store → 注入 tmp_path store / session_store / chat_store。
- **`tests/test_llm_config.py` `_make_client`**：注入 tmp_path store（config 已隔离到 `QUANT_LLM_CONFIG`）。

### 7.3 新增测试

**store 层（test_agent_store.py）**：
- `session_summaries` 排序（updated_at 降序）、分页（offset/limit）、标题截断、`MAX(id)` tie-breaker（同一秒两条消息）。

**API 层（test_agent_api.py）**：
- `/api/chat/history` 分页：多会话下 `offset` 返回正确切片、`has_more` 正确。

## 8. 错误处理与边界

- 无历史：`session_summaries` 返回 `[]`，`has_more=false`，侧栏「暂无历史」。
- `limit`/`offset` 非法（负数、过大）：`limit` clamp 到 `[1,100]`，`offset` 置 0。
- 分页数据变化：新会话产生时下次 `loadHistory(true)` 重置即可，无需补偿。
- 影子库兜底与显式注入叠加：显式 `tmp_path` store 优先级更高（覆盖环境变量默认路径）。

## 9. 验证

- `pytest -q` 全绿。
- 清理后 `data/agent.db` 仅剩 2 个真实会话；`curl "/api/chat/history?limit=100"` 确认排序确定（同秒并列不再乱序）、`has_more` 正确。
- 手工打开 `/chat`：侧栏高度受限可滚动，滚到底自动加载下一页，选中会话自动展开详情。

## 10. 不做的事（YAGNI）

- 不做游标分页 / 基于时间点的 keyset 分页（offset 足够，规模可控）。
- 不做会话删除/归档的 UI（无需求）。
- 不改 `/api/chat/sessions/{sid}` 详情端点（已满足）。
- 不给 `chat_messages` 建额外索引（现有规模 GROUP BY 足够；如未来膨胀再评估）。
