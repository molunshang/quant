using System;
using System.Collections.Generic;
using System.Threading.Tasks;

namespace Quant.Strategy.Services
{
    /// <summary>
    /// 实时监控服务接口
    /// </summary>
    public interface IRealTimeMonitorService
    {
        /// <summary>
        /// 监控市场异常
        /// </summary>
        Task<MarketAlert> MonitorMarketAnomaly(string stockCode);

        /// <summary>
        /// 监控风险预警
        /// </summary>
        Task<RiskAlert> MonitorRiskAlert(string stockCode);

        /// <summary>
        /// 监控系统异常
        /// </summary>
        Task<SystemAlert> MonitorSystemAnomaly();

        /// <summary>
        /// 获取实时监控状态
        /// </summary>
        Task<MonitorStatus> GetMonitorStatus();

        /// <summary>
        /// 订阅监控事件
        /// </summary>
        Task SubscribeToMonitorEvents(Action<MonitorEvent> onEvent);
    }

    /// <summary>
    /// 市场异常预警
    /// </summary>
    public class MarketAlert
    {
        /// <summary>
        /// 股票代码
        /// </summary>
        public string StockCode { get; set; }

        /// <summary>
        /// 预警类型
        /// </summary>
        public MarketAlertType AlertType { get; set; }

        /// <summary>
        /// 预警级别
        /// </summary>
        public AlertLevel Level { get; set; }

        /// <summary>
        /// 预警消息
        /// </summary>
        public string Message { get; set; }

        /// <summary>
        /// 预警时间
        /// </summary>
        public DateTime AlertTime { get; set; }

        /// <summary>
        /// 相关数据
        /// </summary>
        public Dictionary<string, object> Data { get; set; }
    }

    /// <summary>
    /// 风险预警
    /// </summary>
    public class RiskAlert
    {
        /// <summary>
        /// 股票代码
        /// </summary>
        public string StockCode { get; set; }

        /// <summary>
        /// 预警类型
        /// </summary>
        public RiskAlertType AlertType { get; set; }

        /// <summary>
        /// 预警级别
        /// </summary>
        public AlertLevel Level { get; set; }

        /// <summary>
        /// 预警消息
        /// </summary>
        public string Message { get; set; }

        /// <summary>
        /// 预警时间
        /// </summary>
        public DateTime AlertTime { get; set; }

        /// <summary>
        /// 相关数据
        /// </summary>
        public Dictionary<string, object> Data { get; set; }
    }

    /// <summary>
    /// 系统异常预警
    /// </summary>
    public class SystemAlert
    {
        /// <summary>
        /// 预警类型
        /// </summary>
        public SystemAlertType AlertType { get; set; }

        /// <summary>
        /// 预警级别
        /// </summary>
        public AlertLevel Level { get; set; }

        /// <summary>
        /// 预警消息
        /// </summary>
        public string Message { get; set; }

        /// <summary>
        /// 预警时间
        /// </summary>
        public DateTime AlertTime { get; set; }

        /// <summary>
        /// 相关数据
        /// </summary>
        public Dictionary<string, object> Data { get; set; }
    }

    /// <summary>
    /// 监控状态
    /// </summary>
    public class MonitorStatus
    {
        /// <summary>
        /// 是否正在监控
        /// </summary>
        public bool IsMonitoring { get; set; }

        /// <summary>
        /// 监控开始时间
        /// </summary>
        public DateTime StartTime { get; set; }

        /// <summary>
        /// 监控的股票数量
        /// </summary>
        public int MonitoredStockCount { get; set; }

        /// <summary>
        /// 预警数量
        /// </summary>
        public int AlertCount { get; set; }

        /// <summary>
        /// 最后更新时间
        /// </summary>
        public DateTime LastUpdateTime { get; set; }
    }

    /// <summary>
    /// 监控事件
    /// </summary>
    public class MonitorEvent
    {
        /// <summary>
        /// 事件类型
        /// </summary>
        public MonitorEventType EventType { get; set; }

        /// <summary>
        /// 事件数据
        /// </summary>
        public object EventData { get; set; }

        /// <summary>
        /// 事件时间
        /// </summary>
        public DateTime EventTime { get; set; }
    }

    /// <summary>
    /// 市场预警类型
    /// </summary>
    public enum MarketAlertType
    {
        /// <summary>
        /// 价格异常
        /// </summary>
        PriceAnomaly,

        /// <summary>
        /// 成交量异常
        /// </summary>
        VolumeAnomaly,

        /// <summary>
        /// 涨跌幅异常
        /// </summary>
        PriceChangeAnomaly,

        /// <summary>
        /// 流动性异常
        /// </summary>
        LiquidityAnomaly,

        /// <summary>
        /// 波动率异常
        /// </summary>
        VolatilityAnomaly
    }

    /// <summary>
    /// 风险预警类型
    /// </summary>
    public enum RiskAlertType
    {
        /// <summary>
        /// 止损预警
        /// </summary>
        StopLoss,

        /// <summary>
        /// 止盈预警
        /// </summary>
        TakeProfit,

        /// <summary>
        /// 仓位预警
        /// </summary>
        PositionLimit,

        /// <summary>
        /// 行业预警
        /// </summary>
        IndustryLimit,

        /// <summary>
        /// 流动性预警
        /// </summary>
        LiquidityRisk
    }

    /// <summary>
    /// 系统预警类型
    /// </summary>
    public enum SystemAlertType
    {
        /// <summary>
        /// 性能预警
        /// </summary>
        Performance,

        /// <summary>
        /// 资源预警
        /// </summary>
        Resource,

        /// <summary>
        /// 网络预警
        /// </summary>
        Network,

        /// <summary>
        /// 数据预警
        /// </summary>
        Data,

        /// <summary>
        /// 异常预警
        /// </summary>
        Exception
    }

    /// <summary>
    /// 预警级别
    /// </summary>
    public enum AlertLevel
    {
        /// <summary>
        /// 信息
        /// </summary>
        Info,

        /// <summary>
        /// 警告
        /// </summary>
        Warning,

        /// <summary>
        /// 错误
        /// </summary>
        Error,

        /// <summary>
        /// 严重
        /// </summary>
        Critical
    }

    /// <summary>
    /// 监控事件类型
    /// </summary>
    public enum MonitorEventType
    {
        /// <summary>
        /// 市场预警
        /// </summary>
        MarketAlert,

        /// <summary>
        /// 风险预警
        /// </summary>
        RiskAlert,

        /// <summary>
        /// 系统预警
        /// </summary>
        SystemAlert,

        /// <summary>
        /// 状态更新
        /// </summary>
        StatusUpdate
    }
} 