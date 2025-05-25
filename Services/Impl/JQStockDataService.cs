using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using DataEventDriven.Models;
using Microsoft.Extensions.Logging;
using Quant.Strategy.Services;

namespace Quant.Strategy.Services.Impl
{
    /// <summary>
    /// 掘金量化股票数据服务实现
    /// </summary>
    public class JQStockDataService : IStockDataService
    {
        private readonly ILogger<JQStockDataService> _logger;
        private readonly JQDataClient _jqClient;

        public JQStockDataService(
            ILogger<JQStockDataService> logger,
            JQDataClient jqClient)
        {
            _logger = logger;
            _jqClient = jqClient;
        }

        public async Task<List<string>> GetAllStocks()
        {
            try
            {
                // 获取所有A股股票列表
                var stocks = await _jqClient.GetAllSecurities(SecurityType.STOCK);
                return stocks.Select(s => s.Code).ToList();
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "获取股票列表失败");
                throw;
            }
        }

        public async Task<Dictionary<string, StockFundamental>> GetStockFundamentals(List<string> stockCodes)
        {
            try
            {
                var result = new Dictionary<string, StockFundamental>();
                
                // 获取基本面数据
                var q = await _jqClient.Query(
                    indicator("pe_ratio", "pb_ratio", "market_cap", "dividend_ratio"),
                    stockCodes
                );
                
                foreach (var stock in stockCodes)
                {
                    var data = q[stock];
                    result[stock] = new StockFundamental
                    {
                        PE = data["pe_ratio"],
                        PB = data["pb_ratio"],
                        MarketCap = data["market_cap"] / 100000000, // 转换为亿元
                        DividendYield = data["dividend_ratio"] / 100 // 转换为小数
                    };
                }
                
                return result;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "获取股票基本面数据失败");
                throw;
            }
        }

        public async Task<Dictionary<string, StockTradingData>> GetStockTradingData(List<string> stockCodes)
        {
            try
            {
                var result = new Dictionary<string, StockTradingData>();
                
                // 获取交易数据
                var q = await _jqClient.Query(
                    indicator("open", "close", "high", "low", "volume", "money"),
                    stockCodes
                );
                
                foreach (var stock in stockCodes)
                {
                    var data = q[stock];
                    result[stock] = new StockTradingData
                    {
                        Open = data["open"],
                        Close = data["close"],
                        High = data["high"],
                        Low = data["low"],
                        Volume = data["volume"],
                        Amount = data["money"]
                    };
                }
                
                return result;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "获取股票交易数据失败");
                throw;
            }
        }
    }
} 