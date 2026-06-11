# coding=utf-8
"""
ETF 筛选与数据清洗管道（EtfFilterPipeline）

在 ETF 双动量策略中执行四层硬性筛选：
1. 规模筛选 — AUM > 5 亿
2. 流动性筛选 — 20 日均成交额 > 5000 万
3. 折溢价筛选 — |折溢价率| ≤ 1%（仅实时模式）
4. 大类资产去重 — 每类只保留流动性最强的一只

可与任何动量/轮动策略组合使用，也可独立运行进行 ETF 市场扫描。

参考文档: docs/strategies/etfFilterPipeline.md
"""

from __future__ import print_function, absolute_import
import numpy as np
import pandas as pd


# ============================================================
# 大类资产映射表
#
# 每一组代表一个底层逻辑独立的资产类别。在去重阶段，
# 每个大类只保留流动性最强（20 日均成交额最大）的一只 ETF。
#
# 新增或修改大类只需编辑此字典。
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
        'SHSE.515080',   # 广发中证2000ETF（与中小盘共享候选，按流动性归属）
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

# 如果某标的在上述任何大类中都没有出现，但通过了前三层筛选，
# 它会被归入"其他"池，并按流动性择优入选。
# 如不需要"其他"池，可将 ENABLE_FALLBACK_OTHER 设为 False。
ENABLE_FALLBACK_OTHER = False
MAX_OTHER_SLOTS = 2   # "其他"池最多入选数量


