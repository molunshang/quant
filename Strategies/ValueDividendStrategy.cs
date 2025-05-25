using System;
using System.Collections.Generic;
using System.Linq;
using GMSDK;

namespace DataEventDriven.Strategies
{
    /// <summary>
    /// 价值股息策略
    /// 选股标准：
    /// 1. 破净（PB < 1）
    /// 2. 日交易量 > 1亿
    /// 3. 股息率 > 1%
    /// 
    /// 卖出条件：
    /// 1. PB回升至行业均值
    /// 2. PE从历史低位（<20%分位）升至中高位（>70%分位）
    /// 
    /// 风险控制：
    /// 1. 单只股票最大持仓比例
    /// 2. 单个行业最大持仓比例
    /// 3. 交易频率限制
    /// </summary>
    public class ValueDividendStrategy : Strategy
    {
        // 策略参数
        private const double MIN_VOLUME = 100000000; // 最小交易量 1亿
        private const double MIN_DIVIDEND_YIELD = 0.01; // 最小股息率 1%
        private const double MAX_PB = 1.0; // 最大市净率 1
        private const double BUY_AMOUNT = 10000; // 每次买入金额 1万元
        private const double PE_LOW_PERCENTILE = 0.2; // PE低位分位数
        private const double PE_HIGH_PERCENTILE = 0.7; // PE高位分位数
        private const double PRICE_DROP_THRESHOLD = 0.5; // 价格下跌阈值 50%

        // 风险控制参数
        private const double MAX_STOCK_POSITION_RATIO = 0.1; // 单只股票最大持仓比例 10%
        private const double MAX_INDUSTRY_POSITION_RATIO = 0.3; // 单个行业最大持仓比例 30%
        private const int MIN_TRADE_INTERVAL_DAYS = 5; // 最小交易间隔天数
        private const int BATCH_COUNT = 3; // 分批交易次数

        // 交易记录
        private readonly Dictionary<string, DateTime> _lastTradeTime = new Dictionary<string, DateTime>();
        private readonly Dictionary<string, int> _tradeBatchCount = new Dictionary<string, int>();
        private readonly Dictionary<string, double> _lastTradePrices = new Dictionary<string, double>();

        // 缓存数据
        private Dictionary<string, Fundamental> _fundamentalsCache = new Dictionary<string, Fundamental>();
        private Dictionary<string, double> _industryPBCache = new Dictionary<string, double>();
        private Dictionary<string, double> _pePercentileCache = new Dictionary<string, double>();

        public ValueDividendStrategy(string token, string strategyId, StrategyMode mode)
            : base(token, strategyId, mode) { }

        public override void OnInit()
        {
            Console.WriteLine("策略初始化...");

            // 订阅所有A股行情
            var stocks = GMApi.GetStocks();
            if (stocks.status == 0)
            {
                foreach (var stock in stocks.data)
                {
                    Subscribe(stock.symbol, "1d"); // 订阅日线数据
                }
            }

            // 初始化基本面数据缓存
            UpdateFundamentalsCache();
        }

        private void UpdateFundamentalsCache()
        {
            var stocks = GMApi.GetStocks();
            if (stocks.status != 0) return;

            var symbols = stocks.data.Select(s => s.symbol).ToArray();
            var fundamentals = GMApi.GetFundamentals(symbols);
            if (fundamentals.status != 0) return;

            _fundamentalsCache = fundamentals.data.ToDictionary(f => f.symbol, f => f);

            // 计算行业PB
            var industryGroups = fundamentals.data.GroupBy(f => f.industry);
            foreach (var group in industryGroups)
            {
                var avgPB = group.Average(f => f.pb);
                _industryPBCache[group.Key] = avgPB;
            }

            // 计算PE分位数
            foreach (var stock in stocks.data)
            {
                if (!_fundamentalsCache.TryGetValue(stock.symbol, out var fundamental)) continue;
                
                var industryStocks = fundamentals.data.Where(f => f.industry == fundamental.industry);
                var sortedPEs = industryStocks.Select(f => f.pe).OrderBy(pe => pe).ToList();
                var peIndex = sortedPEs.IndexOf(fundamental.pe);
                _pePercentileCache[stock.symbol] = (double)peIndex / sortedPEs.Count;
            }
        }

