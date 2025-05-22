using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging;
using Quant.Strategy.Services;

namespace Quant.Strategy.Services.Impl
{
    /// <summary>
    /// 掘金量化交易成本优化服务实现
    /// </summary>
    public class JQTradeCostOptimizationService : ITradeCostOptimizationService
    {
        private readonly ILogger<JQTradeCostOptimizationService> _logger;
        private readonly JQDataClient _jqClient;
        private readonly ITradeCostService _tradeCostService;
        private readonly ITradeHistoryService _tradeHistoryService;

        // 交易成本参数
        private const decimal MIN_COMMISSION = 5m; // 最低佣金
        private const decimal COMMISSION_RATE = 0.0003m; // 佣金费率
        private const decimal STAMP_DUTY_RATE = 0.001m; // 印花税税率
        private const decimal TRANSFER_FEE_RATE = 0.00002m; // 过户费费率
        private const decimal SLIPPAGE_THRESHOLD = 0.001m; // 滑点阈值

        public JQTradeCostOptimizationService(
            ILogger<JQTradeCostOptimizationService> logger,
            JQDataClient jqClient,
            ITradeCostService tradeCostService,
            ITradeHistoryService tradeHistoryService)
        {
            _logger = logger;
            _jqClient = jqClient;
            _tradeCostService = tradeCostService;
            _tradeHistoryService = tradeHistoryService;
        }

        public async Task<TradeTiming> GetOptimalTradeTiming(string stockCode, decimal targetPrice, int quantity)
        {
            try
            {
                // 获取历史交易数据
                var tradeHistory = await _tradeHistoryService.GetTradeHistory(stockCode);
                var recentTrades = tradeHistory
                    .Where(t => t.TradeTime >= DateTime.Now.AddDays(-30))
                    .OrderByDescending(t => t.TradeTime)
                    .ToList();

                // 获取当前市场数据
                var currentPrice = await _jqClient.GetPrice(stockCode);
                var volume = await _jqClient.GetVolume(stockCode);
                var bidAskSpread = await _jqClient.GetBidAskSpread(stockCode);

                // 分析交易时机
                var timing = new TradeTiming
                {
                    SuggestedTime = DateTime.Now,
                    SuggestedPrice = currentPrice,
                    ExpectedCostSaving = 0,
                    Score = 0
                };

                // 计算交易时机评分
                var priceScore = CalculatePriceScore(currentPrice, targetPrice);
                var volumeScore = CalculateVolumeScore(volume, quantity);
                var spreadScore = CalculateSpreadScore(bidAskSpread);
                var timeScore = CalculateTimeScore(recentTrades);

                timing.Score = (priceScore + volumeScore + spreadScore + timeScore) / 4;
                timing.ExpectedCostSaving = CalculateExpectedCostSaving(stockCode, currentPrice, quantity);

                _logger.LogInformation($"获取股票 {stockCode} 最优交易时机：" +
                    $"建议时间 {timing.SuggestedTime}，" +
                    $"建议价格 {timing.SuggestedPrice}，" +
                    $"预期成本节省 {timing.ExpectedCostSaving:N2}，" +
                    $"评分 {timing.Score:F2}");

                return timing;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, $"获取股票 {stockCode} 最优交易时机失败");
                throw;
            }
        }

