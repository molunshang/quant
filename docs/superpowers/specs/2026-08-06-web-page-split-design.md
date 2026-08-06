# 前端页面按功能拆分 — 设计

日期：2026-08-06
状态：已批准（多页面 HTML + 四页划分）

## 背景与目标

当前 `web/index.html`（595 行）把四大功能全部塞进一个页面：回测控制台、AI 目标优化聊天、数据预缓存、LLM 设置。后端 API 已按功能分好路由，前端却无对应结构。

目标：按功能拆成独立页面，用顶部导航切换。纯前端拆分，后端 API 零改动（仅新增 3 个页面路由）。不引入构建工具、不引入框架。

## 1. 页面结构与路由

```
web/
├── common.css          # 主题变量、顶部导航、panel/btn/table/status 等公共样式
├── common.js           # 导航高亮、$ 工具、loadSymbols/loadStrategies（跨页共用）
├── index.html          # 回测控制台
├── chat.html           # AI 目标优化
├── data.html           # 数据预缓存
└── settings.html       # LLM 设置
```

服务端路由（`api/main.py`）：

| URL | 返回 |
|-----|------|
| `/` | `index.html`（回测，保持现状） |
| `/chat` | `chat.html` |
| `/data` | `data.html` |
| `/settings` | `settings.html` |

- `/web` 静态挂载保留，用于加载 `common.css` / `common.js`。
- 新增三个 GET 路由，各自 `FileResponse` 返回对应页面。`/` 路由保持不变。

**验证**：`GET /`、`/chat`、`/data`、`/settings` 均返回 200 且 `Content-Type` 含 `text/html`。

## 2. 各页面承载内容（功能完全平移，逻辑不变）

| 页面 | 内容 |
|------|------|
| **回测**（index.html） | 左侧设置：标的/类型/频率/日期/复权/初始资金/策略（内置/自定义源码切换）/参数 JSON；运行按钮；右侧结果区：指标卡片、权益/K线/回撤三图 Tab、交易记录表。含原有 `runBacktest`、`renderResults`、三个 `build*Option` 与 `chart` 函数。 |
| **聊天**（chat.html） | 聊天日志、provider 下拉（`/api/providers`）、输入框、发送按钮、SSE 事件处理（turn/tool/tool_error/backtest_results/clarify/confirm/running/error/done）、断线恢复输入框。 |
| **数据**（data.html） | 预缓存表单（标的/频率/日期/复权）、提交、刷新已缓存按钮、任务列表（3 秒轮询 `/api/data/precache/jobs`）。 |
| **设置**（settings.html） | Provider 列表（设默认/测试/编辑/删除）、增改表单、设默认；`loadLlmProviders`、`fillLlmForm`、`saveLlmProvider`、`testLlmForm` 等逻辑全部平移。 |

## 3. 共享与边界

- **common.css**：从现有 `<style>` 抽取主题变量（`:root`）、`body`、header、panel、label/input/select/textarea、btn、status、hidden、tab、table、metric 样式。各页保留自身布局所需样式。
- **common.js**：只放真正跨页共享的：导航高亮、`$` 工具、`loadSymbols`、`loadStrategies`。各页业务逻辑留在各页 `<script>`，不集中到 common.js。
- **页面间零耦合**：聊天页自带 provider 加载，设置页自带 LLM CRUD，两者不互相依赖；回测图表函数留在回测页。
- 后端 API 不新增、不修改既有接口，只加 3 个 GET 页面路由。

## 4. 导航

每个页面顶部同一 header，含站点标题 + 4 个导航链接：回测、AI 优化、数据预缓存、设置。当前页高亮（`common.js` 根据当前路径设置 active 类）。

## 5. 测试

- 现有 `tests/test_api.py` 等不受影响（无前端页面引用）。
- 新增测试：4 个页面路由 `GET /`、`/chat`、`/data`、`/settings` 返回 200 且 `Content-Type` 含 `text/html`（挂载在 `WEB_DIR` 存在的前提下）。

## 非目标

- 不引入构建工具 / 前端框架 / SPA 路由。
- 不改任何业务功能与后端 API 行为。
- 不回测页与结果页二次拆分（表单与结果保持同页，因结果依赖表单状态）。
