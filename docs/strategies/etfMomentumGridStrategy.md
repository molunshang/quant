# ETF 双动量 + 动态网格交易策略类
自动选择ETF，集成趋势跟踪（长线持股）与网格振荡（日内/短线高抛低吸）的复合交易策略

``` python

import numpy as np
import pandas as pd
class EtfMomentumGridStrategy:
    def __init__(self, etf_pool, money_fund, lookback=20, total_capital=500000, grid_step=0.02):
        """
        :param total_capital: 该策略分配的总资金（如 50 万）
        :param grid_step: 网格间距，默认 2% 触发一次低吸高抛
        """
        self.etf_pool = etf_pool
        self.money_fund = money_fund
        self.lookback = lookback
        self.total_capital = total_capital
        self.grid_step = grid_step
        
        # 内部交易状态机：记录当前网格的“锚定中轴价”和持仓基准
        self.current_regime = 'CASH' # CASH(空仓货基) 或 TREND(趋势持股)
        self.current_asset = None
        self.base_price = 0.0
    def calculate_trading_signals(self, hist_data_dict, current_positions):
        """
        核心交易策略引擎
        :param current_positions: 当前实盘/回测持仓字典，格式如 {'510300': 10000(股)}
        :return: dict, {'资产代码': 目标持有金额(元)}
        """
        target_portfolio = {}
        valid_momentum = {}
        
        # 1. 标的选择策略：计算相对动量与绝对动量
        for etf in self.etf_pool:
            df = hist_data_dict.get(etf)
            if df is None or len(df) < self.lookback: continue
            
            prices = df['close'].values
            current_price = prices[-1]
            ma_price = np.mean(prices[-self.lookback:])
            
            # 绝对动量：价格必须在均线之上
            if current_price > ma_price:
                return_rate = (current_price - prices[-self.lookback]) / prices[-self.lookback]
                valid_momentum[etf] = return_rate
        # 2. 交易触发策略 (Trading Logic)
        if not valid_momentum:
            # 【交易信号：全场空仓】
            self.current_regime = 'CASH'
            self.current_asset = self.money_fund
            target_portfolio[self.money_fund] = self.total_capital
            return target_portfolio
        # 找出动量第一名的标的
        best_etf = max(valid_momentum, key=valid_momentum.get)
        current_etf_price = hist_data_dict[best_etf]['close'].iloc[-1]
        # 3. 状态机切换与仓位交易控制
        if self.current_asset != best_etf:
            # 【交易信号：换仓/新开仓】 动量标的发生切换，以当前价建立全新中轴
            self.current_regime = 'TREND'
            self.current_asset = best_etf
            self.base_price = current_etf_price
            # 初始化底仓：动量策略首次买入一般分配 60% 资金，预留 40% 给网格低吸
            target_portfolio[best_etf] = self.total_capital * 0.6
        else:
            # 【交易信号：网格维持期】 标的未变，动态跟踪最新价格触发网格增减仓
            price_change = (current_etf_price - self.base_price) / self.base_price
            current_holding_val = current_positions.get(best_etf, 0) * current_etf_price
            
            if price_change <= -self.grid_step:
                # 价格下跌超过2%：触发【低吸交易】，加仓10%
                new_target_val = min(self.total_capital, current_holding_val + (self.total_capital * 0.1))
                target_portfolio[best_etf] = new_target_val
                self.base_price = current_etf_price # 更新网格中轴
                
            elif price_change >= self.grid_step:
                # 价格上涨超过2%：触发【高抛交易】，减仓10%
                new_target_val = max(self.total_capital * 0.3, current_holding_val - (self.total_capital * 0.1))
                target_portfolio[best_etf] = new_target_val
                self.base_price = current_etf_price # 更新网格中轴
            else:
                # 价格在网格内震荡，【持股不动】
                target_portfolio[best_etf] = current_holding_val
        # 剩余的闲置资金自动归入货币基金
        allocated_val = sum(target_portfolio.values())
        if allocated_val < self.total_capital:
            target_portfolio[self.money_fund] = self.total_capital - allocated_val
        return target_portfolio

```
