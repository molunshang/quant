//////////////////////////////////////////////////////////////////////////
//数据事件驱动
//策略描述：
//典型如选股交易策略。比如，策略每日收盘前10分钟执行：选股->决策逻辑->交易->退出。可能无需订阅实时数据

using GMSDK;
using Newtonsoft.Json;
using System;

namespace DataEventDriven
{
    public class MyStrategy : Strategy
    {
        public MyStrategy(string token, string strategyId, StrategyMode mode) : base(token, strategyId, mode) { }

        //重写OnInit事件， 进行策略开发
        public override void OnInit()
        {
            System.Console.WriteLine("OnInit");

            //订阅浦发银行，bar频率为一天
            Subscribe("SHSE.600000", "1d");
            return;
        }

        //重写OnBar事件
        public override void OnBar(Bar bar)
        {
            System.Console.WriteLine("OnBar：{0}", JsonConvert.SerializeObject(bar));
        }
    }
    class Program
    {
        static void Main(string[] args)
        {
            //回测最近一个月
            var startTime = DateTime.Now.AddDays(-30).ToString("yyyy-MM-dd 8:20:0");
            var endTime = DateTime.Now.AddDays(-1).ToString("yyyy-MM-dd 17:30:00");
            MyStrategy s = new MyStrategy("e20f302d95ed3058db274c2f918afd5bb2c3af02", "f1d469f8-3723-11f0-a307-80fa5b398f88", StrategyMode.MODE_BACKTEST);
            s.SetBacktestConfig(startTime, endTime);
            int nRet = s.Run();
            if (nRet != 0)
            {
                System.Console.WriteLine("回测失败, 错误码: {0}", nRet);
            }
            else
            {
                System.Console.WriteLine("回测完成！");
            }
            System.Console.Read();
        }
    }
}