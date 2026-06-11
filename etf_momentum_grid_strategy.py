# coding=utf-8
"""
ETF 双动量 + 动态网格交易策略

基于掘金量化 Python SDK 实现：
- 在 ETF 池中通过双动量（绝对动量 + 相对动量）选择最优标的
- 状态机管理：CASH（空仓持货基）和 TREND（趋势持股）
- 趋势持股期间通过网格交易低吸高抛增减仓位
- 闲置资金自动配置货币基金

参考文档: docs/strategies/etfMomentumGridStrategy.md
"""

from __future__ import print_function, absolute_import
from gm.api import *
import numpy as np
import pandas as pd


# ============================================================
# 策略参数配置（可修改）
# ============================================================

# ETF 标的池：主要宽基指数和行业 ETF
ETF_POOL = [
    'SHSE.510300',   # 沪深300ETF
    'SHSE.510050',   # 上证50ETF
    'SHSE.510500',   # 中证500ETF
    'SZSE.159915',   # 创业板ETF
    'SZSE.159919',   # 沪深300ETF(深)
    'SHSE.512880',   # 证券ETF
    'SHSE.512100',   # 中证1000ETF
    'SZSE.159845',   # 中证1000ETF(深)
    'SHSE.588000',   # 科创50ETF
]

# 货币基金（闲置资金去处）
MONEY_FUND = 'SHSE.511880'  # 银华日利

# 策略参数
TOTAL_CAPITAL = 500000       # 总资金（元）
LOOKBACK = 20                # 动量计算回看窗口（交易日）
GRID_STEP = 0.02             # 网格间距（2%）
INITIAL_POSITION_RATIO = 0.60  # 首次建仓比例（60%）
MIN_POSITION_RATIO = 0.30    # 网格减仓下限（30%）
GRID_TRADE_RATIO = 0.10      # 每次网格交易比例（10%）

# 执行时间
MOMENTUM_CHECK_TIME = '09:35:00'   # 每日动量检查和交易时间
GRID_CHECK_FREQUENCY = '300s'      # 日内网格检查频率（5分钟）


def init(context):
    """策略初始化"""
    # 1. 存储策略参数
    context.etf_pool = ETF_POOL
    context.money_fund = MONEY_FUND
    context.total_capital = TOTAL_CAPITAL
    context.lookback = LOOKBACK
    context.grid_step = GRID_STEP
    context.init_position_ratio = INITIAL_POSITION_RATIO
    context.min_position_ratio = MIN_POSITION_RATIO
    context.grid_trade_ratio = GRID_TRADE_RATIO

    # 2. 状态机初始化
    context.current_regime = 'CASH'     # CASH 或 TREND
    context.current_asset = None        # 当前持仓的 ETF symbol
    context.base_price = 0.0            # 网格锚定中轴价

    # 3. 记录日志用的辅助变量
    context.change_count = 0            # 换仓次数
    context.grid_trade_count = 0        # 网格交易次数

    # 4. 订阅行情数据
    #    ETF 池日线：用于动量计算
    subscribe_etf_str = ','.join(context.etf_pool)
    subscribe(symbols=subscribe_etf_str, frequency='1d', count=LOOKBACK + 10, format='df')
    #    ETF 池分钟线：用于日内网格监控
    subscribe(symbols=subscribe_etf_str, frequency=GRID_CHECK_FREQUENCY, count=10, format='df')
    #    货币基金日线
    subscribe(symbols=context.money_fund, frequency='1d', count=10, format='df')

    # 5. 设置定时任务：每日盘后动量检查
    schedule(schedule_func=algo_momentum_check, date_rule='1d', time_rule=MOMENTUM_CHECK_TIME)

    log(level='info', msg='ETF 动量网格策略初始化完成，ETF池: {}'.format(context.etf_pool), source='strategy')
    log(level='info', msg='货币基金: {}, 总资金: {}'.format(context.money_fund, context.total_capital), source='strategy')


