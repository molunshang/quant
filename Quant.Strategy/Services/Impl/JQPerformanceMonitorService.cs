using System;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Linq;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging;
using Quant.Strategy.Services;

namespace Quant.Strategy.Services.Impl
{
    /// <summary>
    /// 掘金量化性能监控服务实现
    /// </summary>
    public class JQPerformanceMonitorService : IPerformanceMonitorService
    {
        private readonly ILogger<JQPerformanceMonitorService> _logger;
        private readonly ConcurrentDictionary<string, ConcurrentQueue<TimeSpan>> _strategyExecutionTimes;
        private readonly ConcurrentDictionary<string, ConcurrentQueue<(TimeSpan ResponseTime, bool IsSuccess)>> _apiCallTimes;
        private readonly Process _currentProcess;

        public JQPerformanceMonitorService(ILogger<JQPerformanceMonitorService> logger)
        {
            _logger = logger;
            _strategyExecutionTimes = new ConcurrentDictionary<string, ConcurrentQueue<TimeSpan>>();
            _apiCallTimes = new ConcurrentDictionary<string, ConcurrentQueue<(TimeSpan ResponseTime, bool IsSuccess)>>();
            _currentProcess = Process.GetCurrentProcess();
        }

        public async Task<StrategyPerformanceMetrics> GetStrategyPerformanceMetrics()
        {
            try
            {
                var metrics = new StrategyPerformanceMetrics();
                var executionTimes = _strategyExecutionTimes.Values
                    .SelectMany(q => q)
                    .ToList();

                if (executionTimes.Any())
                {
                    metrics.AverageExecutionTime = executionTimes.Average(t => t.TotalMilliseconds);
                    metrics.MaxExecutionTime = executionTimes.Max(t => t.TotalMilliseconds);
                    metrics.MinExecutionTime = executionTimes.Min(t => t.TotalMilliseconds);
                    metrics.ExecutionCount = executionTimes.Count;
                }

                metrics.PeakMemoryUsage = _currentProcess.PeakWorkingSet64 / (1024.0 * 1024.0); // 转换为MB
                metrics.PeakCpuUsage = await GetPeakCpuUsage();

                _logger.LogInformation($"获取策略性能指标：" +
                    $"平均执行时间 {metrics.AverageExecutionTime:F2}ms，" +
                    $"最大执行时间 {metrics.MaxExecutionTime:F2}ms，" +
                    $"最小执行时间 {metrics.MinExecutionTime:F2}ms，" +
                    $"执行次数 {metrics.ExecutionCount}，" +
                    $"内存使用峰值 {metrics.PeakMemoryUsage:F2}MB，" +
                    $"CPU使用峰值 {metrics.PeakCpuUsage:F2}%");

                return metrics;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "获取策略性能指标失败");
                throw;
            }
        }

