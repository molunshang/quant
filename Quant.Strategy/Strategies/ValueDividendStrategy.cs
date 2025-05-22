using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using DataEventDriven;
using GMSDK;
using Microsoft.Extensions.Logging;
using Quant.Strategy.Models;
using Quant.Strategy.Services;

namespace Quant.Strategy.Strategies
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
    /// 
    /// 交易规则：
    /// 1. 每次卖出后立即执行选股购买策略
    /// 2. 对于历史持有过的股票，如果当前价格低于上次交易价格的50%则购买
    /// 3. 收盘时购买1日期国债逆回购
    /// </summary>
    public class ValueDividendStrategy : MyStrategy
    {
        private readonly IStockDataService _stockDataService;
        private readonly IPositionService _positionService;
        private readonly IIndustryDataService _industryDataService;
        private readonly ITradeCostService _tradeCostService;
        private readonly ITradeHistoryService _tradeHistoryService;
        private readonly IRepoService _repoService;
        private readonly ILogger<ValueDividendStrategy> _logger;
        
        // 策略参数
        private const decimal MIN_VOLUME = 100000000; // 最小交易量 1亿
        private const decimal MIN_DIVIDEND_YIELD = 0.01m; // 最小股息率 1%
        private const decimal MAX_PB = 1.0m; // 最大市净率 1
        private const decimal BUY_AMOUNT = 10000m; // 每次买入金额 1万元
        private const decimal PE_LOW_PERCENTILE = 0.2m; // PE低位分位数
        private const decimal PE_HIGH_PERCENTILE = 0.7m; // PE高位分位数
        private const decimal PRICE_DROP_THRESHOLD = 0.5m; // 价格下跌阈值 50%
        
        // 风险控制参数
        private const decimal MAX_STOCK_POSITION_RATIO = 0.1m; // 单只股票最大持仓比例 10%
        private const decimal MAX_INDUSTRY_POSITION_RATIO = 0.3m; // 单个行业最大持仓比例 30%
        private const int MIN_TRADE_INTERVAL_DAYS = 5; // 最小交易间隔天数
        private const int BATCH_COUNT = 3; // 分批交易次数
        
        // 交易记录
        private readonly Dictionary<string, DateTime> _lastTradeTime = new Dictionary<string, DateTime>();
        private readonly Dictionary<string, int> _tradeBatchCount = new Dictionary<string, int>();

        public ValueDividendStrategy(string token, string strategyId, StrategyMode mode):base(token,strategyId,mode)
        {
            //_stockDataService = stockDataService;
            //_positionService = positionService;
            //_industryDataService = industryDataService;
            //_tradeCostService = tradeCostService;
            //_tradeHistoryService = tradeHistoryService;
            //_repoService = repoService;
            //_logger = logger;
        }

        private async Task<bool> CanTrade(string stockCode)
        {
            // 检查交易间隔
            if (_lastTradeTime.TryGetValue(stockCode, out var lastTrade))
            {
                var daysSinceLastTrade = (DateTime.Today - lastTrade).TotalDays;
                if (daysSinceLastTrade < MIN_TRADE_INTERVAL_DAYS)
                {
                    _logger.LogInformation($"股票 {stockCode} 距离上次交易不足 {MIN_TRADE_INTERVAL_DAYS} 天，跳过交易");
                    return false;
                }
            }
            return true;
        }

        private async Task<bool> CheckPositionLimit(string stockCode, decimal amount)
        {
            try
            {
                // 获取当前持仓
                var positions = await _positionService.GetPositions();
                var totalValue = positions.Values.Sum(p => p.MarketValue);
                
                // 检查单只股票持仓限制
                var stockPosition = positions.GetValueOrDefault(stockCode)?.MarketValue ?? 0;
                if ((stockPosition + amount) / totalValue > MAX_STOCK_POSITION_RATIO)
                {
                    _logger.LogWarning($"股票 {stockCode} 持仓比例将超过限制 {MAX_STOCK_POSITION_RATIO:P2}");
                    return false;
                }
                
                // 检查行业持仓限制
                var industryCode = await _industryDataService.GetStockIndustry(stockCode);
                var industryStocks = positions.Keys.Where(async k => 
                    await _industryDataService.GetStockIndustry(k) == industryCode);
                var industryValue = positions
                    .Where(p => industryStocks.Contains(p.Key))
                    .Sum(p => p.Value.MarketValue);
                
                if ((industryValue + amount) / totalValue > MAX_INDUSTRY_POSITION_RATIO)
                {
                    _logger.LogWarning($"行业 {industryCode} 持仓比例将超过限制 {MAX_INDUSTRY_POSITION_RATIO:P2}");
                    return false;
                }
                
                return true;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, $"检查持仓限制失败");
                return false;
            }
        }

        private async Task<TradeResult> BuyWithBatches(string stockCode, decimal totalAmount)
        {
            try
            {
                // 获取当前价格
                var price = await _stockDataService.GetStockTradingData(new List<string> { stockCode });
                var currentPrice = price[stockCode].Close;
                
                // 计算每批买入金额
                var batchAmount = totalAmount / BATCH_COUNT;
                
                // 获取当前批次
                var currentBatch = _tradeBatchCount.GetValueOrDefault(stockCode, 0);
                if (currentBatch >= BATCH_COUNT)
                {
                    _logger.LogInformation($"股票 {stockCode} 已完成所有批次买入");
                    return new TradeResult { Success = true };
                }
                
                // 计算本次买入数量
                var quantity = (int)(batchAmount / currentPrice / 100) * 100;
                if (quantity <= 0)
                {
                    return new TradeResult
                    {
                        Success = false,
                        ErrorMessage = "买入金额不足，无法买入100股"
                    };
                }
                
                // 计算交易成本
                var cost = await _tradeCostService.CalculateBuyCost(stockCode, currentPrice, quantity);
                
                // 执行买入
                var result = await _positionService.Buy(stockCode, cost.TotalCost);
                
                if (result.Success)
                {
                    // 更新交易记录
                    _lastTradeTime[stockCode] = DateTime.Today;
                    _tradeBatchCount[stockCode] = currentBatch + 1;
                    
                    _logger.LogInformation($"股票 {stockCode} 第 {currentBatch + 1} 批买入成功，" +
                        $"数量 {result.Quantity} 股，价格 {result.Price} 元，" +
                        $"成本 {cost.TotalCost} 元（含佣金 {cost.Commission} 元，过户费 {cost.TransferFee} 元）");
                }
                
                return result;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, $"分批买入股票 {stockCode} 失败");
                return new TradeResult
                {
                    Success = false,
                    ErrorMessage = ex.Message
                };
            }
        }

        public async Task<List<string>> SelectStocks()
        {
            try
            {
                _logger.LogInformation("开始执行选股策略...");
                
                // 获取所有A股股票列表
                var allStocks = await _stockDataService.GetAllStocks();
                
                // 获取股票基本面数据
                var stockFundamentals = await _stockDataService.GetStockFundamentals(allStocks);
                
                // 获取股票交易数据
                var stockTradingData = await _stockDataService.GetStockTradingData(allStocks);
                
                // 筛选符合条件的股票
                var selectedStocks = allStocks
                    .Where(stock => 
                    {
                        var fundamental = stockFundamentals[stock];
                        var tradingData = stockTradingData[stock];
                        
                        return fundamental.PB < MAX_PB && // 破净
                               tradingData.Volume > MIN_VOLUME && // 交易量大于1亿
                               fundamental.DividendYield > MIN_DIVIDEND_YIELD; // 股息率大于1%
                    })
                    .ToList();

                _logger.LogInformation($"选股完成，共选出 {selectedStocks.Count} 只股票");
                return selectedStocks;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "选股过程中发生错误");
                throw;
            }
        }

        private async Task<bool> ShouldSell(string stockCode, StockFundamental fundamental)
        {
            try
            {
                // 获取股票所属行业
                var industryCode = await _industryDataService.GetStockIndustry(stockCode);
                
                // 获取行业平均PB
                var industryAvgPB = await _industryDataService.GetIndustryAveragePB(industryCode);
                
                // 获取PE分位数
                var pePercentile = await _industryDataService.GetPEPercentile(stockCode);
                
                // 判断是否满足卖出条件
                bool pbCondition = fundamental.PB >= industryAvgPB; // PB回升至行业均值
                bool peCondition = pePercentile > PE_HIGH_PERCENTILE; // PE升至高位
                
                if (pbCondition)
                {
                    _logger.LogInformation($"股票 {stockCode} 满足PB卖出条件：当前PB {fundamental.PB} >= 行业均值 {industryAvgPB}");
                }
                
                if (peCondition)
                {
                    _logger.LogInformation($"股票 {stockCode} 满足PE卖出条件：当前PE分位数 {pePercentile} > {PE_HIGH_PERCENTILE}");
                }
                
                return pbCondition || peCondition;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, $"检查股票 {stockCode} 卖出条件时发生错误");
                return false;
            }
        }

        private async Task<bool> ShouldBuy(string stockCode, decimal currentPrice)
        {
            try
            {
                // 获取最后一次交易价格
                var lastTradePrice = await _tradeHistoryService.GetLastTradePrice(stockCode);
                
                if (lastTradePrice.HasValue)
                {
                    // 如果当前价格低于上次交易价格的50%，则买入
                    if (currentPrice <= lastTradePrice.Value * PRICE_DROP_THRESHOLD)
                    {
                        _logger.LogInformation($"股票 {stockCode} 当前价格 {currentPrice} 低于上次交易价格 {lastTradePrice.Value} 的50%，满足买入条件");
                        return true;
                    }
                    else
                    {
                        _logger.LogInformation($"股票 {stockCode} 当前价格 {currentPrice} 高于上次交易价格 {lastTradePrice.Value} 的50%，不满足买入条件");
                        return false;
                    }
                }
                
                // 如果没有历史交易记录，则按照原有选股标准判断
                return true;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, $"检查股票 {stockCode} 买入条件时发生错误");
                return false;
            }
        }

        private async Task BuyRepoAtClose()
        {
            try
            {
                // 获取当前时间
                var now = DateTime.Now;
                
                // 判断是否接近收盘时间（14:55）
                if (now.Hour == 14 && now.Minute >= 55)
                {
                    // 获取账户余额
                    var positions = await _positionService.GetPositions();
                    var totalValue = positions.Values.Sum(p => p.MarketValue);
                    var availableCash = positions.Values.Sum(p => p.AvailableCash);
                    
                    if (availableCash > 0)
                    {
                        // 获取国债逆回购利率
                        var repoRate = await _repoService.GetRepoRate(1);
                        _logger.LogInformation($"当前1日期国债逆回购利率为 {repoRate:P2}");
                        
                        // 购买国债逆回购
                        var result = await _repoService.BuyRepo(availableCash, 1);
                        
                        if (result.Success)
                        {
                            _logger.LogInformation($"成功购买1日期国债逆回购，金额 {availableCash} 元");
                        }
                        else
                        {
                            _logger.LogError($"购买国债逆回购失败：{result.ErrorMessage}");
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "购买国债逆回购时发生错误");
            }
        }

        public override void OnInit()
        {
            try
            {
                // 执行选股
                var selectedStocks = await SelectStocks();
                
                // 获取当前持仓
                var positions = await _positionService.GetPositions();
                
                // 获取持仓股票的基本面数据
                var stockFundamentals = await _stockDataService.GetStockFundamentals(positions.Keys.ToList());
                
                // 检查持仓股票是否需要卖出
                foreach (var position in positions.Values)
                {
                    if (!await CanTrade(position.StockCode))
                    {
                        continue;
                    }
                    
                    var fundamental = stockFundamentals[position.StockCode];
                    
                    if (await ShouldSell(position.StockCode, fundamental))
                    {
                        _logger.LogInformation($"开始卖出股票 {position.StockCode}，持仓数量 {position.Quantity} 股");
                        
                        // 计算交易成本
                        var cost = await _tradeCostService.CalculateSellCost(
                            position.StockCode,
                            fundamental.PE, // 使用当前价格
                            position.Quantity
                        );
                        
                        var tradeResult = await _positionService.Sell(position.StockCode);
                        
                        if (tradeResult.Success)
                        {
                            // 记录交易
                            await _tradeHistoryService.RecordTrade(
                                position.StockCode,
                                tradeResult.Price,
                                tradeResult.Quantity,
                                false
                            );
                            
                            _logger.LogInformation($"股票 {position.StockCode} 卖出成功，" +
                                $"成交数量 {tradeResult.Quantity} 股，成交价格 {tradeResult.Price} 元，" +
                                $"成本 {cost.TotalCost} 元（含佣金 {cost.Commission} 元，" +
                                $"印花税 {cost.StampDuty} 元，过户费 {cost.TransferFee} 元）");
                            
                            // 更新交易记录
                            _lastTradeTime[position.StockCode] = DateTime.Today;
                            
                            // 卖出后立即执行选股购买策略
                            await ExecuteBuyStrategy(selectedStocks, positions);
                        }
                        else
                        {
                            _logger.LogError($"股票 {position.StockCode} 卖出失败：{tradeResult.ErrorMessage}");
                        }
                    }
                }
                
                // 对未持仓的股票进行买入
                await ExecuteBuyStrategy(selectedStocks, positions);
                
                // 收盘时购买国债逆回购
                await BuyRepoAtClose();
                
                _logger.LogInformation("策略执行完成");
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "策略执行过程中发生错误");
                throw;
            }
        }

        private async Task ExecuteBuyStrategy(List<string> selectedStocks, Dictionary<string, Position> positions)
        {
            foreach (var stockCode in selectedStocks)
            {
                if (!positions.ContainsKey(stockCode))
                {
                    if (!await CanTrade(stockCode))
                    {
                        continue;
                    }
                    
                    // 获取当前价格
                    var price = await _stockDataService.GetStockTradingData(new List<string> { stockCode });
                    var currentPrice = price[stockCode].Close;
                    
                    // 检查是否满足买入条件
                    if (!await ShouldBuy(stockCode, currentPrice))
                    {
                        continue;
                    }
                    
                    if (!await CheckPositionLimit(stockCode, BUY_AMOUNT))
                    {
                        continue;
                    }
                    
                    _logger.LogInformation($"开始分批买入股票 {stockCode}，总买入金额 {BUY_AMOUNT} 元");
                    
                    var tradeResult = await BuyWithBatches(stockCode, BUY_AMOUNT);
                    
                    if (tradeResult.Success)
                    {
                        // 记录交易
                        await _tradeHistoryService.RecordTrade(
                            stockCode,
                            tradeResult.Price,
                            tradeResult.Quantity,
                            true
                        );
                    }
                    else
                    {
                        _logger.LogError($"股票 {stockCode} 买入失败：{tradeResult.ErrorMessage}");
                    }
                }
                else
                {
                    _logger.LogInformation($"股票 {stockCode} 已持仓，跳过买入");
                }
            }
        }
    }
} 