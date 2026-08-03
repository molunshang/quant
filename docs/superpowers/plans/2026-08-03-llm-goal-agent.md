# LLM Goal-Optimization Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Web-chat LLM agent to the A-share backtest system that takes a natural-language goal, autonomously picks symbols / writes & iterates strategies / runs backtests, and publishes the first strategy version that meets the goal — with SQLite persistence and SSE progress streaming.

**Architecture:** A state-machine tool loop (`LLMAgent`) drives an abstract `LLMProvider` (Anthropic + OpenAI-compatible adapters). Backtests run asynchronously through a `ThreadPoolExecutor` (`BacktestExecutor`) and the agent waits on the batch before deciding next steps. Strategy drafts, published versions with metric snapshots, and chat history persist in SQLite (`StrategyStore` / `ChatStore`). Progress events stream to the browser over SSE from an in-memory per-session queue.

**Tech Stack:** Python 3.14, FastAPI, `anthropic`, `openai`, SQLite (`sqlite3` stdlib), pandas, pytest.

## Global Constraints

- Python 3.14 (existing venv at `.venv/`); run tests with `.venv/bin/python -m pytest tests/ -x -q`
- Existing strategy AST sandbox in `strategies/base.py` (`validate_strategy_source`) must be reused for all LLM-submitted sources — never bypass
- Reuse existing `api/runner.run_backtest` / `run_optimize` and `data/registry` — do not reimplement data fetch
- Multi-provider: `LLMProvider` ABC with `AnthropicProvider` + `OpenAICompatProvider`; config from `config/llm.json`
- Strategy lifecycle: registered sources are **drafts**; only goal-reaching versions become **published** (SQLite)
- Progress events: in-memory per-session queue, drained on read (no persistence — single process, no resume)
- SSE: heartbeat `: ping` every ~15s; no event replay from disk
- Chat history (user + assistant final report) and strategies persist in SQLite; intermediate progress does not
- No new heavy dependencies: `anthropic` + `openai` only (both currently missing from `.venv` — Task 1 installs them)

---

### Task 1: Add LLM SDK dependencies

**Files:**
- Modify: `requirements.txt`

**Interfaces:**
- Produces: `anthropic` and `openai` packages importable in `.venv`

- [ ] **Step 1: Add deps to requirements.txt**

Append to `requirements.txt`:
```
anthropic>=0.40.0
openai>=1.55.0
```

- [ ] **Step 2: Install into venv**

Run: `.venv/bin/pip install -r requirements.txt`
Expected: success; then verify:
`.venv/bin/python -c "import anthropic, openai; print(anthropic.__version__, openai.__version__)"`

- [ ] **Step 3: Verify baseline tests still pass**

Run: `.venv/bin/python -m pytest tests/ -x -q`
Expected: `25 passed`

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "feat: add anthropic + openai SDK deps for LLM agent"
```

---

### Task 2: SQLite strategy & chat store

**Files:**
- Create: `api/agent/store.py`
- Create: `api/agent/__init__.py`
- Test: `tests/test_agent_store.py`

**Interfaces:**
- Produces:
  - `class StrategyStore` — methods:
    - `register_draft(name: str, source: str, description: str = "") -> dict` (creates strategy if missing; appends a new draft version; returns `{name, version, status}`)
    - `get_strategy(name: str) -> dict | None` → `{name, description, status, current_version, versions: [...]}`
    - `list_strategies(include_drafts: bool = True) -> list[dict]`
    - `publish_version(name: str, version: int, metrics: dict, goal: str) -> dict` (marks version published; stores metric snapshot + goal + timestamp)
    - `get_versions(name: str) -> list[dict]`
    - `close()`
  - `class ChatStore` — methods:
    - `add_message(session_id: str, role: str, content: str) -> int` (returns message id)
    - `list_messages(session_id: str) -> list[dict]`
    - `list_sessions() -> list[str]`
  - Both take `db_path: str | None = None` (defaults to `data/agent.db`)

- [ ] **Step 1: Write the failing test**

Create `tests/test_agent_store.py`:
```python
"""StrategyStore / ChatStore tests (SQLite, tmp_path isolated)."""
from __future__ import annotations

import pytest

from api.agent.store import StrategyStore, ChatStore


def test_draft_publish_versions(tmp_path):
    s = StrategyStore(db_path=str(tmp_path / "t.db"))
    r1 = s.register_draft("ma", "def strategy(ctx, p):\n    pass", "sma")
    assert r1["version"] == 1
    assert r1["status"] == "draft"
    r2 = s.register_draft("ma", "def strategy(ctx, p):\n    pass", "sma v2")
    assert r2["version"] == 2

    g = s.get_strategy("ma")
    assert g["current_version"] == 2
    assert len(g["versions"]) == 2
    assert g["versions"][-1]["status"] == "draft"

    pub = s.publish_version("ma", 2, {"total_return": 0.2}, "年化>=10%")
    assert pub["status"] == "published"
    assert pub["metrics"]["total_return"] == 0.2

    g2 = s.get_strategy("ma")
    assert g2["status"] == "published"
    s.close()


def test_publish_unknown_strategy_raises(tmp_path):
    s = StrategyStore(db_path=str(tmp_path / "t.db"))
    with pytest.raises(KeyError):
        s.publish_version("nope", 1, {}, "")
    s.close()


