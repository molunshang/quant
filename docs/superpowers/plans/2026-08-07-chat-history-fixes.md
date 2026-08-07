# 聊天历史侧栏修复 + 测试隔离 + 事实库清理 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 `/chat` 历史侧栏（确定排序 + 可滚动 + 增量加载）、让测试永不写事实库（影子库 + 隔离）、清理 `data/agent.db` 的测试垃圾。

**Architecture:** 后端把 `/api/chat/history` 从 Python N+1 循环改为单条 `GROUP BY` SQL 聚合 + offset 分页，返回 `has_more`；前端侧栏加滚动容器 + `IntersectionObserver` 哨兵增量追加。测试用 `conftest.py` 的 `QUANT_AGENT_DB` 影子库兜底 + 逐测试 `tmp_path` 注入 + 假 provider，彻底隔离事实库与真实 LLM。最后一次性脚本清理事实库。

**Tech Stack:** Python 3.14, FastAPI, SQLite, pytest, 原生 JS（无框架）, IntersectionObserver。

## Global Constraints

- 排序必须**确定**：`ORDER BY updated_at DESC, MAX(id) DESC, session_id ASC`（同秒并列用 `MAX(id)` 作 tie-breaker）。
- `/api/chat/history` 分页默认 `limit=30`，clamp 到 `[1,100]`，返回 `{sessions, has_more}`；`has_more = len(sessions) == limit`。
- 测试禁止写 `data/agent.db`：`conftest.py` 会话级 `QUANT_AGENT_DB` 影子库兜底；每个用到 `register_agent_routes` 的测试注入 tmp_path store。
- `test_chat_returns_session` 必须改为**假 provider + 假 config**，禁止真实调用本地 LLM。
- 事实库清理（手工脚本）只保留 `474dee0be1a6`、`43eff297c199` 两个会话与 `etf_ma_cross`、`etf_trend` 两个策略；删除 `smoke_strat`；把 `43eff297c199` 的 `agent_sessions.status='running'` 置为 `done`。
- 复用现有 `_synchronized` RLock 保护所有新 store 方法。
- 沿用项目现有风格：`tmp_path` 隔离 SQLite、`FakeGateProvider`/`FakeProvider` 假 provider（`complete(*, system, messages, tools, ...)` 契约）。

---

### Task 1: 影子库路径 + `session_summaries` + 批量查询（store 层）

**Files:**
- Modify: `api/agent/store.py`（`DB_PATH`、`ChatStore`、`StrategyStore`、`AgentSessionStore`）
- Test: `tests/test_agent_store.py`

**Interfaces:**
- Consumes: 现有 `_synchronized`、`_now()`。
- Produces:
  - `ChatStore.session_summaries(offset: int, limit: int) -> list[dict]`，每项 `{session_id, title, created_at, updated_at, message_count}`，标题 = 首条 user 消息前 30 字（超长加 `…`），按 `updated_at DESC, MAX(id) DESC, session_id ASC` 排序。
  - `StrategyStore.list_session_strategy_names(session_ids: list[str]) -> dict[str, list[str]]`（`{session_id: [names]}`）。
  - `AgentSessionStore.get_many(session_ids: list[str]) -> dict[str, dict]`（`{session_id: row}`，row 为 dict 或 None 的映射）。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_agent_store.py`）

```python
def test_session_summaries_sorted_and_truncated(tmp_path):
    c = ChatStore(db_path=str(tmp_path / "t.db"))
    c.add_message("s2", "user", "旧")
    c.add_message("s1", "user", "新" * 40)  # 40 字 -> 截断加省略号
    c.add_message("s1", "assistant", "完成")
    # 相同秒级时间戳场景：手动对齐 created_at 以触发 tie-breaker
    c.conn.execute("UPDATE chat_messages SET created_at='2026-01-01T00:00:00.000000+00:00'")
    c.conn.commit()
    sums = c.session_summaries(0, 10)
    # 同秒 -> 依赖 MAX(id) DESC：s1 最后一条 id 更大 -> s1 在前
    assert [s["session_id"] for s in sums] == ["s1", "s2"]
    assert sums[0]["message_count"] == 2
    assert sums[0]["title"].endswith("…")
    assert len(sums[0]["title"]) == 31
    assert sums[1]["title"] == "旧"