        public async Task<int> GetOptimalTradeQuantity(string stockCode, decimal price, decimal amount)
        {
            try
            {
                // 计算理论数量
                var theoreticalQuantity = (int)(amount / price);
                if (theoreticalQuantity <= 0)
                {
                    return 0;
                }

                // 获取当前市场数据
                var volume = await _jqClient.GetVolume(stockCode);
                var bidAskSpread = await _jqClient.GetBidAskSpread(stockCode);

                // 根据市场情况调整数量
                var optimalQuantity = theoreticalQuantity;

                // 考虑成交量限制
                if (volume > 0)
                {
                    optimalQuantity = Math.Min(optimalQuantity, (int)(volume * 0.1m)); // 不超过当日成交量的10%
                }

                // 考虑价格滑点
                if (bidAskSpread > SLIPPAGE_THRESHOLD)
                {
                    optimalQuantity = (int)(optimalQuantity * 0.8m); // 滑点较大时减少交易数量
                }

                // 确保数量是100的整数倍
                optimalQuantity = (optimalQuantity / 100) * 100;

                _logger.LogInformation($"获取股票 {stockCode} 最优交易数量：" +
                    $"理论数量 {theoreticalQuantity}，" +
                    $"最优数量 {optimalQuantity}，" +
                    $"价格 {price}，" +
                    $"金额 {amount:N2}");

                return optimalQuantity;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, $"获取股票 {stockCode} 最优交易数量失败");
                throw;
            }
        }

        public async Task<decimal> GetOptimalTradePrice(string stockCode, int quantity)
        {
            try
            {
                // 获取当前市场数据
                var currentPrice = await _jqClient.GetPrice(stockCode);
                var bidPrice = await _jqClient.GetBidPrice(stockCode);
                var askPrice = await _jqClient.GetAskPrice(stockCode);
                var volume = await _jqClient.GetVolume(stockCode);

                // 计算最优价格
                var optimalPrice = currentPrice;

                // 根据买卖方向调整价格
                if (quantity > 0) // 买入
                {
                    optimalPrice = Math.Min(askPrice, currentPrice * 1.001m); // 不超过卖一价和当前价格的0.1%
                }
                else // 卖出
                {
                    optimalPrice = Math.Max(bidPrice, currentPrice * 0.999m); // 不低于买一价和当前价格的0.1%
                }

                // 考虑成交量影响
                if (volume > 0)
                {
                    var impact = Math.Abs(quantity) / (decimal)volume;
                    if (impact > 0.1m) // 如果交易量超过当日成交量的10%
                    {
                        optimalPrice = quantity > 0
                            ? optimalPrice * 1.002m // 买入时适当提高价格
                            : optimalPrice * 0.998m; // 卖出时适当降低价格
                    }
                }

                _logger.LogInformation($"获取股票 {stockCode} 最优交易价格：" +
                    $"当前价格 {currentPrice}，" +
                    $"最优价格 {optimalPrice}，" +
                    $"数量 {quantity}");

                return optimalPrice;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, $"获取股票 {stockCode} 最优交易价格失败");
                throw;
            }
        }

        public async Task<TradeCostAnalysis> GetTradeCostAnalysis(string stockCode, decimal price, int quantity)
        {
            try
            {
                var analysis = new TradeCostAnalysis
                {
                    OptimizationSuggestions = new List<string>()
                };

                // 计算基本交易成本
                var tradeCost = await _tradeCostService.CalculateBuyCost(stockCode, price, quantity);
                analysis.Commission = tradeCost.Commission;
                analysis.StampDuty = tradeCost.StampDuty;
                analysis.TransferFee = tradeCost.TransferFee;

                // 计算滑点成本
                var currentPrice = await _jqClient.GetPrice(stockCode);
                var slippage = Math.Abs(price - currentPrice) / currentPrice;
                analysis.SlippageCost = price * quantity * slippage;

                // 计算总成本
                analysis.TotalCost = analysis.Commission + analysis.StampDuty + 
                    analysis.TransferFee + analysis.SlippageCost;

                // 生成优化建议
                if (analysis.Commission < MIN_COMMISSION)
                {
                    analysis.OptimizationSuggestions.Add(
                        $"当前佣金 {analysis.Commission:N2} 元低于最低佣金 {MIN_COMMISSION} 元，" +
                        $"建议增加交易数量以提高资金利用效率");
                }

                if (slippage > SLIPPAGE_THRESHOLD)
                {
                    analysis.OptimizationSuggestions.Add(
                        $"当前滑点 {slippage:P2} 超过阈值 {SLIPPAGE_THRESHOLD:P2}，" +
                        $"建议调整交易价格或等待更好的交易时机");
                }

                var costRatio = analysis.TotalCost / (price * quantity);
                if (costRatio > 0.003m) // 总成本超过0.3%
                {
                    analysis.OptimizationSuggestions.Add(
                        $"当前总成本率 {costRatio:P2} 较高，" +
                        $"建议优化交易策略以降低交易成本");
                }

                _logger.LogInformation($"获取股票 {stockCode} 交易成本分析：" +
                    $"总成本 {analysis.TotalCost:N2} 元，" +
                    $"佣金 {analysis.Commission:N2} 元，" +
                    $"印花税 {analysis.StampDuty:N2} 元，" +
                    $"过户费 {analysis.TransferFee:N2} 元，" +
                    $"滑点成本 {analysis.SlippageCost:N2} 元，" +
                    $"优化建议数量 {analysis.OptimizationSuggestions.Count}");

                return analysis;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, $"获取股票 {stockCode} 交易成本分析失败");
                throw;
            }
        }

        private decimal CalculatePriceScore(decimal currentPrice, decimal targetPrice)
        {
            var priceDiff = Math.Abs(currentPrice - targetPrice) / targetPrice;
            return Math.Max(0, 1 - priceDiff * 10); // 价格差异越大，分数越低
        }

        private decimal CalculateVolumeScore(long volume, int quantity)
        {
            if (volume <= 0)
            {
                return 0;
            }

            var volumeRatio = (decimal)quantity / volume;
            return Math.Max(0, 1 - volumeRatio * 5); // 交易量占比越大，分数越低
        }

        private decimal CalculateSpreadScore(decimal spread)
        {
            return Math.Max(0, 1 - spread * 100); // 价差越大，分数越低
        }

        private decimal CalculateTimeScore(List<TradeRecord> recentTrades)
        {
            if (!recentTrades.Any())
            {
                return 1;
            }

            var lastTrade = recentTrades.First();
            var hoursSinceLastTrade = (DateTime.Now - lastTrade.TradeTime).TotalHours;
            return Math.Min(1, hoursSinceLastTrade / 24); // 距离上次交易时间越长，分数越高
        }

        private decimal CalculateExpectedCostSaving(string stockCode, decimal price, int quantity)
        {
            // 计算当前交易成本
            var currentCost = price * quantity * (COMMISSION_RATE + STAMP_DUTY_RATE + TRANSFER_FEE_RATE);

            // 计算优化后的交易成本（假设可以节省20%）
            var optimizedCost = currentCost * 0.8m;

            return currentCost - optimizedCost;
        }
    }
} 