def algo_momentum_check(context):
    """
    每日动量检查 + 交易信号生成任务：
    1. 计算 ETF 池中各标的的双动量
    2. 选择最优标的
    3. 状态机切换 / 维持
    4. 生成目标仓位并执行交易
    """
    log(level='info', msg='--- 每日动量检查 ---', source='strategy')

    # 1. 计算各 ETF 的动量值
    momentum_scores = _calculate_momentum(context)
    if momentum_scores is None:
        log(level='warning', msg='动量计算失败，保持现有仓位', source='strategy')
        return

    # 2. 获取当前持仓
    positions = context.account().positions()
    current_positions = _parse_current_positions(positions)

    # 3. 执行状态机逻辑
    _execute_state_machine(context, momentum_scores, current_positions)


def on_bar(context, bars):
    """
    分钟 bar 事件回调：用于日内网格交易监控
    """
    if context.current_regime != 'TREND' or context.current_asset is None:
        return

    # 获取当前持仓 ETF 的最新价格
    symbol = bars[0]['symbol'] if isinstance(bars, list) else bars['symbol']

    # 只处理当前持有的 ETF
    if symbol != context.current_asset:
        return

    # 检查是否需要网格交易
    _grid_trading_check(context, symbol)


def on_backtest_finished(context, indicator):
    """回测结束，输出绩效摘要"""
    print(indicator)
    print('换仓次数: {}, 网格交易次数: {}'.format(
        context.change_count, context.grid_trade_count))


# ============================================================
# 核心逻辑函数
# ============================================================

def _calculate_momentum(context):
    """
    计算 ETF 池中每个标的的双动量

    - 绝对动量：当前价格是否在 MA(lookback) 之上（必要条件）
    - 相对动量：过去 lookback 日的收益率（排名依据）

    返回: {etf_symbol: momentum_return_rate}，仅包含满足绝对动量的标的
    """
    end_time = context.now.strftime('%Y-%m-%d %H:%M:%S')
    valid_momentum = {}

    for etf in context.etf_pool:
        try:
            # 从 context.data 获取日线数据
            data = context.data(symbol=etf, frequency='1d', count=context.lookback + 1)
            if data is None or data.empty or len(data) < context.lookback:
                continue

            prices = data['close'].values
            current_price = prices[-1]
            ma_price = np.mean(prices[-context.lookback:])

            # 绝对动量：价格必须在均线之上
            if current_price > ma_price:
                # 相对动量：过去 lookback 日的收益率
                return_rate = (current_price - prices[-context.lookback]) / prices[-context.lookback]
                valid_momentum[etf] = return_rate

        except Exception as e:
            log(level='warning', msg='计算 {} 动量异常: {}'.format(etf, e), source='strategy')
            continue

    return valid_momentum


def _parse_current_positions(positions):
    """解析当前持仓，返回 {symbol: 持仓金额}"""
    holding_dict = {}
    if not positions:
        return holding_dict

    for pos in positions:
        symbol = pos['symbol']
        vol = pos['volume'] - pos['order_frozen']
        if vol <= 0:
            continue

        # 获取当前价格估算持仓金额
        price_info = current_price(symbols=symbol)
        if price_info:
            current_price_val = price_info[0].get('price', 0)
        else:
            current_price_val = 0

        if current_price_val > 0:
            holding_dict[symbol] = vol * current_price_val

    return holding_dict


