# AI 优化历史查询 + 工具调用详情 + 策略查看 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `/chat` 页重做主从布局：左侧历史优化会话列表，右侧会话详情（消息时间线 + 可展开的完整工具调用入参出参 + 产出策略源码/指标/目标），并支持续聊。

**Architecture:** 新增两张持久化表（`tool_calls`、`session_strategies`），store 层补方法与字段；`LLMAgent` 工具执行循环内同步记录工具调用并把 `register_strategy` 产出关联到当前会话；API 层新增 `/api/chat/history` 与 `/api/chat/sessions/{sid}` 两个只读端点并把 `message_id` 透传给 agent；前端 `chat.html` 全量重写为主从布局。SSE/EventBus 不变，仅给 `tool`/`tool_error` 事件补 `input` 字段以便实时展开。

**Tech Stack:** FastAPI + uvicorn、SQLite（`CREATE TABLE IF NOT EXISTS` 免迁移）、原生 JS + SSE（无新依赖）、pytest（`tmp_path` 隔离 DB / TestClient）。

## Global Constraints

- **不引入任何新依赖**：沿用 FastAPI / SQLite / 原生 JS / 现有 pytest。
- **建表一律 `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS`**，不写迁移脚本。
- **新增 store 方法必须用 `_synchronized` 装饰器保护**（per-instance RLock，嵌套调用安全）。
- **工具调用入参出参完整保留，不截断**：`input_json` 用 `json.dumps(input, ensure_ascii=False)` 原样存，`output_json` 存工具返回的原始 JSON 字符串，错误分支存 `str(e)`。
- 新增 store 方法若被 agent 层工具调用，`name` 需先在 `strategies` 表存在（`link_session_strategy` 内部用 `_strategy_id(name)` 解析，幂等）。
- 代码注释与 UI 文案使用中文。
- 每条任务结束都跑测试并 commit，message 前缀 `feat:`。

---

### Task 1: store 层 — `tool_calls` 表 + ChatStore 方法

**Files:**
- Modify: `api/agent/store.py` — ChatStore 部分
- Test: `tests/test_agent_store.py`

**Interfaces:**
- Consumes: 无（本任务不依赖其他任务）。
- Produces:
  - `ChatStore.add_tool_call(session_id: str, message_id: int, turn: int, name: str, input: dict, output: str | None, is_error: bool) -> int`
  - `ChatStore.list_tool_calls(session_id: str) -> list[dict]`，每条 dict 含 `{id, message_id, turn, name, input, output, is_error, created_at}`，`input` 是 JSON 字符串，`is_error` 是 0/1。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_agent_store.py`）

```python
def test_tool_call_roundtrip(tmp_path):
    c = ChatStore(db_path=str(tmp_path / "t.db"))
    mid = c.add_message("s1", "user", "在沪深300做到年化10%")
    c.add_tool_call("s1", mid, 1, "list_symbols", {"type": "stock"}, '{"symbols": []}', False)
    c.add_tool_call("s1", mid, 2, "register_strategy",
                    {"name": "ma", "source": "def strategy(ctx,p): pass"}, '{"version": 1}', True)
    calls = c.list_tool_calls("s1")
    assert len(calls) == 2
    assert calls[0]["message_id"] == mid
    assert calls[0]["name"] == "list_symbols"
    assert calls[0]["turn"] == 1
    assert calls[0]["input"] == '{"type": "stock"}'
    assert calls[0]["output"] == '{"symbols": []}'
    assert calls[0]["is_error"] == 0
    assert calls[1]["is_error"] == 1
    # 按会话隔离
    c.add_message("s2", "user", "其他")
    assert c.list_tool_calls("s2") == []
    c.close()
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_agent_store.py::test_tool_call_roundtrip -q`
Expected: FAIL（`AttributeError: 'ChatStore' object has no attribute 'add_tool_call'`）

- [ ] **Step 3: 最小实现**（改 `api/agent/store.py`）

`ChatStore._init_schema` 的 `executescript` 内追加：

```python
        CREATE TABLE IF NOT EXISTS tool_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            message_id INTEGER NOT NULL REFERENCES chat_messages(id),
            turn INTEGER NOT NULL,
            name TEXT NOT NULL,
            input_json TEXT NOT NULL,
            output_json TEXT,
            is_error INTEGER NOT NULL DEFAULT 0,
            created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_tool_calls_message ON tool_calls(message_id);
```

`ChatStore` 类内新增方法：

```python
    @_synchronized
    def add_tool_call(self, session_id, message_id, turn, name, input, output, is_error) -> int:
        cur = self.conn.execute(
            "INSERT INTO tool_calls (session_id, message_id, turn, name, input_json, output_json, is_error, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (session_id, message_id, turn, name, json.dumps(input, ensure_ascii=False), output,
             1 if is_error else 0, _now()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    @_synchronized
    def list_tool_calls(self, session_id) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, message_id, turn, name, input_json, output_json, is_error, created_at "
            "FROM tool_calls WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [
            {"id": r["id"], "message_id": r["message_id"], "turn": r["turn"],
             "name": r["name"], "input": r["input_json"], "output": r["output_json"],
             "is_error": r["is_error"], "created_at": r["created_at"]}
            for r in rows
        ]
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_agent_store.py -q`
Expected: PASS（原 4 个测试 + 新 1 个）

- [ ] **Step 5: Commit**

```bash
git add api/agent/store.py tests/test_agent_store.py
git commit -m "feat: store 层新增 tool_calls 表与 add/list_tool_call"
```

---

### Task 2: store 层 — `session_strategies` 表 + StrategyStore 方法/字段

**Files:**
- Modify: `api/agent/store.py` — StrategyStore 部分
- Test: `tests/test_agent_store.py`

**Interfaces:**
- Consumes: Task 1 的 `_synchronized` 模式（无直接依赖，可并行）。
- Produces:
  - `StrategyStore.link_session_strategy(session_id: str, name: str, version: int) -> None` — 内部解析 `name → strategy_id` 后 INSERT，幂等（`INSERT OR REPLACE`）。
  - `StrategyStore.list_session_strategies(session_id: str) -> list[dict]`，每条 `{name, version, status, source, metrics, goal}`。
  - `StrategyStore.get_versions(name)` 返回 dict 增加 `"source"` 字段。
  - `StrategyStore.register_draft(...)` 返回 dict 增加 `"strategy_id"` 字段。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_agent_store.py`）

