# 多因子选股 + 定期再平衡与动态止损策略类
定期的调仓比例控制（再平衡），引入个股层面的主动止损机制，防止持有个股遭遇突发黑天鹅时持续失血

``` python
class MultiFactorStockTradingStrategy:
    def __init__(self, stock_num=30, total_capital=500000, stop_loss_pct=0.10):
        """
        :param stop_loss_pct: 个股最大亏损容忍度，默认 10% 坚决止损
        """
        self.stock_num = stock_num
        self.total_capital = total_capital
        self.stop_loss_pct = stop_loss_pct
        self.single_stock_budget = total_capital / stock_num

    def generate_trading_orders(self, raw_factor_df, current_portfolio_df, is_rebalance_day=False):
        """
        :param raw_factor_df: 原始多因子选股池
        :param current_portfolio_df: 当前实盘个股持仓情况，包含 columns: ['code', 'cost_basis', 'current_price']
                                      cost_basis 是买入成本价，current_price 是最新现价
        :param is_rebalance_day: bool, 是否到了月度调仓日
        :return: dict, {'股票代码': 目标持有金额(元)}
        """
        target_portfolio = {}
        
        # 1. 优先风控执行：盘中/每日动态个股【止损策略】
        active_holdings = {}
        if not current_portfolio_df.empty:
            for _, row in current_portfolio_df.iterrows():
                stock = row['code']
                # 计算个股盈亏
                pnl = (row['current_price'] - row['cost_basis']) / row['cost_basis']
                if pnl <= -self.stop_loss_pct:
                    # 触发风控：个股触及止损线，强制清仓
                    target_portfolio[stock] = 0.0
                    print(f"【风控告警】股票 {stock} 触发止损，涨跌幅 {pnl:.2%}, 立即清仓！")
                else:
                    # 未触及止损的保留在当前持仓里
                    active_holdings[stock] = row['current_price'] * row['volume']

        # 2. 周期调仓交易：【月度再平衡策略】
        if is_rebalance_day:
            print("【调仓日触发】重新洗牌多因子池...")
            # 调用上一节课编写的无耦合多因子清洗与打分逻辑（此处省略具体运算步骤，直接返回 top_list）
            top_stocks = self._pure_factor_ranking_logic(raw_factor_df)
            
            # 构建新的标准等权目标仓位
            for stock in top_stocks:
                target_portfolio[stock] = self.single_stock_budget
                
            # 那些在旧持仓中存在、但没有进入新一期 top_list 且前面未被止损的股票，金额设为 0（即全部卖出）
            for old_stock in active_holdings.keys():
                if old_stock not in target_portfolio:
                    target_portfolio[old_stock] = 0.0
        else:
            # 3. 日常维持策略：【非调仓日保持原状】
            # 没到换仓日，除了被止损的个股外，其余股票继续抱紧，不产生交易摩擦成本
            for stock, current_val in active_holdings.items():
                if stock not in target_portfolio: # 确保不覆盖刚刚被止损清仓的股票
                    target_portfolio[stock] = current_val

        return target_portfolio

    def _pure_factor_ranking_logic(self, raw_factor_df):
        """内部打分占位符，返回前N只股票列表"""
        # 此处承接上文的 MAD -> Z-Score -> Total Score 排序逻辑
        return raw_factor_df.head(self.stock_num)['code'].tolist()
```
