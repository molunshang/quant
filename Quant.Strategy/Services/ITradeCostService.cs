using System.Threading.Tasks;

namespace Quant.Strategy.Services
{
    /// <summary>
    /// 交易成本计算服务接口
    /// </summary>
    public interface ITradeCostService
    {
        /// <summary>
        /// 计算买入成本
        /// </summary>
        /// <param name="stockCode">股票代码</param>
        /// <param name="price">买入价格</param>
        /// <param name="quantity">买入数量</param>
        /// <returns>交易成本信息</returns>
        Task<TradeCost> CalculateBuyCost(string stockCode, decimal price, int quantity);
        
        /// <summary>
        /// 计算卖出成本
        /// </summary>
        /// <param name="stockCode">股票代码</param>
        /// <param name="price">卖出价格</param>
        /// <param name="quantity">卖出数量</param>
        /// <returns>交易成本信息</returns>
        Task<TradeCost> CalculateSellCost(string stockCode, decimal price, int quantity);
    }

    /// <summary>
    /// 交易成本信息
    /// </summary>
    public class TradeCost
    {
        /// <summary>
        /// 交易金额
        /// </summary>
        public decimal Amount { get; set; }
        
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
        /// 总成本
        /// </summary>
        public decimal TotalCost => Amount + Commission + StampDuty + TransferFee;
    }
} 