        public override void OnBar(Bar bar)
        {
            try
            {
                Console.WriteLine($"收到 {bar.symbol} 的日线数据");

                // 获取股票基本面数据
                if (!_fundamentalsCache.TryGetValue(bar.symbol, out var fundamental))
                {
                    Console.WriteLine($"无法获取 {bar.symbol} 的基本面数据");
                    return;
                }

                // 获取当前持仓
                var positions = GetPositions();
                var totalValue = positions.Sum(p => p.marketValue);

                // 检查是否需要卖出
                if (positions.Any(p => p.symbol == bar.symbol))
                {
                    var position = positions.First(p => p.symbol == bar.symbol);
                    if (ShouldSell(bar.symbol, fundamental, bar))
                    {
                        Console.WriteLine($"开始卖出 {bar.symbol}");
                        var orderId = OrderTargetValue(bar.symbol, 0); // 清仓
                        if (!string.IsNullOrEmpty(orderId))
                        {
                            _lastTradeTime[bar.symbol] = DateTime.Today;
                            _lastTradePrices[bar.symbol] = bar.close;
                        }
                    }
                }
                // 检查是否需要买入
                else if (ShouldBuy(bar.symbol, fundamental, bar))
                {
                    if (CanTrade(bar.symbol) && CheckPositionLimit(bar.symbol, BUY_AMOUNT, totalValue))
                    {
                        Console.WriteLine($"开始买入 {bar.symbol}");
                        BuyWithBatches(bar.symbol, BUY_AMOUNT, bar.close);
                    }
                }

                // 收盘时购买国债逆回购
                if (IsCloseTime())
                {
                    BuyRepoAtClose();
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine($"处理 {bar.symbol} 数据时发生错误: {ex.Message}");
            }
        }

        private bool ShouldSell(string symbol, Fundamental fundamental, Bar bar)
        {
            // 获取行业平均PB
            if (!_industryPBCache.TryGetValue(fundamental.industry, out var industryAvgPB))
            {
                Console.WriteLine($"无法获取行业 {fundamental.industry} 的平均PB");
                return false;
            }

            // 获取PE分位数
            if (!_pePercentileCache.TryGetValue(symbol, out var pePercentile))
            {
                Console.WriteLine($"无法获取 {symbol} 的PE分位数");
                return false;
            }

            // 判断是否满足卖出条件
            bool pbCondition = fundamental.pb >= industryAvgPB; // PB回升至行业均值
            bool peCondition = pePercentile > PE_HIGH_PERCENTILE; // PE升至高位

            return pbCondition || peCondition;
        }

        private bool ShouldBuy(string symbol, Fundamental fundamental, Bar bar)
        {
            // 检查选股条件
            bool volumeCondition = bar.volume > MIN_VOLUME;
            bool pbCondition = fundamental.pb < MAX_PB;
            bool dividendCondition = fundamental.dividendYield > MIN_DIVIDEND_YIELD;

            // 检查历史价格条件
            if (_lastTradePrices.TryGetValue(symbol, out var lastPrice))
            {
                return bar.close <= lastPrice * PRICE_DROP_THRESHOLD;
            }

            return volumeCondition && pbCondition && dividendCondition;
        }

        private bool CanTrade(string symbol)
        {
            if (_lastTradeTime.TryGetValue(symbol, out var lastTrade))
            {
                var daysSinceLastTrade = (DateTime.Today - lastTrade).TotalDays;
                return daysSinceLastTrade >= MIN_TRADE_INTERVAL_DAYS;
            }
            return true;
        }

        private bool CheckPositionLimit(string symbol, double amount, double totalValue)
        {
            // 检查单只股票持仓限制
            var positions = GetPositions();
            var currentPosition = positions.FirstOrDefault(p => p.symbol == symbol);
            if (currentPosition != null)
            {
                var positionRatio = currentPosition.marketValue / totalValue;
                if (positionRatio + amount / totalValue > MAX_STOCK_POSITION_RATIO)
                {
                    return false;
                }
            }

            // 检查行业持仓限制
            if (_fundamentalsCache.TryGetValue(symbol, out var fundamental))
            {
                var industryPositions = positions.Where(p => 
                    _fundamentalsCache.TryGetValue(p.symbol, out var f) && 
                    f.industry == fundamental.industry);
                
                var industryValue = industryPositions.Sum(p => p.marketValue);
                if (industryValue + amount > totalValue * MAX_INDUSTRY_POSITION_RATIO)
                {
                    return false;
                }
            }

            return true;
        }

        private void BuyWithBatches(string symbol, double totalAmount, double currentPrice)
        {
            if (!_tradeBatchCount.TryGetValue(symbol, out var batchCount))
            {
                batchCount = 0;
            }

            if (batchCount >= BATCH_COUNT)
            {
                _tradeBatchCount.Remove(symbol);
                return;
            }

            var batchAmount = totalAmount / (BATCH_COUNT - batchCount);
            var orderId = OrderValue(symbol, batchAmount, OrderSide.BUY, OrderType.LIMIT);
            
            if (!string.IsNullOrEmpty(orderId))
            {
                _lastTradeTime[symbol] = DateTime.Today;
                _lastTradePrices[symbol] = currentPrice;
                _tradeBatchCount[symbol] = batchCount + 1;
            }
        }

        private bool IsCloseTime()
        {
            var now = DateTime.Now;
            return now.Hour == 14 && now.Minute >= 50;
        }

        private void BuyRepoAtClose()
        {
            // 获取可用资金
            var account = GetAccount();
            if (account == null || account.availableCash <= 0) return;

            // 购买国债逆回购
            var orderId = OrderValue("SHSE.204001", account.availableCash, OrderSide.BUY, OrderType.LIMIT);
            if (!string.IsNullOrEmpty(orderId))
            {
                Console.WriteLine($"收盘购买国债逆回购，金额：{account.availableCash}");
            }
        }
    }
}