using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging;
using Quant.Strategy.Services;

namespace Quant.Strategy.Services.Impl
{
    /// <summary>
    /// 掘金量化行业数据服务实现
    /// </summary>
    public class JQIndustryDataService : IIndustryDataService
    {
        private readonly ILogger<JQIndustryDataService> _logger;
        private readonly JQDataClient _jqClient;
        private readonly Dictionary<string, string> _stockIndustryCache;

        public JQIndustryDataService(
            ILogger<JQIndustryDataService> logger,
            JQDataClient jqClient)
        {
            _logger = logger;
            _jqClient = jqClient;
            _stockIndustryCache = new Dictionary<string, string>();
        }

        public async Task<string> GetStockIndustry(string stockCode)
        {
            try
            {
                // 先从缓存中获取
                if (_stockIndustryCache.TryGetValue(stockCode, out var industry))
                {
                    return industry;
                }

                // 获取股票所属行业
                var industryInfo = await _jqClient.GetIndustry(stockCode, date: DateTime.Today);
                var industryCode = industryInfo.FirstOrDefault()?.IndustryCode;

                if (string.IsNullOrEmpty(industryCode))
                {
                    throw new Exception($"无法获取股票 {stockCode} 的行业信息");
                }

                // 更新缓存
                _stockIndustryCache[stockCode] = industryCode;
                return industryCode;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, $"获取股票 {stockCode} 行业信息失败");
                throw;
            }
        }

        public async Task<decimal> GetIndustryAveragePB(string industryCode)
        {
            try
            {
                // 获取行业成分股
                var stocks = await _jqClient.GetIndustryStocks(industryCode);
                
                // 获取成分股的PB数据
                var q = await _jqClient.Query(
                    indicator("pb_ratio"),
                    stocks.Select(s => s.Code)
                );
                
                // 计算行业平均PB
                var pbValues = q.Values.Select(data => data["pb_ratio"]).Where(pb => pb > 0);
                return pbValues.Any() ? pbValues.Average() : 0;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, $"获取行业 {industryCode} 平均PB失败");
                throw;
            }
        }

        public async Task<decimal> GetPEPercentile(string stockCode, int days = 250)
        {
            try
            {
                // 获取历史PE数据
                var endDate = DateTime.Today;
                var startDate = endDate.AddDays(-days);
                
                var peData = await _jqClient.GetPrice(
                    stockCode,
                    startDate,
                    endDate,
                    fields: new[] { "pe_ratio" }
                );
                
                // 计算PE分位数
                var peValues = peData.Select(d => d["pe_ratio"]).Where(pe => pe > 0).ToList();
                if (!peValues.Any())
                {
                    return 0;
                }
                
                var currentPE = peValues.Last();
                var percentile = peValues.Count(v => v <= currentPE) / (decimal)peValues.Count;
                
                return percentile;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, $"获取股票 {stockCode} PE分位数失败");
                throw;
            }
        }
    }
} 