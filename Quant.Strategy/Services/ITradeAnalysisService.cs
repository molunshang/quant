using System;
using System.Collections.Generic;
using System.Threading.Tasks;

namespace Quant.Strategy.Services
{
    /// <summary>
    /// 交易分析服务接口
    /// </summary>
    public interface ITradeAnalysisService
    {
        /// <summary>
        /// 获取股票交易统计信息
        /// </summary>
        Task<StockTradeStats> GetStockTradeStats(string stockCode);

        /// <summary>
        /// 获取账户交易统计信息
        /// </summary>
        Task<AccountTradeStats> GetAccountTradeStats();

        /// <summary>
        /// 获取行业交易统计信息
        /// </summary>
        Task<IndustryTradeStats> GetIndustryTradeStats(string industryCode);
    }

    /// <summary>
    /// 股票交易统计信息
    /// </summary>
    public class StockTradeStats
    {
        /// <summary>
        /// 股票代码
        /// </summary>
        public string StockCode { get; set; }

        /// <summary>
        /// 总交易次数
        /// </summary>
        public int TotalTrades { get; set; }

        /// <summary>
        /// 买入次数
        /// </summary>
        public int BuyCount { get; set; }

        /// <summary>
        /// 卖出次数
        /// </summary>
        public int SellCount { get; set; }

        /// <summary>
        /// 平均持仓时间（天）
        /// </summary>
        public double AvgHoldingDays { get; set; }

        /// <summary>
        /// 平均收益率
        /// </summary>
        public decimal AvgReturn { get; set; }

        /// <summary>
        /// 最大收益率
        /// </summary>
        public decimal MaxReturn { get; set; }

        /// <summary>
        /// 最大亏损率
        /// </summary>
        public decimal MaxLoss { get; set; }

        /// <summary>
        /// 胜率
        /// </summary>
        public decimal WinRate { get; set; }
    }

    /// <summary>
    /// 账户交易统计信息
    /// </summary>
    public class AccountTradeStats
    {
        /// <summary>
        /// 总资产
        /// </summary>
        public decimal TotalAssets { get; set; }

        /// <summary>
        /// 现金余额
        /// </summary>
        public decimal CashBalance { get; set; }

        /// <summary>
        /// 持仓市值
        /// </summary>
        public decimal PositionValue { get; set; }

        /// <summary>
        /// 总收益率
        /// </summary>
        public decimal TotalReturn { get; set; }

        /// <summary>
        /// 年化收益率
        /// </summary>
        public decimal AnnualReturn { get; set; }

        /// <summary>
        /// 最大回撤
        /// </summary>
        public decimal MaxDrawdown { get; set; }

        /// <summary>
        /// 夏普比率
        /// </summary>
        public decimal SharpeRatio { get; set; }
    }

    /// <summary>
    /// 行业交易统计信息
    /// </summary>
    public class IndustryTradeStats
    {
        /// <summary>
        /// 行业代码
        /// </summary>
        public string IndustryCode { get; set; }

        /// <summary>
        /// 持仓市值
        /// </summary>
        public decimal PositionValue { get; set; }

        /// <summary>
        /// 持仓比例
        /// </summary>
        public decimal PositionRatio { get; set; }

        /// <summary>
        /// 行业收益率
        /// </summary>
        public decimal Return { get; set; }

        /// <summary>
        /// 行业贡献度
        /// </summary>
        public decimal Contribution { get; set; }
    }
} 