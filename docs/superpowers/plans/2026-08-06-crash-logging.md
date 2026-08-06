# Crash 日志记录 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 任何原因导致的进程崩溃（原生信号级崩溃 + Python 未捕获异常）都自动写入 `data/logs/crash.log`，方便事后定位修复。

**Architecture:** 新增 `data/crash_log.py` 模块，`init_crash_logging()` 一次性配置三件事：① `RotatingFileHandler` 写 `data/logs/crash.log`；② `faulthandler.enable()` + `register(SIGTRAP)` 捕获原生崩溃时 dump 所有线程栈；③ `sys.excepthook` 兜底 Python 未捕获异常。`api/main.py` 模块顶部调用，早于一切业务代码。另给两处线程内 try/except 补 `logging.exception()`，让后台线程异常也进日志。

**Tech Stack:** 全标准库（`logging` + `faulthandler` + `sys` + `signal` + `pathlib`）。无第三方依赖。

## Global Constraints

- 日志文件：`data/logs/crash.log`（项目内，重启不丢）。
- 轮转：`RotatingFileHandler`，`maxBytes = 10 * 1024 * 1024`，`backupCount = 5`，`encoding = "utf-8"`。
- 原生崩溃信号：`faulthandler.enable(file=..., all_threads=True)` 覆盖 `SIGSEGV/SIGABRT/SIGBUS/SIGILL/SIGFPE`；`faulthandler.register(SIGTRAP, file=..., all_threads=True)` 补 SIGTRAP。
- 初始化幂等：`_initialized` 标志防止 `--reload` 重复 import 时重复配置。
- 不引入第三方依赖；不改 `_V8_LOCK` 修复；不替换 uvicorn 访问日志。
- 日志格式：`"%(asctime)s %(levelname)s %(message)s"`，root logger，`level=logging.WARNING`，`basicConfig(force=True)`。

---

### Task 1: 创建 `data/crash_log.py` 模块

**Files:**
- Create: `data/crash_log.py`
- Test: `tests/test_crash_log.py`

**Interfaces:**
- Produces: `init_crash_logging() -> str`（返回日志文件绝对路径；幂等，可重复调用）。

- [ ] **Step 1: Write the failing tests**

`tests/test_crash_log.py`:

```python
"""Tests for crash logging — init, excepthook, faulthandler wiring."""
from __future__ import annotations

import sys
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import faulthandler

from data.crash_log import init_crash_logging


def test_init_creates_log_dir_and_file():
    path = init_crash_logging()
    p = Path(path)
    assert p.name == "crash.log"
    assert p.parent.name == "logs"
    assert p.exists()


def test_rotating_handler_configured():
    init_crash_logging()
    root = logging.getLogger()
    handlers = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
    assert handlers, "root logger should have a RotatingFileHandler"
    h = handlers[0]
    assert h.maxBytes == 10 * 1024 * 1024
    assert h.backupCount == 5
    assert h.encoding == "utf-8"


def test_excepthook_writes_traceback(tmp_path, monkeypatch):
    init_crash_logging()
    log_path = Path(init_crash_logging())
    # avoid writing to the shared crash.log during tests; route to tmp
    import data.crash_log as mod
    orig_handler = logging.getLogger().handlers[0]
    monkeypatch.setattr(mod, "_initialized", False)

    def boom():
        raise ValueError("boom test")

    try:
        boom()
    except ValueError:
        sys.excepthook(*sys.exc_info())

    content = log_path.read_text(encoding="utf-8")
    assert "ValueError: boom test" in content
    assert "boom test" in content


def test_excepthook_preserves_default(capsys):
    """The custom hook must still run the builtin default (not swallow stderr)."""
    init_crash_logging()
    try:
        raise RuntimeError("preserve-default")
    except RuntimeError:
        sys.excepthook(*sys.exc_info())
    err = capsys.readouterr().err
    assert "RuntimeError: preserve-default" in err


def test_faulthandler_enabled():
    init_crash_logging()
    assert faulthandler.is_enabled()


def test_dump_traceback_writes_to_file():
    init_crash_logging()
    path = Path(init_crash_logging())
    handler = logging.getLogger().handlers[0]
    faulthandler.dump_traceback(file=handler.stream, all_threads=True)
    handler.flush()
    assert "Current thread" in path.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_crash_log.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'data.crash_log'`

- [ ] **Step 3: Implement `data/crash_log.py`**

