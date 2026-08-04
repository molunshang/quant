# LLM 配置管理面板 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提供 Web 设置面板 + 校验 API 管理 LLM provider 配置（`config/llm.json`），避免手改 JSON 出错，写后无需重启即生效，并支持带可定位错误原因的连接测试。

**Architecture:** 新增 `ProviderConfigStore`（`api/agent/config.py`）作为 `config/llm.json` 的唯一读写入口：校验 + 原子写 + 写后重载内存 provider 缓存。`api/agent/api.py` 改为经 store 获取 providers，并新增 6 个语义化端点。`web/index.html` 新增「LLM 设置」面板。

**Tech Stack:** Python 3.14 / FastAPI / pytest / SQLite(已有 store 模式) / 原生 JS（无框架）

## Global Constraints

- `config/llm.json` 是唯一配置源；任何写操作必须：先校验 → 失败返回中文 `ConfigError` 且**不写文件** → 成功后立即重载内存 provider 缓存（免重启）。
- `api_key` 支持明文 或 `env:VAR` 引用（保持现有能力，`_expand_env` 在 `api/agent/provider.py`）。
- 连接测试失败时 `error` 必须可定位：`env:` 未设置点名变量；网络/HTTP 错误附 base_url 与底层异常（含 HTTP 状态码）。
- 端点非 RESTful，路径直接体现功能（`/api/llm/providers/add`、`/update`、`/delete`、`/test`、`/list`、`/api/llm/default/set`）。
- 配置路径测试可覆盖：`QUANT_LLM_CONFIG` 环境变量（仅测试用）。

---

### Task 1: ProviderConfigStore — 文件读写 + 读取 + 重载

**Files:**
- Create: `api/agent/config.py`
- Create: `tests/test_llm_config.py`

**Interfaces:**
- Consumes: `load_providers`, `_expand_env`, `AnthropicProvider`, `OpenAICompatProvider` from `api/agent/provider.py` (existing signatures, unmodified).
- Produces: `class ConfigError(Exception)`; `class ProviderConfigStore` with `__init__(path: str | None = None)`, `list() -> dict`, `get(name) -> dict | None`, `get_default() -> str | None`, `providers() -> dict[str, LLMProvider]`. Later tasks add `add/update/delete/set_default/test`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_llm_config.py`:

```python
"""LLM config store + API tests (isolated tmp config, mocked network)."""
from __future__ import annotations

import json

import pytest

from api.agent.config import ConfigError, ProviderConfigStore


def _cfg(tmp_path, text=None):
    path = tmp_path / "llm.json"
    if text is not None:
        path.write_text(text, encoding="utf-8")
    return path


def test_list_empty_when_no_file(tmp_path):
    store = ProviderConfigStore(str(_cfg(tmp_path)))
    assert store.list() == {"default": None, "providers": [], "error": None}


def test_list_reads_existing_file(tmp_path):
    path = _cfg(tmp_path, '{"default": "a", "providers": [{"name": "a", "type": "anthropic"}]}')
    store = ProviderConfigStore(str(path))
    data = store.list()
    assert data["default"] == "a"
    assert [p["name"] for p in data["providers"]] == ["a"]


def test_corrupt_file_raises_config_error(tmp_path):
    store = ProviderConfigStore(str(_cfg(tmp_path, "{not json")))
    with pytest.raises(ConfigError):
        store.list()


def test_providers_builds_from_file(tmp_path):
    path = _cfg(tmp_path, '{"providers": [{"name": "local", "type": "openai_compat", '
                          '"base_url": "http://127.0.0.1:3456", "model": "m1", "api_key": "k"}]}')
    store = ProviderConfigStore(str(path))
    assert "local" in store.providers()


