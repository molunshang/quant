using System;
using System.Collections.Generic;
using System.Threading.Tasks;

namespace Quant.Strategy.Services
{
    /// <summary>
    /// 风险控制服务接口
    /// </summary>
    public interface IRiskControlService
    {
        /// <summary>
        /// 检查是否可以交易
        /// </summary>
        Task<bool> CanTrade(string stockCode);

        /// <summary>
        /// 检查止损条件
        /// </summary>
        Task<bool> CheckStopLoss(string stockCode, decimal currentPrice);

        /// <summary>
        /// 检查止盈条件
        /// </summary>
        Task<bool> CheckTakeProfit(string stockCode, decimal currentPrice);

        /// <summary>
        /// 检查仓位限制
        /// </summary>
        Task<bool> CheckPositionLimit(string stockCode, decimal amount);

        /// <summary>
        /// 检查行业限制
        /// </summary>
        Task<bool> CheckIndustryLimit(string industryCode, decimal amount);

        /// <summary>
        /// 获取风险指标
        /// </summary>
        Task<RiskMetrics> GetRiskMetrics();
    }

    /// <summary>
    /// 风险指标
    /// </summary>
    public class RiskMetrics
    {
        /// <summary>
        /// 最大回撤
        /// </summary>
        public decimal MaxDrawdown { get; set; }

        /// <summary>
        /// 波动率
        /// </summary>
        public decimal Volatility { get; set; }

        /// <summary>
        /// 夏普比率
        /// </summary>
        public decimal SharpeRatio { get; set; }

        /// <summary>
        /// 贝塔系数
        /// </summary>
        public decimal Beta { get; set; }

        /// <summary>
        /// 阿尔法系数
        /// </summary>
        public decimal Alpha { get; set; }

        /// <summary>
        /// 信息比率
        /// </summary>
        public decimal InformationRatio { get; set; }

        /// <summary>
        /// 索提诺比率
        /// </summary>
        public decimal SortinoRatio { get; set; }

        /// <summary>
        /// 卡玛比率
        /// </summary>
        public decimal CalmarRatio { get; set; }
    }
} 