```python
def test_link_session_strategy_roundtrip(tmp_path):
    s = StrategyStore(db_path=str(tmp_path / "t.db"))
    s.register_draft("ma", "def strategy(ctx, p):\n    pass", "sma")
    s.publish_version("ma", 1, {"total_return": 0.2}, "年化>=10%")
    s.link_session_strategy("s1", "ma", 1)
    rows = s.list_session_strategies("s1")
    assert len(rows) == 1
    assert rows[0]["name"] == "ma"
    assert rows[0]["version"] == 1
    assert rows[0]["status"] == "published"
    assert "def strategy" in rows[0]["source"]
    assert rows[0]["metrics"]["total_return"] == 0.2
    assert rows[0]["goal"] == "年化>=10%"
    # 另一会话无关联
    assert s.list_session_strategies("s2") == []
    s.close()


def test_get_versions_includes_source(tmp_path):
    s = StrategyStore(db_path=str(tmp_path / "t.db"))
    s.register_draft("ma", "def strategy(ctx, p):\n    pass v1", "sma")
    versions = s.get_versions("ma")
    assert versions[0]["source"] == "def strategy(ctx, p):\n    pass v1"
    s.close()


def test_register_draft_returns_strategy_id(tmp_path):
    s = StrategyStore(db_path=str(tmp_path / "t.db"))
    r1 = s.register_draft("ma", "def strategy(ctx, p):\n    pass", "sma")
    r2 = s.register_draft("ma", "def strategy(ctx, p):\n    pass", "sma v2")
    assert isinstance(r1["strategy_id"], int)
    assert r1["strategy_id"] == r2["strategy_id"]  # 同名策略复用同一 id
    s.close()
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_agent_store.py -q`
Expected: FAIL（`AttributeError` / 断言缺失字段）

- [ ] **Step 3: 最小实现**（改 `api/agent/store.py`）

`StrategyStore._init_schema` 的 `executescript` 内追加：

```python
        CREATE TABLE IF NOT EXISTS session_strategies (
            session_id TEXT NOT NULL,
            strategy_id INTEGER NOT NULL REFERENCES strategies(id),
            version INTEGER NOT NULL,
            created_at TEXT,
            PRIMARY KEY (session_id, strategy_id, version)
        );
```

`StrategyStore` 类内新增方法：

```python
    @_synchronized
    def link_session_strategy(self, session_id, name, version) -> None:
        sid = self._strategy_id(name)
        self.conn.execute(
            "INSERT OR REPLACE INTO session_strategies (session_id, strategy_id, version, created_at) "
            "VALUES (?,?,?,?)",
            (session_id, sid, version, _now()),
        )
        self.conn.commit()

    @_synchronized
    def list_session_strategies(self, session_id) -> list[dict]:
        rows = self.conn.execute(
            "SELECT s.name, v.version, v.status, v.source, v.metrics_json, v.goal "
            "FROM session_strategies ss "
            "JOIN strategies s ON s.id = ss.strategy_id "
            "JOIN strategy_versions v ON v.strategy_id = s.id AND v.version = ss.version "
            "WHERE ss.session_id = ? ORDER BY ss.created_at, ss.strategy_id, ss.version",
            (session_id,),
        ).fetchall()
        return [
            {"name": r["name"], "version": r["version"], "status": r["status"],
             "source": r["source"],
             "metrics": json.loads(r["metrics_json"]) if r["metrics_json"] else None,
             "goal": r["goal"]}
            for r in rows
        ]
```

`get_versions` 返回 dict 加一行 `"source": r["source"],`（插到 `"version": ...` 之后）：

```python
            {
                "version": r["version"],
                "source": r["source"],
                "status": r["status"],
                ...
```

`register_draft` 返回改为：

```python
        return {"name": name, "version": version, "status": "draft", "strategy_id": sid}
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_agent_store.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/agent/store.py tests/test_agent_store.py
git commit -m "feat: store 层新增 session_strategies 关联表与 link/list 方法"
```

---

### Task 3: agent 层 — 记录工具调用 + 关联会话策略

**Files:**
- Modify: `api/agent/tools.py` — `AgentToolContext`、`register_strategy`
- Modify: `api/agent/agent.py` — `LLMAgent.__init__`、`run`
- Test: `tests/test_agent_agent.py` — 更新 `FakeStore`、新增 `FakeChatStore` 与 3 个新测试

