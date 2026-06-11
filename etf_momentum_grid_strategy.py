# coding=utf-8
"""
ETF 双动量 + 动态网格交易策略（集成 ETF 筛选管道）

基于掘金量化 Python SDK 实现：
- 在 ETF 池中通过双动量（绝对动量 + 相对动量）选择最优标的
- 状态机管理：CASH（空仓持货基）和 TREND（趋势持股）
- 趋势持股期间通过网格交易低吸高抛增减仓位
- 闲置资金自动配置货币基金
- ★ 集成 EtfFilterPipeline：每周自动扫描全市场 ETF，
    执行交易状态/流动性/波动率质量/大类去重四层筛选，动态更新种子池

参考文档:
  docs/strategies/etfMomentumGridStrategy.md
  docs/strategies/etfFilterPipeline.md
"""

from __future__ import print_function, absolute_import
from gm.api import *
import numpy as np
import pandas as pd

# 筛选管道（仅需入口函数，内部封装了全流程）
from etf_filter_pipeline import gm_get_etf_seed_pool


# ============================================================
# 策略参数配置
# ============================================================

# ── 回退 ETF 池（当全市场筛选不可用时使用） ──
#      覆盖各大类资产，确保动量轮动有足够的低相关标的
FALLBACK_ETF_POOL = [
    # A股大盘
    'SHSE.510300',   # 沪深300ETF
    'SHSE.510050',   # 上证50ETF
    # A股中小盘
    'SHSE.510500',   # 中证500ETF
    'SHSE.512100',   # 中证1000ETF
    'SZSE.159915',   # 创业板ETF
    'SHSE.588000',   # 科创50ETF
    # 红利防御
    'SHSE.512890',   # 红利低波ETF
    # 跨境对冲
    'SHSE.513100',   # 纳指100ETF
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

# 定时任务时间
MOMENTUM_CHECK_TIME = '09:35:00'     # 每日动量检查和交易时间
POOL_REFRESH_TIME = '09:31:00'       # ETF 池刷新时间（盘前）
POOL_REFRESH_DATE_RULE = '1w'        # ETF 池刷新频率：每周一次

# 日内网格
GRID_CHECK_FREQUENCY = '300s'        # 日内网格检查频率（5分钟）


# ============================================================
# 策略入口
# ============================================================

def init(context):
    """策略初始化"""
    # 1. 存储策略参数
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

    # 3. 计数器
    context.change_count = 0            # 换仓次数
    context.grid_trade_count = 0        # 网格交易次数
    context.pool_refresh_count = 0      # 池刷新次数

    # 4. ★ 初始化 ETF 种子池（动态筛选 + 回退兜底）
    context.etf_pool = _initialize_etf_pool(context)
    log(level='info', msg='初始 ETF 池 ({} 只): {}'.format(
        len(context.etf_pool), context.etf_pool), source='strategy')

    # 5. 订阅行情数据
    _subscribe_pool(context)

    # 6. 设置定时任务
    #    每周刷新 ETF 池
    schedule(schedule_func=algo_refresh_etf_pool,
             date_rule=POOL_REFRESH_DATE_RULE, time_rule=POOL_REFRESH_TIME)
    #    每日动量检查 + 交易
    schedule(schedule_func=algo_momentum_check,
             date_rule='1d', time_rule=MOMENTUM_CHECK_TIME)

    log(level='info', msg='ETF 动量网格策略初始化完成', source='strategy')
    log(level='info', msg='货币基金: {}  总资金: {:,.0f}'.format(
        context.money_fund, context.total_capital), source='strategy')


# ============================================================
# 定时任务
# ============================================================

def algo_refresh_etf_pool(context):
    """
    定期刷新 ETF 种子池。

    每周执行一次，从全市场重新筛选健康 ETF。
    如果池发生变化，自动重新订阅行情。
    """
    log(level='info', msg='--- ETF 池刷新 ---', source='strategy')
    context.pool_refresh_count += 1

    try:
        new_pool = _initialize_etf_pool(context)
    except Exception as e:
        log(level='error', msg='ETF 池刷新异常: {}，保留现有池'.format(e), source='strategy')
        return

    if not new_pool:
        log(level='warning', msg='刷新结果为空，保留现有池', source='strategy')
        return

    old_pool = set(context.etf_pool)
    new_pool_set = set(new_pool)

    added = new_pool_set - old_pool
    removed = old_pool - new_pool_set

    if added or removed:
        log(level='info', msg='ETF 池变更: +{} -{}'.format(
            list(added), list(removed)), source='strategy')
        context.etf_pool = new_pool
        # 重新订阅新池
        _subscribe_pool(context)
    else:
        log(level='info', msg='ETF 池未变化，保持现有订阅', source='strategy')


def algo_momentum_check(context):
    """
    每日动量检查 + 交易信号生成：
    1. 计算 ETF 池中各标的的双动量
    2. 选择最优标的
    3. 状态机切换 / 维持
    4. 生成目标仓位并执行交易
    """
    log(level='info', msg='--- 每日动量检查 (池: {} 只) ---'.format(
        len(context.etf_pool)), source='strategy')

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


# ============================================================
# 事件回调
# ============================================================

def on_bar(context, bars):
    """
    分钟 bar 事件回调：日内网格交易监控
    """
    if context.current_regime != 'TREND' or context.current_asset is None:
        return

    symbol = bars[0]['symbol'] if isinstance(bars, list) else bars['symbol']

    if symbol != context.current_asset:
        return

    _grid_trading_check(context, symbol)


def on_backtest_finished(context, indicator):
    """回测结束，输出绩效摘要"""
    print(indicator)
    print('换仓次数: {}, 网格交易次数: {}, 池刷新次数: {}'.format(
        context.change_count, context.grid_trade_count, context.pool_refresh_count))


# ============================================================
# ETF 池管理
# ============================================================

def _initialize_etf_pool(context):
    """
    获取当前健康的 ETF 种子池。

    优先使用全市场动态筛选，失败时回退到预设池。
    """
    try:
        end_time = context.now.strftime('%Y-%m-%d %H:%M:%S')

        pool = gm_get_etf_seed_pool(
            end_time=end_time,
            context=context,
        )
        if pool:
            return pool
    except Exception as e:
        log(level='warning', msg='全市场 ETF 筛选失败: {}'.format(e), source='strategy')

    log(level='info', msg='使用回退 ETF 池: {}'.format(FALLBACK_ETF_POOL), source='strategy')
    return list(FALLBACK_ETF_POOL)


def _subscribe_pool(context):
    """
    订阅当前 ETF 池 + 货币基金的行情数据。

    使用 unsubscribe_previous=True 清理旧订阅。
    """
    pool = list(context.etf_pool)
    if not pool:
        log(level='error', msg='ETF 池为空，无法订阅', source='strategy')
        return

    # ETF 池日线：用于动量计算
    subscribe_str = ','.join(pool)
    subscribe(symbols=subscribe_str, frequency='1d',
              count=LOOKBACK + 10, format='df',
              unsubscribe_previous=True)

    # ETF 池分钟线：用于日内网格监控
    subscribe(symbols=subscribe_str, frequency=GRID_CHECK_FREQUENCY,
              count=10, format='df')

    # 货币基金日线
    subscribe(symbols=context.money_fund, frequency='1d',
              count=10, format='df')

    log(level='info', msg='已订阅 {} 只 ETF + 货基'.format(len(pool)),
        source='strategy')


# ============================================================
# 动量计算
# ============================================================

def _calculate_momentum(context):
    """
    计算 ETF 池中每个标的的双动量。

    - 绝对动量：当前价格是否在 MA(lookback) 之上（必要条件）
    - 相对动量：过去 lookback 日的收益率（排名依据）

    返回: {etf_symbol: return_rate}，仅包含满足绝对动量的标的
    """
    valid_momentum = {}

    for etf in context.etf_pool:
        try:
            data = context.data(symbol=etf, frequency='1d',
                                count=context.lookback + 1)
            if data is None or data.empty or len(data) < context.lookback:
                continue

            prices = data['close'].values
            current_price = prices[-1]
            ma_price = np.mean(prices[-context.lookback:])

            # 绝对动量：价格必须在均线之上
            if current_price > ma_price:
                return_rate = ((current_price - prices[-context.lookback])
                               / prices[-context.lookback])
                valid_momentum[etf] = return_rate

        except Exception as e:
            log(level='warning', msg='计算 {} 动量异常: {}'.format(etf, e),
                source='strategy')
            continue

    return valid_momentum


# ============================================================
# 持仓与交易
# ============================================================

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

        price_info = current_price(symbols=symbol)
        if price_info:
            p = price_info[0].get('price', 0)
        else:
            p = 0

        if p > 0:
            holding_dict[symbol] = vol * p

    return holding_dict


def _execute_state_machine(context, momentum_scores, current_positions):
    """
    状态机: CASH ←→ TREND

    - CASH → TREND: 出现满足绝对动量的标的（首次建仓）
    - TREND → CASH: 所有 ETF 都不满足绝对动量（全部跌破均线）
    - TREND → TREND: 维持当前标的或切换到动量第一名
    """
    target_portfolio = {}

    # ---- 全场空仓 ----
    if not momentum_scores:
        if context.current_regime == 'TREND':
            log(level='info', msg='【交易信号：全场空仓】所有ETF跌破均线，转入货基',
                source='strategy')
            context.current_regime = 'CASH'
            context.current_asset = None
            context.base_price = 0.0

            for symbol in current_positions:
                if symbol != context.money_fund:
                    order_target_value(
                        symbol=symbol, value=0,
                        position_side=PositionSide_Long,
                        order_type=OrderType_Market, price=0,
                    )

        target_portfolio[context.money_fund] = context.total_capital
        _execute_trades(context, target_portfolio, current_positions)
        return

    # ---- 选出动量最强的 ETF ----
    best_etf = max(momentum_scores, key=momentum_scores.get)
    best_momentum = momentum_scores[best_etf]

    price_info = current_price(symbols=best_etf)
    if not price_info:
        return
    current_etf_price = price_info[0].get('price', 0)
    if current_etf_price <= 0:
        return

    # ---- 新开仓或换仓 ----
    if context.current_asset != best_etf:
        if context.current_asset is None:
            log(level='info', msg='【交易信号：新开仓】选中 {}，动量 {:.2%}，建仓 {}%'.format(
                best_etf, best_momentum, INITIAL_POSITION_RATIO * 100),
                source='strategy')
        else:
            log(level='info', msg='【交易信号：换仓】{} → {}，新动量 {:.2%}'.format(
                context.current_asset, best_etf, best_momentum),
                source='strategy')
            context.change_count += 1

        context.current_regime = 'TREND'
        context.current_asset = best_etf
        context.base_price = current_etf_price

        # 清仓旧标的
        for symbol in current_positions:
            if symbol != context.money_fund and symbol != best_etf:
                order_target_value(
                    symbol=symbol, value=0,
                    position_side=PositionSide_Long,
                    order_type=OrderType_Market, price=0,
                )

        # 建立新仓
        target_portfolio[best_etf] = context.total_capital * INITIAL_POSITION_RATIO

    # ---- 维持当前标的，执行网格 ----
    else:
        price_change = (current_etf_price - context.base_price) / context.base_price
        current_holding_val = current_positions.get(best_etf, 0)

        if price_change <= -GRID_STEP:
            new_target_val = min(
                context.total_capital,
                current_holding_val + context.total_capital * GRID_TRADE_RATIO,
            )
            target_portfolio[best_etf] = new_target_val
            context.base_price = current_etf_price
            context.grid_trade_count += 1
            log(level='info', msg='【网格低吸】{} 下跌 {:.2%}，加仓至 {:,.0f}'.format(
                best_etf, price_change, new_target_val), source='strategy')

        elif price_change >= GRID_STEP:
            new_target_val = max(
                context.total_capital * MIN_POSITION_RATIO,
                current_holding_val - context.total_capital * GRID_TRADE_RATIO,
            )
            target_portfolio[best_etf] = new_target_val
            context.base_price = current_etf_price
            context.grid_trade_count += 1
            log(level='info', msg='【网格高抛】{} 上涨 {:.2%}，减仓至 {:,.0f}'.format(
                best_etf, price_change, new_target_val), source='strategy')

        else:
            target_portfolio[best_etf] = current_holding_val

    # 剩余资金归入货币基金
    allocated_val = sum(target_portfolio.values())
    if allocated_val < context.total_capital:
        target_portfolio[context.money_fund] = context.total_capital - allocated_val

    _execute_trades(context, target_portfolio, current_positions)


def _grid_trading_check(context, symbol):
    """
    日内分钟级网格检查（实盘/仿真适用）。

    在 on_bar 回调中触发，用于更高频的网格交易。
    """
    bar_data = context.data(symbol=symbol, frequency=GRID_CHECK_FREQUENCY,
                            count=5)
    if bar_data is None or bar_data.empty:
        return

    current_price = bar_data['close'].values[-1]
    if current_price <= 0:
        return

    price_change = (current_price - context.base_price) / context.base_price

    positions = context.account().positions()
    current_holding_val = 0
    for pos in positions:
        if pos['symbol'] == symbol:
            vol = pos['volume'] - pos['order_frozen']
            if vol > 0:
                current_holding_val = vol * current_price
            break

    if abs(price_change) >= GRID_STEP:
        if price_change <= -GRID_STEP:
            new_target_val = min(
                context.total_capital,
                current_holding_val + context.total_capital * GRID_TRADE_RATIO,
            )
            order_target_value(
                symbol=symbol, value=int(new_target_val),
                position_side=PositionSide_Long,
                order_type=OrderType_Market, price=0,
            )
            context.base_price = current_price
            context.grid_trade_count += 1
            log(level='info', msg='【日内网格低吸】{} 价格 {:.2f}'.format(
                symbol, current_price), source='strategy')

        elif price_change >= GRID_STEP:
            new_target_val = max(
                context.total_capital * MIN_POSITION_RATIO,
                current_holding_val - context.total_capital * GRID_TRADE_RATIO,
            )
            order_target_value(
                symbol=symbol, value=int(new_target_val),
                position_side=PositionSide_Long,
                order_type=OrderType_Market, price=0,
            )
            context.base_price = current_price
            context.grid_trade_count += 1
            log(level='info', msg='【日内网格高抛】{} 价格 {:.2f}'.format(
                symbol, current_price), source='strategy')


def _execute_trades(context, target_portfolio, current_positions):
    """
    执行目标仓位调整。先卖出不在目标中的持仓，再调整目标仓位。
    """
    for symbol in current_positions:
        if symbol not in target_portfolio:
            order_target_value(
                symbol=symbol, value=0,
                position_side=PositionSide_Long,
                order_type=OrderType_Market, price=0,
            )

    for symbol, target_val in target_portfolio.items():
        order_target_value(
            symbol=symbol, value=int(target_val),
            position_side=PositionSide_Long,
            order_type=OrderType_Market, price=0,
        )

    log(level='info', msg='状态: {} | 持仓: {} | 中轴价: {:.2f}'.format(
        context.current_regime, context.current_asset or '货基',
        context.base_price), source='strategy')


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
