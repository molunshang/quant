# coding=utf-8
"""
ETF 筛选与数据清洗管道（EtfFilterPipeline）

对全市场 ETF 执行四层筛选，只有通过全部层级的标的才能进入动量轮动池。
所有筛选层均基于掘金免费 SDK 可获取的数据，不依赖付费接口或外部数据源。

筛选流程：
  1. 交易状态筛选 — 剔除停牌、临近退市标的            (get_symbols)
  2. 流动性筛选   — 20 日均成交额 > 5000 万             (history)
  3. 波动率质量   — 年化波动率 < 40% + 价格连续性检查   (history)
  4. 大类资产去重 — 每类只保留流动性最强的一只

可选注入（有外部数据时启用）：
  - aum_dict: {symbol: AUM(元)} → 规模筛选
  - premium_dict: {symbol: 折溢价率} → 折溢价筛选（仅实时模式）

参考文档: docs/strategies/etfFilterPipeline.md
"""

from __future__ import print_function, absolute_import
import numpy as np
import pandas as pd


# ============================================================
# 大类资产映射表
# ============================================================

ASSET_CLASS_MAPPING = {
    'A股大盘': [
        'SHSE.510300',   # 华泰柏瑞沪深300ETF
        'SHSE.510050',   # 华夏上证50ETF
        'SHSE.510180',   # 华安上证180ETF
        'SZSE.159919',   # 嘉实沪深300ETF
    ],
    'A股中小盘': [
        'SHSE.510500',   # 南方中证500ETF
        'SHSE.512100',   # 南方中证1000ETF
        'SHSE.515080',   # 广发中证2000ETF
        'SZSE.159845',   # 华夏中证1000ETF
        'SZSE.159902',   # 华夏中小板ETF
    ],
    'A股创业板/科创': [
        'SZSE.159915',   # 易方达创业板ETF
        'SHSE.588000',   # 华夏科创50ETF
        'SHSE.588080',   # 易方达科创50ETF
        'SZSE.159781',   # 南方双创50ETF
    ],
    '红利防御': [
        'SHSE.515100',   # 景顺长城红利低波100ETF
        'SHSE.510880',   # 华泰柏瑞红利ETF
        'SHSE.512890',   # 华泰柏瑞红利低波ETF
    ],
    '跨境对冲': [
        'SHSE.513100',   # 国泰纳指100ETF
        'SHSE.513500',   # 博时标普500ETF
        'SHSE.513030',   # 华安德國30ETF
        'SZSE.159941',   # 广发纳指100ETF
    ],
    '商品避险': [
        'SHSE.518880',   # 华安黄金ETF
        'SHSE.518850',   # 华夏黄金ETF
        'SZSE.159980',   # 大成有色ETF
        'SHSE.510170',   # 国联安商品ETF
    ],
}

ENABLE_FALLBACK_OTHER = False
MAX_OTHER_SLOTS = 2


# ============================================================
# 筛选管道
# ============================================================