**Interfaces:**
- Consumes: Task 1（`ChatStore.add_tool_call/list_tool_calls`）、Task 2（`StrategyStore.link_session_strategy/list_session_strategies`、`register_draft` 返回 `strategy_id`）。
- Produces:
  - `AgentToolContext.__init__(..., session_id: str | None = None)` 新字段。
  - `LLMAgent.__init__(provider, store, executor, chat_store=None, max_turns=10, max_tools_per_turn=5)` 新增 `chat_store`。
  - `LLMAgent.run(self, session_id, user_message, goal=None, bus=None, message_id=None) -> dict` 新增 `message_id`；进入时 `self._ctx.session_id = session_id`。
  - 工具执行循环：成功/出错分支都调 `chat_store.add_tool_call(...)`；`bus` 的 `tool`/`tool_error` 事件补 `input` 字段。
  - `tools.register_strategy` 成功后 `if ctx.session_id: ctx.store.link_session_strategy(ctx.session_id, name, rec["version"])`。

- [ ] **Step 1: 更新测试夹具 + 写失败测试**（改 `tests/test_agent_agent.py`）

把 `FakeStore` 的 `register_draft` 返回补 `strategy_id`，并新增 `link_session_strategy`：

```python
class FakeStore:
    def __init__(self):
        self.drafts = {}
        self.published = []
        self.linked = []
    def register_draft(self, name, source, description=""):
        v = len(self.drafts.get(name, [])) + 1
        self.drafts[name] = [source]
        return {"name": name, "version": v, "status": "draft", "strategy_id": v}
    def link_session_strategy(self, session_id, name, version):
        self.linked.append((session_id, name, version))
    def get_strategy(self, name):
        if name not in self.drafts:
            return None
        return {"name": name, "status": "draft", "current_version": len(self.drafts[name])}
    def get_source(self, name, version=None):
        if name not in self.drafts:
            return None
        return self.drafts[name][-1]
    def list_strategies(self, include_drafts=True):
        return [{"name": n, "status": "draft", "current_version": len(v)} for n, v in self.drafts.items()]
    def publish_version(self, name, version, metrics, goal):
        self.published.append((name, version))
        return {"name": name, "version": version, "status": "published", "metrics": metrics}
```

在 `FakeBus` 之后新增 `FakeChatStore`：

```python
class FakeChatStore:
    def __init__(self):
        self.calls = []
    def add_tool_call(self, session_id, message_id, turn, name, input, output, is_error):
        self.calls.append({
            "session_id": session_id, "message_id": message_id, "turn": turn,
            "name": name, "input": input, "output": output, "is_error": 1 if is_error else 0,
        })
        return len(self.calls)
    def list_tool_calls(self, session_id):
        return [c for c in self.calls if c["session_id"] == session_id]
```

在文件末尾追加 3 个测试：

```python
def test_agent_records_tool_calls():
    provider = FakeProvider([
        LLMResponse(text=None, tool_uses=[
            ToolCall(id="1", name="register_strategy",
                     input={"name": "ma", "source": "def strategy(ctx, p):\n    pass"}),
            ToolCall(id="2", name="run_backtest", input={"symbol": "510300", "strategy_ref": "ma"}),
        ]),
        LLMResponse(text="完成", tool_uses=[]),
    ])
    chat = FakeChatStore()
    bus = FakeBus()
    agent = LLMAgent(provider=provider, store=FakeStore(), executor=FakeExecutor(),
                     chat_store=chat, max_turns=5, max_tools_per_turn=5)
    agent.run("s1", "做年化10%", message_id=7, bus=bus)
    calls = chat.list_tool_calls("s1")
    assert len(calls) == 2
    assert calls[0]["name"] == "register_strategy"
    assert calls[0]["message_id"] == 7
    assert calls[0]["turn"] == 0
    assert calls[0]["input"]["name"] == "ma"
    assert calls[0]["is_error"] == 0
    assert calls[1]["name"] == "run_backtest"
    # SSE 的 tool 事件携带完整 input，前端可实时展开
    tool_evs = [e for e in bus.events if e.get("type") == "tool"]
    assert tool_evs[0]["name"] == "register_strategy"
    assert tool_evs[0]["input"]["name"] == "ma"


def test_agent_records_tool_error():
    provider = FakeProvider([
        LLMResponse(text=None, tool_uses=[
            ToolCall(id="1", name="publish_strategy", input={"name": "ma", "goal_met": False}),
        ]),
        LLMResponse(text="完成", tool_uses=[]),
    ])
    chat = FakeChatStore()
    agent = LLMAgent(provider=provider, store=FakeStore(), executor=FakeExecutor(),
                     chat_store=chat, max_turns=5, max_tools_per_turn=5)
    agent.run("s1", "做年化10%", message_id=7, bus=FakeBus())
    calls = chat.list_tool_calls("s1")
    assert len(calls) == 1
    assert calls[0]["name"] == "publish_strategy"
    assert calls[0]["is_error"] == 1
    assert "goal not met" in calls[0]["output"]


def test_agent_links_strategy_to_session(tmp_path):
    from api.agent.store import StrategyStore
    store = StrategyStore(db_path=str(tmp_path / "t.db"))
    chat = FakeChatStore()
    provider = FakeProvider([
        LLMResponse(text=None, tool_uses=[
            ToolCall(id="1", name="register_strategy",
                     input={"name": "ma", "source": "def strategy(ctx, p):\n    pass"}),
        ]),
        LLMResponse(text="完成", tool_uses=[]),
    ])
    agent = LLMAgent(provider=provider, store=store, executor=FakeExecutor(),
                     chat_store=chat, max_turns=5, max_tools_per_turn=5)
    agent.run("s1", "注册 ma", message_id=3, bus=FakeBus())
    linked = store.list_session_strategies("s1")
    assert len(linked) == 1
    assert linked[0]["name"] == "ma"
    assert linked[0]["version"] == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_agent_agent.py -q`
Expected: FAIL（`TypeError: __init__() got an unexpected keyword argument 'chat_store'`）

