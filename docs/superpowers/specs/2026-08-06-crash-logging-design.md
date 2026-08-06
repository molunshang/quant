# Crash 日志记录 设计

日期：2026-08-06
状态：已确认

## 背景与动机

V8 崩溃（`py_mini_racer`）以**原生信号级崩溃**形式杀进程（`exit 133` / SIGTRAP），Python 代码无法事后运行，只能靠日志事后定位。但当前项目**完全没有日志设施**——只有一处 `print`，uvicorn 输出靠手动重定向到 `/tmp/quant-agent-uvicorn.log`，且该日志**不会保存崩溃时的线程栈**（进程直接死，Python traceback 无从谈起）。

用户要求：**添加 crash 日志记录**，遇到任何原因导致的 crash，都能根据日志定位修复。

## 决策汇总

| # | 决策 | 内容 |
|---|------|------|
| 1 | 覆盖范围 | **所有原生崩溃** + **普通 Python 异常**（非仅 V8） |
| 2 | 日志位置 | 项目内 `data/logs/crash.log` |
| 3 | 文件组织 | 单文件 + 轮转（`RotatingFileHandler`，max 10MB × 5） |
| 4 | 原生崩溃 | `faulthandler.register()` 注册 `SIGSEGV/SIGABRT/SIGBUS/SIGILL/SIGTRAP`，`all_threads=True`，崩溃时 dump 所有线程栈 |
| 5 | Python 异常 | `sys.excepthook` 兜底未捕获异常（含线程内异常、precache job error），带时间戳 + traceback |
| 6 | 初始化时机 | `api/main.py` 模块顶部调用 `init_crash_logging()`，早于任何业务代码 |
| 7 | 依赖 | 全标准库（`logging` + `faulthandler`），不引入额外依赖 |
| 8 | 业务异常点 | `api/agent/api.py` 的 `_run`、`data/precache.py` 的 `_work` 两个 try/except 补 `logging.exception()`，线程内异常也进 crash.log |

## 语义确认（已与用户确认）

1. **覆盖所有原生崩溃**：不只针对 V8。`faulthandler` 注册常见崩溃信号，任意 native crash（segfault、abort、bus error、illegal instruction、trap）都能 dump 线程栈。
2. **普通异常也记录**：Python 层未捕获异常（HTTP 500、precache job error、后台线程异常）写入同一 crash 日志，便于排查非崩溃问题。
3. **日志放项目内 `data/logs/`**：重启不丢，跟代码一起管理。
4. **单文件 + 轮转**：`crash.log` 单个文件，超过 10MB 自动轮转成 `crash.log.1`...`crash.log.5`。

## 架构

```
data/crash_log.py        (新增) crash 日志模块：init_crash_logging()
  ├── RotatingFileHandler → data/logs/crash.log（单文件，10MB × 5）
  ├── faulthandler.register()  原生崩溃 → 所有线程栈 dump
  └── sys.excepthook            Python 未捕获异常 → traceback
api/main.py             模块顶部调用 init_crash_logging()，早于业务代码
tests/test_crash_log.py  (新增) 验证初始化、excepthook 写入、轮转配置
```

## 组件设计

### 1. `data/crash_log.py`（新增）

**入口**：`init_crash_logging() -> str`，返回日志文件路径。

**职责**：
1. 确保 `data/logs/` 目录存在（`os.makedirs(..., exist_ok=True)`）。
2. 创建 `logging.handlers.RotatingFileHandler`：
   - `filename = data/logs/crash.log`
   - `maxBytes = 10 * 1024 * 1024`（10MB）
   - `backupCount = 5`
   - `encoding = "utf-8"`（日志含中文）
3. 设置 `logging.basicConfig(handlers=[handler], level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")`，挂到 **root logger**（`force=True`，避免 uvicorn 重复初始化）。
4. **`faulthandler.register(signal, file=handler.stream, all_threads=True)`**：
   - 注册 `SIGSEGV`、`SIGABRT`、`SIGBUS`、`SIGILL`、`SIGTRAP`；
   - 崩溃时 dump **所有线程**的 Python 栈（含 C 扩展的调用点，如 `py_mini_racer._make_context`）；
   - `handler.stream` 指向 RotatingFileHandler 已打开的日志文件，崩溃 dump 直接追加写入。
