# Quant - 量化交易系统

## 项目简介
Quant是一个基于.NET Core的量化交易系统，提供完整的量化交易解决方案。系统集成了掘金量化数据接口，支持实时行情监控、策略回测、风险控制等功能。

## 功能特性

### 1. 交易服务
- 交易记录服务：记录和查询历史交易
- 国债逆回购服务：支持不同期限的国债逆回购交易
- 交易分析服务：提供股票、账户和行业交易统计

### 2. 风险控制
- 止损止盈管理
- 仓位控制
- 行业限制
- 风险指标计算

### 3. 实时监控
- 市场异常监控
- 风险预警监控
- 系统异常监控
- 实时事件订阅

### 4. 性能监控
- 策略执行性能监控
- 系统资源监控
- API调用监控
- 性能指标统计

### 5. 交易成本优化
- 最优交易时机
- 最优交易数量
- 最优交易价格
- 交易成本分析

## 技术架构

### 开发环境
- .NET Core 6.0+
- Visual Studio 2022 / VS Code
- Git

### 主要依赖
- Microsoft.Extensions.Logging
- Microsoft.Extensions.DependencyInjection
- System.Collections.Concurrent

### 项目结构
```
src/
├── Quant.Strategy/              # 策略核心模块
│   ├── Services/               # 服务接口
│   │   ├── Impl/              # 服务实现
│   │   └── Interfaces/        # 接口定义
│   └── Models/                # 数据模型
├── Quant.Common/              # 公共模块
└── Quant.Tests/              # 单元测试
```

## 快速开始

### 1. 克隆项目
```bash
git clone https://github.com/molunshang/quant.git
cd quant
```

### 2. 安装依赖
```bash
dotnet restore
```

### 3. 编译项目
```bash
dotnet build
```

### 4. 运行测试
```bash
dotnet test
```

## 使用说明

### 配置服务
```csharp
services.AddScoped<ITradeHistoryService, JQTradeHistoryService>();
services.AddScoped<IRepoService, JQRepoService>();
services.AddScoped<ITradeAnalysisService, JQTradeAnalysisService>();
services.AddScoped<IRiskControlService, JQRiskControlService>();
services.AddScoped<IRealTimeMonitorService, JQRealTimeMonitorService>();
services.AddScoped<IPerformanceMonitorService, JQPerformanceMonitorService>();
```

### 使用示例
```csharp
// 交易记录
var tradeHistory = await _tradeHistoryService.RecordTrade(new TradeRecord
{
    StockCode = "000001",
    TradeType = TradeType.Buy,
    Price = 10.5m,
    Quantity = 100
});

// 风险控制
var canTrade = await _riskControlService.CanTrade("000001");
var stopLoss = await _riskControlService.CheckStopLoss("000001", 10.0m);

// 实时监控
var marketAlert = await _monitorService.MonitorMarketAnomaly("000001");
var riskAlert = await _monitorService.MonitorRiskAlert("000001");
```

## 贡献指南
1. Fork 项目
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 创建 Pull Request

## 许可证
本项目采用 MIT 许可证 - 详见 [LICENSE](LICENSE) 文件

## 联系方式
- 项目维护者：[molunshang](https://github.com/molunshang)
- 项目地址：[https://github.com/molunshang/quant](https://github.com/molunshang/quant) 