class EtfFilterPipeline:
    """
    ETF 筛选管道。

    所有筛选层均使用掘金免费 SDK 可获取的数据。AUM 和折溢价率
    作为可选注入项——用户有外部数据时传入即可启用对应筛选层。
    """

    def __init__(self,
                 min_volume_20d=50_000_000,
                 max_annual_vol=0.40,
                 min_aum=500_000_000,
                 max_premium_rate=0.01,
                 asset_class_mapping=None):
        """
        :param min_volume_20d:   20 日均成交额阈值（元），默认 5000 万
        :param max_annual_vol:   年化波动率上限，默认 40%
        :param min_aum:          最小资产净值（元），默认 5 亿。
                                 需要传入 aum_dict 才会生效。
        :param max_premium_rate: 最大折溢价率绝对值，默认 1%。
                                 需要传入 premium_dict 才会生效。
        :param asset_class_mapping: 大类资产映射
        """
        self.min_volume_20d = min_volume_20d
        self.max_annual_vol = max_annual_vol
        self.min_aum = min_aum
        self.max_premium_rate = max_premium_rate
        self.asset_class_mapping = asset_class_mapping or ASSET_CLASS_MAPPING

        self._code_to_class = {}
        for class_name, codes in self.asset_class_mapping.items():
            for code in codes:
                self._code_to_class[code] = class_name

    # ----------------------------------------------------------
    # 核心筛选
    # ----------------------------------------------------------

    def filter_and_select_seeds(self,
                                etf_info_df,
                                daily_bar_dict,
                                aum_dict=None,
                                premium_dict=None,
                                verbose=True):
        """
        执行完整的四层筛选流程。

        :param etf_info_df: DataFrame
            全市场 ETF 基础信息，必须包含列: ['symbol']
            可选列: ['is_suspended', 'delisted_date', 'sec_name']
        :param daily_bar_dict: dict
            格式: {symbol: DataFrame(columns=['close','amount','volume'])}
            每日 OHLCV 数据，排序不限（内部自动按日期排序）。
            必须覆盖至少 20 个交易日。
        :param aum_dict: dict, 可选
            {symbol: float} 每个 ETF 的最新资产净值（元）。
            传入则启用规模筛选层；不传则跳过。
        :param premium_dict: dict, 可选
            {symbol: float} 每个 ETF 的当前折溢价率。
            传入则启用折溢价筛选层；不传则跳过。
        :param verbose: 是否打印筛选日志
        :return: list[str], 通过全部筛选的 ETF symbol 列表
        """
        if etf_info_df is None or etf_info_df.empty:
            self._log(verbose, '输入为空 → 空池')
            return []

        if not daily_bar_dict:
            self._log(verbose, '无行情数据 → 空池')
            return []

        symbols_all = set(etf_info_df['symbol'].tolist())
        symbols_with_data = set(daily_bar_dict.keys())
        symbols_common = symbols_all & symbols_with_data
        self._log(verbose, '全市场 {} 只, 有行情数据 {} 只, 交集 {} 只'.format(
            len(symbols_all), len(symbols_with_data), len(symbols_common)))

        # ---- 第 1 层: 交易状态筛选 ----
        # 剔除停牌、临近退市、数据不足的标的
        passed = self._filter_trading_status(etf_info_df, daily_bar_dict,
                                             symbols_common, verbose)
        if not passed:
            return []

        # ---- 第 2 层: 流动性筛选 ----
        # 20 日均成交额 > 5000 万
        # 同时这也是规模代理——日均成交额不足的 ETF 规模也不会大
        passed = self._filter_liquidity(passed, daily_bar_dict, verbose)
        if not passed:
            return []

        # ---- 第 3 层: 波动率质量 ----
        # 年化波动率 < 40% + 无异常价格跳空
        passed = self._filter_volatility_quality(passed, daily_bar_dict, verbose)
        if not passed:
            return []

        # ---- 可选层 A: 规模筛选（需外部 AUM 数据） ----
        if aum_dict is not None:
            passed = self._filter_aum(passed, aum_dict, verbose)
            if not passed:
                return []
        else:
            self._log(verbose, '规模筛选(AUM): 跳过（未传入 aum_dict）。'
                      '流动性筛选已作为规模代理。')

        # ---- 可选层 B: 折溢价筛选（需外部实时数据） ----
        if premium_dict is not None:
            passed = self._filter_premium(passed, premium_dict, verbose)
            if not passed:
                return []
        else:
            self._log(verbose, '折溢价筛选: 跳过（未传入 premium_dict）。'
                      '仅实时模式 + 已订阅 tick 时可用。')

        # ---- 第 4 层: 大类资产去重 ----
        final_pool = self._dedup_by_asset_class(passed, daily_bar_dict, verbose)

        self._log(verbose, '=' * 40)
        self._log(verbose, '最终种子池 ({} 只): {}'.format(
            len(final_pool), final_pool))
        return final_pool

    # ----------------------------------------------------------
    # 各筛选层实现
    # ----------------------------------------------------------

    def _filter_trading_status(self, etf_info_df, daily_bar_dict,
                                symbols_common, verbose):
        """第 1 层: 剔除停牌 / 退市 / 数据不足"""
        passed = []
        skipped_suspended = 0
        skipped_delisting = 0
        skipped_insufficient = 0

        for sym in symbols_common:
            bar_df = daily_bar_dict.get(sym)
            if bar_df is None or len(bar_df) < 20:
                skipped_insufficient += 1
                continue

            # 检查是否停牌
            if 'is_suspended' in etf_info_df.columns:
                row = etf_info_df[etf_info_df['symbol'] == sym]
                if not row.empty and row.iloc[0].get('is_suspended'):
                    skipped_suspended += 1
                    continue

            # 检查是否临近退市（30 天内）
            if 'delisted_date' in etf_info_df.columns:
                row = etf_info_df[etf_info_df['symbol'] == sym]
                if not row.empty:
                    dd = row.iloc[0].get('delisted_date')
                    if pd.notna(dd):
                        try:
                            days_left = (pd.Timestamp(dd) - pd.Timestamp.now()).days
                            if days_left < 30:
                                skipped_delisting += 1
                                continue
                        except Exception:
                            pass

            passed.append(sym)

        self._log(verbose,
            '交易状态筛选: 停牌 {}, 退市 {}, 数据不足 {} → 剩余 {} 只'.format(
                skipped_suspended, skipped_delisting,
                skipped_insufficient, len(passed)))
        return passed

    def _filter_liquidity(self, symbols, daily_bar_dict, verbose):
        """第 2 层: 20 日均成交额"""
        passed = []
        for sym in symbols:
            bar_df = daily_bar_dict[sym]
            amounts = bar_df['amount'].values[-20:]
            avg_amount = np.mean(amounts)
            if avg_amount >= self.min_volume_20d:
                passed.append(sym)

        self._log(verbose, '流动性筛选: → {} 只 (20日均成交额 >= {:,.0f}万)'.format(
            len(passed), self.min_volume_20d / 1e4))
        return passed

    def _filter_volatility_quality(self, symbols, daily_bar_dict, verbose):
        """第 3 层: 波动率质量 + 价格连续性"""
        passed = []
        skipped_vol = 0
        skipped_gap = 0

        for sym in symbols:
            bar_df = daily_bar_dict[sym]
            closes = bar_df['close'].values[-21:]  # 21 根 bar → 20 个日收益
            if len(closes) < 21:
                continue

            # 年化波动率 = 日收益率 std * sqrt(252)
            daily_ret = np.diff(closes) / closes[:-1]
            daily_ret = daily_ret[np.isfinite(daily_ret)]
            if len(daily_ret) < 15:
                continue

            ann_vol = np.std(daily_ret) * np.sqrt(252)
            if ann_vol > self.max_annual_vol:
                skipped_vol += 1
                continue

            # 价格连续性：单日涨跌幅 > 15% 视为异常
            if np.any(np.abs(daily_ret) > 0.15):
                skipped_gap += 1
                continue

            passed.append(sym)

        self._log(verbose,
            '波动率质量: 高波动 {}, 异常跳空 {} → 剩余 {} 只 (年化波动率 < {:.0%})'.format(
                skipped_vol, skipped_gap, len(passed), self.max_annual_vol))
        return passed

    def _filter_aum(self, symbols, aum_dict, verbose):
        """可选层 A: 规模筛选（需外部提供 AUM）"""
        passed = [s for s in symbols
                  if s in aum_dict and aum_dict[s] >= self.min_aum]
        self._log(verbose, '规模筛选(AUM): → {} 只 (AUM >= {:.1f}亿)'.format(
            len(passed), self.min_aum / 1e8))
        return passed

    def _filter_premium(self, symbols, premium_dict, verbose):
        """可选层 B: 折溢价筛选（需外部提供实时溢价率）"""
        passed = [s for s in symbols
                  if s in premium_dict
                  and abs(premium_dict[s]) <= self.max_premium_rate]
        self._log(verbose, '折溢价筛选: → {} 只 (|rate| <= {:.1%})'.format(
            len(passed), self.max_premium_rate))
        return passed

    # ----------------------------------------------------------
    # 大类资产去重
    # ----------------------------------------------------------

    def _dedup_by_asset_class(self, passed_codes, daily_bar_dict, verbose):
        """
        每大类只保留流动性最强的一只。
        未命中大类映射的标的按 ENABLE_FALLBACK_OTHER 处理。
        """
        final_pool = []

        for class_name, candidates in self.asset_class_mapping.items():
            intersection = [c for c in candidates if c in passed_codes]
            if intersection:
                best = max(intersection,
                           key=lambda c: daily_bar_dict[c]['amount'].values[-20:].mean())
                final_pool.append(best)
                self._log(verbose, '  [{}] {}'.format(class_name, best))
            else:
                self._log(verbose, '  [{}] 无达标标的'.format(class_name))

        if ENABLE_FALLBACK_OTHER:
            classified = set()
            for codes in self.asset_class_mapping.values():
                classified.update(codes)
            unclassified = [c for c in passed_codes
                            if c not in classified and c not in final_pool]
            if unclassified:
                unclassified.sort(
                    key=lambda c: daily_bar_dict[c]['amount'].values[-20:].mean(),
                    reverse=True)
                for code in unclassified[:MAX_OTHER_SLOTS]:
                    final_pool.append(code)
                    self._log(verbose, '  [其他] {}'.format(code))

        classes_covered = len(
            set(self._code_to_class.get(c, '其他') for c in final_pool))
        self._log(verbose, '大类去重: → {} 只 (覆盖 {} 大类)'.format(
            len(final_pool), classes_covered))
        return final_pool

    # ----------------------------------------------------------
    # 辅助
    # ----------------------------------------------------------

    def get_asset_class(self, symbol):
        return self._code_to_class.get(symbol, '其他')

    @staticmethod
    def _log(verbose, msg):
        if verbose:
            print('[EtfFilterPipeline] {}'.format(msg))


