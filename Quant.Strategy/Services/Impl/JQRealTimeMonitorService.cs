using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging;
using Quant.Strategy.Services;

namespace Quant.Strategy.Services.Impl
{
    /// <summary>
    /// 掘金量化实时监控服务实现
    /// </summary>
    public class JQRealTimeMonitorService : IRealTimeMonitorService
    {
        private readonly ILogger<JQRealTimeMonitorService> _logger;
        private readonly JQDataClient _jqClient;
        private readonly IRiskControlService _riskControlService;
        private readonly IPerformanceMonitorService _performanceMonitorService;
        private readonly ConcurrentDictionary<string, DateTime> _lastAlertTimes;
        private readonly ConcurrentDictionary<string, int> _alertCounts;
        private readonly ConcurrentBag<Action<MonitorEvent>> _eventSubscribers;
        private readonly ConcurrentDictionary<string, decimal> _priceHistory;
        private readonly ConcurrentDictionary<string, long> _volumeHistory;
        private readonly ConcurrentDictionary<string, decimal> _volatilityHistory;

        // 监控参数
        private const decimal PRICE_CHANGE_THRESHOLD = 0.05m; // 价格变化阈值
        private const decimal VOLUME_CHANGE_THRESHOLD = 2.0m; // 成交量变化阈值
        private const decimal VOLATILITY_THRESHOLD = 0.02m; // 波动率阈值
        private const int ALERT_INTERVAL_SECONDS = 60; // 预警间隔（秒）
        private const int MAX_ALERTS_PER_HOUR = 10; // 每小时最大预警次数

        public JQRealTimeMonitorService(
            ILogger<JQRealTimeMonitorService> logger,
            JQDataClient jqClient,
            IRiskControlService riskControlService,
            IPerformanceMonitorService performanceMonitorService)
        {
            _logger = logger;
            _jqClient = jqClient;
            _riskControlService = riskControlService;
            _performanceMonitorService = performanceMonitorService;
            _lastAlertTimes = new ConcurrentDictionary<string, DateTime>();
            _alertCounts = new ConcurrentDictionary<string, int>();
            _eventSubscribers = new ConcurrentBag<Action<MonitorEvent>>();
            _priceHistory = new ConcurrentDictionary<string, decimal>();
            _volumeHistory = new ConcurrentDictionary<string, long>();
            _volatilityHistory = new ConcurrentDictionary<string, decimal>();
        }

        public async Task<MarketAlert> MonitorMarketAnomaly(string stockCode)
        {
            try
            {
                // 检查预警频率限制
                if (!CanAlert(stockCode))
                {
                    return null;
                }

                // 获取当前市场数据
                var currentPrice = await _jqClient.GetPrice(stockCode);
                var currentVolume = await _jqClient.GetVolume(stockCode);
                var currentVolatility = await _jqClient.GetVolatility(stockCode);

                // 获取历史数据
                var lastPrice = _priceHistory.GetOrAdd(stockCode, currentPrice);
                var lastVolume = _volumeHistory.GetOrAdd(stockCode, currentVolume);
                var lastVolatility = _volatilityHistory.GetOrAdd(stockCode, currentVolatility);

                // 计算变化率
                var priceChange = Math.Abs(currentPrice - lastPrice) / lastPrice;
                var volumeChange = (decimal)currentVolume / lastVolume;
                var volatilityChange = Math.Abs(currentVolatility - lastVolatility);

                // 更新历史数据
                _priceHistory[stockCode] = currentPrice;
                _volumeHistory[stockCode] = currentVolume;
                _volatilityHistory[stockCode] = currentVolatility;

                // 检查异常
                if (priceChange > PRICE_CHANGE_THRESHOLD)
                {
                    return CreateMarketAlert(stockCode, MarketAlertType.PriceAnomaly,
                        $"股票 {stockCode} 价格异常：变化率 {priceChange:P2} 超过阈值 {PRICE_CHANGE_THRESHOLD:P2}",
                        new Dictionary<string, object>
                        {
                            { "CurrentPrice", currentPrice },
                            { "LastPrice", lastPrice },
                            { "PriceChange", priceChange }
                        });
                }

                if (volumeChange > VOLUME_CHANGE_THRESHOLD)
                {
                    return CreateMarketAlert(stockCode, MarketAlertType.VolumeAnomaly,
                        $"股票 {stockCode} 成交量异常：变化率 {volumeChange:P2} 超过阈值 {VOLUME_CHANGE_THRESHOLD:P2}",
                        new Dictionary<string, object>
                        {
                            { "CurrentVolume", currentVolume },
                            { "LastVolume", lastVolume },
                            { "VolumeChange", volumeChange }
                        });
                }

                if (volatilityChange > VOLATILITY_THRESHOLD)
                {
                    return CreateMarketAlert(stockCode, MarketAlertType.VolatilityAnomaly,
                        $"股票 {stockCode} 波动率异常：变化率 {volatilityChange:P2} 超过阈值 {VOLATILITY_THRESHOLD:P2}",
                        new Dictionary<string, object>
                        {
                            { "CurrentVolatility", currentVolatility },
                            { "LastVolatility", lastVolatility },
                            { "VolatilityChange", volatilityChange }
                        });
                }

                return null;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, $"监控股票 {stockCode} 市场异常失败");
                throw;
            }
        }

