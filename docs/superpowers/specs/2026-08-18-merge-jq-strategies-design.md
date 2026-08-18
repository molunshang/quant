# 合并掘金策略仓库 设计

日期：2026-08-18

## 背景与问题

当前项目（quant-agent）是**自研 A 股回测引擎 + LLM Agent 优化**系统。远端仓库 `git@github.com:molunshang/quant.git` 是**基于掘金量化平台的策略集合**（依赖 `gm.api` SDK），包含 3 个策略 + 掘金 SDK 文档。

两者架构完全不同：
- **quant-agent**：自研 `BacktestEngine`（`initialize(ctx)` + `handle_data(ctx)`），数据来自 akshare + CSV 缓存，有 Web + LLM Agent。
- **quant（掘金）**：掘金 `gm.api`（`init(context)` + `on_bar` 回调 + `schedule` 定时任务），数据来自掘金 SDK，跑掘金终端回测/实盘。

需求：合并两个仓库到同一 Git 仓库并推送远端，**掘金策略保留能跑（掘金平台），自研引擎保留能跑（回测+Agent）**，合并后结构需调整适配。

## 设计决策（已与用户确认）

1. **双轨共存**：两个项目互不调用。掘金策略原样保留（跑掘金终端），自研引擎原样保留（回测 + LLM Agent）。不做移植、不做兼容层。
2. **掘金内容放 `jq/` 子目录**：远端全部内容（策略脚本、筛选管道、下载脚本、README、docs/）放入 `jq/`。自研内容保持在顶层不动。
3. **`git subtree add` 引入历史**：完整保留掘金 12 个提交历史，作为 `jq/` 前缀的子目录引入，当前 129 个提交不变。将来可单独 `git subtree pull` 同步远端更新。
4. **docs 冲突天然解决**：subtree 把远端 `docs/` 放进 `jq/docs/`，与自研 `docs/superpowers/` 完全隔离，无冲突。
5. **依赖独立**：掘金策略需要 `gm` 包（掘金 SDK），自研引擎不需要。`requirements.txt` 合并时需协调——掘金的 `gm` 不应进入自研核心依赖。

## 目录布局（合并后）

```
quant-agent/
├── api/                  # 自研 FastAPI（不动）
├── engine/               # 自研回测引擎（不动）
├── strategies/           # 自研策略（不动）
├── data/                 # 自研数据层（不动）
├── web/                  # 自研 Web（不动）
├── tests/                # 自研测试（不动）
├── docs/superpowers/     # 自研 spec/plan（不动）
├── jq/                   # ★ 掘金仓库（subtree 引入）
│   ├── docs/
│   │   ├── sdk/          # 掘金 SDK 文档
│   │   └── strategies/   # 策略设计文档
│   ├── etf_filter_pipeline.py
│   ├── etf_momentum_grid_strategy.py
│   ├── multi_factor_stock_strategy.py
│   ├── download_sdk_docs.py
│   ├── README.md
│   └── requirements.txt  # 掘金依赖（gm 等）
├── README.md             # 更新：说明双项目结构
├── requirements.txt      # 自研依赖（不含 gm）
└── .gitignore            # 合并两边规则
```

## 依赖隔离

掘金策略依赖 `gm>=3.0.0`（掘金 SDK），自研引擎不需要。合并后：
- 自研 `requirements.txt`：保持现状（fastapi/uvicorn/pandas/akshare/anthropic/openai），**不加入 gm**。
- 掘金 `jq/requirements.txt`：保留原样（gm/numpy/pandas/html2text）。
- 若需要单命令装全，可在根 README 说明 `pip install -r jq/requirements.txt` 可选装掘金依赖。

## 涉及操作

### 1. `git subtree add`

```bash
git subtree add --prefix=jq origin main
```

> **不带 `--squash`**：完整保留掘金 12 个独立提交历史（用户选的"保留历史"）。`--squash` 会把历史压缩成单提交，不采用。`origin` 是 remote 名（已配置），`main` 是远端分支。

### 2. `.gitignore` 合并

合并两边规则，补掘金忽略项：
- 自研已有：`.venv/` `__pycache__/` `*.pyc` `.pytest_cache/` `data/cache/` `data/agent.db` `*.log` `.DS_Store`
- 掘金新增：`*.pyo` `venv/` `.env`

### 3. `README.md` 更新

顶部说明仓库现在是**双项目结构**：
- **quant-agent**（自研回测 + LLM Agent）—— 现有 README 内容
- **jq/**（掘金策略）—— 指向 `jq/README.md`，说明跑掘金终端使用

### 4. `requirements.txt` 保持自研

不加入 `gm`。掘金依赖在 `jq/requirements.txt`。

### 5. 验证

- 自研测试全绿：`pytest -q`（当前 211 passed）
- 掘金策略文件在 `jq/` 下存在、可读
- `git log` 显示掘金历史已并入

## 不做的事（YAGNI）

- **不移植**掘金策略到自研引擎（双轨共存，不做兼容层/翻译）
- **不统一**两套策略接口（`init(context)` vs `initialize(ctx)` 保持各自）
- **不删除**任何一边的现有内容
- **不修改**掘金策略代码本身（保持能跑掘金）
- **不做** LLM Agent 调用掘金策略（未来扩展，本次不做）

## 风险与备注

- **掘金 `gm` 依赖**：只有跑掘金策略才需要。不加入自研 requirements，避免污染自研环境。
- **subtree 历史体积**：掘金 12 个提交并入，仓库历史变大但可接受。
- **将来同步**：远端掘金仓库更新时，可用 `git subtree pull --prefix=jq origin main` 单独同步，不影响自研。