def test_session_summaries_pagination(tmp_path):
    c = ChatStore(db_path=str(tmp_path / "t.db"))
    for i in range(5):
        c.add_message(f"s{i}", "user", f"m{i}")
    # 用显式 created_at（随 id 递增）消除时间戳随机性，顺序完全确定
    c.conn.execute(
        "UPDATE chat_messages SET created_at = printf('2026-01-0%dT00:00:00.000000+00:00', id)"
    )
    c.conn.commit()
    page1 = c.session_summaries(0, 2)
    page2 = c.session_summaries(2, 2)
    page3 = c.session_summaries(4, 2)
    assert [s["session_id"] for s in page1] == ["s4", "s3"]
    assert [s["session_id"] for s in page2] == ["s2", "s1"]
    assert [s["session_id"] for s in page3] == ["s0"]
    assert len(page1) == 2 and len(page2) == 2 and len(page3) == 1


def test_list_session_strategy_names_batch(tmp_path):
    s = StrategyStore(db_path=str(tmp_path / "t.db"))
    s.register_draft("ma", "def strategy(ctx, p):\n    pass", "sma")
    s.register_draft("bb", "def strategy(ctx, p):\n    pass", "bb")
    s.link_session_strategy("s1", "ma", 1)
    s.link_session_strategy("s1", "bb", 1)
    s.link_session_strategy("s2", "ma", 1)
    got = s.list_session_strategy_names(["s1", "s2", "s3"])
    assert got["s1"] == ["bb", "ma"]  # 按名称排序
    assert got["s2"] == ["ma"]
    assert got["s3"] == []


def test_get_many(tmp_path):
    a = AgentSessionStore(db_path=str(tmp_path / "t.db"))
    a.set("s1", "running", goal_json='{"universe": ["沪深300"]}')
    a.set("s2", "done")
    got = a.get_many(["s1", "s2", "s3"])
    assert got["s1"]["status"] == "running"
    assert got["s2"]["status"] == "done"
    assert got["s3"] is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_agent_store.py -v`
Expected: FAIL（`AttributeError: 'ChatStore' object has no attribute 'session_summaries'` 等）

- [ ] **Step 3: 实现 store 层改动**

`api/agent/store.py`：

```python
def _default_db_path() -> str:
    return os.environ.get("QUANT_AGENT_DB") or os.path.join(DB_DIR, "agent.db")

DB_PATH = _default_db_path()  # 兼容旧引用；构造时请用 _default_db_path() 读取最新环境变量
```

三个 store 类（`StrategyStore`、`ChatStore`、`AgentSessionStore`）的 `__init__` 中，所有 `db_path or DB_PATH` 改为 `db_path or _default_db_path()`（每类两处：`os.makedirs(os.path.dirname(...))` 与 `sqlite3.connect(...)`）。这样 `QUANT_AGENT_DB` 在**构造时**读取，即使模块导入早于测试设置环境变量也能生效。

`ChatStore` 新增：

```python
@_synchronized
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

`StrategyStore` 新增：

```python
@_synchronized
def list_session_strategy_names(self, session_ids: list[str]) -> dict[str, list[str]]:
    if not session_ids:
        return {}
    qmarks = ",".join("?" * len(session_ids))
    rows = self.conn.execute(
        f"SELECT ss.session_id, s.name FROM session_strategies ss "
        f"JOIN strategies s ON s.id = ss.strategy_id "
        f"WHERE ss.session_id IN ({qmarks}) ORDER BY s.name",
        session_ids,
    ).fetchall()
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r["session_id"], []).append(r["name"])
    return out
```

`AgentSessionStore` 新增：

