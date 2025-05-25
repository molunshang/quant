using System.Threading.Tasks;

namespace Quant.Strategy.Services
{
    /// <summary>
    /// 国债逆回购服务接口
    /// </summary>
    public interface IRepoService
    {
        /// <summary>
        /// 购买国债逆回购
        /// </summary>
        /// <param name="amount">购买金额</param>
        /// <param name="days">期限（天数）</param>
        /// <returns>交易结果</returns>
        Task<TradeResult> BuyRepo(decimal amount, int days = 1);

        /// <summary>
        /// 获取国债逆回购利率
        /// </summary>
        /// <param name="days">期限（天数）</param>
        /// <returns>年化利率</returns>
        Task<decimal> GetRepoRate(int days = 1);
    }
} 