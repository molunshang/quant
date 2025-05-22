using System;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging;
using Quant.Strategy.Services;

namespace Quant.Strategy.Services.Impl
{
    /// <summary>
    /// 掘金量化交易成本计算服务实现
    /// </summary>
    public class JQTradeCostService : ITradeCostService
    {
        private readonly ILogger<JQTradeCostService> _logger;
        private readonly JQDataClient _jqClient;
        
        // 交易成本参数
        private const decimal COMMISSION_RATE = 0.0003m; // 佣金费率 0.03%
        private const decimal MIN_COMMISSION = 5m; // 最低佣金 5元
        private const decimal STAMP_DUTY_RATE = 0.001m; // 印花税税率 0.1%
        private const decimal TRANSFER_FEE_RATE = 0.00002m; // 过户费费率 0.002%

        public JQTradeCostService(
            ILogger<JQTradeCostService> logger,
            JQDataClient jqClient)
        {
            _logger = logger;
            _jqClient = jqClient;
        }

        public async Task<TradeCost> CalculateBuyCost(string stockCode, decimal price, int quantity)
        {
            try
            {
                var amount = price * quantity;
                var commission = Math.Max(amount * COMMISSION_RATE, MIN_COMMISSION);
                var transferFee = amount * TRANSFER_FEE_RATE;
                
                return new TradeCost
                {
                    Amount = amount,
                    Commission = commission,
                    StampDuty = 0, // 买入不收印花税
                    TransferFee = transferFee
                };
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, $"计算股票 {stockCode} 买入成本失败");
                throw;
            }
        }

        public async Task<TradeCost> CalculateSellCost(string stockCode, decimal price, int quantity)
        {
            try
            {
                var amount = price * quantity;
                var commission = Math.Max(amount * COMMISSION_RATE, MIN_COMMISSION);
                var stampDuty = amount * STAMP_DUTY_RATE;
                var transferFee = amount * TRANSFER_FEE_RATE;
                
                return new TradeCost
                {
                    Amount = amount,
                    Commission = commission,
                    StampDuty = stampDuty,
                    TransferFee = transferFee
                };
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, $"计算股票 {stockCode} 卖出成本失败");
                throw;
            }
        }
    }
} 