def test_providers_degrades_on_bad_entry(tmp_path):
    # provider missing 'name' -> load_providers KeyError -> empty providers + error exposed
    path = _cfg(tmp_path, '{"providers": [{"type": "anthropic", "api_key": "k", "model": "m"}]}')
    store = ProviderConfigStore(str(path))
    assert store.providers() == {}
    assert "解析失败" in (store.list()["error"] or "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_llm_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'api.agent.config'`

- [ ] **Step 3: Write minimal implementation**

Create `api/agent/config.py`:

```python
"""LLM provider configuration management: validated, atomic read/write of config/llm.json."""
from __future__ import annotations

import json
import os
import threading

from .provider import AnthropicProvider, OpenAICompatProvider, _expand_env, load_providers

DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config", "llm.json"
)
VALID_TYPES = ("anthropic", "openai_compat")


class ConfigError(Exception):
    """Invalid provider config. Message is user-facing Chinese."""


def _default_path() -> str:
    return os.environ.get("QUANT_LLM_CONFIG") or DEFAULT_PATH


class ProviderConfigStore:
    def __init__(self, path: str | None = None):
        self.path = path or _default_path()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._lock = threading.RLock()
        self._default: str | None = None
        self._providers: dict[str, object] = {}
        self._load_error: str | None = None
        if os.path.exists(self.path):
            try:
                self._reload()
            except ConfigError as e:
                self._load_error = str(e)

    # ---- file I/O ----
    def _read(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except FileNotFoundError:
            return {"default": None, "providers": []}
        except json.JSONDecodeError as e:
            raise ConfigError(f"配置文件格式错误: {e}") from e
        if not isinstance(cfg, dict):
            raise ConfigError("配置文件格式错误: 顶层必须是 JSON 对象")
        providers = cfg.get("providers", [])
        if not isinstance(providers, list):
            raise ConfigError("配置文件格式错误: providers 必须是数组")
        return {"default": cfg.get("default"), "providers": providers}

    def _write(self, cfg: dict) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def _reload(self) -> None:
        with self._lock:
            cfg = self._read()
            self._default = cfg["default"]
            self._load_error = None
            try:
                self._providers = load_providers(self.path)
            except (ValueError, KeyError) as e:
                self._providers = {}
                self._load_error = f"配置解析失败: {e}"

    # ---- reads ----
    def list(self) -> dict:
        cfg = self._read()
        return {"default": cfg["default"], "providers": cfg["providers"], "error": self._load_error}

    def get(self, name: str) -> dict | None:
        for p in self.list()["providers"]:
            if p.get("name") == name:
                return p
        return None

    def get_default(self) -> str | None:
        with self._lock:
            return self._default

    def providers(self) -> dict[str, object]:
        with self._lock:
            return dict(self._providers)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_llm_config.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add api/agent/config.py tests/test_llm_config.py
git commit -m "feat: ProviderConfigStore config read/write/reload with graceful degradation"
```

---

### Task 2: 校验 + 增删改 + 设默认

**Files:**
- Modify: `api/agent/config.py` (add `_validate`, `add`, `update`, `delete`, `set_default` — append inside the class before a new `# ---- mutations` section)
- Modify: `tests/test_llm_config.py` (append tests)

**Interfaces:**
- Consumes: Task 1 `_read`/`_write`/`_reload`/`list`.
- Produces: `add(p: dict) -> dict`, `update(name: str, p: dict) -> dict`, `delete(name: str) -> dict`, `set_default(name: str) -> dict` — each returns `self.list()` after write+reload; raise `ConfigError` (Chinese) on validation failure / unknown name.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_llm_config.py`:

```python
def test_add_valid_writes_file(tmp_path):
    store = ProviderConfigStore(str(_cfg(tmp_path)))
    data = store.add({"name": "p1", "type": "anthropic", "model": "m", "api_key": "k"})
    assert [p["name"] for p in data["providers"]] == ["p1"]
    assert json.loads(_cfg(tmp_path).read_text())["providers"][0]["name"] == "p1"
    assert "p1" in store.providers()  # reloaded, live


def test_add_duplicate_name_rejected_no_write(tmp_path):
    path = _cfg(tmp_path, '{"providers": [{"name": "p1", "type": "anthropic", "model": "m", "api_key": "k"}]}')
    store = ProviderConfigStore(str(path))
    with pytest.raises(ConfigError, match="已存在"):
        store.add({"name": "p1", "type": "anthropic", "model": "m2", "api_key": "k"})
    assert [p["name"] for p in store.list()["providers"]] == ["p1"]


def test_add_invalid_type_rejected(tmp_path):
    store = ProviderConfigStore(str(_cfg(tmp_path)))
    with pytest.raises(ConfigError, match="类型"):
        store.add({"name": "p1", "type": "unknown", "model": "m", "api_key": "k"})


def test_add_openai_compat_requires_base_url(tmp_path):
    store = ProviderConfigStore(str(_cfg(tmp_path)))
    with pytest.raises(ConfigError, match="base_url"):
        store.add({"name": "p1", "type": "openai_compat", "model": "m", "api_key": "k"})


def test_add_empty_model_rejected(tmp_path):
    store = ProviderConfigStore(str(_cfg(tmp_path)))
    with pytest.raises(ConfigError, match="模型"):
        store.add({"name": "p1", "type": "anthropic", "model": " ", "api_key": "k"})


def test_add_empty_key_rejected(tmp_path):
    store = ProviderConfigStore(str(_cfg(tmp_path)))
    with pytest.raises(ConfigError, match="API Key"):
        store.add({"name": "p1", "type": "anthropic", "model": "m", "api_key": ""})


def test_update_edits_provider(tmp_path):
    path = _cfg(tmp_path, '{"providers": [{"name": "p1", "type": "anthropic", "model": "m1", "api_key": "k"}]}')
    store = ProviderConfigStore(str(path))
    data = store.update("p1", {"type": "anthropic", "model": "m2", "api_key": "k"})
    p1 = next(p for p in data["providers"] if p["name"] == "p1")
    assert p1["model"] == "m2"


def test_update_unknown_name_rejected(tmp_path):
    store = ProviderConfigStore(str(_cfg(tmp_path)))
    with pytest.raises(ConfigError, match="不存在"):
        store.update("nope", {"type": "anthropic", "model": "m", "api_key": "k"})


def test_delete_removes_provider(tmp_path):
    path = _cfg(tmp_path, '{"providers": [{"name": "p1", "type": "anthropic", "model": "m", "api_key": "k"}]}')
    store = ProviderConfigStore(str(path))
    data = store.delete("p1")
    assert data["providers"] == []
    assert "p1" not in store.providers()


def test_delete_default_clears_default(tmp_path):
    path = _cfg(tmp_path, '{"default": "p1", "providers": [{"name": "p1", "type": "anthropic", "model": "m", "api_key": "k"}]}')
    store = ProviderConfigStore(str(path))
    assert store.delete("p1")["default"] is None


def test_delete_unknown_rejected(tmp_path):
    store = ProviderConfigStore(str(_cfg(tmp_path)))
    with pytest.raises(ConfigError, match="不存在"):
        store.delete("nope")


def test_set_default(tmp_path):
    path = _cfg(tmp_path, '{"providers": [{"name": "p1", "type": "anthropic", "model": "m", "api_key": "k"},'
                          '{"name": "p2", "type": "anthropic", "model": "m", "api_key": "k"}]}')
    store = ProviderConfigStore(str(path))
    data = store.set_default("p2")
    assert data["default"] == "p2"
    assert store.get_default() == "p2"


def test_set_default_unknown_rejected(tmp_path):
    store = ProviderConfigStore(str(_cfg(tmp_path)))
    with pytest.raises(ConfigError, match="不存在"):
        store.set_default("nope")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_llm_config.py -v`
Expected: 12 FAIL (`AttributeError: 'ProviderConfigStore' object has no attribute 'add'` etc.)

- [ ] **Step 3: Write minimal implementation**

Append inside `ProviderConfigStore` (after `providers` method):

```python
    # ---- validation ----
    def _validate(self, p: dict, existing: set[str]) -> None:
        name = p.get("name", "")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError("名称不能为空")
        if name in existing:
            raise ConfigError(f"provider 名称已存在: {name}")
        typ = p.get("type")
        if typ not in VALID_TYPES:
            raise ConfigError(f"类型必须为 {'/'.join(VALID_TYPES)}，当前: {typ}")
        if not isinstance(p.get("model"), str) or not p["model"].strip():
            raise ConfigError("模型名称不能为空")
        api_key = p.get("api_key", "")
        if not isinstance(api_key, str) or not api_key.strip():
            raise ConfigError("API Key 不能为空")
        base_url = p.get("base_url", "")
        if typ == "openai_compat" and not (isinstance(base_url, str) and base_url.strip()):
            raise ConfigError("openai_compat 类型必须填写 base_url")
        if typ == "openai_compat" and not base_url.startswith(("http://", "https://")):
            raise ConfigError("base_url 必须以 http:// 或 https:// 开头")

    # ---- mutations (each validates, writes, reloads) ----
    def add(self, p: dict) -> dict:
        with self._lock:
            cfg = self._read()
            self._validate(p, {x["name"] for x in cfg["providers"]})
            cfg["providers"].append(p)
            self._write(cfg)
            self._reload()
            return self.list()

    def update(self, name: str, p: dict) -> dict:
        with self._lock:
            cfg = self._read()
            names = [x["name"] for x in cfg["providers"]]
            if name not in names:
                raise ConfigError(f"provider 不存在: {name}")
            p = dict(p)
            p["name"] = name  # name is identity, not editable
            existing = set(names) - {name}
            self._validate(p, existing)
            cfg["providers"] = [p if x["name"] == name else x for x in cfg["providers"]]
            self._write(cfg)
            self._reload()
            return self.list()

    def delete(self, name: str) -> dict:
        with self._lock:
            cfg = self._read()
            kept = [x for x in cfg["providers"] if x["name"] != name]
            if len(kept) == len(cfg["providers"]):
                raise ConfigError(f"provider 不存在: {name}")
            cfg["providers"] = kept
            if cfg.get("default") == name:
                cfg["default"] = None
            self._write(cfg)
            self._reload()
            return self.list()

    def set_default(self, name: str) -> dict:
        with self._lock:
            cfg = self._read()
            if not any(x["name"] == name for x in cfg["providers"]):
                raise ConfigError(f"provider 不存在: {name}")
            cfg["default"] = name
            self._write(cfg)
            self._reload()
            return self.list()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_llm_config.py -v`
Expected: 17 PASS

- [ ] **Step 5: Commit**

```bash
git add api/agent/config.py tests/test_llm_config.py
git commit -m "feat: validate + add/update/delete/set_default with atomic write and reload"
```

---

### Task 3: 连接测试 + 可定位错误原因

**Files:**
- Modify: `api/agent/config.py` (add `test`, `_build`, `_ping`)
- Modify: `tests/test_llm_config.py` (append tests)

**Interfaces:**
- Consumes: `_validate`, `_expand_env` (provider.py), `AnthropicProvider`/`OpenAICompatProvider` constructors.
- Produces: `test(p: dict) -> dict` — `{"ok": True, "error": None}` on success; `{"ok": False, "error": <locatable reason>}` on failure. Never raises; never makes a network call with a missing `env:` var.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_llm_config.py`:

```python
class _FakeProvider:
    def __init__(self, exc=None):
        self.exc = exc
        self.called = False

    def complete(self, **kwargs):
        self.called = True
        if self.exc:
            raise self.exc
        from api.agent.provider import LLMResponse
        return LLMResponse(text="pong")


class _StatusError(Exception):
    def __init__(self, status_code, msg):
        super().__init__(msg)
        self.status_code = status_code


def _store_with_fake_build(monkeypatch, tmp_path, exc=None):
    store = ProviderConfigStore(str(_cfg(tmp_path)))
    fake = _FakeProvider(exc)
    monkeypatch.setattr(store, "_build", lambda p: fake)
    return store, fake


def test_test_connection_ok(monkeypatch, tmp_path):
    store, fake = _store_with_fake_build(monkeypatch, tmp_path)
    res = store.test({"name": "p", "type": "anthropic", "model": "m", "api_key": "k"})
    assert res["ok"] is True
    assert res["error"] is None
    assert fake.called


def test_test_env_missing(tmp_path):
    store = ProviderConfigStore(str(_cfg(tmp_path)))
    res = store.test({"name": "p", "type": "anthropic", "model": "m", "api_key": "env:NOT_SET_XYZ"})
    assert res["ok"] is False
    assert "NOT_SET_XYZ" in res["error"]


def test_test_error_includes_base_url_and_detail(monkeypatch, tmp_path):
    store, _ = _store_with_fake_build(monkeypatch, tmp_path, exc=ConnectionError("Connection refused"))
    res = store.test({"name": "p", "type": "openai_compat", "base_url": "http://127.0.0.1:9",
                      "model": "m", "api_key": "k"})
    assert res["ok"] is False
    assert "http://127.0.0.1:9" in res["error"]
    assert "Connection refused" in res["error"]


def test_test_error_includes_http_status(monkeypatch, tmp_path):
    store, _ = _store_with_fake_build(monkeypatch, tmp_path, exc=_StatusError(401, "Unauthorized"))
    res = store.test({"name": "p", "type": "anthropic", "model": "m", "api_key": "k"})
    assert res["ok"] is False
    assert "401" in res["error"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_llm_config.py -v`
Expected: 4 FAIL (`AttributeError: 'ProviderConfigStore' object has no attribute 'test'`)

- [ ] **Step 3: Write minimal implementation**

Append inside `ProviderConfigStore` (after `set_default`):

```python
    # ---- test connectivity ----
    def test(self, p: dict) -> dict:
        api_key = p.get("api_key", "")
        if isinstance(api_key, str) and api_key.startswith("env:"):
            var = api_key[4:]
            if not os.environ.get(var):
                return {"ok": False, "error": f"环境变量 {var} 未设置"}
        try:
            self._validate(dict(p, name=p.get("name") or "_test"), set())
        except ConfigError as e:
            return {"ok": False, "error": str(e)}
        base_url = p.get("base_url") or "(使用默认地址)"
        try:
            self._ping(self._build(p))
        except Exception as e:  # noqa: BLE001 - surface underlying error for diagnosis
            status = getattr(e, "status_code", None)
            detail = f"HTTP {status}: {e}" if status else (str(e) or type(e).__name__)
            return {"ok": False, "error": f"{base_url} → {detail}"}
        return {"ok": True, "error": None}

    def _build(self, p: dict):
        typ = p["type"]
        api_key = _expand_env(p.get("api_key", ""))
        model = p.get("model", "claude-opus-5")
        if typ == "anthropic":
            return AnthropicProvider(api_key=api_key, base_url=p.get("base_url"), model=model)
        return OpenAICompatProvider(api_key=api_key, base_url=p["base_url"], model=model)

    def _ping(self, provider) -> None:
        provider.complete(
            system="ping",
            messages=[{"role": "user", "content": "ping"}],
            tools=[],
            max_tokens=1,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_llm_config.py -v`
Expected: 21 PASS

- [ ] **Step 5: Commit**

```bash
git add api/agent/config.py tests/test_llm_config.py
git commit -m "feat: provider test-connection with locatable error reasons"
```

---

### Task 4: API 端点 + 集成测试

**Files:**
- Modify: `api/agent/api.py` (replace `providers = load_providers()`; add 6 endpoints; chat provider resolution)
- Modify: `tests/test_llm_config.py` (append API section)

**Interfaces:**
- Consumes: `ProviderConfigStore`, `ConfigError` from Task 1–3.
- Produces: endpoints `GET /api/llm/providers/list`, `POST /api/llm/providers/add|update|delete|test`, `POST /api/llm/default/set`; `/api/providers` and `/api/chat` now read from the store.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_llm_config.py`:

```python
def _make_client(tmp_path, monkeypatch):
    import os

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.agent.api import register_agent_routes

    cfg_path = tmp_path / "llm.json"
    cfg_path.write_text('{"providers": []}', encoding="utf-8")
    monkeypatch.setenv("QUANT_LLM_CONFIG", str(cfg_path))
    app = FastAPI()
    register_agent_routes(app)
    return TestClient(app), cfg_path


def test_api_list_empty(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    r = client.get("/api/llm/providers/list")
    assert r.status_code == 200
    assert r.json()["providers"] == []


def test_api_add_and_list(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    body = {"name": "p1", "type": "openai_compat", "base_url": "http://127.0.0.1:3456",
            "model": "m1", "api_key": "k"}
    r = client.post("/api/llm/providers/add", json=body)
    assert r.status_code == 200
    assert [p["name"] for p in r.json()["providers"]] == ["p1"]
    r2 = client.get("/api/llm/providers/list")
    assert [p["name"] for p in r2.json()["providers"]] == ["p1"]


def test_api_add_invalid_returns_400(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    r = client.post("/api/llm/providers/add",
                    json={"name": "", "type": "anthropic", "model": "m", "api_key": "k"})
    assert r.status_code == 400
    assert "名称" in r.json()["detail"]


def test_api_update(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    client.post("/api/llm/providers/add", json={"name": "p1", "type": "openai_compat",
                "base_url": "http://x", "model": "m1", "api_key": "k"})
    r = client.post("/api/llm/providers/update", json={"name": "p1", "type": "openai_compat",
                    "base_url": "http://x", "model": "m2", "api_key": "k"})
    assert r.status_code == 200
    p1 = next(p for p in r.json()["providers"] if p["name"] == "p1")
    assert p1["model"] == "m2"


def test_api_delete(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    client.post("/api/llm/providers/add", json={"name": "p1", "type": "openai_compat",
                "base_url": "http://x", "model": "m1", "api_key": "k"})
    r = client.post("/api/llm/providers/delete", json={"name": "p1"})
    assert r.status_code == 200
    assert r.json()["providers"] == []


def test_api_default_set(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    client.post("/api/llm/providers/add", json={"name": "p1", "type": "openai_compat",
                "base_url": "http://x", "model": "m1", "api_key": "k"})
    r = client.post("/api/llm/default/set", json={"name": "p1"})
    assert r.status_code == 200
    assert r.json()["default"] == "p1"


def test_api_test_env_missing(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    r = client.post("/api/llm/providers/test", json={"name": "p1", "type": "anthropic",
                    "model": "m", "api_key": "env:NOT_SET_XYZ"})
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert "NOT_SET_XYZ" in r.json()["error"]


def test_api_chat_uses_default_provider(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    client.post("/api/llm/providers/add", json={"name": "p1", "type": "openai_compat",
                "base_url": "http://127.0.0.1:1", "model": "m1", "api_key": "k"})
    client.post("/api/llm/default/set", json={"name": "p1"})
    r = client.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 200
    assert "session_id" in r.json()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_llm_config.py -v`
Expected: API tests FAIL (`404 Not Found` on `/api/llm/providers/list`)

- [ ] **Step 3: Write minimal implementation**

In `api/agent/api.py`:

Replace the import line `from .provider import load_providers` with:

```python
from .config import ConfigError, ProviderConfigStore
```

Replace `providers = load_providers()  # {name: LLMProvider}` with:

```python
    config = ProviderConfigStore()  # {name: LLMProvider} cache, reloaded on every write
```

Replace the provider resolution block in `/api/chat`:

```python
        provider_name = body.get("provider")
        providers = config.providers()
        if not providers:
            raise HTTPException(status_code=400, detail="no provider configured")
        if provider_name:
            provider = providers.get(provider_name)
            if provider is None:
                raise HTTPException(status_code=400, detail=f"未知 provider: {provider_name}")
        else:
            default = config.get_default()
            provider = providers.get(default) if default else next(iter(providers.values()))
```

Replace the `/api/providers` endpoint body:

```python
    @app.get("/api/providers")
    def providers_list():
        return {"providers": list(config.providers().keys())}
```

Add these endpoints after `/api/providers`:

```python
    @app.get("/api/llm/providers/list")
    def llm_providers_list():
        try:
            return config.list()
        except ConfigError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/llm/providers/add")
    def llm_providers_add(body: dict):
        try:
            return config.add(body)
        except ConfigError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/llm/providers/update")
    def llm_providers_update(body: dict):
        name = body.get("name")
        if not name:
            raise HTTPException(status_code=400, detail="缺少 name")
        try:
            return config.update(name, body)
        except ConfigError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/llm/providers/delete")
    def llm_providers_delete(body: dict):
        name = body.get("name")
        if not name:
            raise HTTPException(status_code=400, detail="缺少 name")
        try:
            return config.delete(name)
        except ConfigError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/llm/providers/test")
    def llm_providers_test(body: dict):
        return config.test(body)

    @app.post("/api/llm/default/set")
    def llm_default_set(body: dict):
        name = body.get("name")
        if not name:
            raise HTTPException(status_code=400, detail="缺少 name")
        try:
            return config.set_default(name)
        except ConfigError as e:
            raise HTTPException(status_code=400, detail=str(e))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_llm_config.py tests/test_agent_api.py -v`
Expected: all PASS (Task 1–4 tests + existing agent API tests stay green)

- [ ] **Step 5: Commit**

```bash
git add api/agent/api.py tests/test_llm_config.py
git commit -m "feat: LLM config API endpoints (list/add/update/delete/test/default) wired to store"
```

---

### Task 5: Web 设置面板

**Files:**
- Modify: `web/index.html`

**Interfaces:**
- Consumes: Task 4 endpoints.
- Produces: 面板 UI（列表 + 添加/编辑表单 + 测试 + 删除确认 + 默认 radio），并在配置变更后刷新聊天面板的 provider 下拉。

- [ ] **Step 1: 添加面板 HTML**

在 `web/index.html` 中，`</div>`（chatPanel 结束，约 line 140）之后、`<script>` 之前插入：

```html
<div class="panel" id="llmPanel">
  <h3>⚙️ LLM 设置</h3>
  <div id="llmProviderList" style="border:1px solid var(--border);border-radius:6px;padding:8px;font-size:12px;margin-bottom:8px;"></div>
  <label for="llmName">名称</label>
  <input id="llmName">
  <label for="llmType">类型</label>
  <select id="llmType">
    <option value="anthropic">anthropic</option>
    <option value="openai_compat">openai_compat</option>
  </select>
  <label for="llmBaseUrl">base_url（openai_compat 必填）</label>
  <input id="llmBaseUrl" placeholder="http://127.0.0.1:3456">
  <label for="llmModel">模型</label>
  <input id="llmModel" placeholder="opengo/deepseek-v4-flash">
  <label for="llmApiKey">API Key（env:VAR 或明文）</label>
  <input id="llmApiKey" placeholder="env:ANTHROPIC_API_KEY">
  <div style="margin-top:8px;">
    <button id="llmSave" class="btn">保存</button>
    <button id="llmTestForm" class="btn">测试此配置</button>
    <span id="llmTestResult" class="status"></span>
  </div>
  <div id="llmMsg" class="status"></div>
</div>
```

- [ ] **Step 2: 添加面板 JS**

在 `<script>` 内、`loadProviders()` 定义之后追加：

```js
let editingLlm = null;

async function loadLlmProviders() {
  const r = await fetch('/api/llm/providers/list');
  const data = await r.json();
  const list = $('llmProviderList');
  list.innerHTML = '';
  if (data.error) { list.textContent = '⚠ ' + data.error; return; }
  if (!data.providers.length) { list.textContent = '（无 provider，请在下表添加）'; return; }
  data.providers.forEach(p => {
    const row = document.createElement('div');
    row.style.cssText = 'display:flex;gap:8px;align-items:center;padding:6px 0;border-bottom:1px solid var(--border);';
    const def = document.createElement('input');
    def.type = 'radio'; def.name = 'llmDefault';
    def.checked = p.name === data.default;
    def.onclick = () => setLlmDefault(p.name);
    const label = document.createElement('span');
    label.style.flex = '1';
    label.textContent = `${p.name}（${p.type} · ${p.model}${p.base_url ? ' · ' + p.base_url : ''}）`;
    const testBtn = document.createElement('button');
    testBtn.className = 'btn'; testBtn.textContent = '测试';
    testBtn.onclick = async () => {
      const res = await fetch('/api/llm/providers/test', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(p)});
      const j = await res.json();
      label.textContent = `${p.name}（${p.type} · ${p.model}${p.base_url ? ' · ' + p.base_url : ''}） ${j.ok ? '✓ 连接成功' : '✗ ' + j.error}`;
    };
    const editBtn = document.createElement('button');
    editBtn.className = 'btn'; editBtn.textContent = '编辑';
    editBtn.onclick = () => fillLlmForm(p);
    const delBtn = document.createElement('button');
    delBtn.className = 'btn'; delBtn.textContent = '删除';
    delBtn.onclick = async () => {
      if (!confirm(`删除 provider ${p.name}?`)) return;
      const res = await fetch('/api/llm/providers/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name:p.name})});
      const j = await res.json();
      if (res.ok) { $('llmMsg').textContent = '已删除'; loadLlmProviders(); loadProviders(); }
      else { $('llmMsg').textContent = j.detail || '删除失败'; }
    };
    row.append(def, label, testBtn, editBtn, delBtn);
    list.appendChild(row);
  });
}

function fillLlmForm(p) {
  editingLlm = p.name;
  $('llmName').value = p.name;
  $('llmType').value = p.type;
  $('llmBaseUrl').value = p.base_url || '';
  $('llmModel').value = p.model || '';
  $('llmApiKey').value = p.api_key || '';
}

async function setLlmDefault(name) {
  const res = await fetch('/api/llm/default/set', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name})});
  if (!res.ok) { const j = await res.json(); $('llmMsg').textContent = j.detail || '设置失败'; }
  loadLlmProviders();
}

function llmFormData() {
  return {
    name: $('llmName').value.trim(),
    type: $('llmType').value,
    base_url: $('llmBaseUrl').value.trim(),
    model: $('llmModel').value.trim(),
    api_key: $('llmApiKey').value.trim(),
  };
}

async function saveLlmProvider() {
  const url = editingLlm ? '/api/llm/providers/update' : '/api/llm/providers/add';
  const body = llmFormData();
  if (editingLlm) body.name = editingLlm;
  const res = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  const j = await res.json();
  if (res.ok) {
    editingLlm = null;
    $('llmName').value = ''; $('llmBaseUrl').value = ''; $('llmModel').value = ''; $('llmApiKey').value = '';
    $('llmMsg').textContent = '已保存';
    loadLlmProviders();
    loadProviders();
  } else {
    $('llmMsg').textContent = j.detail || '保存失败';
  }
}

async function testLlmForm() {
  $('llmTestResult').textContent = '测试中…';
  const res = await fetch('/api/llm/providers/test', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(llmFormData())});
  const j = await res.json();
  $('llmTestResult').textContent = j.ok ? '✓ 连接成功' : '✗ ' + j.error;
}

$('llmSave').addEventListener('click', saveLlmProvider);
$('llmTestForm').addEventListener('click', testLlmForm);
loadLlmProviders();
```

- [ ] **Step 3: 让聊天下拉支持刷新**

在 `loadProviders()` 的 `data.providers.forEach` 之前加一行清空，避免重复加载：

```js
  chatProvider.innerHTML = '';
```

- [ ] **Step 4: 手动验证**

```bash
.venv/bin/uvicorn api.main:app --reload
```
浏览器打开 http://127.0.0.1:8000 ，逐项验证：
1. 「LLM 设置」面板显示现有 provider（`anthropic-local`）。
2. 添加一个 provider（name/type/model/api_key），保存后列表出现该行，且 `config/llm.json` 文件内容正确、格式合法。
3. 点「测试」——测试一个 `env:` 未设置的 key 应显示 `✗ 环境变量 XXX 未设置`；测试真实可达的 provider 应显示 `✓ 连接成功`。
4. 编辑某行，修改 model，保存后列表与 JSON 同步更新。
5. 点行内 radio 设默认，刷新页面后默认保持。
6. 删除有确认弹窗，删除后默认项自动清空。
7. 聊天面板 provider 下拉包含新增的 provider。
8. 服务不重启，新配置即时生效（无需 `--reload` 触发的重启）。

- [ ] **Step 5: Commit**

```bash
git add web/index.html
git commit -m "feat: LLM 设置 web 面板 (list/add/edit/delete/test/default)"
```

---

## Self-Review

**1. Spec coverage:**
- ✅ 架构/数据模型/校验规则 → Task 1+2
- ✅ 6 个语义化端点 → Task 4
- ✅ 写后即时生效（免重启）→ `_reload` 在每个 mutation 后调用，`test_reload` 覆盖（Task 2 `test_add_valid_writes_file` 断言 `providers()` 立即反映）
- ✅ 测试连接 + 可定位错误原因（env 未设置 / base_url + 底层异常 / HTTP 状态码）→ Task 3
- ✅ 默认 provider 与聊天解析顺序（显式 → default → 第一个）→ Task 4 `test_api_chat_uses_default_provider`
- ✅ Web 面板 + 删除确认 + 默认 radio → Task 5
- ✅ 错误处理（校验失败不写盘、读损坏不崩溃）→ Task 1 `test_corrupt_file_raises_config_error`、Task 2 各 rejected 测试

**2. Placeholder scan:** 无 TBD/TODO/「适当处理」类占位；每个代码步骤含完整代码与预期输出。

**3. Type consistency:** `_read`/`_write`/`_reload`/`list`/`providers`/`get_default`/`_validate`/`add`/`update`/`delete`/`set_default`/`test`/`_build`/`_ping` 在各任务间签名一致；端点路径与 JS fetch 完全对应（`/api/llm/providers/list|add|update|delete|test`、`/api/llm/default/set`）。