def _execute_state_machine(context, momentum_scores, current_positions):
    """
    状态机执行逻辑

    状态: CASH（空仓/货基）  TREND（趋势持股）

    状态转换规则：
    - CASH → TREND: 出现满足绝对动量的标的（首次建仓）
    - TREND → CASH: 所有 ETF 都不满足绝对动量（全部跌破均线）
    - TREND → TREND:
        - 当前标的仍在均线上方且动量最高 → 维持，执行网格
        - 换仓（动量第一名切换）→ 卖出旧标的，买入新标的
    """
    target_portfolio = {}  # {symbol: 目标金额}

    # ---- 情况 1: 全场无满足绝对动量的标的 → 空仓 ----
    if not momentum_scores:
        if context.current_regime == 'TREND':
            log(level='info', msg='【交易信号：全场空仓】所有ETF跌破均线，转入货基', source='strategy')
            context.current_regime = 'CASH'
            context.current_asset = None
            context.base_price = 0.0

            # 清仓所有持仓 ETF
            for symbol, val in current_positions.items():
                if symbol != context.money_fund:
                    order_target_value(
                        symbol=symbol,
                        value=0,
                        position_side=PositionSide_Long,
                        order_type=OrderType_Market,
                        price=0
                    )

        # 全部资金归入货币基金
        target_portfolio[context.money_fund] = context.total_capital
        _execute_trades(context, target_portfolio, current_positions)
        return

    # ---- 情况 2: 选出动量最强的 ETF ----
    best_etf = max(momentum_scores, key=momentum_scores.get)
    best_momentum = momentum_scores[best_etf]

    # 获取当前最佳 ETF 的最新价格
    price_info = current_price(symbols=best_etf)
    if not price_info:
        return
    current_etf_price = price_info[0].get('price', 0)
    if current_etf_price <= 0:
        return

    # ---- 情况 2a: 新开仓或换仓 ----
    if context.current_asset != best_etf:
        if context.current_asset is None:
            log(level='info', msg='【交易信号：新开仓】选中 {}, 动量 {:.2%}, 建仓 {}%'.format(
                best_etf, best_momentum, INITIAL_POSITION_RATIO * 100), source='strategy')
        else:
            log(level='info', msg='【交易信号：换仓】从 {} 切换到 {}, 新动量 {:.2%}'.format(
                context.current_asset, best_etf, best_momentum), source='strategy')
            context.change_count += 1

        # 更新状态机
        context.current_regime = 'TREND'
        context.current_asset = best_etf
        context.base_price = current_etf_price

        # 清仓旧标的
        if context.current_asset != best_etf:
            for symbol in current_positions:
                if symbol != context.money_fund and symbol != best_etf:
                    order_target_value(
                        symbol=symbol,
                        value=0,
                        position_side=PositionSide_Long,
                        order_type=OrderType_Market,
                        price=0
                    )

        # 建立新仓：60% 资金买入，40% 预留给网格
        target_portfolio[best_etf] = context.total_capital * INITIAL_POSITION_RATIO

    # ---- 情况 2b: 维持当前标的，执行网格 ----
    else:
        price_change = (current_etf_price - context.base_price) / context.base_price
        current_holding_val = current_positions.get(best_etf, 0)

        if price_change <= -GRID_STEP:
            # 价格下跌超过 2%：低吸加仓
            new_target_val = min(
                context.total_capital,
                current_holding_val + context.total_capital * GRID_TRADE_RATIO
            )
            target_portfolio[best_etf] = new_target_val
            context.base_price = current_etf_price  # 更新网格中轴
            context.grid_trade_count += 1
            log(level='info', msg='【网格低吸】{} 下跌 {:.2%}，加仓至 {:.0f}'.format(
                best_etf, price_change, new_target_val), source='strategy')

        elif price_change >= GRID_STEP:
            # 价格上涨超过 2%：高抛减仓
            new_target_val = max(
                context.total_capital * MIN_POSITION_RATIO,
                current_holding_val - context.total_capital * GRID_TRADE_RATIO
            )
            target_portfolio[best_etf] = new_target_val
            context.base_price = current_etf_price  # 更新网格中轴
            context.grid_trade_count += 1
            log(level='info', msg='【网格高抛】{} 上涨 {:.2%}，减仓至 {:.0f}'.format(
                best_etf, price_change, new_target_val), source='strategy')

        else:
            # 价格在网格区间内：维持现有仓位
            target_portfolio[best_etf] = current_holding_val

    # 剩余资金归入货币基金
    allocated_val = sum(target_portfolio.values())
    if allocated_val < context.total_capital:
        target_portfolio[context.money_fund] = context.total_capital - allocated_val

    # 执行交易
    _execute_trades(context, target_portfolio, current_positions)


