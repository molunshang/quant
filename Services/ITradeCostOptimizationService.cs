using System;
using System.Collections.Generic;
using System.Threading.Tasks;

namespace Quant.Strategy.Services
{
    /// <summary>
    /// 交易成本优化服务接口
    /// </summary>
    public interface ITradeCostOptimizationService
    {
        /// <summary>
        /// 获取最优交易时机
        /// </summary>
        Task<TradeTiming> GetOptimalTradeTiming(string stockCode, decimal targetPrice, int quantity);

        /// <summary>
        /// 获取最优交易数量
        /// </summary>
        Task<int> GetOptimalTradeQuantity(string stockCode, decimal price, decimal amount);

        /// <summary>
        /// 获取最优交易价格
        /// </summary>
        Task<decimal> GetOptimalTradePrice(string stockCode, int quantity);

        /// <summary>
        /// 获取交易成本分析
        /// </summary>
        Task<TradeCostAnalysis> GetTradeCostAnalysis(string stockCode, decimal price, int quantity);
    }

    /// <summary>
    /// 交易时机
    /// </summary>
    public class TradeTiming
    {
        /// <summary>
        /// 建议交易时间
        /// </summary>
        public DateTime SuggestedTime { get; set; }

        /// <summary>
        /// 建议交易价格
        /// </summary>
        public decimal SuggestedPrice { get; set; }

        /// <summary>
        /// 预期成本节省
        /// </summary>
        public decimal ExpectedCostSaving { get; set; }

        /// <summary>
        /// 交易时机评分
        /// </summary>
        public decimal Score { get; set; }
    }

    /// <summary>
    /// 交易成本分析
    /// </summary>
    public class TradeCostAnalysis
    {
        /// <summary>
        /// 总成本
        /// </summary>
        public decimal TotalCost { get; set; }

        /// <summary>
        /// 佣金
        /// </summary>
        public decimal Commission { get; set; }

        /// <summary>
        /// 印花税
        /// </summary>
        public decimal StampDuty { get; set; }

        /// <summary>
        /// 过户费
        /// </summary>
        public decimal TransferFee { get; set; }

        /// <summary>
        /// 滑点成本
        /// </summary>
        public decimal SlippageCost { get; set; }

        /// <summary>
        /// 成本优化建议
        /// </summary>
        public List<string> OptimizationSuggestions { get; set; }
    }
} 