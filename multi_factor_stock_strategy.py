# coding=utf-8
"""
多因子选股 + 定期再平衡 + 动态止损策略

基于掘金量化 Python SDK 实现：
- 使用多因子模型（估值、动量、质量、波动率等）对股票池打分排序
- 每月调仓日等权配置前 N 只股票
- 每日盘中检测持仓个股，触发止损线立即清仓
- 非调仓日维持现有持仓不变，减少交易摩擦

参考文档: docs/strategies/multiFactorStockTradingStrategy.md
"""

from __future__ import print_function, absolute_import
from gm.api import *
import numpy as np
import pandas as pd


# ============================================================
# 策略参数配置（可修改）
# ============================================================
STOCK_NUM = 30                 # 持仓股票数量
TOTAL_CAPITAL = 500000         # 总资金（元）
STOP_LOSS_PCT = 0.10           # 个股止损线（10%）
REBALANCE_DATE_RULE = '1m'     # 调仓频率：每月
REBALANCE_TIME_RULE = '09:35:00'  # 调仓时间
STOP_LOSS_TIME_RULE = '09:40:00'  # 止损检查时间（每日）

# 选股池：沪深300成分股
STOCK_INDEX = 'SHSE.000300'

# 因子计算用的历史窗口
MOMENTUM_WINDOW = 60           # 动量因子：60个交易日
VOLATILITY_WINDOW = 60         # 波动率因子：60个交易日

# 多因子权重（可根据回测结果调整）
FACTOR_WEIGHTS = {
    'value': 0.30,      # 估值因子（EP, BP）
    'momentum': 0.20,   # 动量因子
    'quality': 0.25,    # 质量因子（ROE）
    'volatility': 0.15, # 低波动因子（取负值）
    'size': 0.10,       # 规模因子（流通市值，取负值）
}


def init(context):
    """策略初始化"""
    # 1. 存储策略参数到全局 context
    context.stock_num = STOCK_NUM
    context.total_capital = TOTAL_CAPITAL
    context.stop_loss_pct = STOP_LOSS_PCT
    context.single_stock_budget = TOTAL_CAPITAL / STOCK_NUM
    context.factor_weights = FACTOR_WEIGHTS

    # 2. 获取股票池：沪深300成分股
    context.stock_pool = _get_stock_pool(context)
    if not context.stock_pool:
        log(level='error', msg='获取股票池失败，策略终止', source='strategy')
        stop()
        return

    log(level='info', msg='股票池数量: {}'.format(len(context.stock_pool)), source='strategy')

    # 3. 订阅股票池的日线行情（用于实时价格和止损监控）
    #    同时也为定时任务中的因子计算订阅分钟数据
    subscribe_symbols = ','.join(context.stock_pool)
    subscribe(symbols=subscribe_symbols, frequency='1d', count=1, format='df')
    subscribe(symbols=subscribe_symbols, frequency='60s', count=1, format='df')

    # 4. 设置定时任务
    #    每月调仓日执行选股和调仓
    schedule(schedule_func=algo_rebalance, date_rule=REBALANCE_DATE_RULE, time_rule=REBALANCE_TIME_RULE)
    #    每日盘中执行止损检查
    schedule(schedule_func=algo_stop_loss_check, date_rule='1d', time_rule=STOP_LOSS_TIME_RULE)

    # 5. 记录调仓日标记
    context.is_rebalance_day = False
    context.last_trade_date = None

    log(level='info', msg='多因子选股策略初始化完成', source='strategy')