def _grid_trading_check(context, symbol):
    """
    日内分钟级别网格检查（实盘/仿真模式适用）

    在 on_bar 回调中触发，用于更高频率的网格交易检查
    """
    # 获取分钟 bar 数据
    bar_data = context.data(symbol=symbol, frequency=GRID_CHECK_FREQUENCY, count=5)
    if bar_data is None or bar_data.empty:
        return

    current_price = bar_data['close'].values[-1]
    if current_price <= 0:
        return

    price_change = (current_price - context.base_price) / context.base_price

    # 获取当前该标的的持仓金额
    positions = context.account().positions()
    current_holding_val = 0
    for pos in positions:
        if pos['symbol'] == symbol:
            vol = pos['volume'] - pos['order_frozen']
            if vol > 0:
                current_holding_val = vol * current_price
            break

    if abs(price_change) >= GRID_STEP:
        # 价格突破网格线，触发交易
        # 注意：此处去重逻辑确保不会在同一网格区间内重复交易
        # 通过 base_price 的更新来实现
        if price_change <= -GRID_STEP:
            new_target_val = min(
                context.total_capital,
                current_holding_val + context.total_capital * GRID_TRADE_RATIO
            )
            order_target_value(
                symbol=symbol,
                value=int(new_target_val),
                position_side=PositionSide_Long,
                order_type=OrderType_Market,
                price=0
            )
            context.base_price = current_price
            context.grid_trade_count += 1
            log(level='info', msg='【日内网格低吸】{} 价格 {:.2f}'.format(symbol, current_price), source='strategy')

        elif price_change >= GRID_STEP:
            new_target_val = max(
                context.total_capital * MIN_POSITION_RATIO,
                current_holding_val - context.total_capital * GRID_TRADE_RATIO
            )
            order_target_value(
                symbol=symbol,
                value=int(new_target_val),
                position_side=PositionSide_Long,
                order_type=OrderType_Market,
                price=0
            )
            context.base_price = current_price
            context.grid_trade_count += 1
            log(level='info', msg='【日内网格高抛】{} 价格 {:.2f}'.format(symbol, current_price), source='strategy')


def _execute_trades(context, target_portfolio, current_positions):
    """
    执行目标仓位调整交易

    使用 order_target_value 自动计算买卖差额
    """
    # 先处理卖出：不在目标中的持仓清仓
    for symbol in current_positions:
        if symbol not in target_portfolio:
            order_target_value(
                symbol=symbol,
                value=0,
                position_side=PositionSide_Long,
                order_type=OrderType_Market,
                price=0
            )

    # 再处理目标仓位
    for symbol, target_val in target_portfolio.items():
        order_target_value(
            symbol=symbol,
            value=int(target_val),
            position_side=PositionSide_Long,
            order_type=OrderType_Market,
            price=0
        )

    # 日志输出当前状态
    log(level='info', msg='状态: {} | 持仓: {} | 中轴价: {:.2f}'.format(
        context.current_regime,
        context.current_asset or '货基',
        context.base_price
    ), source='strategy')


# ============================================================
# 策略入口
# ============================================================
if __name__ == '__main__':
    """
    strategy_id: 策略ID（由系统生成）
    filename: 文件名（与本文件名保持一致）
    mode: 运行模式 MODE_LIVE(实时) 或 MODE_BACKTEST(回测)
    token: 用户token（系统设置-密钥管理中获取）
    backtest_start_time: 回测开始时间
    backtest_end_time: 回测结束时间
    backtest_adjust: 复权方式 ADJUST_NONE(不复权) ADJUST_PREV(前复权) ADJUST_POST(后复权)
    backtest_initial_cash: 回测初始资金
    backtest_commission_ratio: 回测佣金比例
    backtest_slippage_ratio: 回测滑点比例
    """
    run(
        strategy_id='your_strategy_id',
        filename='etf_momentum_grid_strategy.py',
        mode=MODE_BACKTEST,
        token='your_token',
        backtest_start_time='2022-01-01 08:00:00',
        backtest_end_time='2024-01-01 16:00:00',
        backtest_adjust=ADJUST_PREV,
        backtest_initial_cash=TOTAL_CAPITAL,
        backtest_commission_ratio=0.0001,
        backtest_slippage_ratio=0.0001,
    )