        public async Task<RiskAlert> MonitorRiskAlert(string stockCode)
        {
            try
            {
                // 检查预警频率限制
                if (!CanAlert(stockCode))
                {
                    return null;
                }

                // 获取当前价格
                var currentPrice = await _jqClient.GetPrice(stockCode);

                // 检查止损条件
                if (await _riskControlService.CheckStopLoss(stockCode, currentPrice))
                {
                    return CreateRiskAlert(stockCode, RiskAlertType.StopLoss,
                        $"股票 {stockCode} 触发止损条件",
                        new Dictionary<string, object>
                        {
                            { "CurrentPrice", currentPrice }
                        });
                }

                // 检查止盈条件
                if (await _riskControlService.CheckTakeProfit(stockCode, currentPrice))
                {
                    return CreateRiskAlert(stockCode, RiskAlertType.TakeProfit,
                        $"股票 {stockCode} 触发止盈条件",
                        new Dictionary<string, object>
                        {
                            { "CurrentPrice", currentPrice }
                        });
                }

                // 检查仓位限制
                var position = await _jqClient.GetPosition(stockCode);
                if (position != null)
                {
                    if (!await _riskControlService.CheckPositionLimit(stockCode, position.MarketValue))
                    {
                        return CreateRiskAlert(stockCode, RiskAlertType.PositionLimit,
                            $"股票 {stockCode} 超过最大持仓限制",
                            new Dictionary<string, object>
                            {
                                { "Position", position }
                            });
                    }
                }

                return null;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, $"监控股票 {stockCode} 风险预警失败");
                throw;
            }
        }

        public async Task<SystemAlert> MonitorSystemAnomaly()
        {
            try
            {
                // 获取系统资源指标
                var resourceMetrics = await _performanceMonitorService.GetSystemResourceMetrics();
                var performanceMetrics = await _performanceMonitorService.GetStrategyPerformanceMetrics();
                var apiMetrics = await _performanceMonitorService.GetApiCallMetrics();

                // 检查CPU使用率
                if (resourceMetrics.CurrentCpuUsage > 80)
                {
                    return CreateSystemAlert(SystemAlertType.Performance,
                        $"系统CPU使用率过高：{resourceMetrics.CurrentCpuUsage:F2}%",
                        new Dictionary<string, object>
                        {
                            { "CpuUsage", resourceMetrics.CurrentCpuUsage }
                        });
                }

                // 检查内存使用率
                if (resourceMetrics.CurrentMemoryUsage > 1024) // 超过1GB
                {
                    return CreateSystemAlert(SystemAlertType.Resource,
                        $"系统内存使用率过高：{resourceMetrics.CurrentMemoryUsage:F2}MB",
                        new Dictionary<string, object>
                        {
                            { "MemoryUsage", resourceMetrics.CurrentMemoryUsage }
                        });
                }

                // 检查API调用成功率
                if (apiMetrics.SuccessRate < 0.95m)
                {
                    return CreateSystemAlert(SystemAlertType.Network,
                        $"API调用成功率过低：{apiMetrics.SuccessRate:P2}",
                        new Dictionary<string, object>
                        {
                            { "SuccessRate", apiMetrics.SuccessRate },
                            { "ErrorCount", apiMetrics.ErrorCount }
                        });
                }

                return null;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "监控系统异常失败");
                throw;
            }
        }

        public async Task<MonitorStatus> GetMonitorStatus()
        {
            try
            {
                var status = new MonitorStatus
                {
                    IsMonitoring = true,
                    StartTime = DateTime.Now.AddHours(-1), // 假设监控已运行1小时
                    MonitoredStockCount = _priceHistory.Count,
                    AlertCount = _alertCounts.Values.Sum(),
                    LastUpdateTime = DateTime.Now
                };

                _logger.LogInformation($"获取监控状态：" +
                    $"监控股票数量 {status.MonitoredStockCount}，" +
                    $"预警数量 {status.AlertCount}，" +
                    $"最后更新时间 {status.LastUpdateTime}");

                return status;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "获取监控状态失败");
                throw;
            }
        }

        public Task SubscribeToMonitorEvents(Action<MonitorEvent> onEvent)
        {
            try
            {
                _eventSubscribers.Add(onEvent);
                _logger.LogInformation("添加监控事件订阅者");
                return Task.CompletedTask;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "添加监控事件订阅者失败");
                throw;
            }
        }

        private bool CanAlert(string stockCode)
        {
            var now = DateTime.Now;
            var lastAlertTime = _lastAlertTimes.GetOrAdd(stockCode, DateTime.MinValue);
            var alertCount = _alertCounts.GetOrAdd(stockCode, 0);

            // 检查预警间隔
            if ((now - lastAlertTime).TotalSeconds < ALERT_INTERVAL_SECONDS)
            {
                return false;
            }

            // 检查预警次数限制
            if (alertCount >= MAX_ALERTS_PER_HOUR)
            {
                return false;
            }

            // 更新预警时间和次数
            _lastAlertTimes[stockCode] = now;
            _alertCounts[stockCode] = alertCount + 1;

            return true;
        }

        private MarketAlert CreateMarketAlert(string stockCode, MarketAlertType alertType, string message, Dictionary<string, object> data)
        {
            var alert = new MarketAlert
            {
                StockCode = stockCode,
                AlertType = alertType,
                Level = GetAlertLevel(alertType),
                Message = message,
                AlertTime = DateTime.Now,
                Data = data
            };

            NotifySubscribers(MonitorEventType.MarketAlert, alert);
            return alert;
        }

        private RiskAlert CreateRiskAlert(string stockCode, RiskAlertType alertType, string message, Dictionary<string, object> data)
        {
            var alert = new RiskAlert
            {
                StockCode = stockCode,
                AlertType = alertType,
                Level = GetAlertLevel(alertType),
                Message = message,
                AlertTime = DateTime.Now,
                Data = data
            };

            NotifySubscribers(MonitorEventType.RiskAlert, alert);
            return alert;
        }

        private SystemAlert CreateSystemAlert(SystemAlertType alertType, string message, Dictionary<string, object> data)
        {
            var alert = new SystemAlert
            {
                AlertType = alertType,
                Level = GetAlertLevel(alertType),
                Message = message,
                AlertTime = DateTime.Now,
                Data = data
            };

            NotifySubscribers(MonitorEventType.SystemAlert, alert);
            return alert;
        }

        private AlertLevel GetAlertLevel(MarketAlertType alertType)
        {
            return alertType switch
            {
                MarketAlertType.PriceAnomaly => AlertLevel.Warning,
                MarketAlertType.VolumeAnomaly => AlertLevel.Info,
                MarketAlertType.PriceChangeAnomaly => AlertLevel.Warning,
                MarketAlertType.LiquidityAnomaly => AlertLevel.Error,
                MarketAlertType.VolatilityAnomaly => AlertLevel.Warning,
                _ => AlertLevel.Info
            };
        }

        private AlertLevel GetAlertLevel(RiskAlertType alertType)
        {
            return alertType switch
            {
                RiskAlertType.StopLoss => AlertLevel.Critical,
                RiskAlertType.TakeProfit => AlertLevel.Warning,
                RiskAlertType.PositionLimit => AlertLevel.Error,
                RiskAlertType.IndustryLimit => AlertLevel.Error,
                RiskAlertType.LiquidityRisk => AlertLevel.Warning,
                _ => AlertLevel.Info
            };
        }

        private AlertLevel GetAlertLevel(SystemAlertType alertType)
        {
            return alertType switch
            {
                SystemAlertType.Performance => AlertLevel.Error,
                SystemAlertType.Resource => AlertLevel.Warning,
                SystemAlertType.Network => AlertLevel.Error,
                SystemAlertType.Data => AlertLevel.Critical,
                SystemAlertType.Exception => AlertLevel.Critical,
                _ => AlertLevel.Info
            };
        }

        private void NotifySubscribers(MonitorEventType eventType, object eventData)
        {
            var monitorEvent = new MonitorEvent
            {
                EventType = eventType,
                EventData = eventData,
                EventTime = DateTime.Now
            };

            foreach (var subscriber in _eventSubscribers)
            {
                try
                {
                    subscriber(monitorEvent);
                }
                catch (Exception ex)
                {
                    _logger.LogError(ex, "通知监控事件订阅者失败");
                }
            }
        }
    }
} 