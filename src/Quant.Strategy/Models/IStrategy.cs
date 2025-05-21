using System.Threading.Tasks;

namespace Quant.Strategy.Models
{
    /// <summary>
    /// 策略接口
    /// </summary>
    public interface IStrategy
    {
        /// <summary>
        /// 执行策略
        /// </summary>
        Task Execute();
    }
} 