# ============================================================
# 掘金 SDK 数据获取
# ============================================================

def gm_fetch_all_etf_symbols():
    """
    获取全市场 ETF 列表（含当日交易状态）。

    数据来源:
      get_symbol_infos(sec_type1=1020, sec_type2=102001) → 基本信息（免费）
      get_symbols(...) → 当日 is_suspended, pre_close 等（免费）

    :return: DataFrame, columns:
        symbol, sec_name, exchange, is_suspended, delisted_date
    """
    try:
        from gm.api import get_symbol_infos, get_symbols
    except ImportError:
        print('[gm_fetch_all_etf_symbols] gm.api 不可用')
        return pd.DataFrame()

    try:
        # 全市场 ETF 基本信息
        info = get_symbol_infos(sec_type1=1020, sec_type2=102001, df=True)
        if info is None or info.empty:
            return pd.DataFrame()

        # 当日交易状态（停牌、退市日期等）
        symbols_str = ','.join(info['symbol'].tolist()[:200])
        live_info = get_symbols(sec_type1=1020, sec_type2=102001,
                                symbols=symbols_str, df=True)

        if live_info is not None and not live_info.empty:
            live_cols = ['symbol', 'is_suspended']
            if 'delisted_date' in live_info.columns:
                live_cols.append('delisted_date')
            info = info.merge(live_info[live_cols], on='symbol', how='left')

        # 补全缺失列
        if 'is_suspended' not in info.columns:
            info['is_suspended'] = False
        if 'delisted_date' not in info.columns:
            info['delisted_date'] = pd.NaT

        info['is_suspended'] = info['is_suspended'].fillna(False)
        print('[gm_fetch_all_etf_symbols] 全市场 ETF: {} 只'.format(len(info)))
        return info

    except Exception as e:
        print('[gm_fetch_all_etf_symbols] 异常: {}'.format(e))
        return pd.DataFrame()