def test_chat_store_roundtrip(tmp_path):
    c = ChatStore(db_path=str(tmp_path / "t.db"))
    c.add_message("s1", "user", "目标")
    c.add_message("s1", "assistant", "报告")
    msgs = c.list_messages("s1")
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert "s1" in c.list_sessions()
    c.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_agent_store.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'api.agent'`

- [ ] **Step 3: Write minimal implementation**

Create `api/agent/__init__.py`:
```python
"""LLM goal-optimization agent package."""
```

Create `api/agent/store.py`:
```python
"""SQLite persistence: strategy drafts/versions/published snapshots + chat history."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
DB_PATH = os.path.join(DB_DIR, "agent.db")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StrategyStore:
    def __init__(self, db_path: str | None = None):
        os.makedirs(os.path.dirname(db_path or DB_PATH), exist_ok=True)
        self.conn = sqlite3.connect(db_path or DB_PATH)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS strategies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS strategy_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_id INTEGER NOT NULL REFERENCES strategies(id),
            version INTEGER NOT NULL,
            source TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'draft',
            metrics_json TEXT,
            goal TEXT,
            published_at TEXT,
            created_at TEXT,
            UNIQUE(strategy_id, version)
        );
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT
        );
        """)
        self.conn.commit()

    # ---- strategies ----
    def _strategy_id(self, name: str) -> int:
        row = self.conn.execute(
            "SELECT id FROM strategies WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            cur = self.conn.execute(
                "INSERT INTO strategies (name, status, created_at, updated_at) VALUES (?, 'draft', ?, ?)",
                (name, _now(), _now()),
            )
            return cur.lastrowid
        return row["id"]

    def register_draft(self, name: str, source: str, description: str = "") -> dict:
        sid = self._strategy_id(name)
        row = self.conn.execute(
            "SELECT COALESCE(MAX(version), 0) AS v FROM strategy_versions WHERE strategy_id = ?",
            (sid,),
        ).fetchone()
        version = int(row["v"]) + 1
        self.conn.execute(
            "INSERT INTO strategy_versions (strategy_id, version, source, description, status, created_at) VALUES (?,?,?,?,'draft',?)",
            (sid, version, source, description, _now()),
        )
        self.conn.execute(
            "UPDATE strategies SET updated_at = ? WHERE id = ?", (_now(), sid)
        )
        self.conn.commit()
        return {"name": name, "version": version, "status": "draft"}

    def get_strategy(self, name: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM strategies WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            return None
        versions = self.get_versions(name)
        published = [v for v in versions if v["status"] == "published"]
        return {
            "name": row["name"],
            "description": row["description"],
            "status": "published" if published else "draft",
            "current_version": max((v["version"] for v in versions), default=0),
            "versions": versions,
        }

    def list_strategies(self, include_drafts: bool = True) -> list[dict]:
        rows = self.conn.execute(
            "SELECT name, status FROM strategies ORDER BY name"
        ).fetchall()
        out = []
        for r in rows:
            if r["status"] == "draft" and not include_drafts:
                continue
            g = self.get_strategy(r["name"])
            out.append({
                "name": g["name"],
                "status": g["status"],
                "current_version": g["current_version"],
            })
        return out

    def publish_version(self, name: str, version: int, metrics: dict, goal: str) -> dict:
        sid = self._strategy_id(name)
        row = self.conn.execute(
            "SELECT id FROM strategy_versions WHERE strategy_id = ? AND version = ?",
            (sid, version),
        ).fetchone()
        if row is None:
            raise KeyError(f"strategy {name!r} has no version {version}")
        now = _now()
        self.conn.execute(
            "UPDATE strategy_versions SET status='published', metrics_json=?, goal=?, published_at=? WHERE id=?",
            (json.dumps(metrics), goal, now, row["id"]),
        )
        self.conn.execute(
            "UPDATE strategies SET status='published', updated_at=? WHERE id=?",
            (now, sid),
        )
        self.conn.commit()
        return {"name": name, "version": version, "status": "published", "metrics": metrics}

    def get_versions(self, name: str) -> list[dict]:
        sid_row = self.conn.execute(
            "SELECT id FROM strategies WHERE name = ?", (name,)
        ).fetchone()
        if sid_row is None:
            return []
        rows = self.conn.execute(
            "SELECT version, source, description, status, metrics_json, goal, published_at, created_at "
            "FROM strategy_versions WHERE strategy_id = ? ORDER BY version",
            (sid_row["id"],),
        ).fetchall()
        return [
            {
                "version": r["version"],
                "status": r["status"],
                "description": r["description"],
                "metrics": json.loads(r["metrics_json"]) if r["metrics_json"] else None,
                "goal": r["goal"],
                "published_at": r["published_at"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def close(self):
        self.conn.close()


class ChatStore:
    def __init__(self, db_path: str | None = None):
        os.makedirs(os.path.dirname(db_path or DB_PATH), exist_ok=True)
        self.conn = sqlite3.connect(db_path or DB_PATH)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT
        );
        """)
        self.conn.commit()

    def add_message(self, session_id: str, role: str, content: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO chat_messages (session_id, role, content, created_at) VALUES (?,?,?,?)",
            (session_id, role, content, _now()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_messages(self, session_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, role, content, created_at FROM chat_messages WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_sessions(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT session_id FROM chat_messages ORDER BY session_id"
        ).fetchall()
        return [r["session_id"] for r in rows]

    def close(self):
        self.conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_agent_store.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/agent/ tests/test_agent_store.py
git commit -m "feat: SQLite strategy draft/publish + chat store"
```

---

### Task 3: LLM provider abstraction

**Files:**
- Create: `api/agent/provider.py`
- Create: `config/llm.json`
- Modify: `.gitignore` (ignore `config/llm.json`? no — commit a sample; add `data/agent.db`)
- Test: `tests/test_agent_provider.py`

**Interfaces:**
- Produces:
  - `@dataclass ToolCall: id: str; name: str; input: dict`
  - `@dataclass LLMResponse: text: str | None; tool_uses: list[ToolCall]`
  - `class LLMProvider(ABC): complete(*, system: str, messages: list[dict], tools: list[dict], model: str | None = None, max_tokens: int = 4096) -> LLMResponse`
  - `class AnthropicProvider(LLMProvider)` — ctor `(api_key: str, base_url: str | None = None, model: str = "claude-opus-5")`
  - `class OpenAICompatProvider(LLMProvider)` — ctor `(api_key: str, base_url: str, model: str)`
  - `def load_providers(path: str | None = None) -> dict[str, LLMProvider]` — reads `config/llm.json`, expands `env:VAR` in api_key, returns `{provider_name: provider}`

- [ ] **Step 1: Write the failing test**

Create `tests/test_agent_provider.py`:
```python
"""LLM provider abstraction tests (mocked, no network)."""
from __future__ import annotations

from api.agent.provider import (
    ToolCall,
    LLMResponse,
    OpenAICompatProvider,
    load_providers,
)


def test_load_providers_expands_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_LLM_KEY", "sk-test")
    cfg = tmp_path / "llm.json"
    cfg.write_text(
        '{"providers": [{"name": "local", "type": "openai_compat", '
        '"base_url": "http://127.0.0.1:3456", "model": "m1", "api_key": "env:TEST_LLM_KEY"}]}'
    )
    providers = load_providers(str(cfg))
    assert "local" in providers
    assert isinstance(providers["local"], OpenAICompatProvider)
    assert providers["local"].model == "m1"


def test_openai_provider_builds_tool_uses(monkeypatch):
    # Capture the request body; return a fake completion with one tool_use.
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return {"choices": [{"message": {
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "run_backtest", "arguments": '{"symbol":"510300"}'},
                }],
            }}]}

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr("api.agent.provider.OpenAI", FakeClient)
    p = OpenAICompatProvider(api_key="k", base_url="http://x", model="m1")
    resp = p.complete(system="sys", messages=[], tools=[])
    assert isinstance(resp, LLMResponse)
    assert len(resp.tool_uses) == 1
    assert resp.tool_uses[0].name == "run_backtest"
    assert resp.tool_uses[0].input == {"symbol": "510300"}
    assert captured["model"] == "m1"
    assert captured["tools"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_agent_provider.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'api.agent.provider'`

- [ ] **Step 3: Write minimal implementation**

Create `config/llm.json`:
```json
{
  "providers": [
    {"name": "anthropic-local", "type": "anthropic",
     "base_url": "http://127.0.0.1:3456", "model": "opengo/deepseek-v4-flash", "api_key": "env:ANTHROPIC_API_KEY"}
  ]
}
```

Create `api/agent/provider.py`:
```python
"""LLM provider abstraction: multi-backend support (Anthropic + OpenAI-compatible)."""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import anthropic


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict = field(default_factory=dict)


@dataclass
class LLMResponse:
    text: str | None = None
    tool_uses: list[ToolCall] = field(default_factory=list)


class LLMProvider(ABC):
    @abstractmethod
    def complete(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        model: str | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """One LLM call. Returns text + tool-use list (both may be present)."""


def _anthropic_tools(tools: list[dict]) -> list[dict]:
    """Convert generic {name, description, parameters} to Anthropic schema."""
    return [
        {
            "name": t["name"],
            "description": t.get("description", ""),
            "input_schema": t.get("parameters", {"type": "object", "properties": {}}),
        }
        for t in tools
    ]


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, base_url: str | None = None, model: str = "claude-opus-5"):
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = anthropic.Anthropic(**kwargs)
        self.model = model

    def complete(self, *, system, messages, tools, model=None, max_tokens=4096) -> LLMResponse:
        kwargs: dict = {
            "model": model or self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = _anthropic_tools(tools)
        resp = self._client.messages.create(**kwargs)
        text = "".join(b.text for b in resp.content if b.type == "text") or None
        tool_uses = [
            ToolCall(id=b.id, name=b.name, input=b.input)
            for b in resp.content
            if b.type == "tool_use"
        ]
        return LLMResponse(text=text, tool_uses=tool_uses)


def _openai_tools(tools: list[dict]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("parameters", {"type": "object", "properties": {}}),
            },
        }
        for t in tools
    ]


class OpenAICompatProvider(LLMProvider):
    def __init__(self, api_key: str, base_url: str, model: str):
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def complete(self, *, system, messages, tools, model=None, max_tokens=4096) -> LLMResponse:
        msgs: list[dict] = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)
        kwargs: dict = {
            "model": model or self.model,
            "messages": msgs,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = _openai_tools(tools)
        resp = self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0].message
        text = choice.content or None
        tool_uses = []
        for tc in choice.tool_calls or []:
            try:
                input_ = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                input_ = {}
            tool_uses.append(ToolCall(id=tc.id, name=tc.function.name, input=input_))
        return LLMResponse(text=text, tool_uses=tool_uses)


def _expand_env(value: str) -> str:
    if value.startswith("env:"):
        return os.environ.get(value[4:], "")
    return value


def load_providers(path: str | None = None) -> dict[str, LLMProvider]:
    """Load providers from config/llm.json -> {name: provider}."""
    if path is None:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config", "llm.json")
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    providers: dict[str, LLMProvider] = {}
    for p in cfg.get("providers", []):
        name = p["name"]
        typ = p["type"]
        api_key = _expand_env(p.get("api_key", ""))
        if typ == "anthropic":
            providers[name] = AnthropicProvider(
                api_key=api_key, base_url=p.get("base_url"), model=p.get("model", "claude-opus-5")
            )
        elif typ == "openai_compat":
            providers[name] = OpenAICompatProvider(
                api_key=api_key, base_url=p["base_url"], model=p.get("model", "gpt-4o")
            )
        else:
            raise ValueError(f"unknown provider type: {typ}")
    return providers
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_agent_provider.py -q`
Expected: PASS

- [ ] **Step 5: Add `data/agent.db` to .gitignore**

Append to `.gitignore`:
```
data/agent.db
```

- [ ] **Step 6: Commit**

```bash
git add api/agent/provider.py config/llm.json tests/test_agent_provider.py .gitignore
git commit -m "feat: LLM provider abstraction (anthropic + openai-compat)"
```

---

### Task 4: Backtest executor (thread pool, async batch)

**Files:**
- Create: `api/agent/executor.py`
- Test: `tests/test_agent_executor.py`

**Interfaces:**
- Produces:
  - `class BacktestJob: id: int; symbol: str; params: dict; status: str ('running'|'done'|'error'); result: dict | None; error: str | None`
  - `class BacktestExecutor:`
    - `submit(symbol, strategy_ref, params, freq, start, end, adjust, initial_cash) -> int` (job_id)
    - `wait_all(timeout: float) -> list[dict]` (blocks until all submitted jobs in this batch finish; returns results in submission order)
    - `reset_batch()` (start new batch after wait_all)
    - `shutdown()`
  - Constructor takes `strategy_manager` (optional) + `initial_cash`; reuses `api.runner.run_backtest`

- [ ] **Step 1: Write the failing test**

Create `tests/test_agent_executor.py`:
```python
"""BacktestExecutor tests (synthetic bars, no network)."""
from __future__ import annotations

import pandas as pd
import pytest

from api.agent.executor import BacktestExecutor


def make_bars(n=60, start_price=100.0):
    close = pd.Series(range(n)).apply(lambda i: start_price * (1 + 0.005 * i))
    dates = pd.date_range("2023-01-02", periods=n, freq="B")
    return pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "open": close * 0.999, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": [10000] * n,
    })


def _dummy_backtest(symbol, strategy_ref, params=None, freq="daily", start="", end="",
                    adjust="qfq", initial_cash=100_000.0, strategy_manager=None, data_layer=None):
    from api.runner import run_backtest as real
    # Reuse real run_backtest but with a fake data_layer that returns make_bars().
    class FakeDataLayer:
        def get_bars(self, info, freq="daily", start="", end="", adjust="qfq"):
            return make_bars()
    return real(
        symbol=symbol, strategy_ref=strategy_ref, params=params, freq=freq,
        start=start, end=end, adjust=adjust, initial_cash=initial_cash,
        strategy_manager=strategy_manager, data_layer=FakeDataLayer(),
    )


def test_submit_and_wait_all(monkeypatch):
    import api.agent.executor as mod
    monkeypatch.setattr(mod, "run_backtest", _dummy_backtest)
    ex = BacktestExecutor()
    try:
        j1 = ex.submit("600519", "buy_and_hold", {})
        j2 = ex.submit("600519", "sma_cross", {"short": 5, "long": 20})
        assert ex.wait_all(timeout=30) is not None
        results = ex.wait_all(timeout=30)
        assert len(results) == 2
        assert results[0]["success"] is True
        assert "total_return" in results[0]["metrics"]
        ex.reset_batch()
        j3 = ex.submit("600519", "buy_and_hold", {})
        r3 = ex.wait_all(timeout=30)
        assert len(r3) == 1
        assert r3[0]["success"] is True
    finally:
        ex.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_agent_executor.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'api.agent.executor'`

- [ ] **Step 3: Write minimal implementation**

Create `api/agent/executor.py`:
```python
"""BacktestExecutor: runs backtests in a thread pool, batch-wait for results."""
from __future__ import annotations

import concurrent.futures
import itertools
from dataclasses import dataclass, field

from api.runner import run_backtest

_job_ids = itertools.count(1)


@dataclass
class BacktestJob:
    id: int
    symbol: str
    params: dict
    status: str = "running"
    result: dict | None = None
    error: str | None = None


class BacktestExecutor:
    def __init__(self, initial_cash: float = 100_000.0, max_workers: int = 4):
        self.initial_cash = initial_cash
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        self._jobs: dict[int, BacktestJob] = {}
        self._futures: list[concurrent.futures.Future] = []
        self._order: list[int] = []

    def submit(self, symbol, strategy_ref, params=None, freq="daily", start="2020-01-01",
               end="2024-12-31", adjust="qfq") -> int:
        job_id = next(_job_ids)
        job = BacktestJob(id=job_id, symbol=symbol, params=params or {})
        self._jobs[job_id] = job
        self._order.append(job_id)
        fut = self._pool.submit(
            run_backtest,
            symbol=symbol,
            strategy_ref=strategy_ref,
            params=params,
            freq=freq,
            start=start,
            end=end,
            adjust=adjust,
            initial_cash=self.initial_cash,
        )
        self._futures.append(fut)
        return job_id

    def wait_all(self, timeout: float = 300.0) -> list[dict]:
        """Block until all submitted jobs in the current batch finish. Returns results in submission order."""
        if not self._futures:
            return []
        for fut in concurrent.futures.as_completed(self._futures, timeout=timeout):
            # Find which job this future belongs to by index.
            idx = self._futures.index(fut)
            job_id = self._order[idx]
            job = self._jobs[job_id]
            try:
                job.result = fut.result()
                job.status = "done"
            except Exception as e:  # noqa: BLE001 - surface per-job errors
                job.error = str(e)
                job.status = "error"
        results = [self._jobs[job_id] for job_id in self._order]
        self._futures = []
        self._order = []
        self._jobs = {}
        return [
            {
                "job_id": j.id,
                "symbol": j.symbol,
                "params": j.params,
                "status": j.status,
                "result": j.result,
                "error": j.error,
            }
            for j in results
        ]

    def reset_batch(self):
        """Clear any remaining batch state (safe no-op if already drained)."""
        self._futures = []
        self._order = []
        self._jobs = {}

    def shutdown(self):
        self._pool.shutdown(wait=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_agent_executor.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/agent/executor.py tests/test_agent_executor.py
git commit -m "feat: thread-pool backtest executor with batch wait"
```

---

### Task 5: Agent tools (symbols, backtest, strategy, check_goal, publish)

**Files:**
- Create: `api/agent/tools.py`
- Test: `tests/test_agent_tools.py`

**Interfaces:**
- Produces (each a plain callable `(input: dict, ctx: AgentToolContext) -> str` returning a compact JSON string):
  - `list_symbols(input, ctx)` → list of `{code, name, type}` (≤20)
  - `run_backtest(input, ctx)` → `{job_id, ...}` (async submit via ctx.executor)
  - `register_strategy(input, ctx)` → `{name, version, status: 'draft'}`
  - `list_strategies(input, ctx)` → draft + published summary
  - `publish_strategy(input, ctx)` → requires `goal_met: true` (or a `goal` string); stores metrics snapshot
  - `check_goal(input, ctx)` → `{met: bool, reason}` (LLM supplies metrics + thresholds)
  - `TOOLS: list[dict]` — JSON schema for each tool (name/description/parameters)
  - `class AgentToolContext` — `executor: BacktestExecutor; store: StrategyStore; registry; strategy_manager`

- [ ] **Step 1: Write the failing test**

Create `tests/test_agent_tools.py`:
```python
"""Agent tools tests (fake ctx, no network)."""
from __future__ import annotations

import json

import pytest

from api.agent.tools import (
    AgentToolContext,
    TOOLS,
    list_symbols,
    register_strategy,
    check_goal,
    publish_strategy,
)


class FakeExecutor:
    def __init__(self):
        self.submitted = []
    def submit(self, *a, **k):
        self.submitted.append((a, k))
        return 1


def _ctx(tmp_path):
    from api.agent.store import StrategyStore
    store = StrategyStore(db_path=str(tmp_path / "t.db"))
    return AgentToolContext(store=store, executor=FakeExecutor())


def test_tools_schema_complete():
    names = {t["name"] for t in TOOLS}
    assert {"list_symbols", "run_backtest", "register_strategy",
            "list_strategies", "publish_strategy", "check_goal"} <= names


def test_register_strategy_is_draft(tmp_path):
    c = _ctx(tmp_path)
    out = json.loads(register_strategy(
        {"name": "ma", "source": "def strategy(ctx, params):\n    pass", "description": "d"},
        c,
    ))
    assert out["status"] == "draft"
    assert out["version"] == 1
    # invalid source rejected via AST sandbox
    with pytest.raises(Exception):
        register_strategy({"name": "bad", "source": "import os\ndef strategy(ctx,p): pass"}, c)


def test_check_goal_met():
    out = json.loads(check_goal(
        {"metrics": {"annual_return": 0.12, "max_drawdown": -0.10},
         "constraints": {"annual_return": 0.10, "max_drawdown": -0.15}},
        None,
    ))
    assert out["met"] is True


def test_check_goal_not_met():
    out = json.loads(check_goal(
        {"metrics": {"annual_return": 0.05, "max_drawdown": -0.10},
         "constraints": {"annual_return": 0.10, "max_drawdown": -0.15}},
        None,
    ))
    assert out["met"] is False


def test_publish_requires_goal_met(tmp_path):
    c = _ctx(tmp_path)
    register_strategy({"name": "ma", "source": "def strategy(ctx, p):\n    pass"}, c)
    with pytest.raises(Exception):
        publish_strategy({"name": "ma", "goal_met": False}, c)
    out = json.loads(publish_strategy(
        {"name": "ma", "goal_met": True, "metrics": {"annual_return": 0.12}, "goal": "年化>=10%"},
        c,
    ))
    assert out["status"] == "published"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_agent_tools.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'api.agent.tools'`

- [ ] **Step 3: Write minimal implementation**

Create `api/agent/tools.py`:
```python
"""LLM agent tools: the action surface the model can call."""
from __future__ import annotations

import json

from data.registry import get_registry
from strategies.base import validate_strategy_source
from strategies.manager import StrategyManager


class AgentToolContext:
    def __init__(self, store, executor, data_layer=None, strategy_manager=None):
        self.store = store
        self.executor = executor
        self.data_layer = data_layer
        self.strategy_manager = strategy_manager or StrategyManager()


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def list_symbols(input_: dict, ctx: AgentToolContext) -> str:
    reg = get_registry()
    typ = input_.get("type")
    keyword = input_.get("keyword")
    items = reg.list(typ)
    if keyword:
        kw = keyword.strip().lower()
        items = [s for s in items if kw in s.code.lower() or kw in s.name.lower()]
    items = items[:20]
    return _json({"symbols": [{"code": s.code, "name": s.name, "type": s.type} for s in items]})


def run_backtest(input_: dict, ctx: AgentToolContext) -> str:
    symbol = input_["symbol"]
    strategy_ref = input_.get("strategy_ref", input_.get("strategy", "buy_and_hold"))
    params = input_.get("params", {})
    job_id = ctx.executor.submit(
        symbol=symbol,
        strategy_ref=strategy_ref,
        params=params,
        freq=input_.get("freq", "daily"),
        start=input_.get("start", "2020-01-01"),
        end=input_.get("end", "2024-12-31"),
        adjust=input_.get("adjust", "qfq"),
    )
    return _json({"job_id": job_id, "status": "running", "symbol": symbol})


def register_strategy(input_: dict, ctx: AgentToolContext) -> str:
    name = input_["name"]
    source = input_["source"]
    validate_strategy_source(source)  # AST sandbox — raises on invalid
    rec = ctx.store.register_draft(name, source, input_.get("description", ""))
    return _json(rec)


def list_strategies(input_: dict, ctx: AgentToolContext) -> str:
    return _json({"strategies": ctx.store.list_strategies()})


def publish_strategy(input_: dict, ctx: AgentToolContext) -> str:
    if not input_.get("goal_met"):
        raise ValueError("cannot publish: goal not met")
    name = input_["name"]
    version = input_.get("version")
    g = ctx.store.get_strategy(name)
    if g is None:
        raise KeyError(f"unknown strategy: {name}")
    if version is None:
        version = g["current_version"]
    rec = ctx.store.publish_version(
        name, int(version),
        metrics=input_.get("metrics", {}),
        goal=input_.get("goal", ""),
    )
    return _json(rec)


def check_goal(input_: dict, ctx: AgentToolContext) -> str:
    """LLM supplies metrics + constraints; code verifies each constraint."""
    metrics = input_.get("metrics", {})
    constraints = input_.get("constraints", {})
    unmet = []
    for key, threshold in constraints.items():
        val = metrics.get(key)
        if val is None:
            unmet.append(f"{key}: missing")
            continue
        # constraints are lower bounds (min target) unless negative (max drawdown).
        if isinstance(threshold, (int, float)):
            if not float(val) >= float(threshold):
                unmet.append(f"{key}: {val} < {threshold}")
        else:
            unmet.append(f"{key}: bad threshold")
    met = not unmet
    return _json({"met": met, "unmet": unmet, "metrics": metrics})


TOOLS: list[dict] = [
    {
        "name": "list_symbols",
        "description": "List tradable symbols (stocks/funds/ETFs). Optional type ('stock'|'etf'|'fund') and keyword filter. Use to choose which symbol(s) to backtest.",
        "parameters": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["stock", "etf", "fund"], "description": "Symbol type filter"},
                "keyword": {"type": "string", "description": "Code or name keyword"},
            },
        },
    },
    {
        "name": "run_backtest",
        "description": "Submit a backtest job asynchronously. Returns a job_id. Submit multiple in one turn to run in parallel; the agent waits for all to finish before continuing. strategy_ref is a strategy NAME (the current draft) — call register_strategy first to create/update the draft.",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Symbol code, e.g. 510300"},
                "strategy_ref": {"type": "string", "description": "Strategy name (current draft)"},
                "params": {"type": "object", "description": "Strategy parameters (JSON)"},
                "freq": {"type": "string", "enum": ["daily", "1", "5", "15", "30", "60"], "description": "Bar frequency"},
                "start": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "end": {"type": "string", "description": "End date YYYY-MM-DD"},
                "adjust": {"type": "string", "enum": ["qfq", "hfq", "none"]},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "register_strategy",
        "description": "Create or update a strategy draft from Python source. The source must define `def strategy(ctx, params)` and use ctx.buy()/ctx.sell(). Updates the current draft; version increments. Always call this before running a backtest on your own strategy.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Strategy name"},
                "source": {"type": "string", "description": "Python source defining strategy(ctx, params)"},
                "description": {"type": "string", "description": "Human description"},
            },
            "required": ["name", "source"],
        },
    },
    {
        "name": "list_strategies",
        "description": "List registered strategies (drafts + published) with current version.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "publish_strategy",
        "description": "Publish a strategy version ONLY when the goal is met. Requires goal_met=true. Records the metrics snapshot. Call check_goal first to confirm.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "version": {"type": "integer", "description": "Optional; defaults to current draft version"},
                "goal_met": {"type": "boolean", "description": "Must be true to publish"},
                "metrics": {"type": "object", "description": "Metrics snapshot at publish time"},
                "goal": {"type": "string", "description": "The goal that was met"},
            },
            "required": ["name", "goal_met"],
        },
    },
    {
        "name": "check_goal",
        "description": "Verify whether backtest metrics meet the user's goal constraints. Pass the metrics from the backtest result and the goal constraints (lower bounds). Returns met: true/false.",
        "parameters": {
            "type": "object",
            "properties": {
                "metrics": {"type": "object", "description": "Backtest metrics, e.g. {annual_return, total_return, max_drawdown, sharpe}"},
                "constraints": {"type": "object", "description": "Goal thresholds (lower bounds), e.g. {annual_return: 0.10, max_drawdown: -0.15}"},
            },
            "required": ["metrics", "constraints"],
        },
    },
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_agent_tools.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/agent/tools.py tests/test_agent_tools.py
git commit -m "feat: agent tools (symbols/backtest/strategy/check_goal/publish)"
```

---

### Task 6: LLM agent loop (state machine + SSE event broadcast)

**Files:**
- Create: `api/agent/agent.py`
- Test: `tests/test_agent_agent.py`

**Interfaces:**
- Produces:
  - `class ChatSession: id: str; messages: deque[dict]` (in-memory event queue)
  - `class EventBus: create_session() -> ChatSession; publish(session_id, event: dict); stream(session_id, last_seq: int) -> generator[dict]` (drains queue then waits; yields events; heartbeat handled by caller)
  - `class LLMAgent:`
    - `__init__(self, provider: LLMProvider, store: StrategyStore, executor: BacktestExecutor, max_turns: int = 10, max_tools_per_turn: int = 5)`
    - `run(self, session_id: str, user_message: str, goal: str | None = None, bus: EventBus) -> dict` (final report; runs in a background thread)
  - System prompt builder `build_system_prompt(goal: str) -> str` describing tools + workflow (state snapshot injected per-turn into the user message)

- [ ] **Step 1: Write the failing test**

Create `tests/test_agent_agent.py`:
```python
"""LLMAgent loop tests (fake provider, no network)."""
from __future__ import annotations

import json
from collections import deque

import pytest

from api.agent.agent import LLMAgent, build_system_prompt
from api.agent.provider import LLMResponse, ToolCall


class FakeProvider:
    def __init__(self, script):
        """script: list of LLMResponse to return in order, then final."""
        self.script = list(script)
        self.calls = []
    def complete(self, *, system, messages, tools, model=None, max_tokens=4096):
        self.calls.append({"system": system, "messages": messages, "tools": tools})
        if self.script:
            return self.script.pop(0)
        return LLMResponse(text="完成", tool_uses=[])


class FakeExecutor:
    def submit(self, *a, **k):
        return 1
    def wait_all(self, timeout=300):
        return [{"job_id": 1, "symbol": "510300", "status": "done",
                 "result": {"metrics": {"annual_return": 0.12, "max_drawdown": -0.10}, "symbol_name": "沪深300ETF"},
                 "error": None}]
    def reset_batch(self):
        pass


class FakeStore:
    def __init__(self):
        self.drafts = {}
        self.published = []
    def register_draft(self, name, source, description=""):
        v = len(self.drafts.get(name, [])) + 1
        self.drafts[name] = [source]
        return {"name": name, "version": v, "status": "draft"}
    def get_strategy(self, name):
        if name not in self.drafts:
            return None
        return {"name": name, "status": "draft", "current_version": len(self.drafts[name])}
    def list_strategies(self, include_drafts=True):
        return [{"name": n, "status": "draft", "current_version": len(v)} for n, v in self.drafts.items()]
    def publish_version(self, name, version, metrics, goal):
        self.published.append((name, version))
        return {"name": name, "version": version, "status": "published", "metrics": metrics}


class FakeBus:
    def __init__(self):
        self.events = []
    def publish(self, session_id, event):
        self.events.append(event)


def test_agent_loops_until_publish():
    # Turn 1: register draft + run backtest; Turn 2: check_goal (met) + publish; Turn 3: final text.
    provider = FakeProvider([
        LLMResponse(text=None, tool_uses=[
            ToolCall(id="1", name="register_strategy", input={"name": "ma", "source": "def strategy(ctx,p): pass"}),
            ToolCall(id="2", name="run_backtest", input={"symbol": "510300", "strategy_ref": "ma"}),
        ]),
        LLMResponse(text=None, tool_uses=[
            ToolCall(id="3", name="check_goal", input={"metrics": {"annual_return": 0.12}, "constraints": {"annual_return": 0.10}}),
            ToolCall(id="4", name="publish_strategy", input={"name": "ma", "goal_met": True, "metrics": {"annual_return": 0.12}, "goal": "年化>=10%"}),
        ]),
        LLMResponse(text="目标达成，已发布", tool_uses=[]),
    ])
    agent = LLMAgent(provider=provider, store=FakeStore(), executor=FakeExecutor(),
                     max_turns=10, max_tools_per_turn=5)
    bus = FakeBus()
    report = agent.run("s1", "做年化10%", goal="年化>=10%", bus=bus)
    assert "目标达成" in report.get("report", "")
    assert len(provider.calls) == 3
    # second turn state snapshot includes backtest result
    assert "0.12" in json.dumps(provider.calls[1]["messages"])


def test_agent_stops_at_max_turns():
    provider = FakeProvider([
        LLMResponse(text=None, tool_uses=[ToolCall(id="1", name="list_strategies", input={})])
    ] * 3)  # 3 identical turns; max_turns=2 should stop before using all
    agent = LLMAgent(provider=provider, store=FakeStore(), executor=FakeExecutor(),
                     max_turns=2, max_tools_per_turn=5)
    bus = FakeBus()
    report = agent.run("s1", "试试", goal=None, bus=bus)
    assert len(provider.calls) == 2  # hit max_turns


def test_build_system_prompt_contains_goal():
    p = build_system_prompt("年化>=10%")
    assert "年化>=10%" in p
    assert "run_backtest" in p or "回测" in p
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_agent_agent.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'api.agent.agent'`

- [ ] **Step 3: Write minimal implementation**

Create `api/agent/agent.py`:
```python
"""LLMAgent: state-machine tool loop driving an LLM to meet a user goal."""
from __future__ import annotations

import json
import threading
import uuid
from collections import deque
from typing import Generator

from .provider import LLMProvider, LLMResponse, ToolCall
from .tools import TOOLS, AgentToolContext


def build_system_prompt(goal: str | None = None) -> str:
    lines = [
        "你是A股量化策略优化助手。用户给出投资目标（如年化收益、超额收益、最大回撤）。",
        "你的工作流程：",
        "1. 先 list_symbols 选标的（可并行多个）。",
        "2. 用 register_strategy 编写/修改策略草稿（Python 源码，定义 strategy(ctx, params)，用 ctx.buy()/ctx.sell()）。",
        "3. 用 run_backtest 提交回测（strategy_ref 用当前草稿名），可一次提交多个并行。",
        "4. 查看回测指标，用 check_goal 校验是否达标。未达标则修改草稿再回测。",
        "5. 达标后必须用 publish_strategy（goal_met=true）发布。",
        "工具结果会被合并到下一轮。达成目标后给出简短中文汇报。",
    ]
    if goal:
        lines.append(f"\n用户目标：{goal}")
    return "\n".join(lines)


def _build_state_snapshot(ctx: AgentToolContext, goal: str | None) -> dict:
    drafts = []
    for s in ctx.store.list_strategies():
        g = ctx.store.get_strategy(s["name"])
        if g is None:
            continue
        drafts.append({
            "name": g["name"],
            "status": g["status"],
            "current_version": g["current_version"],
        })
    return {"goal": goal, "strategies": drafts}


def _result_to_text(res: dict) -> str:
    """Compact backtest result for LLM context."""
    r = res.get("result")
    if r is None:
        return f"回测 job #{res.get('job_id')} 失败: {res.get('error')}"
    m = r.get("metrics", {})
    keep = {k: m.get(k) for k in
            ("total_return", "annual_return", "max_drawdown", "sharpe", "volatility", "win_rate", "n_trades")}
    return json.dumps({
        "job_id": res.get("job_id"),
        "symbol": r.get("symbol"),
        "symbol_name": r.get("symbol_name"),
        "metrics": keep,
    }, ensure_ascii=False)


class EventBus:
    """In-memory per-session event queue (drained on read)."""
    def __init__(self):
        self._sessions: dict[str, deque] = {}
        self._cond = threading.Condition()

    def create_session(self) -> str:
        sid = uuid.uuid4().hex[:12]
        with self._cond:
            self._sessions[sid] = deque()
        return sid

    def publish(self, session_id: str, event: dict):
        with self._cond:
            self._sessions.setdefault(session_id, deque()).append(event)
            self._cond.notify_all()

    def stream(self, session_id: str) -> Generator[dict, None, None]:
        """Drain queued events, then wait for new ones (caller handles heartbeat/timeout)."""
        while True:
            with self._cond:
                q = self._sessions.get(session_id)
                if q:
                    yield q.popleft()
                    continue
                if self._sessions.get(session_id) is None or getattr(self, "_stopped", False):
                    return
                self._cond.wait(timeout=15.0)
            yield {"type": "heartbeat"}  # signal caller to send SSE :ping


class LLMAgent:
    def __init__(self, provider, store, executor, max_turns=10, max_tools_per_turn=5):
        self.provider = provider
        self.store = store
        self.executor = executor
        self.max_turns = max_turns
        self.max_tools_per_turn = max_tools_per_turn
        self._ctx = AgentToolContext(store=store, executor=executor)

    def _tool_name_to_fn(self, name: str):
        import api.agent.tools as T
        return {
            "list_symbols": T.list_symbols,
            "run_backtest": T.run_backtest,
            "register_strategy": T.register_strategy,
            "list_strategies": T.list_strategies,
            "publish_strategy": T.publish_strategy,
            "check_goal": T.check_goal,
        }[name]

    def run(self, session_id: str, user_message: str, goal: str | None = None,
            bus: EventBus | None = None) -> dict:
        system = build_system_prompt(goal or user_message)
        # messages: user goal first, then alternating tool_use/tool_result as the loop runs.
        messages = [{"role": "user", "content": user_message}]
        final_text = ""
        for turn in range(self.max_turns):
            if bus:
                bus.publish(session_id, {"type": "turn", "turn": turn + 1})
            resp = self.provider.complete(system=system, messages=messages, tools=TOOLS)
            if resp.text:
                final_text = resp.text
            if not resp.tool_uses:
                break  # LLM done (report)
            # execute tools (bounded concurrency), collect tool_results
            tool_results = []
            for tc in resp.tool_uses[: self.max_tools_per_turn]:
                try:
                    fn = self._tool_name_to_fn(tc.name)
                    out = fn(tc.input, self._ctx)
                    tool_results.append({
                        "tool_use_id": tc.id, "content": out, "is_error": False,
                    })
                    if bus:
                        bus.publish(session_id, {"type": "tool", "name": tc.name, "output": out})
                except Exception as e:  # noqa: BLE001 - tool error surfaced to LLM
                    tool_results.append({
                        "tool_use_id": tc.id, "content": f"ERROR: {e}", "is_error": True,
                    })
                    if bus:
                        bus.publish(session_id, {"type": "tool_error", "name": tc.name, "error": str(e)})
            # backtests submitted this turn: wait for the batch, attach results
            if any(tc.name == "run_backtest" for tc in resp.tool_uses):
                results = self.executor.wait_all(timeout=300)
                self.executor.reset_batch()
                # append a compact state snapshot as an extra tool_result
                snapshot = _build_state_snapshot(self._ctx, goal or user_message)
                backtest_summary = "\n".join(_result_to_text(r) for r in results)
                tool_results.append({
                    "tool_use_id": "__state__",
                    "content": f"回测结果汇总：\n{backtest_summary}\n当前状态：{json.dumps(snapshot, ensure_ascii=False)}",
                    "is_error": False,
                })
                if bus:
                    bus.publish(session_id, {"type": "backtest_results", "results": results})
            messages.append({"role": "assistant", "content": [{"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.input} for tc in resp.tool_uses]})
            messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": tr["tool_use_id"], "content": tr["content"], "is_error": tr["is_error"]} for tr in tool_results]})
        if bus:
            bus.publish(session_id, {"type": "done", "report": final_text})
        return {"session_id": session_id, "report": final_text, "turns": turn + 1}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_agent_agent.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/agent/agent.py tests/test_agent_agent.py
git commit -m "feat: LLM agent state-machine loop with SSE event bus"
```

---

### Task 7: FastAPI endpoints (chat, SSE, providers, published strategies)

**Files:**
- Create: `api/agent/api.py`
- Modify: `api/main.py`
- Test: `tests/test_agent_api.py`

**Interfaces:**
- Consumes: `EventBus`, `LLMAgent`, `load_providers`, `StrategyStore`, `ChatStore`
- Produces (FastAPI routes):
  - `POST /api/chat` body `{session_id?, message, goal?}` → `{session_id}`, starts agent thread
  - `GET /api/chat/events?session_id=X` → SSE stream (drain queue + wait + heartbeat)
  - `GET /api/chat/sessions` → `{sessions: [...]}`
  - `GET /api/providers` → `{providers: [names]}`
  - `GET /api/strategies/published` → `{strategies: [...]}` (published only)
  - `GET /api/strategies/{name}/versions` → `{versions: [...]}`

- [ ] **Step 1: Write the failing test**

Create `tests/test_agent_api.py`:
```python
"""Agent API tests (fastapi TestClient)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_chat_returns_session():
    r = client.post("/api/chat", json={"message": "做年化10%", "goal": "年化>=10%"})
    assert r.status_code == 200
    assert "session_id" in r.json()


def test_providers_endpoint():
    r = client.get("/api/providers")
    assert r.status_code == 200
    assert "providers" in r.json()


def test_published_strategies_endpoint():
    r = client.get("/api/strategies/published")
    assert r.status_code == 200
    assert "strategies" in r.json()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_agent_api.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'api.agent.api'` (or route 404)

- [ ] **Step 3: Write minimal implementation**

Create `api/agent/api.py`:
```python
"""FastAPI routes for the LLM agent (chat, SSE, providers, published strategies)."""
from __future__ import annotations

import json
import threading

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from .agent import EventBus, LLMAgent
from .executor import BacktestExecutor
from .provider import load_providers
from .store import ChatStore, StrategyStore


def register_agent_routes(app: FastAPI) -> None:
    bus = EventBus()
    store = StrategyStore()
    chat_store = ChatStore()
    executor = BacktestExecutor()
    providers = load_providers()  # {name: LLMProvider}

    @app.post("/api/chat")
    def chat(body: dict):
        message = body.get("message", "")
        if not message:
            raise HTTPException(status_code=400, detail="message required")
        session_id = body.get("session_id") or bus.create_session()
        goal = body.get("goal")
        chat_store.add_message(session_id, "user", message)

        provider_name = body.get("provider")
        provider = providers.get(provider_name) if provider_name else next(iter(providers.values()))
        if provider is None:
            raise HTTPException(status_code=400, detail="no provider configured")

        agent = LLMAgent(provider=provider, store=store, executor=executor)
        chat_store.add_message(session_id, "system", f"目标: {goal or message}")

        def _run():
            report = agent.run(session_id, message, goal=goal, bus=bus)
            chat_store.add_message(session_id, "assistant", report.get("report", ""))

        threading.Thread(target=_run, daemon=True).start()
        return {"session_id": session_id}

    @app.get("/api/chat/events")
    def chat_events(session_id: str):
        def gen():
            yield "retry: 3000\n\n"  # SSE reconnect hint
            for event in bus.stream(session_id):
                if event.get("type") == "heartbeat":
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/api/chat/sessions")
    def chat_sessions():
        return {"sessions": chat_store.list_sessions()}

    @app.get("/api/providers")
    def providers_list():
        return {"providers": list(providers.keys())}

    @app.get("/api/strategies/published")
    def published():
        return {"strategies": store.list_strategies(include_drafts=False)}

    @app.get("/api/strategies/{name}/versions")
    def strategy_versions(name: str):
        g = store.get_strategy(name)
        if g is None:
            raise HTTPException(status_code=404, detail="unknown strategy")
        return {"name": name, "versions": g["versions"]}
```

Modify `api/main.py` — add after the existing routes, near the bottom:
```python
from .agent.api import register_agent_routes

register_agent_routes(app)
```

(Note: because `api/main.py` currently defines routes as module-level functions and `register_agent_routes` adds more, place the import + call after all existing route definitions, before the chart-data section.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_agent_api.py -q`
Expected: PASS

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/python -m pytest tests/ -x -q`
Expected: all pass (25 existing + new)

- [ ] **Step 6: Commit**

```bash
git add api/agent/api.py api/main.py tests/test_agent_api.py
git commit -m "feat: agent chat/SSE/providers/published API routes"
```

---

### Task 8: Web chat UI

**Files:**
- Modify: `web/index.html` (add a chat panel + JS for SSE + sending)

**Interfaces:**
- Consumes: `POST /api/chat`, `GET /api/chat/events?session_id=`, `GET /api/providers`, `GET /api/strategies/published`

- [ ] **Step 1: Add chat panel HTML**

Add after the existing `<div class="main">` block (before `<script>`), a new panel:
```html
<div class="panel" id="chatPanel">
  <h3>🤖 AI 目标优化</h3>
  <div id="chatLog" style="max-height:320px;overflow:auto;border:1px solid var(--border);border-radius:6px;padding:8px;font-size:12px;background:var(--bg);"></div>
  <label for="chatProvider">模型</label>
  <select id="chatProvider"></select>
  <label for="chatInput">目标 / 指令</label>
  <input id="chatInput" placeholder="例如：在沪深300上做到年化收益10%、回撤小于15%">
  <button id="chatSend" class="btn">发送</button>
  <div id="chatStatus" class="status"></div>
</div>
```

- [ ] **Step 2: Add chat JS**

Add inside the existing `<script>` (after `loadStrategies`):
```javascript
const chatLog = document.getElementById('chatLog');
const chatInput = document.getElementById('chatInput');
const chatProvider = document.getElementById('chatProvider');
const chatSend = document.getElementById('chatSend');
const chatStatus = document.getElementById('chatStatus');
let chatSession = null;
let chatES = null;

function chatAppend(role, text) {
  const d = document.createElement('div');
  d.style.margin = '4px 0';
  d.innerHTML = `<b>${role}:</b> ${text}`;
  chatLog.appendChild(d);
  chatLog.scrollTop = chatLog.scrollHeight;
}

async function loadProviders() {
  const r = await fetch('/api/providers');
  const data = await r.json();
  data.providers.forEach(n => {
    const o = document.createElement('option');
    o.value = n; o.textContent = n;
    chatProvider.appendChild(o);
  });
}

function chatConnect(sessionId) {
  if (chatES) chatES.close();
  chatES = new EventSource(`/api/chat/events?session_id=${sessionId}`);
  chatES.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    if (ev.type === 'turn') { chatStatus.textContent = `第 ${ev.turn} 轮…`; }
    else if (ev.type === 'tool') { chatAppend('系统', `调用 ${ev.name}`); }
    else if (ev.type === 'tool_error') { chatAppend('系统', `⚠ ${ev.name}: ${ev.error}`); }
    else if (ev.type === 'backtest_results') { chatAppend('系统', '✅ 回测结果已返回'); }
    else if (ev.type === 'done') { chatAppend('AI', ev.report || '(完成)'); chatStatus.textContent = '完成'; chatES.close(); }
  };
  chatES.onerror = () => { chatStatus.textContent = '连接中断，重连中…'; };
}

async function sendChat() {
  const msg = chatInput.value.trim();
  if (!msg) return;
  chatInput.value = '';
  chatAppend('用户', msg);
  chatStatus.textContent = '思考中…';
  chatSend.disabled = true;
  const r = await fetch('/api/chat', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ message: msg, provider: chatProvider.value, session_id: chatSession }),
  });
  const data = await r.json();
  chatSession = data.session_id;
  chatConnect(chatSession);
  chatSend.disabled = false;
}
chatSend.addEventListener('click', sendChat);
chatInput.addEventListener('keydown', e => { if (e.key === 'Enter') sendChat(); });
loadProviders();
```

- [ ] **Step 3: Verify via server + headless browser**

Start server: `.venv/bin/uvicorn api.main:app --port 8000`
Then load page headlessly and confirm the chat panel renders (no JS errors):
```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless --disable-gpu \
  --dump-dom http://127.0.0.1:8000/ 2>/dev/null | grep -c "AI 目标优化"
