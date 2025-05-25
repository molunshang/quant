using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using DataEventDriven.Services;
using Microsoft.Extensions.Logging;

namespace Quant.Strategy.Services.Impl
{
    /// <summary>
    /// 掘金量化风险控制服务实现
    /// </summary>
    public class JQRiskControlService : IRiskControlService
    {
        private readonly ILogger<JQRiskControlService> _logger;
        private readonly JQDataClient _jqClient;
        private readonly IPositionService _positionService;
        private readonly IIndustryDataService _industryDataService;
        private readonly ITradeHistoryService _tradeHistoryService;

        // 风险控制参数
        private const decimal MAX_STOCK_POSITION_RATIO = 0.1m; // 单个股票最大持仓比例
        private const decimal MAX_INDUSTRY_POSITION_RATIO = 0.3m; // 单个行业最大持仓比例
        private const decimal STOP_LOSS_THRESHOLD = 0.1m; // 止损阈值
        private const decimal TAKE_PROFIT_THRESHOLD = 0.2m; // 止盈阈值
        private const int MIN_TRADE_INTERVAL_DAYS = 5; // 最小交易间隔天数

        public JQRiskControlService(
            ILogger<JQRiskControlService> logger,
            JQDataClient jqClient,
            IPositionService positionService,
            IIndustryDataService industryDataService,
            ITradeHistoryService tradeHistoryService)
        {
            _logger = logger;
            _jqClient = jqClient;
            _positionService = positionService;
            _industryDataService = industryDataService;
            _tradeHistoryService = tradeHistoryService;
        }

        public async Task<bool> CanTrade(string stockCode)
        {
            try
            {
                // 获取最近一次交易时间
                var tradeHistory = await _tradeHistoryService.GetTradeHistory(stockCode);
                if (!tradeHistory.Any())
                {
                    return true;
                }

                var lastTrade = tradeHistory.First();
                var daysSinceLastTrade = (DateTime.Now - lastTrade.TradeTime).TotalDays;

                if (daysSinceLastTrade < MIN_TRADE_INTERVAL_DAYS)
                {
                    _logger.LogWarning($"股票 {stockCode} 距离上次交易时间不足 {MIN_TRADE_INTERVAL_DAYS} 天");
                    return false;
                }

                return true;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, $"检查股票 {stockCode} 是否可以交易失败");
                return false;
            }
        }

