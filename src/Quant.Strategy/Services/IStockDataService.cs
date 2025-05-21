using System.Collections.Generic;
using System.Threading.Tasks;
using Quant.Strategy.Models;

namespace Quant.Strategy.Services
{
    /// <summary>
    /// 股票数据服务接口
    /// </summary>
    public interface IStockDataService
    {
        /// <summary>
        /// 获取所有A股股票列表
        /// </summary>
        /// <returns>股票代码列表</returns>
        Task<List<string>> GetAllStocks();
        
        /// <summary>
        /// 获取股票基本面数据
        /// </summary>
        /// <param name="stockCodes">股票代码列表</param>
        /// <returns>股票基本面数据字典，key为股票代码</returns>
        Task<Dictionary<string, StockFundamental>> GetStockFundamentals(List<string> stockCodes);
        
        /// <summary>
        /// 获取股票交易数据
        /// </summary>
        /// <param name="stockCodes">股票代码列表</param>
        /// <returns>股票交易数据字典，key为股票代码</returns>
        Task<Dictionary<string, StockTradingData>> GetStockTradingData(List<string> stockCodes);
    }
} 