```python
@_synchronized
def get_many(self, session_ids: list[str]) -> dict[str, dict]:
    if not session_ids:
        return {}
    qmarks = ",".join("?" * len(session_ids))
    rows = self.conn.execute(
        f"SELECT session_id, status, goal_json, questions_json, confirm_summary_json, updated_at "
        f"FROM agent_sessions WHERE session_id IN ({qmarks})",
        session_ids,
    ).fetchall()
    return {r["session_id"]: dict(r) for r in rows}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_agent_store.py -v`
Expected: PASS（新 4 个 + 原 8 个）

- [ ] **Step 5: 提交**

```bash
git add api/agent/store.py tests/test_agent_store.py
git commit -m "feat(store): 会话摘要聚合分页 + 批量策略/状态查询，DB_PATH 支持 QUANT_AGENT_DB"
```

---

### Task 2: `/api/chat/history` 分页端点（api 层）

**Files:**
- Modify: `api/agent/api.py:141-161`（`chat_history` 函数）
- Test: `tests/test_agent_api.py`

**Interfaces:**
- Consumes: Task 1 的 `session_summaries`、`list_session_strategy_names`、`get_many`。
- Produces: `/api/chat/history?offset=0&limit=30` → `{"sessions": [...], "has_more": bool}`；每项含 `strategy_names`、`status`。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_agent_api.py`）

```python
def test_chat_history_pagination(tmp_path):
    client, store, chat, sess = _make_client(tmp_path)
    for i in range(5):
        chat.add_message(f"s{i}", "user", f"目标 {i}")
    r = client.get("/api/chat/history", params={"offset": 0, "limit": 2})
    data = r.json()
    assert len(data["sessions"]) == 2
    assert data["has_more"] is True
    assert data["sessions"][0]["session_id"] == "s4"  # 最新在前
    r2 = client.get("/api/chat/history", params={"offset": 4, "limit": 2})
    data2 = r2.json()
    assert len(data2["sessions"]) == 1
    assert data2["has_more"] is False
    assert data2["sessions"][0]["session_id"] == "s0"
    # 非法参数 clamp
    r3 = client.get("/api/chat/history", params={"offset": -1, "limit": 1000})
    assert r3.status_code == 200
    assert len(r3.json()["sessions"]) == 5
    assert r3.json()["has_more"] is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_agent_api.py::test_chat_history_pagination -v`
Expected: FAIL（当前端点无 offset/limit 参数，`data["has_more"]` KeyError）

- [ ] **Step 3: 重写 `chat_history` 端点**

`api/agent/api.py:141-161` 替换为：

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
            "status": (rows.get(s["session_id"]) or {}).get("status") or "done",
        } for s in summaries]
        return {"sessions": sessions, "has_more": len(sessions) == limit}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_agent_api.py -v`
Expected: PASS（新分页 + 原有 history/detail/404 测试。注意：原 `test_chat_history_and_detail` 断言 `strategy_names == ["ma"]`，批量查询按名称排序 → 单策略不受影响）

- [ ] **Step 5: 提交**

```bash
git add api/agent/api.py tests/test_agent_api.py
git commit -m "feat(api): /api/chat/history 支持 offset/limit 分页并返回 has_more"
```

---

### Task 3: 前端侧栏滚动 + 增量加载（chat.html）

**Files:**
- Modify: `web/chat.html`（CSS + `loadHistory` + 哨兵）

**Interfaces:**
- Consumes: Task 2 的 `/api/chat/history?offset=&limit=` 与 `has_more` 响应。
- Produces: 侧栏独立滚动容器、`IntersectionObserver` 增量加载、分页状态管理。

- [ ] **Step 1: 修改 CSS**（`web/chat.html` `<style>` 内，`#chatHistory` 附近）

替换现有 `.layout`/`#chatHistory` 样式：

```css
.layout { height: calc(100vh - 140px); align-items: stretch; }
#chatHistory { flex:0 0 280px; display:flex; flex-direction:column; min-height:0;
  border-right:1px solid var(--border); padding-right:10px; }
#historyList { flex:1 1 auto; overflow-y:auto; min-height:0; margin-top:10px; }
```