- [ ] **Step 3: 最小实现**

`api/agent/tools.py` — `AgentToolContext` 加 `session_id`：

```python
class AgentToolContext:
    def __init__(self, store, executor, data_layer=None, strategy_manager=None, session_id=None):
        self.store = store
        self.executor = executor
        self.data_layer = data_layer
        self.strategy_manager = strategy_manager or StrategyManager()
        self.session_id = session_id
```

`api/agent/tools.py` — `register_strategy` 成功后关联会话：

```python
def register_strategy(input_: dict, ctx: AgentToolContext) -> str:
    name = input_["name"]
    source = input_["source"]
    validate_strategy_source(source)  # AST sandbox — raises on invalid
    rec = ctx.store.register_draft(name, source, input_.get("description", ""))
    if ctx.session_id:
        ctx.store.link_session_strategy(ctx.session_id, name, rec["version"])
    return _json(rec)
```

`api/agent/agent.py` — 构造器与 `run` 签名：

```python
    def __init__(self, provider, store, executor, chat_store=None, max_turns=10, max_tools_per_turn=5):
        self.provider = provider
        self.store = store
        self.executor = executor
        self.chat_store = chat_store
        self.max_turns = max_turns
        self.max_tools_per_turn = max_tools_per_turn
        self._manager = StrategyManager()
        self._ctx = AgentToolContext(store=store, executor=executor, strategy_manager=self._manager)
```

```python
    def run(self, session_id: str, user_message: str, goal: str | None = None,
            bus: EventBus | None = None, message_id: int | None = None) -> dict:
        self._ctx.session_id = session_id
        system = build_system_prompt(goal or user_message)
```

`api/agent/agent.py` — 工具执行循环（`run` 内，`for tc in resp.tool_uses[: self.max_tools_per_turn]:`）改造为：

```python
            for tc in resp.tool_uses[: self.max_tools_per_turn]:
                try:
                    fn = self._tool_name_to_fn(tc.name)
                    out = fn(tc.input, self._ctx)
                    tool_results.append({
                        "tool_use_id": tc.id, "content": out, "is_error": False,
                    })
                    if bus:
                        bus.publish(session_id, {"type": "tool", "name": tc.name, "output": out, "input": tc.input})
                    if self.chat_store is not None and message_id is not None:
                        self.chat_store.add_tool_call(session_id, message_id, turn, tc.name, tc.input, out, is_error=False)
                except Exception as e:  # noqa: BLE001 - tool error surfaced to LLM
                    tool_results.append({
                        "tool_use_id": tc.id, "content": f"ERROR: {e}", "is_error": True,
                    })
                    if bus:
                        bus.publish(session_id, {"type": "tool_error", "name": tc.name, "error": str(e), "input": tc.input})
                    if self.chat_store is not None and message_id is not None:
                        self.chat_store.add_tool_call(session_id, message_id, turn, tc.name, tc.input, str(e), is_error=True)
```

- [ ] **Step 4: 运行确认通过 + 无回归**

Run: `python -m pytest tests/test_agent_agent.py tests/test_agent_gate.py -q`
Expected: PASS（含新增 3 个；gate 测试因 `run`/`handle_chat` 关键字参数新增而保持通过）

- [ ] **Step 5: Commit**

```bash
git add api/agent/tools.py api/agent/agent.py tests/test_agent_agent.py
git commit -m "feat: agent 工具调用入参出参入库并关联会话策略"
```

---

### Task 4: API 层 — 历史/详情端点 + message_id 透传 + 可注入路由

**Files:**
- Modify: `api/agent/api.py`
- Test: `tests/test_agent_api.py`

**Interfaces:**
- Consumes: Task 1、2、3 的方法与签名。
- Produces:
  - `handle_chat(session_id, message, goal, provider, bus, session_store, chat_store, store, executor, message_id=None)` — 新增关键字参数 `message_id`；确认分支 `LLMAgent(..., chat_store=chat_store)` 且 `agent.run(..., message_id=message_id)`。
  - `register_agent_routes(app, bus=None, store=None, chat_store=None, executor=None, config=None, session_store=None)` — 全参数可选注入，缺省建真实实例（`api/main.py` 调用不变）。
  - `GET /api/chat/history` → `{"sessions": [{session_id, title, created_at, updated_at, message_count, strategy_names, status}]}`，按 `updated_at` 降序。
  - `GET /api/chat/sessions/{sid}` → `{"session_id", "messages": [{id, role, content, created_at, tool_calls: [{name, input, output, is_error, turn}]}], "strategies": [...]}`；未知 sid → 404。
  - `/api/chat` 拿到 `add_message` 返回的 `msg_id`，透传 `handle_chat(..., message_id=msg_id)`。

- [ ] **Step 1: 写失败测试**（改 `tests/test_agent_api.py`，文件顶部 import 区追加）

```python
from fastapi import FastAPI, TestClient
from api.agent.api import register_agent_routes
from api.agent.store import StrategyStore, ChatStore, AgentSessionStore
```

文件末尾追加：