class EtfFilterPipeline:
    """
    ETF 筛选管道

    独立于具体策略平台，核心方法 `filter_and_select_seeds()` 接收
    DataFrame 和 dict 即可完成筛选，不依赖掘金 SDK。
    """

    def __init__(self, min_aum=500_000_000, min_volume_20d=50_000_000,
                 max_premium_rate=0.01, asset_class_mapping=None):
        """
        :param min_aum: 最小资产净值（元），默认 5 亿
        :param min_volume_20d: 过去20天日均成交额（元），默认 5000 万
        :param max_premium_rate: 最大折溢价率绝对值，默认 1%
        :param asset_class_mapping: 大类资产映射 dict，默认使用内置映射
        """
        self.min_aum = min_aum
        self.min_volume_20d = min_volume_20d
        self.max_premium_rate = max_premium_rate
        self.asset_class_mapping = asset_class_mapping or ASSET_CLASS_MAPPING

        # 构建候选代码 → 大类名称的反查表
        self._code_to_class = {}
        for class_name, codes in self.asset_class_mapping.items():
            for code in codes:
                self._code_to_class[code] = class_name

    # ----------------------------------------------------------
    # 核心方法：全流程筛选
    # ----------------------------------------------------------

    def filter_and_select_seeds(self, etf_info_df, daily_amount_dict,
                                 premium_dict=None, verbose=True):
        """
        执行完整的四层筛选流程，返回最终种子池。

        :param etf_info_df: DataFrame，包含全市场 ETF 基础信息
                            必须包含列: ['symbol', 'sec_name']
                            可选列: ['aum']
        :param daily_amount_dict: dict, {symbol: np.array / pd.Series}
                                   每个 ETF 过去 N 天的每日成交额序列
        :param premium_dict: dict, {symbol: float}
                             每个 ETF 的当前折溢价率（仅实时模式），
                             传 None 则跳过折溢价筛选
        :param verbose: 是否打印筛选日志
        :return: list[str], 通过全部筛选的 ETF symbol 列表
        """
        if etf_info_df is None or etf_info_df.empty:
            self._log(verbose, '输入数据为空，返回空池')
            return []

        total_in = len(etf_info_df)

        # ---- 第 1 层: 规模筛选 ----
        if 'aum' in etf_info_df.columns:
            etf_info_df = etf_info_df[etf_info_df['aum'] >= self.min_aum].copy()
            self._log(verbose, '规模筛选: {} → {} (AUM >= {:.1f}亿)'.format(
                total_in, len(etf_info_df), self.min_aum / 1e8))
            total_in = len(etf_info_df)
        else:
            self._log(verbose, '规模筛选: 跳过（输入不含 aum 列）')

        # ---- 第 2 层: 流动性筛选 ----
        liquid_codes = []
        for _, row in etf_info_df.iterrows():
            symbol = row['symbol']
            amount_series = daily_amount_dict.get(symbol)
            if amount_series is not None and len(amount_series) >= 20:
                avg_amount = np.mean(amount_series[-20:])
                if avg_amount >= self.min_volume_20d:
                    liquid_codes.append(symbol)

        passed = etf_info_df[etf_info_df['symbol'].isin(liquid_codes)]
        self._log(verbose, '流动性筛选: {} → {} (20日均成交额 >= {:,.0f}万)'.format(
            total_in, len(passed), self.min_volume_20d / 1e4))

        # ---- 第 3 层: 折溢价筛选（可选） ----
        if premium_dict is not None:
            premium_passed = []
            for _, row in passed.iterrows():
                symbol = row['symbol']
                rate = premium_dict.get(symbol)
                if rate is not None and abs(rate) <= self.max_premium_rate:
                    premium_passed.append(symbol)
            passed = passed[passed['symbol'].isin(premium_passed)]
            self._log(verbose, '折溢价筛选: → {} (|折溢价率| <= {:.1%})'.format(
                len(passed), self.max_premium_rate))
        else:
            self._log(verbose, '折溢价筛选: 跳过（无 premium_dict 输入）')

        # ---- 第 4 层: 大类资产去重 ----
        final_pool = self._dedup_by_asset_class(passed['symbol'].tolist(),
                                                  daily_amount_dict, verbose)
        return final_pool

    # ----------------------------------------------------------
    # 大类资产去重
    # ----------------------------------------------------------

    def _dedup_by_asset_class(self, passed_codes, daily_amount_dict, verbose):
        """
        在每个大类资产中只保留流动性最强的一只。

        未命中任何大类的标的：
        - 若 ENABLE_FALLBACK_OTHER=True，按流动性择优入选最多 MAX_OTHER_SLOTS 只
        - 否则直接丢弃
        """
        final_pool = []

        # 按大类处理
        for class_name, candidate_codes in self.asset_class_mapping.items():
            class_intersection = [c for c in candidate_codes if c in passed_codes]
            if class_intersection:
                best = max(class_intersection,
                           key=lambda c: np.mean(daily_amount_dict[c][-20:]))
                final_pool.append(best)
                self._log(verbose, '  [{}] 入选: {}'.format(class_name, best))

        # 处理未命中大类但通过前三层筛选的标的
        if ENABLE_FALLBACK_OTHER:
            classified = set()
            for codes in self.asset_class_mapping.values():
                classified.update(codes)
            unclassified = [c for c in passed_codes
                            if c not in classified and c not in final_pool]
            if unclassified:
                unclassified.sort(
                    key=lambda c: np.mean(daily_amount_dict[c][-20:]),
                    reverse=True
                )
                for code in unclassified[:MAX_OTHER_SLOTS]:
                    final_pool.append(code)
                    self._log(verbose, '  [其他] 入选: {}'.format(code))

        self._log(verbose, '大类去重: → {} 只（{}大类）'.format(
            len(final_pool),
            len(set(self._code_to_class.get(c, '其他') for c in final_pool))))

        return final_pool

    # ----------------------------------------------------------
    # 辅助方法
    # ----------------------------------------------------------

    def get_asset_class(self, symbol):
        """查询某个 symbol 所属的大类名称"""
        return self._code_to_class.get(symbol, '其他')

    @staticmethod
    def _log(verbose, msg):
        if verbose:
            print('[EtfFilterPipeline] {}'.format(msg))


# ============================================================
# 掘金 SDK 数据获取辅助函数
#
# 以下函数封装了从掘金 SDK 中获取全市场 ETF 数据并构造
# EtfFilterPipeline 所需输入的逻辑。
# ============================================================

def gm_fetch_all_etf_symbols(sec_type2=102001):
    """
    从掘金 SDK 获取全市场 ETF 列表。

    :param sec_type2: 基金细类，默认 102001=ETF
    :return: DataFrame, columns 包含 ['symbol', 'sec_name', 'exchange']
    """
    try:
        from gm.api import get_symbol_infos
        result = get_symbol_infos(sec_type1=1020, sec_type2=sec_type2, df=True)
        if result is not None and not result.empty:
            return result[['symbol', 'sec_name', 'exchange', 'sec_type2']]
    except Exception as e:
        print('[gm_fetch_all_etf_symbols] 获取失败: {}'.format(e))
    return pd.DataFrame(columns=['symbol', 'sec_name', 'exchange', 'sec_type2'])