- [ ] **Step 2: 修改 JS 分页状态与 `loadHistory`**

`web/chat.html` `<script>`：在 `let historyData = [];` 后新增：

```js
const HISTORY_PAGE_SIZE = 30;
let historyPage = 0;
let historyDone = false;
let historyLoading = false;
let historyAutoSelect = true;   // chatNew 时为 false，避免自动选中旧会话
let historyObserver = null;
```

替换 `loadHistory` 函数（保持 `selectSession`/`markActive` 等逻辑一致）：

```js
function appendHistoryItem(s) {
  const item = el('div', 'history-item');
  item.dataset.sid = s.session_id;
  item.appendChild(el('div', 't', s.title || '(无标题)'));
  const meta = (s.updated_at || '').replace('T', ' ').slice(0, 16)
    + ' · ' + s.message_count + ' 条'
    + (s.strategy_names && s.strategy_names.length ? ' · ' + s.strategy_names.join(', ') : '')
    + ' · ' + s.status;
  item.appendChild(el('div', 'm', meta));
  item.addEventListener('click', () => selectSession(s.session_id));
  const sentinel = document.getElementById('historySentinel');
  if (sentinel) historyList.insertBefore(item, sentinel);
  else historyList.appendChild(item);
  return item;
}

async function loadHistoryPage() {
  if (historyLoading || historyDone) return;
  historyLoading = true;
  const sentinel = document.getElementById('historySentinel');
  if (sentinel) sentinel.textContent = '加载中…';
  let data;
  try {
    const r = await fetch('/api/chat/history?offset=' + (historyPage * HISTORY_PAGE_SIZE) + '&limit=' + HISTORY_PAGE_SIZE);
    data = await r.json();
  } catch (e) {
    historyDone = true;
    if (sentinel) sentinel.textContent = '加载失败';
    historyLoading = false;
    return;
  }
  const sessions = data.sessions || [];
  const firstPage = historyPage === 0;
  sessions.forEach(s => { historyData.push(s); appendHistoryItem(s); });
  historyPage++;
  if (!data.has_more) historyDone = true;
  if (sentinel) sentinel.textContent = historyDone ? '' : '滚动加载更多';
  if (!historyData.length && historyDone) {
    historyList.appendChild(el('div', 'empty', '暂无历史，点击「新建会话」开始'));
  }
  if (firstPage && historyAutoSelect && !chatSession && historyData.length) {
    selectSession(historyData[0].session_id);
  }
  if (chatSession && historyData.some(x => x.session_id === chatSession)) {
    setStatusFromHistory();
  }
  historyLoading = false;
}
```

替换原 `loadHistory` 定义（哨兵与 observer 在此重建，确保 `innerHTML=''` 后增量加载仍工作）：

```js
function loadHistory(autoSelect = true) {
  historyPage = 0; historyDone = false; historyLoading = false;
  historyAutoSelect = autoSelect;
  historyData = [];
  historyList.innerHTML = '';
  if (historyObserver) { historyObserver.disconnect(); historyObserver = null; }
  // 哨兵必须在列表底部（appendHistoryItem 会 insertBefore 它），并在此重建
  const sentinel = el('div', 'empty', '加载中…');
  sentinel.id = 'historySentinel';
  historyList.appendChild(sentinel);
  historyObserver = new IntersectionObserver(entries => {
    if (entries[0].isIntersecting) loadHistoryPage();
  }, { root: historyList, rootMargin: '100px' });
  historyObserver.observe(sentinel);
  return loadHistoryPage();
}
```

脚本底部原 `loadHistory();` 调用不变（首次进入即建哨兵 + 加载第 1 页）。

- [ ] **Step 3: 事件时机调整**

`chatConnect` 的 `done` 分支与 `error` 分支保留 `loadHistory()`（`loadHistory` 现已重置分页并重建哨兵/observer，行为正确，无需改调用点）。`chatNew` 分支在 `markActive(null)` 后追加 `loadHistory(false)`——**不自动选中**，保持「新建会话」空面板：

