using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using DataEventDriven.Services;
using Microsoft.Extensions.Logging;

namespace Quant.Strategy.Services.Impl
{
    /// <summary>
    /// 掘金量化交易分析服务实现
    /// </summary>
    public class JQTradeAnalysisService : ITradeAnalysisService
    {
        private readonly ILogger<JQTradeAnalysisService> _logger;
        private readonly JQDataClient _jqClient;
        private readonly ITradeHistoryService _tradeHistoryService;
        private readonly IPositionService _positionService;
        private readonly IIndustryDataService _industryDataService;

        public JQTradeAnalysisService(
            ILogger<JQTradeAnalysisService> logger,
            JQDataClient jqClient,
            ITradeHistoryService tradeHistoryService,
            IPositionService positionService,
            IIndustryDataService industryDataService)
        {
            _logger = logger;
            _jqClient = jqClient;
            _tradeHistoryService = tradeHistoryService;
            _positionService = positionService;
            _industryDataService = industryDataService;
        }

        public async Task<StockTradeStats> GetStockTradeStats(string stockCode)
        {
            try
            {
                var tradeHistory = await _tradeHistoryService.GetTradeHistory(stockCode);
                if (!tradeHistory.Any())
                {
                    return new StockTradeStats { StockCode = stockCode };
                }

                var stats = new StockTradeStats
                {
                    StockCode = stockCode,
                    TotalTrades = tradeHistory.Count,
                    BuyCount = tradeHistory.Count(t => t.IsBuy),
                    SellCount = tradeHistory.Count(t => !t.IsBuy)
                };

                // 计算持仓时间和收益率
                var holdingPeriods = new List<(DateTime BuyTime, DateTime SellTime, decimal BuyPrice, decimal SellPrice)>();
                var currentBuy = default((DateTime Time, decimal Price)?);

                foreach (var trade in tradeHistory.OrderBy(t => t.TradeTime))
                {
                    if (trade.IsBuy)
                    {
                        currentBuy = (trade.TradeTime, trade.Price);
                    }
                    else if (currentBuy.HasValue)
                    {
                        holdingPeriods.Add((
                            currentBuy.Value.Time,
                            trade.TradeTime,
                            currentBuy.Value.Price,
                            trade.Price
                        ));
                        currentBuy = null;
                    }
                }

                if (holdingPeriods.Any())
                {
                    stats.AvgHoldingDays = holdingPeriods.Average(p => (p.SellTime - p.BuyTime).TotalDays);
                    
                    var returns = holdingPeriods.Select(p => (p.SellPrice - p.BuyPrice) / p.BuyPrice).ToList();
                    stats.AvgReturn = returns.Average();
                    stats.MaxReturn = returns.Max();
                    stats.MaxLoss = returns.Min();
                    stats.WinRate = (decimal)returns.Count(r => r > 0) / returns.Count;
                }

                _logger.LogInformation($"获取股票 {stockCode} 交易统计信息：" +
                    $"总交易 {stats.TotalTrades} 次，" +
                    $"买入 {stats.BuyCount} 次，" +
                    $"卖出 {stats.SellCount} 次，" +
                    $"平均持仓 {stats.AvgHoldingDays:F1} 天，" +
                    $"平均收益率 {stats.AvgReturn:P2}，" +
                    $"胜率 {stats.WinRate:P2}");

                return stats;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, $"获取股票 {stockCode} 交易统计信息失败");
                throw;
            }
        }