```python
def _make_client(tmp_path):
    """隔离的 agent 路由：注入 tmp_path 下的 store，不碰真实 data/agent.db。"""
    app = FastAPI()
    store = StrategyStore(str(tmp_path / "s.db"))
    chat = ChatStore(str(tmp_path / "c.db"))
    sess = AgentSessionStore(str(tmp_path / "a.db"))
    register_agent_routes(app, store=store, chat_store=chat, session_store=sess)
    return TestClient(app), store, chat, sess


def test_chat_history_empty(tmp_path):
    client, *_ = _make_client(tmp_path)
    r = client.get("/api/chat/history")
    assert r.status_code == 200
    assert r.json() == {"sessions": []}


def test_chat_history_and_detail(tmp_path):
    client, store, chat, sess = _make_client(tmp_path)
    # 直接写 store，避免 /api/chat 触发真实 LLM 调用
    msg_id = chat.add_message("s1", "user", "在沪深300做到年化10%")
    chat.add_message("s1", "assistant", "完成")
    chat.add_tool_call("s1", msg_id, 1, "list_symbols", {"type": "stock"}, '{"symbols": []}', 0)
    store.register_draft("ma", "def strategy(ctx, p):\n    pass", "sma")
    store.link_session_strategy("s1", "ma", 1)
    # history
    r = client.get("/api/chat/history")
    data = r.json()
    assert len(data["sessions"]) == 1
    s = data["sessions"][0]
    assert s["session_id"] == "s1"
    assert "在沪深300做到年化10%" in s["title"]
    assert s["message_count"] == 2
    assert s["strategy_names"] == ["ma"]
    assert s["status"] == "done"  # agent_sessions 无行时视为 done
    # detail
    r2 = client.get("/api/chat/sessions/s1")
    d = r2.json()
    assert d["session_id"] == "s1"
    assert len(d["messages"]) == 2
    msg = d["messages"][0]
    assert msg["role"] == "user"
    assert len(msg["tool_calls"]) == 1
    assert msg["tool_calls"][0]["name"] == "list_symbols"
    assert msg["tool_calls"][0]["input"] == '{"type": "stock"}'
    assert msg["tool_calls"][0]["is_error"] == 0
    assert d["messages"][1]["tool_calls"] == []
    assert len(d["strategies"]) == 1
    assert d["strategies"][0]["name"] == "ma"
    assert "def strategy" in d["strategies"][0]["source"]


def test_chat_session_detail_unknown_404(tmp_path):
    client, *_ = _make_client(tmp_path)
    r = client.get("/api/chat/sessions/__nope__")
    assert r.status_code == 404
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_agent_api.py -q`
Expected: FAIL（`404 Not Found`，端点未注册）

- [ ] **Step 3: 最小实现**（改 `api/agent/api.py`）

`handle_chat` 签名与确认分支：

```python
def handle_chat(session_id, message, goal, provider, bus, session_store, chat_store, store, executor,
                message_id=None) -> dict:
```

```python
            agent = LLMAgent(provider=provider, store=store, executor=executor, chat_store=chat_store)
            report = agent.run(session_id, format_goal_text(goal_dict), goal=goal_dict, bus=bus,
                               message_id=message_id)
```

`register_agent_routes` 签名改为可注入（内部语句不变，仅把直接构造改为 `or` 回退）：

```python
def register_agent_routes(app: FastAPI, bus=None, store=None, chat_store=None, executor=None,
                          config=None, session_store=None) -> None:
    bus = bus or EventBus()
    store = store or StrategyStore()
    chat_store = chat_store or ChatStore()
    executor = executor or BacktestExecutor()
    config = config or ProviderConfigStore()
    session_store = session_store or AgentSessionStore()
```

`/api/chat` 端点透传 message_id：

```python
        msg_id = chat_store.add_message(session_id, "user", message)
```

```python
                handle_chat(session_id, message, goal, provider, bus,
                            session_store, chat_store, store, executor, message_id=msg_id)
```

`/api/chat/history` 与 `/api/chat/sessions/{sid}` 新端点（放在现有 `/api/chat/sessions` 之后）：

```python
    @app.get("/api/chat/history")
    def chat_history():
        sessions = []
        for sid in chat_store.list_sessions():
            msgs = chat_store.list_messages(sid)
            if not msgs:
                continue
            first_user = next((m["content"] for m in msgs if m["role"] == "user"), "")
            title = first_user[:30] + ("…" if len(first_user) > 30 else "")
            row = session_store.get(sid)
            sessions.append({
                "session_id": sid,
                "title": title,
                "created_at": msgs[0]["created_at"],
                "updated_at": msgs[-1]["created_at"],
                "message_count": len(msgs),
                "strategy_names": [s["name"] for s in store.list_session_strategies(sid)],
                "status": row["status"] if row else "done",
            })
        sessions.sort(key=lambda s: s["updated_at"] or "", reverse=True)
        return {"sessions": sessions}

    @app.get("/api/chat/sessions/{sid}")
    def chat_session_detail(sid: str):
        msgs = chat_store.list_messages(sid)
        if not msgs:
            raise HTTPException(status_code=404, detail="unknown session")
        calls = chat_store.list_tool_calls(sid)
        by_msg: dict[int, list] = {}
        for c in calls:
            by_msg.setdefault(c["message_id"], []).append({
                "name": c["name"], "input": c["input"], "output": c["output"],
                "is_error": c["is_error"], "turn": c["turn"],
            })
        messages = [
            {"id": m["id"], "role": m["role"], "content": m["content"], "created_at": m["created_at"],
             "tool_calls": by_msg.get(m["id"], [])}
            for m in msgs
        ]
        return {"session_id": sid, "messages": messages, "strategies": store.list_session_strategies(sid)}
```

- [ ] **Step 4: 运行确认通过 + 无回归**

Run: `python -m pytest tests/test_agent_api.py tests/test_agent_gate.py -q`
Expected: PASS（`test_chat_endpoint_still_returns_session` 等 gate 测试因 `handle_chat` 仅加关键字参数而保持通过）

- [ ] **Step 5: Commit**

```bash
git add api/agent/api.py tests/test_agent_api.py
git commit -m "feat: 新增 /api/chat/history 与会话详情端点并透传 message_id"
```

