using System;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging;
using Quant.Strategy.Services;

namespace Quant.Strategy.Services.Impl
{
    /// <summary>
    /// 掘金量化国债逆回购服务实现
    /// </summary>
    public class JQRepoService : IRepoService
    {
        private readonly ILogger<JQRepoService> _logger;
        private readonly JQDataClient _jqClient;
        
        // 国债逆回购代码
        private const string REPO_CODE_PREFIX = "204001"; // 1日期国债逆回购代码
        
        public JQRepoService(
            ILogger<JQRepoService> logger,
            JQDataClient jqClient)
        {
            _logger = logger;
            _jqClient = jqClient;
        }

        public async Task<TradeResult> BuyRepo(decimal amount, int days = 1)
        {
            try
            {
                // 获取当前国债逆回购价格
                var repoCode = GetRepoCode(days);
                var price = await _jqClient.GetPrice(repoCode);
                
                if (price <= 0)
                {
                    return new TradeResult
                    {
                        Success = false,
                        ErrorMessage = $"获取国债逆回购 {repoCode} 价格失败"
                    };
                }
                
                // 计算可购买数量（国债逆回购以1000元为1手）
                var quantity = (int)(amount / 1000);
                if (quantity <= 0)
                {
                    return new TradeResult
                    {
                        Success = false,
                        ErrorMessage = "购买金额不足1000元"
                    };
                }
                
                // 执行购买
                var result = await _jqClient.Buy(repoCode, quantity);
                
                if (result.Success)
                {
                    _logger.LogInformation($"成功购买国债逆回购 {repoCode}，" +
                        $"数量 {quantity} 手，金额 {amount} 元，期限 {days} 天");
                }
                else
                {
                    _logger.LogError($"购买国债逆回购失败：{result.ErrorMessage}");
                }
                
                return result;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, $"购买国债逆回购失败");
                return new TradeResult
                {
                    Success = false,
                    ErrorMessage = ex.Message
                };
            }
        }

        public async Task<decimal> GetRepoRate(int days = 1)
        {
            try
            {
                var repoCode = GetRepoCode(days);
                
                // 获取国债逆回购价格
                var price = await _jqClient.GetPrice(repoCode);
                
                if (price <= 0)
                {
                    _logger.LogError($"获取国债逆回购 {repoCode} 价格失败");
                    return 0;
                }
                
                // 计算年化利率（价格/100即为年化利率）
                var rate = price / 100;
                
                _logger.LogInformation($"国债逆回购 {repoCode} 当前年化利率：{rate:P2}");
                return rate;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, $"获取国债逆回购利率失败");
                throw;
            }
        }

        private string GetRepoCode(int days)
        {
            // 根据天数返回对应的国债逆回购代码
            return days switch
            {
                1 => "204001", // 1日期
                2 => "204002", // 2日期
                3 => "204003", // 3日期
                4 => "204004", // 4日期
                7 => "204007", // 7日期
                14 => "204014", // 14日期
                28 => "204028", // 28日期
                91 => "204091", // 91日期
                182 => "204182", // 182日期
                _ => throw new ArgumentException($"不支持的国债逆回购期限：{days}天")
            };
        }
    }
} 