```python
"""Crash logging — native crashes (faulthandler) + uncaught Python exceptions.

Everything lands in data/logs/crash.log (rotating, utf-8) so any process
crash can be diagnosed afterwards from the log alone.
"""
from __future__ import annotations

import faulthandler
import logging
import os
import signal
import sys
import traceback
from logging.handlers import RotatingFileHandler

_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "logs")
_LOG_FILE = os.path.join(_LOG_DIR, "crash.log")
_MAX_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 5
_FORMAT = "%(asctime)s %(levelname)s %(message)s"

_initialized = False


def _excepthook(exc_type, exc_value, exc_tb):
    """Log uncaught exceptions, then pass through to the default hook."""
    logging.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def init_crash_logging() -> str:
    """Install crash logging. Idempotent; returns the log file path."""
    global _initialized
    if _initialized:
        return _LOG_FILE

    os.makedirs(_LOG_DIR, exist_ok=True)
    handler = RotatingFileHandler(
        _LOG_FILE, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
    )
    logging.basicConfig(
        handlers=[handler], level=logging.WARNING,
        format=_FORMAT, force=True,
    )

    # Native crashes: SIGSEGV/ABRT/BUS/ILL/FPE dump all-threads stacks via enable().
    faulthandler.enable(file=handler.stream, all_threads=True)
    # SIGTRAP is the V8/py_mini_racer crash signal (exit 133); enable() does not
    # cover it, so register it explicitly to the same log file.
    faulthandler.register(signal.SIGTRAP, file=handler.stream, all_threads=True)

    sys.excepthook = _excepthook

    _initialized = True
    return _LOG_FILE
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_crash_log.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Run full suite to confirm no regression**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 143 + 6 = **149 passed**

- [ ] **Step 6: Commit**

```bash
git add data/crash_log.py tests/test_crash_log.py
git commit -m "feat: crash 日志模块 — faulthandler + excepthook + RotatingFileHandler → data/logs/crash.log"
```

---

### Task 2: `api/main.py` 启动时挂载 crash 日志

**Files:**
- Modify: `api/main.py:1-35`（import 区 + `app = FastAPI(...)` 之前）
- Test: `tests/test_crash_log.py`（新增一个集成测试）

**Interfaces:**
- Consumes: `init_crash_logging() -> str` from Task 1.
- Produces: server 启动时 crash 日志已初始化；任何后续崩溃都会记录。

- [ ] **Step 1: Add the integration test**

在 `tests/test_crash_log.py` 末尾追加：

```python
def test_api_main_imports_init_crash_logging():
    """Importing api.main must call init_crash_logging (early wiring)."""
    import api.main  # noqa: F401
    import data.crash_log as mod
    assert mod._initialized is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_crash_log.py::test_api_main_imports_init_crash_logging -v`
Expected: FAIL — `AssertionError: assert False is True`（因为 `api.main` 还没调用 init）

- [ ] **Step 3: Modify `api/main.py`**

在 `api/main.py` 顶部 import 区（`from __future__ import annotations` 之后、其他 import 之前）加：

```python
from __future__ import annotations

import json
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from data.crash_log import init_crash_logging

