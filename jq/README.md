# quant

基于[掘金量化](https://www.myquant.cn)平台的量化交易策略集合。

## 📁 项目结构

```
quant
├── docs/
│   ├── sdk/                    # 掘金 SDK 文档
│   │   ├── API介绍/            #   API 函数详细说明
│   │   ├── 快速开始.md
│   │   ├── 策略程序架构.md
│   │   └── ...
│   └── strategies/             # 策略设计文档
│       ├── multiFactorStockTradingStrategy.md
│       ├── etfMomentumGridStrategy.md
│       └── etfFilterPipeline.md
├── multi_factor_stock_strategy.py   # 多因子选股 + 再平衡 + 止损
├── etf_momentum_grid_strategy.py    # ETF 双动量 + 网格交易
├── etf_filter_pipeline.py           # ETF 筛选管道（全市场扫描）
├── download_sdk_docs.py             # SDK 文档下载脚本
└── requirements.txt
```

## 🚀 快速开始

### 安装

```bash
# 安装依赖
pip install -r requirements.txt
```

### 运行策略

在[掘金量化终端](https://www.myquant.cn)中加载策略文件，或通过外部 IDE 运行：

1. 在终端「系统设置 → 密钥管理」获取 token
2. 创建策略后获取 strategy_id
3. 修改策略文件 `if __name__ == '__main__'` 中的 `strategy_id` 和 `token`
4. 运行策略文件

```bash
python multi_factor_stock_strategy.py    # 回测模式
python etf_momentum_grid_strategy.py     # 回测模式
```

> 实时模式需将 `mode=MODE_LIVE`，并在交易时段运行。

## 📖 策略

### 多因子选股 + 定期再平衡 + 动态止损

`multi_factor_stock_strategy.py`

- 估值、动量、质量、波动率、规模五因子打分选股
- 月度等权再平衡（持仓 30 只）
- 个股盘中触及 10% 止损线时立即清仓
- 详情：[策略文档](docs/strategies/multiFactorStockTradingStrategy.md)

### ETF 双动量 + 动态网格交易

`etf_momentum_grid_strategy.py`

- 绝对动量过滤 + 相对动量排名选 ETF
- 状态机管理：空仓（货基）↔ 趋势持股
- 网格交易高抛低吸（±2% 触发，预留 40% 资金给低吸）
- 闲置资金自动配置货币基金
- ★ 集成 ETF 筛选管道，全市场动态筛选健康标的
- 详情：[策略文档](docs/strategies/etfMomentumGridStrategy.md) | [筛选管道](docs/strategies/etfFilterPipeline.md)

### ETF 筛选与数据清洗管道

`etf_filter_pipeline.py`

- 三层漏斗筛选：规模（AUM > 5亿）→ 流动性（20日均成交额 > 5000万）→ 折溢价（|rate| ≤ 1%）
- 大类资产去重：每类只保留流动性最强的一只，防止影子共振
- 可独立运行扫描全市场 ETF 健康度
- 详情：[筛选管道文档](docs/strategies/etfFilterPipeline.md)

## 📚 SDK 文档

[→ 掘金 Python SDK 文档](docs/README.md)

## 📄 许可

MIT
