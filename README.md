# quant-agent

中国A股回测系统 — 支持自定义策略、Web 可视化、Agent API 自动迭代。

## 特性

- **自定义策略**：策略即 Python 函数，通过 API 提交源码或引用内置策略
- **Web 可视化**：权益曲线、回撤曲线、K线买卖点标记（ECharts）
- **Agent API**：`/api/backtest`、`/api/optimize`、`/api/strategies` — agent 可编程调用、参数扫描自动迭代最优策略
- **多标的**：股票、基金、ETF
- **多数据源容错**：东方财富（首选）→ 新浪（降级）
- **A股交易规则**：T+1、涨跌停、印花税（股票卖出 0.05%，ETF/基金免征）、佣金、一手 100 股
- **日线 + 分钟线**：bar 频率可配置

## 快速开始

```bash
# 1. 创建虚拟环境并安装依赖
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. 启动 Web 服务
.venv/bin/uvicorn api.main:app --reload

# 3. 打开 http://127.0.0.1:8000
```

## API 概览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/backtest` | 运行回测，返回指标+权益曲线+交易记录 |
| POST | `/api/strategies` | 注册/更新策略 |
| GET  | `/api/strategies` | 列出策略 |
| POST | `/api/optimize` | 参数网格扫描 |
| GET  | `/api/symbols` | 可交易标的名录 |
| GET  | `/api/meta/bars` | bar 频率列表 |

## 目录结构

```
quant-agent/
├── data/           # 数据层：DataSource 抽象 + 多源实现 + CSV 缓存
├── engine/         # 回测引擎：事件驱动 + A股规则
├── strategies/     # 策略层：内置策略
├── api/            # FastAPI 接口
├── web/            # Web 前端
├── viz/            # 图表数据生成
└── tests/          # 单元测试
```