def algo_rebalance(context):
    """
    月度调仓任务：
    1. 获取最新财务数据和行情数据
    2. 多因子打分
    3. 生成目标持仓
    4. 执行调仓交易
    """
    log(level='info', msg='【调仓日触发】开始多因子选股流程...', source='strategy')

    # 设置调仓标记
    context.is_rebalance_day = True

    # 1. 获取当前持仓
    positions = context.account().positions()
    current_holdings = _parse_positions(positions)

    # 2. 获取历史行情数据用于计算因子
    end_time = context.now.strftime('%Y-%m-%d %H:%M:%S')
    hist_data = _fetch_historical_data(context, end_time)

    if hist_data is None or hist_data.empty:
        log(level='error', msg='获取历史行情数据失败，跳过调仓', source='strategy')
        context.is_rebalance_day = False
        return

    # 3. 获取财务数据用于价值和质量因子
    fundamental_data = _fetch_fundamental_data(context)

    # 4. 多因子打分
    factor_scores = _calculate_factor_scores(hist_data, fundamental_data)
    if factor_scores is None or factor_scores.empty:
        log(level='error', msg='因子打分失败，跳过调仓', source='strategy')
        context.is_rebalance_day = False
        return

    # 5. 选出得分最高的 top N 只股票
    top_stocks = factor_scores.nlargest(context.stock_num, 'total_score')['symbol'].tolist()
    log(level='info', msg='本期入选股票: {}'.format(top_stocks), source='strategy')

    # 6. 生成目标仓位并执行交易
    _execute_rebalance(context, top_stocks, current_holdings, hist_data)

    context.is_rebalance_day = False
    context.last_trade_date = context.now.strftime('%Y-%m-%d')
    log(level='info', msg='【调仓完成】', source='strategy')


def algo_stop_loss_check(context):
    """
    每日止损检查任务：
    遍历当前持仓，对触发止损线的个股强制清仓
    """
    positions = context.account().positions()
    if not positions:
        return

    for pos in positions:
        symbol = pos['symbol']
        cost_basis = pos['vwap']          # 持仓均价
        current_volume = pos['volume'] - pos['order_frozen']

        if current_volume <= 0:
            continue

        # 获取当前最新价格
        price_info = current_price(symbols=symbol)
        if not price_info:
            continue
        current_price_val = price_info[0].get('price', 0)
        if current_price_val <= 0:
            continue

        # 计算盈亏
        pnl = (current_price_val - cost_basis) / cost_basis
        if pnl <= -context.stop_loss_pct:
            # 触发止损：以市价清仓
            log(level='warning',
                msg='【风控告警】股票 {} 触发止损，涨跌幅 {:.2%}，成本 {:.2f}，现价 {:.2f}，立即清仓！'.format(
                    symbol, pnl, cost_basis, current_price_val),
                source='strategy')

            # 清仓该股票（按持仓量卖出）
            order_volume(
                symbol=symbol,
                volume=current_volume,
                side=OrderSide_Sell,
                order_type=OrderType_Market,
                position_effect=PositionEffect_Close,
                price=0
            )


def on_bar(context, bars):
    """
    bar 数据事件回调（备用，主逻辑通过 schedule 驱动）
    """
    pass


def on_backtest_finished(context, indicator):
    """回测结束，打印绩效指标"""
    print(indicator)


# ============================================================
# 内部辅助函数
# ============================================================

def _get_stock_pool(context):
    """获取选股池（沪深300成分股）"""
    try:
        constituents = stk_get_index_constituents(index=STOCK_INDEX)
        if constituents is not None and not constituents.empty:
            return constituents['symbol'].tolist()
    except Exception as e:
        log(level='error', msg='获取指数成分股失败: {}'.format(e), source='strategy')

    # Fallback: 使用部分代表性股票
    fallback_pool = [
        'SHSE.600519', 'SHSE.600036', 'SHSE.601318', 'SHSE.600900',
        'SHSE.601166', 'SHSE.600276', 'SHSE.600030', 'SHSE.601012',
        'SHSE.600887', 'SHSE.601398', 'SZSE.000858', 'SZSE.000333',
        'SZSE.002594', 'SZSE.300750', 'SZSE.000001', 'SZSE.002475',
    ]
    log(level='warning', msg='使用 fallback 股票池，数量: {}'.format(len(fallback_pool)), source='strategy')
    return fallback_pool


def _parse_positions(positions):
    """解析持仓列表为字典 {symbol: dict}"""
    holdings = {}
    if not positions:
        return holdings
    for pos in positions:
        symbol = pos['symbol']
        vol = pos['volume'] - pos['order_frozen']
        if vol <= 0:
            continue
        holdings[symbol] = {
            'volume': vol,
            'vwap': pos['vwap'],
            'market_value': pos.get('market_value', 0),
        }
    return holdings


