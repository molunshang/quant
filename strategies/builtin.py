"""Built-in strategies shipped with the system.

Each is a plain `strategy(ctx, params)` function, so users can copy them into
their own custom strategy and tweak.
"""
from __future__ import annotations

import numpy as np

from .indicators import rsi, sma


def buy_and_hold(ctx, params: dict) -> None:
    """Buy at the first bar with all cash; hold to the end."""
    if ctx.bar_index == 0:
        ctx.buy()
    return


def sma_cross(ctx, params: dict) -> None:
    """Golden-cross / death-cross on SMA(short) vs SMA(long)."""
    short = int(params.get("short", 20))
    long_ = int(params.get("long", 60))
    bars = ctx.bars_upto()
    if len(bars) < long_:
        return
    close = bars["close"].astype(float)
    s_short = sma(close, short)
    s_long = sma(close, long_)
    prev_short = s_short.iloc[-2]
    prev_long = s_long.iloc[-2]
    cur_short = s_short.iloc[-1]
    cur_long = s_long.iloc[-1]
    if prev_short <= prev_long and cur_short > cur_long:
        ctx.buy()  # golden cross
    elif prev_short >= prev_long and cur_short < cur_long:
        ctx.sell()  # death cross
    return


def rsi_reversal(ctx, params: dict) -> None:
    """Buy when RSI crosses below oversold, sell when above overbought."""
    period = int(params.get("period", 14))
    oversold = float(params.get("oversold", 30))
    overbought = float(params.get("overbought", 70))
    bars = ctx.bars_upto()
    if len(bars) < period + 2:
        return
    r = rsi(bars["close"].astype(float), period)
    prev_r = r.iloc[-2]
    cur_r = r.iloc[-1]
    if prev_r >= oversold and cur_r < oversold:
        ctx.buy()
    elif prev_r <= overbought and cur_r > overbought:
        ctx.sell()
    return


BUILTIN_STRATEGIES: dict[str, dict] = {
    "buy_and_hold": {
        "name": "buy_and_hold",
        "description": "买入持有：首日全仓买入，一直持有",
        "func": buy_and_hold,
        "builtin": True,
        "params_schema": {},
    },
    "sma_cross": {
        "name": "sma_cross",
        "description": "均线金叉死叉：SMA(short) 上穿 SMA(long) 买入，下穿卖出",
        "func": sma_cross,
        "builtin": True,
        "params_schema": {"short": 20, "long": 60},
    },
    "rsi_reversal": {
        "name": "rsi_reversal",
        "description": "RSI 反转：跌破超卖线买入，升破超买线卖出",
        "func": rsi_reversal,
        "builtin": True,
        "params_schema": {"period": 14, "oversold": 30, "overbought": 70},
    },
}