        public async Task<bool> CheckStopLoss(string stockCode, decimal currentPrice)
        {
            try
            {
                var lastTradePrice = await _tradeHistoryService.GetLastTradePrice(stockCode);
                if (!lastTradePrice.HasValue)
                {
                    return false;
                }

                var priceChange = (currentPrice - lastTradePrice.Value) / lastTradePrice.Value;
                var shouldStopLoss = priceChange <= -STOP_LOSS_THRESHOLD;

                if (shouldStopLoss)
                {
                    _logger.LogWarning($"股票 {stockCode} 触发止损条件：" +
                        $"当前价格 {currentPrice}，上次交易价格 {lastTradePrice.Value}，" +
                        $"跌幅 {priceChange:P2}");
                }

                return shouldStopLoss;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, $"检查股票 {stockCode} 止损条件失败");
                return false;
            }
        }

        public async Task<bool> CheckTakeProfit(string stockCode, decimal currentPrice)
        {
            try
            {
                var lastTradePrice = await _tradeHistoryService.GetLastTradePrice(stockCode);
                if (!lastTradePrice.HasValue)
                {
                    return false;
                }

                var priceChange = (currentPrice - lastTradePrice.Value) / lastTradePrice.Value;
                var shouldTakeProfit = priceChange >= TAKE_PROFIT_THRESHOLD;

                if (shouldTakeProfit)
                {
                    _logger.LogInformation($"股票 {stockCode} 触发止盈条件：" +
                        $"当前价格 {currentPrice}，上次交易价格 {lastTradePrice.Value}，" +
                        $"涨幅 {priceChange:P2}");
                }

                return shouldTakeProfit;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, $"检查股票 {stockCode} 止盈条件失败");
                return false;
            }
        }

        public async Task<bool> CheckPositionLimit(string stockCode, decimal amount)
        {
            try
            {
                var positions = await _positionService.GetPositions();
                var totalValue = positions.Values.Sum(p => p.MarketValue);
                var currentPosition = positions.GetValueOrDefault(stockCode)?.MarketValue ?? 0;
                var newPosition = currentPosition + amount;
                var positionRatio = newPosition / totalValue;

                if (positionRatio > MAX_STOCK_POSITION_RATIO)
                {
                    _logger.LogWarning($"股票 {stockCode} 超过最大持仓比例：" +
                        $"当前持仓 {currentPosition:N2}，新增持仓 {amount:N2}，" +
                        $"总资产 {totalValue:N2}，持仓比例 {positionRatio:P2}");
                    return false;
                }

                return true;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, $"检查股票 {stockCode} 持仓限制失败");
                return false;
            }
        }

        public async Task<bool> CheckIndustryLimit(string industryCode, decimal amount)
        {
            try
            {
                var positions = await _positionService.GetPositions();
                var totalValue = positions.Values.Sum(p => p.MarketValue);

                // 获取行业内的股票
                var industryStocks = positions.Keys
                    .Where(async k => await _industryDataService.GetStockIndustry(k) == industryCode)
                    .ToList();

                var currentIndustryValue = positions
                    .Where(p => industryStocks.Contains(p.Key))
                    .Sum(p => p.Value.MarketValue);

                var newIndustryValue = currentIndustryValue + amount;
                var industryRatio = newIndustryValue / totalValue;

                if (industryRatio > MAX_INDUSTRY_POSITION_RATIO)
                {
                    _logger.LogWarning($"行业 {industryCode} 超过最大持仓比例：" +
                        $"当前持仓 {currentIndustryValue:N2}，新增持仓 {amount:N2}，" +
                        $"总资产 {totalValue:N2}，持仓比例 {industryRatio:P2}");
                    return false;
                }

                return true;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, $"检查行业 {industryCode} 持仓限制失败");
                return false;
            }
        }

        public async Task<RiskMetrics> GetRiskMetrics()
        {
            try
            {
                var metrics = new RiskMetrics();

                // 获取历史净值数据
                var netValues = await _jqClient.GetNetValueHistory();
                if (!netValues.Any())
                {
                    return metrics;
                }

                // 计算收益率序列
                var returns = netValues
                    .Skip(1)
                    .Zip(netValues, (curr, prev) => (curr.Value - prev.Value) / prev.Value)
                    .ToList();

                if (!returns.Any())
                {
                    return metrics;
                }

                // 计算基准收益率序列（以沪深300为例）
                var benchmarkReturns = await _jqClient.GetBenchmarkReturns("000300.SH", netValues.Count);
                
                // 计算各项风险指标
                metrics.MaxDrawdown = CalculateMaxDrawdown(netValues);
                metrics.Volatility = CalculateVolatility(returns);
                metrics.SharpeRatio = CalculateSharpeRatio(returns);
                metrics.Beta = CalculateBeta(returns, benchmarkReturns);
                metrics.Alpha = CalculateAlpha(returns, benchmarkReturns, metrics.Beta);
                metrics.InformationRatio = CalculateInformationRatio(returns, benchmarkReturns);
                metrics.SortinoRatio = CalculateSortinoRatio(returns);
                metrics.CalmarRatio = CalculateCalmarRatio(returns, metrics.MaxDrawdown);

                _logger.LogInformation($"计算风险指标完成：" +
                    $"最大回撤 {metrics.MaxDrawdown:P2}，" +
                    $"波动率 {metrics.Volatility:P2}，" +
                    $"夏普比率 {metrics.SharpeRatio:F2}，" +
                    $"贝塔系数 {metrics.Beta:F2}，" +
                    $"阿尔法系数 {metrics.Alpha:P2}，" +
                    $"信息比率 {metrics.InformationRatio:F2}，" +
                    $"索提诺比率 {metrics.SortinoRatio:F2}，" +
                    $"卡玛比率 {metrics.CalmarRatio:F2}");

                return metrics;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "计算风险指标失败");
                throw;
            }
        }

        private decimal CalculateMaxDrawdown(List<(DateTime Date, decimal Value)> netValues)
        {
            var maxDrawdown = 0m;
            var peak = netValues.First().Value;

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

            return maxDrawdown;
        }

        private decimal CalculateVolatility(List<decimal> returns)
        {
            var avgReturn = returns.Average();
            var variance = returns.Sum(r => Math.Pow((double)(r - avgReturn), 2)) / returns.Count;
            return (decimal)Math.Sqrt(variance * 252); // 年化波动率
        }

        private decimal CalculateSharpeRatio(List<decimal> returns)
        {
            var avgReturn = returns.Average();
            var volatility = CalculateVolatility(returns);
            var riskFreeRate = 0.03m; // 假设无风险利率为3%
            return volatility == 0 ? 0 : (avgReturn * 252 - riskFreeRate) / volatility;
        }

        private decimal CalculateBeta(List<decimal> returns, List<decimal> benchmarkReturns)
        {
            if (returns.Count != benchmarkReturns.Count)
            {
                return 0;
            }

            var covariance = returns.Zip(benchmarkReturns, (r, b) => (r - returns.Average()) * (b - benchmarkReturns.Average())).Sum();
            var benchmarkVariance = benchmarkReturns.Sum(b => Math.Pow((double)(b - benchmarkReturns.Average()), 2));

            return benchmarkVariance == 0 ? 0 : (decimal)(covariance / benchmarkVariance);
        }

        private decimal CalculateAlpha(List<decimal> returns, List<decimal> benchmarkReturns, decimal beta)
        {
            var portfolioReturn = returns.Average() * 252;
            var benchmarkReturn = benchmarkReturns.Average() * 252;
            var riskFreeRate = 0.03m; // 假设无风险利率为3%

            return portfolioReturn - (riskFreeRate + beta * (benchmarkReturn - riskFreeRate));
        }

        private decimal CalculateInformationRatio(List<decimal> returns, List<decimal> benchmarkReturns)
        {
            if (returns.Count != benchmarkReturns.Count)
            {
                return 0;
            }

            var excessReturns = returns.Zip(benchmarkReturns, (r, b) => r - b).ToList();
            var trackingError = CalculateVolatility(excessReturns);
            var excessReturn = excessReturns.Average() * 252;

            return trackingError == 0 ? 0 : excessReturn / trackingError;
        }

        private decimal CalculateSortinoRatio(List<decimal> returns)
        {
            var avgReturn = returns.Average() * 252;
            var riskFreeRate = 0.03m; // 假设无风险利率为3%

            var downsideReturns = returns.Where(r => r < 0).ToList();
            if (!downsideReturns.Any())
            {
                return 0;
            }

            var downsideDeviation = (decimal)Math.Sqrt(downsideReturns.Sum(r => Math.Pow((double)r, 2)) / returns.Count * 252);
            return downsideDeviation == 0 ? 0 : (avgReturn - riskFreeRate) / downsideDeviation;
        }

        private decimal CalculateCalmarRatio(List<decimal> returns, decimal maxDrawdown)
        {
            var annualReturn = returns.Average() * 252;
            return maxDrawdown == 0 ? 0 : annualReturn / maxDrawdown;
        }
    }
} 