---

### Task 5: 前端 — chat.html 重做主从布局 + 页面标记测试

**Files:**
- Modify: `web/chat.html` — 全量重写
- Test: `tests/test_api.py` — 更新 `test_web_pages`

**Interfaces:**
- Consumes: Task 4 的两个端点与 SSE 事件（`tool`/`tool_error` 现含 `input` 字段）。
- Produces: 主从布局页面，含 `id="chatHistory"`（左栏历史列表）与 `id="chatDetail"`（右栏详情）。

- [ ] **Step 1: 写失败测试**（改 `tests/test_api.py::test_web_pages`）

```python
def test_web_pages():
    # Each route must serve its specific page — assert the unique <title> marker.
    expected = {
        "/": ["<title>A股回测系统</title>"],
        "/chat": ["<title>AI 目标优化", 'id="chatHistory"', 'id="chatDetail"'],
        "/data": ["<title>数据预缓存"],
        "/settings": ["<title>LLM 设置"],
    }
    for path, markers in expected.items():
        r = client.get(path)
        assert r.status_code == 200, path
        assert "text/html" in r.headers["content-type"], path
        for marker in markers:
            assert marker in r.text, (path, marker)
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_api.py::test_web_pages -q`
Expected: FAIL（`assert 'id="chatHistory"' in r.text`，旧页面无此 id）

