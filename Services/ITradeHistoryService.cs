using System;
using System.Collections.Generic;
using System.Threading.Tasks;

namespace Quant.Strategy.Services
{
    /// <summary>
    /// 交易记录服务接口
    /// </summary>
    public interface ITradeHistoryService
    {
        /// <summary>
        /// 记录交易
        /// </summary>
        Task RecordTrade(string stockCode, decimal price, int quantity, bool isBuy);

        /// <summary>
        /// 获取股票最后一次交易价格
        /// </summary>
        Task<decimal?> GetLastTradePrice(string stockCode);

        /// <summary>
        /// 获取股票历史交易记录
        /// </summary>
        Task<List<TradeRecord>> GetTradeHistory(string stockCode);
    }

    /// <summary>
    /// 交易记录
    /// </summary>
    public class TradeRecord
    {
        /// <summary>
        /// 股票代码
        /// </summary>
        public string StockCode { get; set; }

        /// <summary>
        /// 交易时间
        /// </summary>
        public DateTime TradeTime { get; set; }

        /// <summary>
        /// 交易价格
        /// </summary>
        public decimal Price { get; set; }

        /// <summary>
        /// 交易数量
        /// </summary>
        public int Quantity { get; set; }

        /// <summary>
        /// 是否买入
        /// </summary>
        public bool IsBuy { get; set; }
    }
} 