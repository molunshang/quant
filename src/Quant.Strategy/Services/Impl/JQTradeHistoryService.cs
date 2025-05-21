using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging;
using Quant.Strategy.Services;

namespace Quant.Strategy.Services.Impl
{
    /// <summary>
    /// 掘金量化交易记录服务实现
    /// </summary>
    public class JQTradeHistoryService : ITradeHistoryService
    {
        private readonly ILogger<JQTradeHistoryService> _logger;
        private readonly JQDataClient _jqClient;
        
        // 使用内存缓存存储交易记录
        private readonly Dictionary<string, List<TradeRecord>> _tradeHistoryCache = new Dictionary<string, List<TradeRecord>>();

        public JQTradeHistoryService(
            ILogger<JQTradeHistoryService> logger,
            JQDataClient jqClient)
        {
            _logger = logger;
            _jqClient = jqClient;
        }

        public async Task RecordTrade(string stockCode, decimal price, int quantity, bool isBuy)
        {
            try
            {
                var record = new TradeRecord
                {
                    StockCode = stockCode,
                    TradeTime = DateTime.Now,
                    Price = price,
                    Quantity = quantity,
                    IsBuy = isBuy
                };

                if (!_tradeHistoryCache.ContainsKey(stockCode))
                {
                    _tradeHistoryCache[stockCode] = new List<TradeRecord>();
                }

                _tradeHistoryCache[stockCode].Add(record);
                
                // 按时间排序
                _tradeHistoryCache[stockCode] = _tradeHistoryCache[stockCode]
                    .OrderByDescending(r => r.TradeTime)
                    .ToList();

                _logger.LogInformation($"记录交易：股票 {stockCode}，价格 {price}，数量 {quantity}，" +
                    $"方向 {(isBuy ? "买入" : "卖出")}，时间 {record.TradeTime}");
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, $"记录交易失败：股票 {stockCode}");
                throw;
            }
        }

        public async Task<decimal?> GetLastTradePrice(string stockCode)
        {
            try
            {
                if (_tradeHistoryCache.TryGetValue(stockCode, out var records) && records.Any())
                {
                    var lastTrade = records.First();
                    _logger.LogInformation($"获取股票 {stockCode} 最后一次交易价格：{lastTrade.Price}");
                    return lastTrade.Price;
                }

                _logger.LogInformation($"股票 {stockCode} 没有交易记录");
                return null;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, $"获取股票 {stockCode} 最后一次交易价格失败");
                throw;
            }
        }

        public async Task<List<TradeRecord>> GetTradeHistory(string stockCode)
        {
            try
            {
                if (_tradeHistoryCache.TryGetValue(stockCode, out var records))
                {
                    _logger.LogInformation($"获取股票 {stockCode} 交易记录，共 {records.Count} 条");
                    return records;
                }

                _logger.LogInformation($"股票 {stockCode} 没有交易记录");
                return new List<TradeRecord>();
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, $"获取股票 {stockCode} 交易记录失败");
                throw;
            }
        }
    }
} 