        public async Task<AccountTradeStats> GetAccountTradeStats()
        {
            try
            {
                var positions = await _positionService.GetPositions();
                var totalValue = positions.Values.Sum(p => p.MarketValue);
                var cashBalance = positions.Values.Sum(p => p.AvailableCash);
                var positionValue = totalValue - cashBalance;

                // 获取历史净值数据
                var netValues = await _jqClient.GetNetValueHistory();
                if (!netValues.Any())
                {
                    return new AccountTradeStats
                    {
                        TotalAssets = totalValue,
                        CashBalance = cashBalance,
                        PositionValue = positionValue
                    };
                }

                var stats = new AccountTradeStats
                {
                    TotalAssets = totalValue,
                    CashBalance = cashBalance,
                    PositionValue = positionValue
                };

                // 计算收益率
                var firstValue = netValues.First();
                var lastValue = netValues.Last();
                stats.TotalReturn = (lastValue - firstValue) / firstValue;

                // 计算年化收益率
                var days = (lastValue.Date - firstValue.Date).TotalDays;
                stats.AnnualReturn = (decimal)Math.Pow(1 + (double)stats.TotalReturn, 365 / days) - 1;

                // 计算最大回撤
                var maxDrawdown = 0m;
                var peak = firstValue.Value;
                foreach (var value in netValues)
                {
                    if (value.Value > peak)
                    {
                        peak = value.Value;
                    }
                    else
                    {
                        var drawdown = (peak - value.Value) / peak;
                        maxDrawdown = Math.Max(maxDrawdown, drawdown);
                    }
                }
                stats.MaxDrawdown = maxDrawdown;

                // 计算夏普比率
                var returns = netValues
                    .Skip(1)
                    .Zip(netValues, (curr, prev) => (curr.Value - prev.Value) / prev.Value)
                    .ToList();
                var avgReturn = returns.Average();
                var stdDev = (decimal)Math.Sqrt(returns.Sum(r => Math.Pow((double)(r - avgReturn), 2)) / returns.Count);
                stats.SharpeRatio = stdDev == 0 ? 0 : avgReturn / stdDev * (decimal)Math.Sqrt(252);

                _logger.LogInformation($"获取账户交易统计信息：" +
                    $"总资产 {stats.TotalAssets:N2} 元，" +
                    $"现金 {stats.CashBalance:N2} 元，" +
                    $"持仓 {stats.PositionValue:N2} 元，" +
                    $"总收益率 {stats.TotalReturn:P2}，" +
                    $"年化收益率 {stats.AnnualReturn:P2}，" +
                    $"最大回撤 {stats.MaxDrawdown:P2}，" +
                    $"夏普比率 {stats.SharpeRatio:F2}");

                return stats;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "获取账户交易统计信息失败");
                throw;
            }
        }

        public async Task<IndustryTradeStats> GetIndustryTradeStats(string industryCode)
        {
            try
            {
                var positions = await _positionService.GetPositions();
                var totalValue = positions.Values.Sum(p => p.MarketValue);

                // 获取行业内的股票
                var industryStocks = positions.Keys
                    .Where(async k => await _industryDataService.GetStockIndustry(k) == industryCode)
                    .ToList();

                var stats = new IndustryTradeStats
                {
                    IndustryCode = industryCode,
                    PositionValue = positions
                        .Where(p => industryStocks.Contains(p.Key))
                        .Sum(p => p.Value.MarketValue)
                };

                stats.PositionRatio = stats.PositionValue / totalValue;

                // 计算行业收益率
                var industryReturns = new List<decimal>();
                foreach (var stockCode in industryStocks)
                {
                    var stockStats = await GetStockTradeStats(stockCode);
                    if (stockStats.TotalTrades > 0)
                    {
                        industryReturns.Add(stockStats.AvgReturn);
                    }
                }

                if (industryReturns.Any())
                {
                    stats.Return = industryReturns.Average();
                    stats.Contribution = stats.Return * stats.PositionRatio;
                }

                _logger.LogInformation($"获取行业 {industryCode} 交易统计信息：" +
                    $"持仓市值 {stats.PositionValue:N2} 元，" +
                    $"持仓比例 {stats.PositionRatio:P2}，" +
                    $"收益率 {stats.Return:P2}，" +
                    $"贡献度 {stats.Contribution:P2}");

                return stats;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, $"获取行业 {industryCode} 交易统计信息失败");
                throw;
            }
        }
    }
} 