        public async Task<SystemResourceMetrics> GetSystemResourceMetrics()
        {
            try
            {
                var metrics = new SystemResourceMetrics
                {
                    CurrentMemoryUsage = _currentProcess.WorkingSet64 / (1024.0 * 1024.0), // 转换为MB
                    CurrentCpuUsage = await GetCurrentCpuUsage(),
                    CurrentDiskUsage = await GetCurrentDiskUsage(),
                    CurrentNetworkUsage = await GetCurrentNetworkUsage(),
                    ThreadCount = _currentProcess.Threads.Count,
                    HandleCount = _currentProcess.HandleCount
                };

                _logger.LogInformation($"获取系统资源指标：" +
                    $"当前内存使用 {metrics.CurrentMemoryUsage:F2}MB，" +
                    $"当前CPU使用率 {metrics.CurrentCpuUsage:F2}%，" +
                    $"当前磁盘使用率 {metrics.CurrentDiskUsage:F2}%，" +
                    $"当前网络使用率 {metrics.CurrentNetworkUsage:F2}%，" +
                    $"线程数 {metrics.ThreadCount}，" +
                    $"句柄数 {metrics.HandleCount}");

                return metrics;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "获取系统资源指标失败");
                throw;
            }
        }

        public async Task<ApiCallMetrics> GetApiCallMetrics()
        {
            try
            {
                var metrics = new ApiCallMetrics();
                var apiCalls = _apiCallTimes.Values
                    .SelectMany(q => q)
                    .ToList();

                if (apiCalls.Any())
                {
                    metrics.CallCount = apiCalls.Count;
                    metrics.AverageResponseTime = apiCalls.Average(c => c.ResponseTime.TotalMilliseconds);
                    metrics.MaxResponseTime = apiCalls.Max(c => c.ResponseTime.TotalMilliseconds);
                    metrics.MinResponseTime = apiCalls.Min(c => c.ResponseTime.TotalMilliseconds);
                    metrics.SuccessRate = (decimal)apiCalls.Count(c => c.IsSuccess) / apiCalls.Count;
                    metrics.ErrorCount = apiCalls.Count(c => !c.IsSuccess);
                    metrics.LastCallTime = DateTime.Now;
                }

                _logger.LogInformation($"获取API调用指标：" +
                    $"调用次数 {metrics.CallCount}，" +
                    $"平均响应时间 {metrics.AverageResponseTime:F2}ms，" +
                    $"最大响应时间 {metrics.MaxResponseTime:F2}ms，" +
                    $"最小响应时间 {metrics.MinResponseTime:F2}ms，" +
                    $"成功率 {metrics.SuccessRate:P2}，" +
                    $"错误次数 {metrics.ErrorCount}");

                return metrics;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "获取API调用指标失败");
                throw;
            }
        }

        public Task RecordStrategyExecutionTime(string strategyName, TimeSpan executionTime)
        {
            try
            {
                var queue = _strategyExecutionTimes.GetOrAdd(strategyName, _ => new ConcurrentQueue<TimeSpan>());
                queue.Enqueue(executionTime);

                // 只保留最近1000次执行记录
                while (queue.Count > 1000)
                {
                    queue.TryDequeue(out _);
                }

                _logger.LogDebug($"记录策略 {strategyName} 执行时间：{executionTime.TotalMilliseconds:F2}ms");
                return Task.CompletedTask;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, $"记录策略 {strategyName} 执行时间失败");
                throw;
            }
        }

        public Task RecordApiCall(string apiName, TimeSpan responseTime, bool isSuccess)
        {
            try
            {
                var queue = _apiCallTimes.GetOrAdd(apiName, _ => new ConcurrentQueue<(TimeSpan ResponseTime, bool IsSuccess)>());
                queue.Enqueue((responseTime, isSuccess));

                // 只保留最近1000次调用记录
                while (queue.Count > 1000)
                {
                    queue.TryDequeue(out _);
                }

                _logger.LogDebug($"记录API {apiName} 调用：" +
                    $"响应时间 {responseTime.TotalMilliseconds:F2}ms，" +
                    $"是否成功 {isSuccess}");

                return Task.CompletedTask;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, $"记录API {apiName} 调用失败");
                throw;
            }
        }

        private async Task<double> GetCurrentCpuUsage()
        {
            try
            {
                var startTime = DateTime.UtcNow;
                var startCpuUsage = _currentProcess.TotalProcessorTime;
                await Task.Delay(1000);

                var endTime = DateTime.UtcNow;
                var endCpuUsage = _currentProcess.TotalProcessorTime;
                var cpuUsedMs = (endCpuUsage - startCpuUsage).TotalMilliseconds;
                var totalMsPassed = (endTime - startTime).TotalMilliseconds * Environment.ProcessorCount;
                var cpuUsageTotal = cpuUsedMs / totalMsPassed * 100;

                return cpuUsageTotal;
            }
            catch
            {
                return 0;
            }
        }

        private async Task<double> GetPeakCpuUsage()
        {
            try
            {
                var peakUsage = 0.0;
                for (int i = 0; i < 5; i++)
                {
                    var usage = await GetCurrentCpuUsage();
                    peakUsage = Math.Max(peakUsage, usage);
                    await Task.Delay(200);
                }
                return peakUsage;
            }
            catch
            {
                return 0;
            }
        }

        private Task<double> GetCurrentDiskUsage()
        {
            try
            {
                var drive = new DriveInfo(Environment.CurrentDirectory);
                return Task.FromResult((double)drive.AvailableFreeSpace / drive.TotalSize * 100);
            }
            catch
            {
                return Task.FromResult(0.0);
            }
        }

        private Task<double> GetCurrentNetworkUsage()
        {
            // 这里需要根据实际情况实现网络使用率的计算
            return Task.FromResult(0.0);
        }
    }
} 