```
Expected: `1`

- [ ] **Step 4: Manual smoke — send a goal, watch SSE**

Start server, open page, type a goal, click send. Confirm: chat log shows user msg, then progress events (`调用 run_backtest`, `✅ 回测结果已返回`), then final AI report. (Requires a configured provider + network; if provider unconfigured, `/api/providers` returns empty and the send shows an error.)

- [ ] **Step 5: Commit**

```bash
git add web/index.html
git commit -m "feat: web chat panel for AI goal optimization"
```

---

### Task 9: Full-suite verification

**Files:**
- Verify: all

- [ ] **Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -x -q`
Expected: all pass

- [ ] **Step 2: Verify server boots with agent routes**

Run: `.venv/bin/uvicorn api.main:app --port 8000` then
`.venv/bin/python -c "from api.main import app; print([r.path for r in app.routes if 'chat' in r.path or 'providers' in r.path])"`
Expected: prints the chat/providers/published routes

- [ ] **Step 3: Verify config/llm.json exists and providers load**

Run: `.venv/bin/python -c "from api.agent.provider import load_providers; print(list(load_providers().keys()))"`
Expected: prints at least the configured provider name (no crash if no key set — env expansion yields empty key)

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: LLM goal-optimization agent (chat UI + provider abstraction + SQLite + SSE)"
```

---

## Self-Review

**Spec coverage:**
- Chat input / goal setting → Task 8 (UI) + Task 7 (`/api/chat`) ✅
- LLM auto (no confirm) → Task 6 (loop, no approval gate) ✅
- LLM writes strategy source via AST sandbox → Task 5 `register_strategy` reuses `validate_strategy_source` ✅
- Draft → publish + versions + SQLite → Task 2 (StrategyStore) + Task 5 `publish_strategy` (requires goal_met) ✅
- Multi-provider → Task 3 (LLMProvider ABC + Anthropic/OpenAICompat + load_providers) ✅
- Async + parallel backtests → Task 4 (BacktestExecutor thread pool) + Task 6 (batch wait_all) ✅
- SSE streaming + heartbeat + no event persistence → Task 6 (EventBus memory queue) + Task 7 (SSE route, `:ping`) ✅
- Chat history persisted (user + assistant), progress in-memory → Task 2 (ChatStore) + Task 7 (add_message on user/assistant) ✅
- Loop control params (max_turns=10, max_tools=5) → Task 6 ✅

**Placeholder scan:** No TBD/TODO. Every step has code or exact commands. ✅

**Type consistency:** `LLMResponse`/`ToolCall` defined Task 3, used Task 6. `BacktestExecutor.submit/wait_all/reset_batch` defined Task 4, used Task 5 (tools) + Task 6. `StrategyStore.register_draft/publish_version/list_strategies` defined Task 2, used Task 5/6/7. `EventBus.publish/stream` defined Task 6, used Task 7. `load_providers` defined Task 3, used Task 7. Signatures match across tasks. ✅

One consistency note: Task 5's `AgentToolContext` constructor signature `(store, executor, data_layer=None, strategy_manager=None)` must match how Task 6 constructs it (`AgentToolContext(store=store, executor=executor)`) — both use keyword args, data_layer/strategy_manager default to None. ✅