def _fetch_historical_data(context, end_time):
    """拉取股票池的历史日线数据"""
    all_data = []
    symbols_str = ','.join(context.stock_pool[:50])  # 分批获取避免超时

    # 计算开始时间：取动量窗口 + 额外余量
    start_time = get_previous_n_trading_dates(
        exchange='SHSE',
        date=context.now.strftime('%Y-%m-%d'),
        n=MOMENTUM_WINDOW + 10
    )
    if not start_time:
        return None
    start_date = start_time[0]

    try:
        # 分批拉取数据（每次最多取部分标的）
        batch_size = 20
        for i in range(0, len(context.stock_pool), batch_size):
            batch = context.stock_pool[i:i + batch_size]
            batch_str = ','.join(batch)
            data = history(
                symbol=batch_str,
                frequency='1d',
                start_time=start_date,
                end_time=end_time,
                fields='symbol,close,volume,amount,eob',
                adjust=ADJUST_PREV,
                df=True
            )
            if data is not None and not data.empty:
                all_data.append(data)

        if not all_data:
            return None

        result = pd.concat(all_data, ignore_index=True)
        return result
    except Exception as e:
        log(level='error', msg='拉取历史数据异常: {}'.format(e), source='strategy')
        return None


def _fetch_fundamental_data(context):
    """拉取基本面数据（估值和盈利能力）"""
    try:
        symbols_str = ','.join(context.stock_pool[:50])
        date_str = context.now.strftime('%Y-%m-%d')

        # 获取最新年报/三季度报的财务数据
        # ROE 通过净利润/股东权益近似估算，这里使用资产负债表和利润表字段
        balance_fields = 'ttl_cur_ast,ttl_ncur_ast,ttl_cur_liab,ttl_ncur_liab,tot_shrhldr_eqy'
        balance = stk_get_fundamentals_balance_pt(
            symbols=symbols_str,
            rpt_type=None,
            date=date_str,
            fields=balance_fields,
            df=True
        )
        return balance
    except Exception as e:
        log(level='warning', msg='获取财务数据失败: {}，仅使用行情因子'.format(e), source='strategy')
        return None


def _calculate_factor_scores(hist_data, fundamental_data):
    """
    计算多因子得分

    因子体系：
    - 价值因子: EP (1/PE 近似) — 用最新价 vs 每股净资产近似
    - 动量因子: 过去 N 日收益率
    - 质量因子: ROE 近似
    - 波动率因子: 过去 N 日波动率（越低越好）
    - 规模因子: 流通市值（越小越好，捕捉小市值溢价）

    处理流程:
    MAD 去极值 → Z-Score 标准化 → 加权求和 → 排名
    """
    symbols = hist_data['symbol'].unique()
    scores = []

    for symbol in symbols:
        df_stock = hist_data[hist_data['symbol'] == symbol].sort_values('eob')
        if len(df_stock) < MOMENTUM_WINDOW:
            continue

        closes = df_stock['close'].values
        volumes = df_stock['volume'].values
        amounts = df_stock['amount'].values

        current_price = closes[-1]

        # --- 动量因子：过去N日收益率 ---
        momentum_ret = (closes[-1] - closes[-min(MOMENTUM_WINDOW, len(closes))]) / \
                       closes[-min(MOMENTUM_WINDOW, len(closes))]

        # --- 波动率因子：过去N日日收益率标准差 ---
        if len(closes) >= VOLATILITY_WINDOW:
            daily_ret = np.diff(closes[-VOLATILITY_WINDOW:]) / closes[-VOLATILITY_WINDOW:-1]
            volatility = np.std(daily_ret) if len(daily_ret) > 0 else 0
        else:
            volatility = 0

        # --- 价值因子：用成交额/成交量作为价格替代 ---
        avg_volume = np.mean(volumes[-20:]) if len(volumes) >= 20 else np.mean(volumes)
        avg_turnover = np.mean(amounts[-20:]) if len(amounts) >= 20 else np.mean(amounts)

        # --- 规模因子：日均成交额作为流通性代理 ---
        size_factor = avg_turnover if avg_turnover > 0 else 0

        scores.append({
            'symbol': symbol,
            'momentum': momentum_ret,
            'volatility': -volatility,    # 取负值（低波动更好）
            'value_proxy': 1.0 / (current_price + 1e-6),  # 低价股偏好（简化）
            'size_factor': -np.log(size_factor + 1e-6) if size_factor > 0 else 0,  # 取负值（小市值偏好）
        })

    if not scores:
        return None

    scores_df = pd.DataFrame(scores)

    # 加入财务因子（如果可用）
    if fundamental_data is not None and not fundamental_data.empty:
        # 简化处理：使用净资产/总资产比例作为质量因子
        for idx, row in scores_df.iterrows():
            sym = row['symbol']
            fund_row = fundamental_data[fundamental_data['symbol'] == sym]
            if not fund_row.empty:
                # 简化：如果获取到财务数据，质量因子 = 净资产 / 总资产
                try:
                    equity = fund_row.iloc[0].get('tot_shrhldr_eqy', 0)
                    total_asset = fund_row.iloc[0].get('ttl_cur_ast', 0) + \
                                  fund_row.iloc[0].get('ttl_ncur_ast', 0)
                    if equity and total_asset and total_asset > 0:
                        scores_df.loc[idx, 'quality'] = equity / total_asset
                    else:
                        scores_df.loc[idx, 'quality'] = 0
                except Exception:
                    scores_df.loc[idx, 'quality'] = 0
            else:
                scores_df.loc[idx, 'quality'] = 0
    else:
        scores_df['quality'] = 0

    # --- MAD 去极值 ---
    factor_cols = ['momentum', 'volatility', 'value_proxy', 'size_factor', 'quality']
    for col in factor_cols:
        if col in scores_df.columns:
            median_val = scores_df[col].median()
            mad_val = (scores_df[col] - median_val).abs().median()
            if mad_val == 0:
                mad_val = scores_df[col].std()
                if mad_val == 0:
                    mad_val = 1e-6
            # 截断超过 ±3 倍 MAD 的值
            upper = median_val + 3 * mad_val
            lower = median_val - 3 * mad_val
            scores_df[col] = scores_df[col].clip(lower, upper)

    # --- Z-Score 标准化 ---
    for col in factor_cols:
        if col in scores_df.columns:
            mean_val = scores_df[col].mean()
            std_val = scores_df[col].std()
            if std_val == 0:
                std_val = 1e-6
            scores_df[col + '_zscore'] = (scores_df[col] - mean_val) / std_val

    # --- 加权总分 ---
    factor_map = {
        'momentum_zscore': FACTOR_WEIGHTS['momentum'],
        'volatility_zscore': FACTOR_WEIGHTS['volatility'],
        'value_proxy_zscore': FACTOR_WEIGHTS['value'],
        'size_factor_zscore': FACTOR_WEIGHTS['size'],
        'quality_zscore': FACTOR_WEIGHTS['quality'],
    }

    scores_df['total_score'] = 0
    for col, weight in factor_map.items():
        if col in scores_df.columns:
            scores_df['total_score'] += scores_df[col].fillna(0) * weight

    return scores_df


