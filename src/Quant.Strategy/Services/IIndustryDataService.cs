using System.Collections.Generic;
using System.Threading.Tasks;

namespace Quant.Strategy.Services
{
    /// <summary>
    /// 行业数据服务接口
    /// </summary>
    public interface IIndustryDataService
    {
        /// <summary>
        /// 获取股票所属行业
        /// </summary>
        /// <param name="stockCode">股票代码</param>
        /// <returns>行业代码</returns>
        Task<string> GetStockIndustry(string stockCode);
        
        /// <summary>
        /// 获取行业平均PB
        /// </summary>
        /// <param name="industryCode">行业代码</param>
        /// <returns>行业平均PB</returns>
        Task<decimal> GetIndustryAveragePB(string industryCode);
        
        /// <summary>
        /// 获取股票历史PE分位数
        /// </summary>
        /// <param name="stockCode">股票代码</param>
        /// <param name="days">历史天数</param>
        /// <returns>PE分位数（0-1之间）</returns>
        Task<decimal> GetPEPercentile(string stockCode, int days = 250);
    }
} 