`chatNew.addEventListener` 内（`markActive(null);` 之后、`chatStatus.textContent` 之前）：

```js
  markActive(null);
  loadHistory(false);
  chatStatus.textContent = '新建会话：输入目标开始';
```

- [ ] **Step 5: 运行前端测试 + 手工验证**

Run: `pytest tests/test_api.py::test_web_pages -v`
Expected: PASS（页面仍含 `id="chatHistory"`、`id="chatDetail"` 标记）

Run: `curl -s "http://localhost:8000/chat" | grep -c "historySentinel"`（如服务在跑）或手工打开页面确认：侧栏固定高度可滚动、滚到底自动加载下一页。

- [ ] **Step 6: 提交**

```bash
git add web/chat.html
git commit -m "feat(web): 历史侧栏独立滚动 + 增量加载"
```

---

### Task 4: 测试隔离（conftest 影子库 + 逐测试注入）

**Files:**
- Create: `tests/conftest.py`
- Modify: `tests/test_agent_api.py`, `tests/test_agent_gate.py`, `tests/test_llm_config.py`

**Interfaces:**
- Consumes: Task 1 的 `QUANT_AGENT_DB` 支持。
- Produces: 所有测试默认路径永不写 `data/agent.db`；`register_agent_routes` 调用点均注入 tmp_path store。

- [ ] **Step 1: 创建 `tests/conftest.py`**

```python
"""Test isolation: redirect the default agent DB to a session-scoped shadow DB.

Must set QUANT_AGENT_DB at IMPORT time (not in a fixture): api.main and the
store modules construct their default stores at import time, before any pytest
fixture runs. Setting it here means even a test that forgets to inject its own
stores can never touch the real data/agent.db.
"""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="quant-test-db-")
os.environ["QUANT_AGENT_DB"] = os.path.join(_TMP, "agent.db")
```

（`tests/conftest.py` 由 pytest 在任何测试模块导入前加载，因此 `os.environ["QUANT_AGENT_DB"]` 在 `api.main` 导入并构造默认 store 时已生效。）

- [ ] **Step 2: 修改 `tests/test_agent_api.py`**

`test_chat_returns_session` 目前会真实调用本地 LLM 并写库。改为假 provider + 假 config + 隔离 store：

```python
def test_chat_returns_session(tmp_path, monkeypatch):
    app = FastAPI()
    store = StrategyStore(str(tmp_path / "s.db"))
    chat = ChatStore(str(tmp_path / "c.db"))
    sess = AgentSessionStore(str(tmp_path / "a.db"))

    # 假 config：写一个 provider 配置，再把 providers() 返回替换为 FakeGateProvider，
    # 这样 handle_chat -> gate_extract 走假 provider，绝不触发真实 LLM 或网络
    cfg_path = tmp_path / "llm.json"
    cfg_path.write_text(json.dumps({"default": "p1", "providers": [{
        "name": "p1", "type": "openai_compat", "base_url": "http://127.0.0.1:1",
        "model": "m", "api_key": "k"}]}), encoding="utf-8")
    cfg = ProviderConfigStore(str(cfg_path))
    fake = FakeGateProvider([LLMResponse(
        text='{"universe": ["沪深300"], "constraints": {"annual_return": 0.10}}', tool_uses=[])])
    cfg.providers = lambda: {"p1": fake}

    register_agent_routes(app, store=store, chat_store=chat, session_store=sess,
                          config=cfg, executor=FakeExecutor())
    client = TestClient(app)
    r = client.post("/api/chat", json={"message": "做年化10%", "goal": "年化>=10%"})
    assert r.status_code == 200
    assert "session_id" in r.json()
    # 后台线程落库后再断言消息已持久化到隔离 store
    import time
    for _ in range(100):
        if chat.list_messages(r.json()["session_id"]):
            break
        time.sleep(0.05)
    assert len(chat.list_messages(r.json()["session_id"])) >= 2
```