# Crash 日志必须在任何业务代码之前初始化：原生崩溃(faulthandler)与
# Python 未捕获异常都会写进 data/logs/crash.log，便于事后定位。
init_crash_logging()

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_crash_log.py::test_api_main_imports_init_crash_logging -v`
Expected: PASS

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: **150 passed**（149 + 1 新集成测试）

- [ ] **Step 6: Commit**

```bash
git add api/main.py tests/test_crash_log.py
git commit -m "feat: api.main 启动时挂载 crash 日志初始化"
```

---

### Task 3: 两个后台线程异常点补 `logging.exception()`

**Files:**
- Modify: `api/agent/api.py:110-117`（`_run()` 的 except）
- Modify: `data/precache.py:60-63`（`_work()` 的 except）
- Test: `tests/test_crash_log.py`（新增）或 `tests/test_precache.py`

**Interfaces:**
- Consumes: Task 1 的 logging 配置（root logger 已挂 RotatingFileHandler，`logging.exception()` 自动进 crash.log）。
- Produces: 后台线程内的异常（chat run、precache job）也写入 crash.log，不再静默。

- [ ] **Step 1: Write failing tests**

在 `tests/test_crash_log.py` 末尾追加（验证 precache `_work` 异常会记录；chat `_run` 较难注入，用 precache 覆盖 logging 调用）：

```python
def test_precache_work_logs_exception(monkeypatch, tmp_path):
    import logging
    import data.crash_log as mod
    mod.init_crash_logging()
    records = []
    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)
    h = Capture()
    logging.getLogger().addHandler(h)

    from data.precache import PrecacheManager, PrecacheJob
    from data.sources import DataLayer

    def boom(self, info, **kw):
        raise RuntimeError("boom precache")

    monkeypatch.setattr(DataLayer, "get_bars", boom)
    mgr = PrecacheManager()
    mgr._dl = DataLayer(cache=False)
    job = PrecacheJob(id=1, symbol="600519", freq="daily", adjust="qfq",
                      start="2024-01-01", end="2024-01-31")
    mgr._work(job)
    assert job.status == "error"
    assert any(r.levelno == logging.ERROR and "boom precache" in str(r.exc_info)
               for r in records), "precache _work failure must be logged"
    logging.getLogger().removeHandler(h)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_crash_log.py::test_precache_work_logs_exception -v`
Expected: FAIL — 没有任何 ERROR record 含 "boom precache"（`_work` 还没 logging）

- [ ] **Step 3: Add `logging.exception()` to `data/precache.py` `_work`**

在 `data/precache.py` 顶部加 `import logging`（若已有则跳过），并修改 `_work` 的 except：

```python
        except Exception as e:  # noqa: BLE001 - per-job error
            logging.exception("precache job %s (%s) failed", job.id, job.symbol)
            with self._lock:
                job.status = "error"
                job.error = str(e)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_crash_log.py::test_precache_work_logs_exception -v`
Expected: PASS

- [ ] **Step 5: Add `logging.exception()` to `api/agent/api.py` `_run`**

在 `api/agent/api.py` 顶部加 `import logging`（若已有则跳过），并修改 `_run` 的 except：

```python
        def _run():
            try:
                handle_chat(session_id, message, goal, provider, bus,
                            session_store, chat_store, store, executor)
            except Exception as e:  # noqa: BLE001 - surface errors, no eternal spinner
                logging.exception("chat run failed for session %s", session_id)
                bus.publish(session_id, {"type": "error", "error": str(e)})
                chat_store.add_message(session_id, "assistant", f"出错了: {e}")
```

- [ ] **Step 6: Run full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: **151 passed**

- [ ] **Step 7: Commit**

```bash
git add data/precache.py api/agent/api.py tests/test_crash_log.py
git commit -m "feat: 后台线程异常点补 logging.exception → crash.log"
```

---

### Task 4: 真实崩溃端到端验证

**Files:** 无（验证用临时脚本）

**Interfaces:** 无。

- [ ] **Step 1: 用真实 MiniRacer 并发崩溃验证 crash.log 捕获**

写临时脚本 `/tmp/crash_e2e.py`：

```python
import sys, threading, signal, faulthandler
sys.path.insert(0, "/Users/zk/code/agent/quant-agent")
from data.crash_log import init_crash_logging
init_crash_logging()  # installs faulthandler + excepthook
import py_mini_racer

def worker(n):
    for i in range(60):
        ctx = py_mini_racer.MiniRacer()
        ctx.eval("function f(){return 1+1;}")
        ctx.call("f")
        del ctx

threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
for t in threads: t.start()
for t in threads: t.join()
```

Run: `.venv/bin/python /tmp/crash_e2e.py`
Expected: 进程因 V8 并发崩溃（exit 133），随后检查 `data/logs/crash.log` 尾部含**所有线程栈**，且每个 worker 线程都停在 `py_mini_racer._make_context`：

```bash
tail -30 data/logs/crash.log
```

Expected: 出现多段 `Thread 0x... [Thread-N (worker)]` 块，含 `py_mini_racer/_mini_racer.py` 帧。

- [ ] **Step 2: 清理临时脚本**

```bash
rm -f /tmp/crash_e2e.py
```

- [ ] **Step 3: 确认最终测试状态**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: **151 passed**

- [ ] **Step 4: 提交（若有 spec/plan 变更）**

```bash
git add docs/superpowers/specs/2026-08-06-crash-logging-design.md
git commit -m "docs: 补充 faulthandler enable/register(SIGTRAP) 机制细节"
```

---

## Self-Review 检查清单

- **Spec 覆盖**：✅ 覆盖所有原生崩溃（enable + register SIGTRAP）→ Task 1；普通异常 → Task 1/3；`data/logs/crash.log` 轮转 → Task 1；`api/main.py` 顶部初始化 → Task 2；两个业务异常点补 logging → Task 3；测试 → Task 1/3；真实崩溃验证 → Task 4。
- **占位符扫描**：✅ 无 TBD/TODO；每步都有确切代码 + 命令 + 预期输出。
- **类型一致性**：✅ `init_crash_logging()` 签名在所有 task 中一致（Task 1 定义，Task 2 消费，Task 4 消费）。`_initialized` 标志在 Task 1 定义、Task 2 测试引用。
