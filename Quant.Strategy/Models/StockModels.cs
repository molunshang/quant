namespace Quant.Strategy.Models
{
    /// <summary>
    /// 股票基本面数据
    /// </summary>
    public class StockFundamental
    {
        /// <summary>
        /// 市净率
        /// </summary>
        public decimal PB { get; set; }
        
        /// <summary>
        /// 股息率
        /// </summary>
        public decimal DividendYield { get; set; }
        
        /// <summary>
        /// 市盈率
        /// </summary>
        public decimal PE { get; set; }
        
        /// <summary>
        /// 总市值（亿元）
        /// </summary>
        public decimal MarketCap { get; set; }
    }

    /// <summary>
    /// 股票交易数据
    /// </summary>
    public class StockTradingData
    {
        /// <summary>
        /// 成交量（股）
        /// </summary>
        public decimal Volume { get; set; }
        
        /// <summary>
        /// 成交额（元）
        /// </summary>
        public decimal Amount { get; set; }
        
        /// <summary>
        /// 开盘价
        /// </summary>
        public decimal Open { get; set; }
        
        /// <summary>
        /// 收盘价
        /// </summary>
        public decimal Close { get; set; }
        
        /// <summary>
        /// 最高价
        /// </summary>
        public decimal High { get; set; }
        
        /// <summary>
        /// 最低价
        /// </summary>
        public decimal Low { get; set; }
    }
} 