def gm_fetch_daily_amounts(symbols, end_time, lookback=30, batch_size=15):
    """
    从掘金 SDK 批量拉取 ETF 的每日成交额。

    :param symbols: list[str], ETF 代码列表
    :param end_time: str, 结束时间 (%Y-%m-%d %H:%M:%S 格式)
    :param lookback: int, 回看交易日天数
    :param batch_size: int, 每批拉取的标的数量
    :return: dict, {symbol: np.array of daily amounts}
    """
    try:
        from gm.api import history
    except ImportError:
        print('[gm_fetch_daily_amounts] gm.api 不可用')
        return {}

    result = {}
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        batch_str = ','.join(batch)
        try:
            # 需足够的历史数据覆盖 lookback
            data = history(
                symbol=batch_str,
                frequency='1d',
                start_time='2020-01-01 09:00:00',  # 掘金自动裁剪到可用范围
                end_time=end_time,
                fields='symbol,amount,eob',
                adjust=2,   # ADJUST_POST 后复权
                df=True,
            )
            if data is not None and not data.empty:
                for sym in batch:
                    sym_data = data[data['symbol'] == sym]['amount']
                    if len(sym_data) >= lookback:
                        result[sym] = sym_data.values[-lookback:]
        except Exception as e:
            print('[gm_fetch_daily_amounts] 批次异常: {}'.format(e))
            continue

    return result


def gm_fetch_premium_rates(symbols, context=None):
    """
    从掘金 SDK 获取 ETF 的当前折溢价率。

    折溢价率 = (price - iopv) / iopv

    :param symbols: list[str], ETF 代码列表
    :param context: 策略 context（用于 schedule 内的 last_tick 调用）
    :return: dict, {symbol: premium_rate}
    """
    try:
        from gm.api import last_tick
    except ImportError:
        return {}

    premium_dict = {}
    symbols_str = ','.join(symbols[:50])  # 批量查询

    try:
        ticks = last_tick(symbols=symbols_str, fields='symbol,price,iopv')
        if ticks:
            for t in ticks:
                symbol = t.get('symbol', '')
                price = t.get('price', 0)
                iopv = t.get('iopv', 0)
                if iopv and iopv > 0:
                    premium_dict[symbol] = (price - iopv) / iopv
    except Exception as e:
        print('[gm_fetch_premium_rates] 获取异常: {}'.format(e))

    return premium_dict


def gm_get_etf_seed_pool(end_time, context=None, check_premium=False):
    """
    一站式函数：从掘金 SDK 获取全市场 ETF → 执行筛选 → 返回种子池。

    可以直接在策略的 schedule 回调中调用：

        context.etf_pool = gm_get_etf_seed_pool(
            end_time=context.now.strftime('%Y-%m-%d %H:%M:%S'),
            context=context,
            check_premium=(context.mode == MODE_LIVE),
        )

    :param end_time: str, 筛选截止时间
    :param context: 策略 context 对象（可选）
    :param check_premium: bool, 是否检查折溢价率（实时模式建议 True）
    :return: list[str], 最终种子池
    """
    print('[gm_get_etf_seed_pool] 开始全市场 ETF 筛选...')

    # Step 1: 获取全市场 ETF 列表
    all_etfs = gm_fetch_all_etf_symbols()
    if all_etfs.empty:
        print('[gm_get_etf_seed_pool] 无法获取 ETF 列表，返回空池')
        return []

    symbols = all_etfs['symbol'].tolist()
    print('[gm_get_etf_seed_pool] 全市场 ETF: {} 只'.format(len(symbols)))

    # Step 2: 批量获取日成交额数据
    daily_amounts = gm_fetch_daily_amounts(symbols, end_time, lookback=30)

    # Step 3: 获取折溢价率（可选）
    premium_dict = None
    if check_premium:
        premium_dict = gm_fetch_premium_rates(symbols, context=context)

    # Step 4: 执行筛选管道
    pipeline = EtfFilterPipeline()
    seed_pool = pipeline.filter_and_select_seeds(
        etf_info_df=all_etfs,
        daily_amount_dict=daily_amounts,
        premium_dict=premium_dict,
        verbose=True,
    )

    print('[gm_get_etf_seed_pool] 最终种子池 ({} 只): {}'.format(
        len(seed_pool), seed_pool))
    return seed_pool


# ============================================================
# 独立运行：扫描当前全市场 ETF 健康度
# ============================================================
if __name__ == '__main__':
    """
    独立运行示例：在设置了 set_token 后可直接扫描 ETF 市场。
    需要先启动掘金终端。
    """
    from gm.api import set_token

    # 设置你的 token
    set_token('your_token')

    # 获取当前日期
    import datetime
    today = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # 执行筛选
    pool = gm_get_etf_seed_pool(end_time=today, check_premium=False)
    print('\n=== 最终推荐 ETF 种子池 ===')
    for i, etf in enumerate(pool, 1):
        print('  {}. {}'.format(i, etf))