def _execute_rebalance(context, top_stocks, current_holdings, hist_data):
    """
    执行调仓交易：
    - 卖出不在 top_stocks 中的持仓
    - 买入 top_stocks 中未持仓的股票
    - 已持有的 top_stocks 调整到目标仓位
    """
    # 计算每只股票的目标金额
    target_value = context.single_stock_budget

    # 获取当前所有持仓的 symbol
    current_symbols = set(current_holdings.keys())
    target_symbols = set(top_stocks)

    # 需要卖出的：当前持仓但不在目标列表中
    to_sell = current_symbols - target_symbols
    for symbol in to_sell:
        if symbol in current_holdings:
            vol = current_holdings[symbol]['volume']
            if vol > 0:
                order_volume(
                    symbol=symbol,
                    volume=vol,
                    side=OrderSide_Sell,
                    order_type=OrderType_Market,
                    position_effect=PositionEffect_Close,
                    price=0
                )
                log(level='info', msg='卖出调仓: {} {}股'.format(symbol, vol), source='strategy')

    # 需要买入的：目标列表中的（使用 order_target_value 自动计算差额）
    for symbol in top_stocks:
        order_target_value(
            symbol=symbol,
            value=int(target_value),
            position_side=PositionSide_Long,
            order_type=OrderType_Market,
            price=0
        )

    log(level='info', msg='调仓完成: 目标持仓 {} 只，卖出 {} 只'.format(
        len(top_stocks), len(to_sell)), source='strategy')


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
        filename='multi_factor_stock_strategy.py',
        mode=MODE_BACKTEST,
        token='your_token',
        backtest_start_time='2022-01-01 08:00:00',
        backtest_end_time='2024-01-01 16:00:00',
        backtest_adjust=ADJUST_PREV,
        backtest_initial_cash=TOTAL_CAPITAL,
        backtest_commission_ratio=0.0003,
        backtest_slippage_ratio=0.0001,
    )
