using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging;
using Quant.Strategy.Services;

namespace Quant.Strategy.Services.Impl
{
    /// <summary>
    /// 掘金量化持仓服务实现
    /// </summary>
    public class JQPositionService : IPositionService
    {
        private readonly ILogger<JQPositionService> _logger;
        private readonly JQDataClient _jqClient;
        private readonly string _accountId;

        public JQPositionService(
            ILogger<JQPositionService> logger,
            JQDataClient jqClient,
            string accountId)
        {
            _logger = logger;
            _jqClient = jqClient;
            _accountId = accountId;
        }

        public async Task<Dictionary<string, Position>> GetPositions()
        {
            try
            {
                // 获取当前持仓
                var positions = await _jqClient.GetPositions(_accountId);
                
                return positions.ToDictionary(
                    p => p.Security,
                    p => new Position
                    {
                        StockCode = p.Security,
                        Quantity = p.Volume,
                        Cost = p.CostBasis,
                        MarketValue = p.MarketValue
                    }
                );
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "获取持仓信息失败");
                throw;
            }
        }

        public async Task<TradeResult> Buy(string stockCode, decimal amount)
        {
            try
            {
                // 获取当前价格
                var price = await _jqClient.GetLastPrice(stockCode);
                
                // 计算可买数量（向下取整到100股）
                var quantity = (int)(amount / price / 100) * 100;
                
                if (quantity <= 0)
                {
                    return new TradeResult
                    {
                        Success = false,
                        ErrorMessage = "买入金额不足，无法买入100股"
                    };
                }
                
                // 执行买入
                var order = await _jqClient.Order(
                    _accountId,
                    stockCode,
                    quantity,
                    OrderType.MARKET,
                    Side.BUY
                );
                
                // 等待订单成交
                var filledOrder = await _jqClient.WaitForOrder(order.Id);
                
                return new TradeResult
                {
                    Success = true,
                    Quantity = filledOrder.FilledVolume,
                    Price = filledOrder.FilledPrice,
                    Amount = filledOrder.FilledVolume * filledOrder.FilledPrice
                };
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, $"买入股票 {stockCode} 失败");
                return new TradeResult
                {
                    Success = false,
                    ErrorMessage = ex.Message
                };
            }
        }

        public async Task<TradeResult> Sell(string stockCode, int? quantity = null)
        {
            try
            {
                // 获取当前持仓
                var positions = await GetPositions();
                if (!positions.TryGetValue(stockCode, out var position))
                {
                    return new TradeResult
                    {
                        Success = false,
                        ErrorMessage = "未持有该股票"
                    };
                }
                
                // 如果未指定数量，则卖出全部
                var sellQuantity = quantity ?? position.Quantity;
                
                // 执行卖出
                var order = await _jqClient.Order(
                    _accountId,
                    stockCode,
                    sellQuantity,
                    OrderType.MARKET,
                    Side.SELL
                );
                
                // 等待订单成交
                var filledOrder = await _jqClient.WaitForOrder(order.Id);
                
                return new TradeResult
                {
                    Success = true,
                    Quantity = filledOrder.FilledVolume,
                    Price = filledOrder.FilledPrice,
                    Amount = filledOrder.FilledVolume * filledOrder.FilledPrice
                };
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, $"卖出股票 {stockCode} 失败");
                return new TradeResult
                {
                    Success = false,
                    ErrorMessage = ex.Message
                };
            }
        }
    }
} 