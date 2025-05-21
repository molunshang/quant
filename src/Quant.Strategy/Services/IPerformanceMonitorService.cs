using System;
using System.Collections.Generic;
using System.Threading.Tasks;

namespace Quant.Strategy.Services
{
    /// <summary>
    /// 性能监控服务接口
    /// </summary>
    public interface IPerformanceMonitorService
    {
        /// <summary>
        /// 获取策略执行性能指标
        /// </summary>
        Task<StrategyPerformanceMetrics> GetStrategyPerformanceMetrics();

        /// <summary>
        /// 获取系统资源使用情况
        /// </summary>
        Task<SystemResourceMetrics> GetSystemResourceMetrics();

        /// <summary>
        /// 获取API调用统计
        /// </summary>
        Task<ApiCallMetrics> GetApiCallMetrics();

        /// <summary>
        /// 记录策略执行时间
        /// </summary>
        Task RecordStrategyExecutionTime(string strategyName, TimeSpan executionTime);

        /// <summary>
        /// 记录API调用
        /// </summary>
        Task RecordApiCall(string apiName, TimeSpan responseTime, bool isSuccess);
    }

    /// <summary>
    /// 策略性能指标
    /// </summary>
    public class StrategyPerformanceMetrics
    {
        /// <summary>
        /// 策略名称
        /// </summary>
        public string StrategyName { get; set; }

        /// <summary>
        /// 平均执行时间（毫秒）
        /// </summary>
        public double AverageExecutionTime { get; set; }

        /// <summary>
        /// 最大执行时间（毫秒）
        /// </summary>
        public double MaxExecutionTime { get; set; }

        /// <summary>
        /// 最小执行时间（毫秒）
        /// </summary>
        public double MinExecutionTime { get; set; }

        /// <summary>
        /// 执行次数
        /// </summary>
        public int ExecutionCount { get; set; }

        /// <summary>
        /// 成功率
        /// </summary>
        public decimal SuccessRate { get; set; }

        /// <summary>
        /// 内存使用峰值（MB）
        /// </summary>
        public double PeakMemoryUsage { get; set; }

        /// <summary>
        /// CPU使用峰值（%）
        /// </summary>
        public double PeakCpuUsage { get; set; }
    }

    /// <summary>
    /// 系统资源指标
    /// </summary>
    public class SystemResourceMetrics
    {
        /// <summary>
        /// 当前内存使用（MB）
        /// </summary>
        public double CurrentMemoryUsage { get; set; }

        /// <summary>
        /// 当前CPU使用率（%）
        /// </summary>
        public double CurrentCpuUsage { get; set; }

        /// <summary>
        /// 当前磁盘使用率（%）
        /// </summary>
        public double CurrentDiskUsage { get; set; }

        /// <summary>
        /// 当前网络带宽使用率（%）
        /// </summary>
        public double CurrentNetworkUsage { get; set; }

        /// <summary>
        /// 线程数
        /// </summary>
        public int ThreadCount { get; set; }

        /// <summary>
        /// 句柄数
        /// </summary>
        public int HandleCount { get; set; }
    }

    /// <summary>
    /// API调用指标
    /// </summary>
    public class ApiCallMetrics
    {
        /// <summary>
        /// API名称
        /// </summary>
        public string ApiName { get; set; }

        /// <summary>
        /// 调用次数
        /// </summary>
        public int CallCount { get; set; }

        /// <summary>
        /// 平均响应时间（毫秒）
        /// </summary>
        public double AverageResponseTime { get; set; }

        /// <summary>
        /// 最大响应时间（毫秒）
        /// </summary>
        public double MaxResponseTime { get; set; }

        /// <summary>
        /// 最小响应时间（毫秒）
        /// </summary>
        public double MinResponseTime { get; set; }

        /// <summary>
        /// 成功率
        /// </summary>
        public decimal SuccessRate { get; set; }

        /// <summary>
        /// 错误次数
        /// </summary>
        public int ErrorCount { get; set; }

        /// <summary>
        /// 最后调用时间
        /// </summary>
        public DateTime LastCallTime { get; set; }
    }
} 