注：`handle_chat` 内 `gate_extract` 通过 `config.providers()` 取 provider 对象（api.py:101-110），把 `cfg.providers` 替换为返回 `{"p1": fake}` 的 lambda 即可让整条链走假 provider。`FakeExecutor` 为防御性兜底（`_extract_and_advance` 只走 gate，不启动 `LLMAgent` 循环，executor 不实际执行）。

`_make_client` 保持不变（现有实现已是 tmp_path 隔离；`executor` 默认 `BacktestExecutor()` 不写库，无需隔离，`test_chat_returns_session` 需单独注入 `FakeExecutor`）：

```python
def _make_client(tmp_path):
    app = FastAPI()
    store = StrategyStore(str(tmp_path / "s.db"))
    chat = ChatStore(str(tmp_path / "c.db"))
    sess = AgentSessionStore(str(tmp_path / "a.db"))
    register_agent_routes(app, store=store, chat_store=chat, session_store=sess)
    return TestClient(app), store, chat, sess
```

修正 `test_providers_endpoint` / `test_published_strategies_endpoint` 用隔离客户端（去掉 `tmp_path`，改用模块级共享隔离客户端）：

```python
def test_providers_endpoint(tmp_path):
    client, *_ = _make_client(tmp_path)
    r = client.get("/api/providers")
    assert r.status_code == 200
    assert "providers" in r.json()
```

```python
def test_published_strategies_endpoint(tmp_path):
    client, *_ = _make_client(tmp_path)
    r = client.get("/api/strategies/published")
    assert r.status_code == 200
    assert "strategies" in r.json()
```

文件顶部补充 import（移除 `from api.main import app`，改为 TestClient 仅从 fastapi.testclient 导入）：

```python
import json
from fastapi.testclient import TestClient
from api.agent.provider import LLMResponse
from api.agent.config import ProviderConfigStore
```

**删除**模块级 `client = TestClient(app)` 与 `from api.main import app`（全局 app 绑定真实 store；改为每个测试 `_make_client(tmp_path)` 自建隔离客户端）。

- [ ] **Step 3: 修改 `tests/test_agent_gate.py`**

`test_chat_endpoint_still_returns_session`（约 389-403 行）：注入 tmp_path store，不再 `register_agent_routes(app)` 落事实库：

```python
def test_chat_endpoint_still_returns_session(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.agent.api import register_agent_routes
    from api.agent.store import AgentSessionStore, ChatStore, StrategyStore

    cfg_path = tmp_path / "llm.json"
    cfg_path.write_text('{"default": null, "providers": []}', encoding="utf-8")
    monkeypatch.setenv("QUANT_LLM_CONFIG", str(cfg_path))
    app = FastAPI()
    register_agent_routes(app,
                          store=StrategyStore(str(tmp_path / "s.db")),
                          chat_store=ChatStore(str(tmp_path / "c.db")),
                          session_store=AgentSessionStore(str(tmp_path / "a.db")))
    client = TestClient(app)
    # 无 provider -> 400，与旧行为一致
    r = client.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 400
    assert "provider" in r.json()["detail"]
```

- [ ] **Step 4: 修改 `tests/test_llm_config.py`**

`_make_client`（约 198-211 行）：注入 tmp_path store：

```python
def _make_client(tmp_path, monkeypatch):
    import os

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.agent.api import register_agent_routes
    from api.agent.store import AgentSessionStore, ChatStore, StrategyStore

    cfg_path = tmp_path / "llm.json"
    cfg_path.write_text('{"providers": []}', encoding="utf-8")
    monkeypatch.setenv("QUANT_LLM_CONFIG", str(cfg_path))
    app = FastAPI()
    register_agent_routes(app,
                          store=StrategyStore(str(tmp_path / "s.db")),
                          chat_store=ChatStore(str(tmp_path / "c.db")),
                          session_store=AgentSessionStore(str(tmp_path / "a.db")))
    return TestClient(app), cfg_path
```