5. **`sys.excepthook = _excepthook`**：
   - 拦截未捕获异常，`logging.critical(...)` 写时间戳 + `traceback.format_exc()`；
   - 同时**调用默认 excepthook**（保留 stderr 输出，不吞异常）。

**幂等**：`_initialized` 标志防止重复初始化（`--reload` 时模块可能重复 import）。

**不做什么**：
- 不替换 uvicorn 的访问日志（HTTP 访问日志是另一回事，本次范围不含）。
- 不改 `_V8_LOCK` 修复（已单独完成）。
- 不引入第三方依赖。
- **不保留 uvicorn 默认格式**：`basicConfig(force=True)` 会把 root logger 统一成我们的 handler。uvicorn 访问日志（`uvicorn.access`）是独立 logger 不受影响；`uvicorn.error` 会进 crash.log。保持简单，不做特殊保留。

### 2. 业务异常点补 `logging`（`api/agent/api.py` + `data/precache.py`）

| 文件 | 位置 | 现状 | 改动 |
|------|------|------|------|
| `api/agent/api.py` | `_run()` 的 `except Exception` | `bus.publish` 错误 + 存 chat 记录 | 补 `logging.exception("chat run failed")` |
| `data/precache.py` | `_work()` 的 `except Exception` | 只写 job 状态 + error 字符串 | 补 `logging.exception("precache job {job.id} failed")` |

### 2. `api/main.py` 集成

模块顶部（`import` 后、定义 `app` 前）调用：

```python
from data.crash_log import init_crash_logging
init_crash_logging()
```

**时机**：模块 import 时执行，早于 FastAPI app 创建、早于 lifespan 启动后台线程、早于任何业务代码。这样如果崩溃发生在启动早期（如 scheduler 线程、precache 线程），日志已在。

### 3. 测试（`tests/test_crash_log.py`）

| 测试 | 验证 |
|------|------|
| `test_init_creates_log_dir_and_file` | `init_crash_logging()` 后 `data/logs/crash.log` 存在且可写 |
| `test_excepthook_writes_traceback` | 手动触发 `sys.excepthook`，验证 crash.log 含 traceback |
| `test_excepthook_preserves_default` | 自定义 excepthook 调用后，默认 excepthook 仍被调用（不吞异常） |
| `test_rotating_handler_configured` | root logger 有 `RotatingFileHandler`，`maxBytes`/`backupCount` 正确 |
| `test_faulthandler_registered` | `faulthandler` 的信号处理函数已注册（`faulthandler.is_enabled()` 或检查注册表） |

**原生崩溃捕获无法单测**（会真的杀掉 pytest 进程）。用 `faulthandler.dump_traceback()`（软 dump，不杀进程）验证 dump 机制写入日志文件。

## 约束与风险

1. **原生崩溃 dump 时机**：`faulthandler.register` 在信号到达时由 C 层同步执行，dump 到 `RotatingFileHandler.stream`（已打开的文件句柄）。进程随后立即死，但 dump 已落盘。已验证 V8 崩溃场景下能 dump 5757 行全线程栈（含 `py_mini_racer._make_context`）。
2. **`force=True` 的 `basicConfig`**：统一 root logger 到 crash.log。uvicorn 访问日志（`uvicorn.access`）是独立 logger，不受影响；`uvicorn.error` 会进 crash.log。**决定：保持简单，不做特殊保留。**
3. **线程内异常**：Python 线程内的未捕获异常**不会**走 `sys.excepthook`（它只处理主线程）。当前代码里线程内异常已被 try/except 包裹（`api/agent/api.py` 的 `_run`、`data/precache.py` 的 `_work`），**本次给这两处补 `logging.exception()`**，线程内异常也进 crash.log。
4. **轮转与并发**：`RotatingFileHandler` 非线程安全，多线程并发写可能丢失尾部。对 crash 场景（低频、致命）可接受；如需严格可加 `threading.Lock`，本次保持简单。