def gm_fetch_etf_daily_bars(symbols, end_time, lookback=30, batch_size=15):
    """
    批量获取 ETF 日线 OHLCV。

    :param symbols:   ETF 代码列表
    :param end_time:  截止时间 (%Y-%m-%d %H:%M:%S)
    :param lookback:  回看天数
    :param batch_size: 每批标的数
    :return: dict, {symbol: DataFrame(columns=['close','amount','volume','eob'])}
             每个 symbol 的 DF 已按 eob 升序排列
    """
    try:
        from gm.api import history
    except ImportError:
        print('[gm_fetch_etf_daily_bars] gm.api 不可用')
        return {}

    result = {}
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        batch_str = ','.join(batch)
        try:
            data = history(
                symbol=batch_str,
                frequency='1d',
                start_time='2020-01-01 09:00:00',
                end_time=end_time,
                fields='symbol,close,amount,volume,eob',
                adjust=2,       # ADJUST_POST 后复权，成交额不用复权
                df=True,
            )
            if data is not None and not data.empty:
                data = data.sort_values('eob')
                for sym in batch:
                    sym_data = data[data['symbol'] == sym]
                    if len(sym_data) >= lookback:
                        sym_data = sym_data.tail(lookback).reset_index(drop=True)
                        result[sym] = sym_data
        except Exception as e:
            print('[gm_fetch_etf_daily_bars] 批次异常: {}'.format(e))
            continue

    return result