- [ ] **Step 5: 运行全套测试确认不写事实库**

Run: `pytest -q`
Expected: 全绿。然后验证事实库未被污染：

Run: `sqlite3 data/agent.db "SELECT COUNT(*) FROM chat_messages;"`
Expected: 与运行测试前一致的数值（当前 392；跑完测试后应**不变**）。

- [ ] **Step 6: 提交**

```bash
git add tests/conftest.py tests/test_agent_api.py tests/test_agent_gate.py tests/test_llm_config.py
git commit -m "test: 影子库兜底 + 逐测试注入，杜绝测试写事实库与真实 LLM 调用"
```

---

### Task 5: 事实库清理（一次性脚本 + 校验）

**Files:**
- Create: `scripts/cleanup_agent_db.py`（临时脚本，执行后删除或保留在 gitignore）

**Interfaces:**
- Consumes: 备份 `data/agent.db`。
- Produces: 清理后仅剩 2 个会话与 2 个策略的干净事实库。

- [ ] **Step 1: 备份 + 写清理脚本**

Run: `cp data/agent.db data/agent.db.bak-$(date +%Y%m%d-%H%M%S)`

创建 `scripts/cleanup_agent_db.py`：

```python
"""一次性清理事实库中的测试垃圾（Option B：保留真实会话/策略）。

保留：会话 474dee0be1a6、43eff297c199；策略 etf_ma_cross、etf_trend。
删除：其余全部会话数据 + smoke_strat 策略。
修正：43eff297c199 的 agent_sessions.status='running' -> 'done'。
"""
import os
import sqlite3
import sys

KEEP_SESSIONS = {"474dee0be1a6", "43eff297c199"}
KEEP_STRATEGIES = {"etf_ma_cross", "etf_trend"}

DB = os.environ.get("QUANT_AGENT_DB") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "agent.db"
)


def main():
    if "--commit" not in sys.argv:
        print("DRY-RUN：仅打印将删除的行数。加 --commit 真正执行。")
        commit = False
    else:
        commit = True
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 会话相关：删除非保留会话
    keep_sql = ",".join("?" * len(KEEP_SESSIONS))
    for table in ("chat_messages", "tool_calls", "session_strategies"):
        rows = cur.execute(
            f"SELECT COUNT(*) n FROM {table} WHERE session_id NOT IN ({keep_sql})",
            tuple(KEEP_SESSIONS),
        ).fetchone()
        print(f"  {table}: 将删除 {rows['n']} 行")
    # 会话无关的 agent_sessions 删除同规则（保留会话之外的行）
    rows = cur.execute(
        f"SELECT COUNT(*) n FROM agent_sessions WHERE session_id NOT IN ({keep_sql})",
        tuple(KEEP_SESSIONS),
    ).fetchone()
    print(f"  agent_sessions: 将删除 {rows['n']} 行")

    # 策略：删除 smoke_strat
    st_rows = cur.execute(
        "SELECT id, name FROM strategies WHERE name NOT IN (?,?)", tuple(KEEP_STRATEGIES)
    ).fetchall()
    for r in st_rows:
        print(f"  strategy {r['name']} (id={r['id']}): 删除策略及其版本")

    if not commit:
        conn.close()
        return

    for table in ("chat_messages", "tool_calls", "session_strategies"):
        cur.execute(
            f"DELETE FROM {table} WHERE session_id NOT IN ({keep_sql})",
            tuple(KEEP_SESSIONS),
        )
    cur.execute(
        f"DELETE FROM agent_sessions WHERE session_id NOT IN ({keep_sql})",
        tuple(KEEP_SESSIONS),
    )
    for r in st_rows:
        cur.execute("DELETE FROM strategy_versions WHERE strategy_id=?", (r["id"],))
        cur.execute("DELETE FROM strategies WHERE id=?", (r["id"],))
    # 修正卡死状态
    cur.execute(
        "UPDATE agent_sessions SET status='done' WHERE session_id='43eff297c199' AND status='running'"
    )
    conn.commit()
    conn.close()
    print("完成。")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: DRY-RUN 预览**

Run: `.venv/bin/python scripts/cleanup_agent_db.py`
Expected: 打印将删除的行数（chat_messages 175 会话行、tool_calls 0、session_strategies 0、agent_sessions 若干、smoke_strat 策略）。

- [ ] **Step 3: 执行清理**

Run: `.venv/bin/python scripts/cleanup_agent_db.py --commit`

- [ ] **Step 4: 校验行数**

Run:
```bash
sqlite3 data/agent.db "SELECT 'chat_messages', COUNT(*) FROM chat_messages UNION ALL SELECT 'tool_calls', COUNT(*) FROM tool_calls UNION ALL SELECT 'session_strategies', COUNT(*) FROM session_strategies UNION ALL SELECT 'strategies', COUNT(*) FROM strategies UNION ALL SELECT 'strategy_versions', COUNT(*) FROM strategy_versions UNION ALL SELECT 'agent_sessions', COUNT(*) FROM agent_sessions;"
```
Expected:
```
chat_messages|25
tool_calls|26
session_strategies|4
strategies|2
strategy_versions|5
agent_sessions|2
```
（`chat_messages` 25 = 474dee0be1a6 的 11 + 43eff297c199 的 14；`tool_calls` 26、`session_strategies` 4 全在 474dee0be1a6；`strategies` 2 = etf_ma_cross + etf_trend；`strategy_versions` 5 = etf_trend 的 4 + etf_ma_cross 的 1；`agent_sessions` 2。若与预期不符，说明有保留会话外的关联行，暂停并人工检查。）

- [ ] **Step 5: 运行全套测试确认仍绿（且影子库未被清理影响）**

Run: `pytest -q`
Expected: 全绿。

- [ ] **Step 6: 提交（脚本作为清理记录保留在 `scripts/`）**

```bash
git add scripts/cleanup_agent_db.py
git commit -m "chore: 清理事实库测试垃圾（保留真实会话/策略），新增一次性清理脚本"
```

---

## Self-Review

**1. Spec coverage:**
- 排序确定（同秒并列）：Task 1 `session_summaries` 排序键 `MAX(id) DESC, session_id ASC` ✓
- 增量加载 / 滚动 / 一次性加载：Task 2 分页 + Task 3 滚动容器 + IntersectionObserver ✓
- 禁止写事实库：Task 1 `QUANT_AGENT_DB` + Task 4 conftest 影子库 + 逐测试注入 ✓
- 清除测试垃圾：Task 5 清理脚本 + 保留 2 会话 2 策略 + 修正 running ✓
- 默认 30/页、clamp [1,100]、has_more：Task 2 ✓

**2. Placeholder scan:** 所有步骤含完整代码与预期输出；无 TBD/TODO。`test_chat_returns_session` 的 FakeGateProvider 注入在 Step 2 有明确的两段合并说明。

**3. Type consistency:**
- `session_summaries` 签名在 Task 1 定义，Task 2 使用 `chat_store.session_summaries(offset, limit)` ✓
- `list_session_strategy_names` / `get_many` 签名跨 Task 1/2 一致 ✓
- `has_more` 在 Task 2 定义，Task 3 前端消费 ✓
- `FakeGateProvider.complete(*, system, messages, tools, ...)` 契约与 gate.py 的 `gate_extract` 调用一致 ✓
- 清理脚本的行数预期与实测数据核对一致（25/26/4/2/2/2）✓

发现一处需留意：`test_chat_returns_session` 走完整 `/api/chat` → `handle_chat` → `_extract_and_advance` → `gate_extract`，只调用 `provider.complete` 一次即返回 `clarify`/`confirm`（不启动 `LLMAgent` 循环，`executor` 不会实际执行），因此注入 `_fake_providers` 即可，`FakeExecutor` 仅为防御性兜底。该测试不会写库——`_extract_and_advance` 只写 `agent_sessions`（已隔离到 tmp_path）。✓