- [ ] **Step 3: 全量重写 `web/chat.html`**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI 目标优化 — A股回测系统</title>
<link rel="stylesheet" href="/web/common.css">
<style>
.wrap { max-width:1100px; margin:24px auto; padding:0 24px; }
.layout { display:flex; gap:16px; align-items:flex-start; }
#chatHistory { flex:0 0 280px; }
.history-item { padding:8px 10px; border:1px solid var(--border); border-radius:6px; margin-bottom:6px; cursor:pointer; font-size:12px; }
.history-item:hover { background:var(--panel); }
.history-item.active { border-color:var(--accent); }
.history-item .t { font-weight:600; }
.history-item .m { color:var(--muted); font-size:11px; margin-top:2px; }
#chatDetail { flex:1 1 auto; min-width:0; }
#chatLog { max-height:400px; overflow:auto; border:1px solid var(--border); border-radius:6px; padding:8px; font-size:12px; background:var(--bg); }
.msg { margin:6px 0; }
.tool-call { margin:4px 0 4px 14px; border:1px solid var(--border); border-radius:4px; }
.tool-call .tc-head { padding:4px 8px; cursor:pointer; user-select:none; }
.tool-call .tc-head:hover { background:var(--panel); }
.tool-call.error .tc-head { color:var(--red); }
.tool-call .tc-body { display:none; padding:4px 8px; border-top:1px dashed var(--border); }
.tool-call .tc-body.show { display:block; }
.tool-call pre { white-space:pre-wrap; word-break:break-all; margin:4px 0; background:var(--panel); padding:6px; border-radius:4px; }
.tool-call .lbl { color:var(--muted); font-size:11px; }
#strategies { margin-top:12px; }
.strategy { margin:4px 0; border:1px solid var(--border); border-radius:6px; padding:6px 8px; font-size:12px; }
.strategy .badge { display:inline-block; padding:1px 6px; border-radius:8px; font-size:11px; margin-right:6px; }
.badge.published { background:var(--green); }
.badge.draft { background:#e67e22; }
.strategy pre { white-space:pre-wrap; word-break:break-all; }
.strategy .sd { display:none; margin-top:6px; }
.strategy .sd.show { display:block; }
.strategy button { width:auto; padding:2px 10px; font-size:12px; margin-left:8px; }
.empty { color:var(--muted); padding:20px; text-align:center; }
</style>
</head>
<body data-page="chat">
<header class="header">
  <div>
    <h1>📈 A股回测系统</h1>
    <div class="sub">股票 · 基金 · ETF &nbsp;|&nbsp; 自定义策略 · Agent API</div>
  </div>
  <nav class="nav">
    <a href="/" class="nav-link" data-page="backtest">回测</a>
    <a href="/chat" class="nav-link" data-page="chat">AI 优化</a>
    <a href="/data" class="nav-link" data-page="data">数据预缓存</a>
    <a href="/settings" class="nav-link" data-page="settings">LLM 设置</a>
  </nav>
</header>

<div class="wrap">
  <div class="layout">
    <aside id="chatHistory">
      <h3>🤖 AI 目标优化</h3>
      <button id="chatNew" class="btn">+ 新建会话</button>
      <div id="historyList" style="margin-top:10px;"></div>
    </aside>
    <section id="chatDetail" class="panel">
      <div id="chatLog"></div>
      <div id="strategies"></div>
      <label for="chatProvider">模型</label>
      <select id="chatProvider"></select>
      <label for="chatInput">目标 / 指令</label>
      <input id="chatInput" placeholder="例如：在沪深300上做到年化收益10%、回撤小于15%">
      <button id="chatSend" class="btn">发送</button>
      <div id="chatStatus" class="status"></div>
    </section>
  </div>
</div>

<script src="/web/common.js"></script>
<script>
const chatLog = document.getElementById('chatLog');
const chatInput = document.getElementById('chatInput');
const chatProvider = document.getElementById('chatProvider');
const chatSend = document.getElementById('chatSend');
const chatStatus = document.getElementById('chatStatus');
const chatNew = document.getElementById('chatNew');
const historyList = document.getElementById('historyList');
let chatSession = null;
let chatES = null;
let historyData = [];

function el(tag, cls, text) {
  const d = document.createElement(tag);
  if (cls) d.className = cls;
  if (text !== undefined) d.textContent = text;
  return d;
}

function chatAppend(role, text, toolCalls) {
  const m = el('div', 'msg');
  m.appendChild(el('b', '', role + ':'));
  m.appendChild(document.createTextNode(' '));
  m.appendChild(document.createTextNode(text));
  chatLog.appendChild(m);
  if (toolCalls && toolCalls.length) renderToolCalls(m, toolCalls);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function toolCallBlock(name, input, output, isError) {
  const block = el('div', 'tool-call' + (isError ? ' error' : ''));
  const head = el('div', 'tc-head', (isError ? '⚠ ' : '▸ ') + '调用 ' + name);
  const body = el('div', 'tc-body');
  body.appendChild(el('div', 'lbl', '入参（完整）'));
  body.appendChild(el('pre', '', input));
  body.appendChild(el('div', 'lbl', '出参（完整）'));
  body.appendChild(el('pre', '', output === null || output === undefined ? '(无)' : output));
  head.addEventListener('click', () => body.classList.toggle('show'));
  block.appendChild(head);
  block.appendChild(body);
  return block;
}

function renderToolCalls(container, calls) {
  calls.forEach(c => container.appendChild(toolCallBlock(c.name, c.input, c.output, c.is_error)));
}

function renderStrategies(strategies) {
  const box = document.getElementById('strategies');
  box.innerHTML = '';
  if (!strategies || !strategies.length) return;
  box.appendChild(el('h3', '', '📋 产出策略'));
  strategies.forEach(s => {
    const row = el('div', 'strategy');
    row.appendChild(el('span', 'badge ' + s.status, s.status === 'published' ? '已发布' : '草稿'));
    row.appendChild(el('span', '', s.name + ' v' + s.version));
    const btn = el('button', 'btn', '查看');
    const sd = el('div', 'sd');
    if (s.goal) sd.appendChild(el('div', '', '目标: ' + s.goal));
    if (s.metrics) sd.appendChild(el('div', '', '指标: ' + JSON.stringify(s.metrics)));
    sd.appendChild(el('div', 'lbl', '源码'));
    sd.appendChild(el('pre', '', s.source));
    btn.addEventListener('click', () => sd.classList.toggle('show'));
    row.appendChild(btn);
    row.appendChild(sd);
    box.appendChild(row);
  });
}

function markActive(sid) {
  historyList.querySelectorAll('.history-item').forEach(i => i.classList.toggle('active', i.dataset.sid === sid));
}

function setStatusFromHistory() {
  const s = (historyData || []).find(x => x.session_id === chatSession);
  const status = s ? s.status : 'done';
  if (status === 'running') { chatStatus.textContent = '运行中…'; chatSend.disabled = true; }
  else if (status === 'pending_clarify') { chatStatus.textContent = '待澄清，请继续输入'; chatSend.disabled = false; }
  else if (status === 'pending_confirm') { chatStatus.textContent = '待确认，请确认或修改目标'; chatSend.disabled = false; }
  else { chatStatus.textContent = ''; chatSend.disabled = false; }
}

async function loadHistory() {
  const r = await fetch('/api/chat/history');
  historyData = (await r.json()).sessions || [];
  historyList.innerHTML = '';
  if (!historyData.length) {
    historyList.appendChild(el('div', 'empty', '暂无历史，点击「新建会话」开始'));
    return;
  }
  historyData.forEach(s => {
    const item = el('div', 'history-item');
    item.dataset.sid = s.session_id;
    item.appendChild(el('div', 't', s.title || '(无标题)'));
    const meta = (s.updated_at || '').replace('T', ' ').slice(0, 16)
      + ' · ' + s.message_count + ' 条'
      + (s.strategy_names && s.strategy_names.length ? ' · ' + s.strategy_names.join(', ') : '')
      + ' · ' + s.status;
    item.appendChild(el('div', 'm', meta));
    item.addEventListener('click', () => selectSession(s.session_id));
    historyList.appendChild(item);
  });
  markActive(chatSession);
  if (!chatSession || !historyData.some(x => x.session_id === chatSession)) {
    selectSession(historyData[0].session_id);
  } else {
    setStatusFromHistory();
  }
}

async function selectSession(sid) {
  chatSession = sid;
  markActive(sid);
  await loadDetail(sid);
}

async function loadDetail(sid) {
  if (chatES) { chatES.close(); chatES = null; }
  const r = await fetch('/api/chat/sessions/' + encodeURIComponent(sid));
  if (r.status === 404) { chatStatus.textContent = '会话不存在'; return; }
  const data = await r.json();
  chatLog.innerHTML = '';
  data.messages.forEach(m => {
    if (m.role === 'system') return;
    chatAppend(m.role === 'user' ? '用户' : 'AI', m.content, m.tool_calls);
  });
  renderStrategies(data.strategies);
  setStatusFromHistory();
}

function chatConnect(sessionId) {
  if (chatES) chatES.close();
  chatES = new EventSource('/api/chat/events?session_id=' + sessionId);
  chatES.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    if (ev.type === 'turn') { chatStatus.textContent = '第 ' + ev.turn + ' 轮…'; }
    else if (ev.type === 'tool') {
      chatLog.appendChild(toolCallBlock(ev.name, ev.input, ev.output, false));
      chatLog.scrollTop = chatLog.scrollHeight;
    }
    else if (ev.type === 'tool_error') {
      chatLog.appendChild(toolCallBlock(ev.name, ev.input, ev.error, true));
      chatLog.scrollTop = chatLog.scrollHeight;
    }
    else if (ev.type === 'backtest_results') { chatStatus.textContent = '回测完成，继续评估…'; }
    else if (ev.type === 'clarify') {
      chatAppend('系统', '🤔 需要澄清：' + (ev.questions || []).join('；'));
      chatStatus.textContent = '请回复以澄清';
      chatSend.disabled = false;
    }
    else if (ev.type === 'confirm') {
      chatAppend('系统', ev.text || '请确认目标');
      chatStatus.textContent = '请确认或修改后回复';
      chatSend.disabled = false;
    }
    else if (ev.type === 'running') { chatStatus.textContent = '已确认，开始执行…'; chatSend.disabled = true; }
    else if (ev.type === 'error') {
      chatAppend('系统', '⚠ 出错: ' + ev.error);
      chatStatus.textContent = '出错';
      chatES.close(); chatES = null;
      chatSend.disabled = false;
      loadHistory();
    }
    else if (ev.type === 'done') {
      chatAppend('AI', ev.report || '(完成)');
      chatStatus.textContent = '完成';
      chatES.close(); chatES = null;
      chatSend.disabled = false;
      // 服务端先写库再回 done，稍等刷新详情，展示完整持久化的工具调用
      setTimeout(() => { loadHistory(); loadDetail(chatSession); }, 300);
    }
  };
  chatES.onerror = () => {
    chatStatus.textContent = '连接中断，重连中…';
    chatSend.disabled = false;
  };
}

async function sendChat() {
  const msg = chatInput.value.trim();
  if (!msg) return;
  chatInput.value = '';
  chatAppend('用户', msg);
  chatStatus.textContent = '思考中…';
  chatSend.disabled = true;
  let data;
  try {
    const r = await fetch('/api/chat', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg, provider: chatProvider.value, session_id: chatSession }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      chatStatus.textContent = '⚠ ' + (err.detail || '发送失败');
      chatSend.disabled = false;
      return;
    }
    data = await r.json();
  } catch (e) {
    chatStatus.textContent = '⚠ 发送失败';
    chatSend.disabled = false;
    return;
  }
  chatSession = data.session_id;
  chatConnect(chatSession);
}

