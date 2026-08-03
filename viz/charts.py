"""Chart data builders. Produce ECharts-ready JSON from backtest results,
shared by the web frontend and the /api/chart endpoint."""
from __future__ import annotations

import numpy as np


def equity_curve_option(result) -> dict:
    """ECharts option: equity vs benchmark line chart."""
    eq = result.equity_curve
    dates = eq["date"].tolist()
    initial = result.ctx.initial_cash
    equity_norm = [(v / initial - 1) * 100 for v in eq["equity"].tolist()]
    bench_close = eq["benchmark"].tolist()
    bench_init = bench_close[0] if bench_close else 1
    bench_norm = [(c / bench_init - 1) * 100 for c in bench_close]
    return {
        "title": {"text": "权益曲线 vs 基准", "left": "center"},
        "tooltip": {"trigger": "axis"},
        "legend": {"data": ["策略", "基准"], "bottom": 0},
        "xAxis": {"type": "category", "data": dates, "name": "日期"},
        "yAxis": {"type": "value", "name": "收益率 %", "axisLabel": {"formatter": "{value}%"}},
        "series": [
            {"name": "策略", "type": "line", "data": equity_norm, "smooth": True, "symbol": "none"},
            {"name": "基准", "type": "line", "data": bench_norm, "smooth": True, "symbol": "none", "lineStyle": {"type": "dashed"}},
        ],
    }


def drawdown_option(result) -> dict:
    """ECharts option: drawdown area chart."""
    eq = result.equity_curve
    dates = eq["date"].tolist()
    equity = np.asarray(eq["equity"].tolist(), dtype=float)
    peak = np.maximum.accumulate(equity)
    dd = (equity / peak - 1) * 100
    return {
        "title": {"text": "回撤", "left": "center"},
        "tooltip": {"trigger": "axis"},
        "xAxis": {"type": "category", "data": dates},
        "yAxis": {"type": "value", "name": "回撤 %", "axisLabel": {"formatter": "{value}%"}},
        "series": [
            {"name": "回撤", "type": "line", "data": [round(v, 3) for v in dd],
             "lineStyle": {"color": "#d9534f", "width": 1}, "areaStyle": {"color": "#d9534f", "opacity": 0.2},
             "symbol": "none"},
        ],
    }


def kline_option(result) -> dict:
    """ECharts option: candlestick with buy/sell markers on the symbol's bars."""
    eq = result.equity_curve
    dates = eq["date"].tolist()
    # reconstruct OHLC from bars (result doesn't carry OHLC directly; use equity curve close)
    closes = eq["close"].tolist()
    opens = eq["close"].tolist()  # placeholder — we only have close in equity curve
    highs = eq["close"].tolist()
    lows = eq["close"].tolist()

    # build OHLC arrays from ctx bars if available
    ctx_bars = result.ctx.bars
    if ctx_bars is not None and not ctx_bars.empty and len(ctx_bars) == len(dates):
        opens = ctx_bars["open"].tolist()
        highs = ctx_bars["high"].tolist()
        lows = ctx_bars["low"].tolist()
        closes = ctx_bars["close"].tolist()
    ohlc = [[o, c, l, h] for o, c, l, h in zip(opens, closes, lows, highs)]

    buys = [t for t in result.trades if t["side"] == "buy"]
    sells = [t for t in result.trades if t["side"] == "sell"]
    buy_pts = [{"name": "买入", "coord": [t["date"], t["price"]], "value": t["price"]} for t in buys]
    sell_pts = [{"name": "卖出", "coord": [t["date"], t["price"]], "value": t["price"]} for t in sells]

    return {
        "title": {"text": "K线图（买卖点）", "left": "center"},
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
        "xAxis": {"type": "category", "data": dates},
        "yAxis": {"type": "value", "scale": True},
        "dataZoom": [{"type": "inside", "start": 60, "end": 100}],
        "series": [
            {"name": "K线", "type": "candlestick", "data": ohlc,
             "itemStyle": {"color": "#d9534f", "color0": "#5cb85c", "borderColor": "#d9534f", "borderColor0": "#5cb85c"}},
            {"name": "买入", "type": "scatter", "data": buy_pts, "symbol": "triangle", "symbolSize": 10,
             "itemStyle": {"color": "#d9534f"}, "tooltip": {"formatter": lambda p: f"买入 @ {p.value[0]}"}},
            {"name": "卖出", "type": "scatter", "data": sell_pts, "symbol": "triangle", "symbolRotate": 180,
             "symbolSize": 10, "itemStyle": {"color": "#5cb85c"}, "tooltip": {"formatter": lambda p: f"卖出 @ {p.value[0]}"}},
        ],
    }