def gm_get_etf_seed_pool(end_time, context=None):
    """
    一站式函数：全市场 ETF 扫描 → 筛选 → 返回种子池。

    在策略 schedule 回调中直接调用:
        pool = gm_get_etf_seed_pool(
            end_time=context.now.strftime('%Y-%m-%d %H:%M:%S'),
            context=context,
        )

    :param end_time: 截止时间
    :param context:  策略 context（可选，保留以兼容旧调用方）
    :return: list[str], 最终种子池
    """
    print('[gm_get_etf_seed_pool] === 开始全市场 ETF 筛选 ===')

    # Step 1: 全市场 ETF 列表 + 交易状态
    all_etfs = gm_fetch_all_etf_symbols()
    if all_etfs.empty:
        print('[gm_get_etf_seed_pool] 无法获取 ETF 列表 → 空池')
        return []

    symbols = all_etfs['symbol'].tolist()
    print('[gm_get_etf_seed_pool] 候选范围: {} 只'.format(len(symbols)))

    # Step 2: 日线数据
    daily_bars = gm_fetch_etf_daily_bars(symbols, end_time, lookback=30)
    if not daily_bars:
        print('[gm_get_etf_seed_pool] 无法获取行情数据 → 空池')
        return []

    # Step 3: 执行筛选（不传 aum_dict 和 premium_dict，
    #          只用免费 SDK 可获取的三层 + 大类去重）
    pipeline = EtfFilterPipeline()
    seed_pool = pipeline.filter_and_select_seeds(
        etf_info_df=all_etfs,
        daily_bar_dict=daily_bars,
        aum_dict=None,          # 免费 SDK 无 AUM，跳过
        premium_dict=None,      # 回测模式无实时溢价，跳过
        verbose=True,
    )

    print('[gm_get_etf_seed_pool] === 完成: {} 只 ==='.format(len(seed_pool)))
    return seed_pool


# ============================================================
# 独立运行
# ============================================================
if __name__ == '__main__':
    from gm.api import set_token

    set_token('your_token')

    import datetime
    today = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    pool = gm_get_etf_seed_pool(end_time=today)
    print('\n最终推荐 ETF 种子池:')
    pipeline = EtfFilterPipeline()
    for i, etf in enumerate(pool, 1):
        cls = pipeline.get_asset_class(etf)
        print('  {}. {} [{}]'.format(i, etf, cls))
