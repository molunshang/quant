# 掘金量化 C# SDK 文档

## 目录
- [快速开始](#快速开始)
  - [指引](#指引)
  - [策略结构](#策略结构)
  - [策略示例](#策略示例)
- [重要概念](#重要概念)
  - [策略基类](#策略基类)
  - [数据查询函数](#数据查询函数)
  - [结果集合类](#结果集合类)
  - [数据结构](#数据结构)
  - [枚举常量](#枚举常量)
  - [错误码](#错误码)

## 快速开始

### 指引

#### 环境准备
1. 系统要求
   - Windows 7及以上操作系统
   - .NET Framework 4.5.2及以上
   - Visual Studio 2013及以上
   - 4GB及以上内存
   - 1GB及以上硬盘空间

2. 开发环境安装
   - 安装Visual Studio
   - 安装.NET Framework
   - 安装Git（可选）
   - 安装NuGet包管理器

3. SDK安装
   - 方式一：通过NuGet安装
     ```powershell
     # 32位系统
     Install-Package gmsdk-net-x86
     
     # 64位系统
     Install-Package gmsdk-net-x64
     ```
   - 方式二：手动安装
     1. 下载SDK安装包
     2. 解压到项目目录
     3. 添加引用

4. 项目配置
   - 添加SDK引用
   - 配置目标框架
   - 设置启动参数
   - 配置调试选项

#### 快速新建策略
1. 创建项目
   ```csharp
   // 创建新的控制台应用程序
   // 添加SDK引用
   using GMSDK;
   ```

2. 编写策略
   ```csharp
   public class MyStrategy : Strategy
   {
       public MyStrategy(string token, string strategyId, StrategyMode mode) 
           : base(token, strategyId, mode) { }

       public override void OnInit()
       {
           Console.WriteLine("策略初始化");
           Subscribe("SHSE.600000", "tick");
       }

       public override void OnTick(Tick tick)
       {
           Console.WriteLine($"收到Tick数据：{tick.symbol}");
       }
   }
   ```

3. 运行策略
   ```csharp
   static void Main(string[] args)
   {
       var strategy = new MyStrategy(
           "your-token",
           "your-strategy-id",
           StrategyMode.MODE_BACKTEST
       );
       strategy.Run();
   }
   ```

### 策略结构

#### 继承策略基类
```csharp
public class MyStrategy : Strategy
{
    public MyStrategy(string token, string strategyId, StrategyMode mode) 
        : base(token, strategyId, mode) { }
}
```

#### 实现必要方法
```csharp
// 初始化方法
public override void OnInit()
{
    // 初始化代码
}

// 行情处理方法
public override void OnTick(Tick tick)
{
    // 处理Tick数据
}

public override void OnBar(Bar bar)
{
    // 处理K线数据
}
```

#### 添加交易逻辑
```csharp
// 下单方法
OrderVolume("SHSE.600000", 100, OrderSide.BUY, OrderType.LIMIT);

// 撤单方法
CancelOrder("order-id");

// 查询持仓
var position = GetPosition("SHSE.600000");
```

### 策略示例

#### 订阅行情策略示例
```csharp
using GMSDK;

namespace example
{
    public class MyStrategy : Strategy
    {
        public MyStrategy(string token, string strategyId, StrategyMode mode) 
            : base(token, strategyId, mode) { }

        public override void OnInit()
        {
            System.Console.WriteLine("OnInit");
            Subscribe("SHSE.600000", "tick");
        }

        public override void OnTick(Tick tick)
        {
            System.Console.WriteLine("{0,-50}{1}", "代码", tick.symbol);
            System.Console.WriteLine("{0,-50}{1}", "时间", tick.createdAt);
            System.Console.WriteLine("{0,-50}{1}", "最新价", tick.price);
            System.Console.WriteLine("{0,-50}{1}", "开盘价", tick.open);
            System.Console.WriteLine("{0,-50}{1}", "最高价", tick.high);
            System.Console.WriteLine("{0,-50}{1}", "最低价", tick.low);
            System.Console.WriteLine("{0,-50}{1}", "成交总量", tick.cumVolume);
            System.Console.WriteLine("{0,-50}{1}", "成交总额", tick.cumAmount);
            System.Console.WriteLine("{0,-50}{1}", "合约持仓量", tick.cumPosition);
            System.Console.WriteLine("{0,-50}{1}", "瞬时成交额", tick.lastAmount);
            System.Console.WriteLine("{0,-50}{1}", "瞬时成交量", tick.lastVolume);
            System.Console.WriteLine("{0,-50}{1}", "一档委买价", tick.quotes[0].bidPrice);
            System.Console.WriteLine("{0,-50}{1}", "一档委买量", tick.quotes[0].bidVolume);
            System.Console.WriteLine("{0,-50}{1}", "一档委卖价", tick.quotes[0].askPrice);
            System.Console.WriteLine("{0,-50}{1}", "一档委卖量", tick.quotes[0].askVolume);
        }
    }
}
```

#### 数据研究示例
```csharp
using GMSDK;

namespace example_datares
{
    public class MyStrategy : Strategy
    {
        public MyStrategy(string token, string strategyId, StrategyMode mode) 
            : base(token, strategyId, mode) { }

        public override void OnInit()
        {
            System.Console.WriteLine("OnInit");

            GMDataList<Tick> ht = GMApi.HistoryTicks(
                "SZSE.000002", 
                "2017-07-11 10:20:00", 
                "2017-07-11 10:30:00"
            );
            
            if (ht.status == 0)
            {
                foreach (var tick in ht.data)
                {
                    // 打印Tick数据
                    System.Console.WriteLine("{0,-50}{1}", "代码", tick.symbol);
                    System.Console.WriteLine("{0,-50}{1}", "时间", tick.createdAt);
                    System.Console.WriteLine("{0,-50}{1}", "最新价", tick.price);
                    // ... 其他数据
                }
            }
        }
    }
}
```

## 重要概念

### 策略基类
策略基类提供了策略开发所需的基本功能，包括：

#### 构造函数
```csharp
public Strategy(string token, string strategyId, StrategyMode mode)
```

#### 初始化方法
```csharp
// 策略初始化
public virtual void OnInit()

// 策略停止
public virtual void OnStop()

// 策略错误
public virtual void OnError(int code, string message)
```

#### 行情事件
```csharp
// 实时行情
public virtual void OnTick(Tick tick)

// K线数据
public virtual void OnBar(Bar bar)

// 分笔数据
public virtual void OnTrade(Trade trade)
```

#### 交易事件
```csharp
// 订单状态变化
public virtual void OnOrder(Order order)

// 成交回报
public virtual void OnTrade(Trade trade)

// 持仓变化
public virtual void OnPosition(Position position)

// 账户变化
public virtual void OnAccount(Account account)
```

#### 定时任务
```csharp
// 定时任务
public virtual void OnTimer()

// 设置定时任务
public void SetTimer(int interval)
```

#### 行情订阅
```csharp
// 订阅单个证券
public void Subscribe(string symbol, string frequency)

// 订阅多个证券
public void Subscribe(string[] symbols, string frequency)

// 取消订阅
public void Unsubscribe(string symbol, string frequency)
```

#### 交易操作
```csharp
// 按数量下单
public string OrderVolume(string symbol, double volume, OrderSide side, OrderType type)

// 按金额下单
public string OrderValue(string symbol, double value, OrderSide side, OrderType type)

// 按目标持仓下单
public string OrderTargetVolume(string symbol, double volume)

// 按目标持仓金额下单
public string OrderTargetValue(string symbol, double value)

// 撤单
public void CancelOrder(string orderId)

// 撤单查询
public void CancelOrderQuery(string orderId)
```

### 数据查询函数
GMApi提供了丰富的数据查询函数：

#### 行情数据查询
```csharp
// 获取历史K线数据
GMDataList<Bar> HistoryBars(string symbol, string frequency, string startTime, string endTime);

// 获取历史Tick数据
GMDataList<Tick> HistoryTicks(string symbol, string startTime, string endTime);

// 获取历史分笔数据
GMDataList<Trade> HistoryTrades(string symbol, string startTime, string endTime);
```

#### 基本面数据查询
```csharp
// 获取股票基本面数据
GMDataList<Fundamental> GetFundamentals(string[] symbols);

// 获取财务指标数据
GMDataList<Financial> GetFinancials(string[] symbols);

// 获取股本结构数据
GMDataList<Shareholder> GetShareholders(string[] symbols);

// 获取资产负债表
GMDataList<BalanceSheet> GetBalanceSheets(string[] symbols);

// 获取利润表
GMDataList<IncomeStatement> GetIncomeStatements(string[] symbols);

// 获取现金流量表
GMDataList<CashFlow> GetCashFlows(string[] symbols);
```

#### 市场数据查询
```csharp
// 获取股票列表
GMDataList<Stock> GetStocks();

// 获取指数列表
GMDataList<Index> GetIndices();

// 获取基金列表
GMDataList<Fund> GetFunds();

// 获取期货列表
GMDataList<Future> GetFutures();

// 获取期权列表
GMDataList<Option> GetOptions();

// 获取债券列表
GMDataList<Bond> GetBonds();

// 获取可转债列表
GMDataList<ConvertibleBond> GetConvertibleBonds();
```

#### 行业数据查询
```csharp
// 获取行业列表
GMDataList<Industry> GetIndustries();

// 获取行业成分股
GMDataList<Stock> GetIndustryStocks(string industry);

// 获取股票所属行业
GMDataList<Industry> GetStockIndustry(string symbol);

// 获取行业指数
GMDataList<Index> GetIndustryIndices();

// 获取行业指数成分股
GMDataList<Stock> GetIndustryIndexStocks(string index);
```

#### 指数数据查询
```csharp
// 获取指数成分股
GMDataList<Stock> GetIndexStocks(string index);

// 获取股票所属指数
GMDataList<Index> GetStockIndices(string symbol);

// 获取指数权重
GMDataList<IndexWeight> GetIndexWeights(string index);

// 获取指数行情
GMDataList<Quote> GetIndexQuotes(string[] indices);
```

#### 财务数据查询
```csharp
// 获取财务指标
GMDataList<FinancialIndicator> GetFinancialIndicators(string[] symbols);

// 获取财务指标历史数据
GMDataList<FinancialIndicator> GetFinancialIndicatorsHistory(string symbol, string indicator, string startTime, string endTime);

// 获取财务指标行业数据
GMDataList<FinancialIndicator> GetFinancialIndicatorsIndustry(string industry, string indicator);

// 获取财务指标市场数据
GMDataList<FinancialIndicator> GetFinancialIndicatorsMarket(string indicator);
```

#### 数据工具函数
```csharp
// 获取复权因子
GMDataList<AdjustFactor> GetAdjustFactor(string[] symbols);

// 获取复权价格
double GetAdjustedPrice(string symbol, double price, string adjustType);

// 获取复权K线
GMDataList<Bar> GetAdjustedBars(string symbol, string frequency, string startTime, string endTime, string adjustType);

// 获取前复权价格
double GetForwardAdjustedPrice(string symbol, double price);

// 获取后复权价格
double GetBackwardAdjustedPrice(string symbol, double price);

// 获取不复权价格
double GetUnadjustedPrice(string symbol, double price);
```

### 结果集合类
用于处理API返回的数据集合：

#### GMDataList<T>
```csharp
public class GMDataList<T>
{
    public int status;        // 状态码
    public string message;    // 错误信息
    public List<T> data;      // 数据列表
}
```

#### GMDataArray<T>
```csharp
public class GMDataArray<T>
{
    public int status;        // 状态码
    public string message;    // 错误信息
    public T[] data;         // 数据数组
}
```

### 数据结构
SDK中定义的主要数据结构：

#### Tick：行情数据
```csharp
public class Tick
{
    public string symbol;     // 证券代码
    public DateTime createdAt;// 时间
    public double price;      // 最新价
    public double open;       // 开盘价
    public double high;       // 最高价
    public double low;        // 最低价
    public double cumVolume;  // 成交总量
    public double cumAmount;  // 成交总额
    public double cumPosition;// 合约持仓量
    public double lastAmount; // 瞬时成交额
    public double lastVolume; // 瞬时成交量
    public Quote[] quotes;    // 委托档位
}
```

#### Quote：委托档位
```csharp
public class Quote
{
    public double bidPrice;   // 买价
    public double bidVolume;  // 买量
    public double askPrice;   // 卖价
    public double askVolume;  // 卖量
}
```

#### Bar：K线数据
```csharp
public class Bar
{
    public string symbol;     // 证券代码
    public DateTime time;     // 时间
    public double open;       // 开盘价
    public double high;       // 最高价
    public double low;        // 最低价
    public double close;      // 收盘价
    public double volume;     // 成交量
    public double amount;     // 成交额
}
```

#### Order：订单数据
```csharp
public class Order
{
    public string orderId;    // 订单ID
    public string symbol;     // 证券代码
    public OrderSide side;    // 买卖方向
    public OrderType type;    // 订单类型
    public double price;      // 委托价格
    public double volume;     // 委托数量
    public double filled;     // 成交数量
    public OrderStatus status;// 订单状态
    public DateTime time;     // 委托时间
}
```

#### Trade：成交数据
```csharp
public class Trade
{
    public string tradeId;    // 成交ID
    public string orderId;    // 订单ID
    public string symbol;     // 证券代码
    public OrderSide side;    // 买卖方向
    public double price;      // 成交价格
    public double volume;     // 成交数量
    public DateTime time;     // 成交时间
}
```

#### Position：持仓数据
```csharp
public class Position
{
    public string symbol;     // 证券代码
    public double volume;     // 持仓数量
    public double cost;       // 持仓成本
    public double marketValue;// 市值
    public double available;  // 可用数量
}
```

#### Account：账户数据
```csharp
public class Account
{
    public double totalAsset;     // 总资产
    public double cash;           // 现金
    public double frozenCash;     // 冻结资金
    public double marketValue;    // 市值
    public double availableCash;  // 可用资金
}
```

#### Fundamental：基本面数据
```csharp
public class Fundamental
{
    public string symbol;         // 证券代码
    public string name;           // 证券名称
    public string industry;       // 所属行业
    public double pe;             // 市盈率
    public double pb;             // 市净率
    public double ps;             // 市销率
    public double dividend;       // 股息
    public double dividendYield;  // 股息率
    public double eps;            // 每股收益
    public double bps;            // 每股净资产
    public double roe;            // 净资产收益率
    public double roa;            // 总资产收益率
    public double grossMargin;    // 毛利率
    public double netMargin;      // 净利率
    public double debtToEquity;   // 资产负债率
}
```

#### Financial：财务数据
```csharp
public class Financial
{
    public string symbol;         // 证券代码
    public DateTime reportDate;   // 报告期
    public double revenue;        // 营业收入
    public double netProfit;      // 净利润
    public double totalAssets;    // 总资产
    public double totalLiabilities;// 总负债
    public double netAssets;      // 净资产
    public double operatingCashFlow;// 经营现金流
    public double investingCashFlow;// 投资现金流
    public double financingCashFlow;// 筹资现金流
}
```

#### Shareholder：股东数据
```csharp
public class Shareholder
{
    public string symbol;         // 证券代码
    public DateTime reportDate;   // 报告期
    public string name;           // 股东名称
    public double shares;         // 持股数量
    public double ratio;          // 持股比例
    public string type;           // 股东类型
}
```

#### BalanceSheet：资产负债表
```csharp
public class BalanceSheet
{
    public string symbol;         // 证券代码
    public DateTime reportDate;   // 报告期
    public double totalAssets;    // 总资产
    public double totalLiabilities;// 总负债
    public double netAssets;      // 净资产
    public double currentAssets;  // 流动资产
    public double nonCurrentAssets;// 非流动资产
    public double currentLiabilities;// 流动负债
    public double nonCurrentLiabilities;// 非流动负债
}
```

#### IncomeStatement：利润表
```csharp
public class IncomeStatement
{
    public string symbol;         // 证券代码
    public DateTime reportDate;   // 报告期
    public double revenue;        // 营业收入
    public double costOfSales;    // 营业成本
    public double grossProfit;    // 毛利润
    public double operatingProfit;// 营业利润
    public double netProfit;      // 净利润
    public double eps;            // 每股收益
}
```

#### CashFlow：现金流量表
```csharp
public class CashFlow
{
    public string symbol;         // 证券代码
    public DateTime reportDate;   // 报告期
    public double operatingCashFlow;// 经营现金流
    public double investingCashFlow;// 投资现金流
    public double financingCashFlow;// 筹资现金流
    public double netCashFlow;    // 净现金流
}
```

#### Stock：股票数据
```csharp
public class Stock
{
    public string symbol;         // 证券代码
    public string name;           // 证券名称
    public string exchange;       // 交易所
    public string industry;       // 所属行业
    public string status;         // 状态
    public DateTime listDate;     // 上市日期
    public double totalShares;    // 总股本
    public double floatShares;    // 流通股本
}
```

#### Index：指数数据
```csharp
public class Index
{
    public string symbol;         // 指数代码
    public string name;           // 指数名称
    public string exchange;       // 交易所
    public string category;       // 指数类别
    public DateTime baseDate;     // 基期
    public double baseValue;      // 基期值
}
```

#### Fund：基金数据
```csharp
public class Fund
{
    public string symbol;         // 基金代码
    public string name;           // 基金名称
    public string type;           // 基金类型
    public string manager;        // 基金经理
    public string company;        // 基金公司
    public DateTime establishDate;// 成立日期
    public double size;           // 基金规模
}
```

#### Future：期货数据
```csharp
public class Future
{
    public string symbol;         // 期货代码
    public string name;           // 期货名称
    public string exchange;       // 交易所
    public string category;       // 期货类别
    public double multiplier;     // 合约乘数
    public double margin;         // 保证金比例
}
```

#### Option：期权数据
```csharp
public class Option
{
    public string symbol;         // 期权代码
    public string name;           // 期权名称
    public string exchange;       // 交易所
    public string type;           // 期权类型
    public double strikePrice;    // 行权价
    public DateTime expireDate;   // 到期日
    public double multiplier;     // 合约乘数
}
```

#### Bond：债券数据
```csharp
public class Bond
{
    public string symbol;         // 债券代码
    public string name;           // 债券名称
    public string exchange;       // 交易所
    public string type;           // 债券类型
    public double faceValue;      // 面值
    public double couponRate;     // 票面利率
    public DateTime issueDate;    // 发行日
    public DateTime expireDate;   // 到期日
}
```

#### ConvertibleBond：可转债数据
```csharp
public class ConvertibleBond
{
    public string symbol;         // 可转债代码
    public string name;           // 可转债名称
    public string exchange;       // 交易所
    public double faceValue;      // 面值
    public double couponRate;     // 票面利率
    public double convertPrice;   // 转股价
    public double convertRatio;   // 转股比例
    public DateTime issueDate;    // 发行日
    public DateTime expireDate;   // 到期日
}
```

#### Industry：行业数据
```csharp
public class Industry
{
    public string code;           // 行业代码
    public string name;           // 行业名称
    public string level;          // 行业级别
    public string parent;         // 父级行业
}
```

#### IndexWeight：指数权重
```csharp
public class IndexWeight
{
    public string index;          // 指数代码
    public string symbol;         // 成分股代码
    public double weight;         // 权重
    public DateTime date;         // 日期
}
```

#### FinancialIndicator：财务指标
```csharp
public class FinancialIndicator
{
    public string symbol;         // 证券代码
    public string indicator;      // 指标名称
    public double value;          // 指标值
    public DateTime date;         // 日期
}
```

#### AdjustFactor：复权因子
```csharp
public class AdjustFactor
{
    public string symbol;         // 证券代码
    public DateTime date;         // 日期
    public double factor;         // 复权因子
}
```

### 枚举常量
SDK中定义的主要枚举：

#### StrategyMode：策略模式
```csharp
public enum StrategyMode
{
    MODE_BACKTEST,    // 回测模式
    MODE_LIVE         // 实盘模式
}
```

#### OrderType：订单类型
```csharp
public enum OrderType
{
    LIMIT,           // 限价单
    MARKET,          // 市价单
    STOP,            // 止损单
    STOP_LIMIT       // 止损限价单
}
```

#### OrderSide：订单方向
```csharp
public enum OrderSide
{
    BUY,             // 买入
    SELL             // 卖出
}
```

#### OrderStatus：订单状态
```csharp
public enum OrderStatus
{
    PENDING,         // 待成交
    PARTIAL,         // 部分成交
    FILLED,          // 全部成交
    CANCELLED,       // 已撤单
    REJECTED         // 已拒绝
}
```

### 错误码
SDK中定义的主要错误码：
- 0：成功
- -1：失败
- -2：参数错误
- -3：网络错误
- -4：超时
- -5：未登录
- -6：无权限
- -7：余额不足
- -8：持仓不足
- -9：下单失败
- -10：撤单失败
- -11：行情订阅失败
- -12：行情取消订阅失败
- -13：数据查询失败
- -14：账户查询失败
- -15：持仓查询失败
- -16：订单查询失败
- -17：撤单查询失败
- -18：历史数据查询失败
- -19：基本面数据查询失败
- -20：财务数据查询失败
