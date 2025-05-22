using System.Collections.Generic;
using System.Threading.Tasks;
using Quant.Strategy.Models;

namespace Quant.Strategy.Services
{
    /// <summary>
    /// 持仓管理服务接口
    /// </summary>
    public interface IPositionService
    {
        /// <summary>
        /// 获取当前持仓信息
        /// </summary>
        /// <returns>持仓信息字典，key为股票代码</returns>
        Task<Dictionary<string, Position>> GetPositions();
        
        /// <summary>
        /// 买入股票
        /// </summary>
        /// <param name="stockCode">股票代码</param>
        /// <param name="amount">买入金额（元）</param>
        /// <returns>交易结果</returns>
        Task<TradeResult> Buy(string stockCode, decimal amount);

        /// <summary>
        /// 卖出股票
        /// </summary>
        /// <param name="stockCode">股票代码</param>
        /// <param name="quantity">卖出数量，如果为null则卖出全部</param>
        /// <returns>交易结果</returns>
        Task<TradeResult> Sell(string stockCode, int? quantity = null);
    }

    /// <summary>
    /// 持仓信息
    /// </summary>
    public class Position
    {
        /// <summary>
        /// 股票代码
        /// </summary>
        public string StockCode { get; set; }
        
        /// <summary>
        /// 持仓数量
        /// </summary>
        public int Quantity { get; set; }
        
        /// <summary>
        /// 持仓成本
        /// </summary>
        public decimal Cost { get; set; }
        
        /// <summary>
        /// 当前市值
        /// </summary>
        public decimal MarketValue { get; set; }
    }

    /// <summary>
    /// 交易结果
    /// </summary>
    public class TradeResult
    {
        /// <summary>
        /// 是否成功
        /// </summary>
        public bool Success { get; set; }
        
        /// <summary>
        /// 交易数量
        /// </summary>
        public int Quantity { get; set; }
        
        /// <summary>
        /// 交易价格
        /// </summary>
        public decimal Price { get; set; }
        
        /// <summary>
        /// 交易金额
        /// </summary>
        public decimal Amount { get; set; }
        
        /// <summary>
        /// 错误信息
        /// </summary>
        public string ErrorMessage { get; set; }
    }
} 