async function loadProviders() {
  const r = await fetch('/api/providers');
  const data = await r.json();
  chatProvider.innerHTML = '';
  data.providers.forEach(n => {
    const o = document.createElement('option');
    o.value = n; o.textContent = n;
    chatProvider.appendChild(o);
  });
}

chatNew.addEventListener('click', () => {
  if (chatES) { chatES.close(); chatES = null; }
  chatSession = null;
  chatLog.innerHTML = '';
  document.getElementById('strategies').innerHTML = '';
  markActive(null);
  chatStatus.textContent = '新建会话：输入目标开始';
  chatSend.disabled = false;
  chatInput.focus();
});
chatSend.addEventListener('click', sendChat);
chatInput.addEventListener('keydown', e => { if (e.key === 'Enter') sendChat(); });
loadProviders();
loadHistory();
</script>
</body>
</html>
```

- [ ] **Step 4: 运行确认通过 + 无回归**

Run: `python -m pytest tests/test_api.py -q`
Expected: PASS（含更新后的 `test_web_pages`）

- [ ] **Step 5: Commit**

```bash
git add web/chat.html tests/test_api.py
git commit -m "feat: chat 页重做为主从布局，支持历史查询/工具调用详情/策略查看"
```

---

## Self-Review

**Spec coverage:**
- §4.1 两张表 → Task 1（tool_calls）、Task 2（session_strategies）✓
- §4.2 store 方法 → Task 1（add/list_tool_call）、Task 2（link/list_session_strategies、get_versions source、register_draft strategy_id）✓
- §5 agent 循环 → Task 3（chat_store、message_id、成功+出错记录、AgentToolContext.session_id、register_strategy 关联）✓；§5.3 时序 message_id 分组 → Task 4 detail 端点 `by_msg` 按 message_id 分组 ✓
- §6.1 两个端点 + 404 → Task 4 ✓；§6.2 复用 → get_versions source 在 Task 2 ✓
- §7 前端主从布局 / 折叠工具块 / 策略查看 / 续聊 / 新建会话 / 自动选中第一个 / 状态 → Task 5 ✓
- §8 完整保留不截断 → Task 1 `json.dumps` 不截断 + Global Constraints；空历史 → Task 5 `empty` 分支；老库升级 → `CREATE TABLE IF NOT EXISTS` ✓
- §9 测试 → Task 1-5 各层对应测试 ✓
- §10 YAGNI → 未做对比/删除/导出/分页 ✓

**Placeholder scan:** 无 TBD/TODO；每步含完整代码与命令。

**Type consistency:**
- `add_tool_call` 签名（Task 1 定义）与 agent 调用（Task 3）一致：`(session_id, message_id, turn, name, input, output, is_error)`。
- `link_session_strategy(session_id, name, version)`（Task 2）与 tools 调用（Task 3）一致；`rec["version"]` 来自 `register_draft` 返回。
- `handle_chat` 关键字参数 `message_id`（Task 4）与 `agent.run(..., message_id=message_id)` 一致；`LLMAgent(..., chat_store=chat_store)` 与 Task 3 构造器一致。
- detail 端点 `tool_calls` 的 `{name, input, output, is_error, turn}`（Task 4）与前端 `toolCallBlock(name, input, output, isError)` 字段（Task 5）一致；history 的 `status/strategy_names/message_count` 与前端元信息渲染一致。
- 测试中 `test_chat_history_and_detail` 直接写 `store/chat/sess`，依赖 Task 4 的注入式 `register_agent_routes`；`_make_client` 与注册签名一致。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-07-ai-optim-history.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
