"""Indicator helpers usable from any strategy (built-in or user).

These operate on the bar history returned by `ctx.bars_upto(lookback)`.
Keep them dependency-light: numpy only.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(values: pd.Series | np.ndarray, period: int) -> pd.Series:
    """Simple moving average."""
    s = pd.Series(values)
    return s.rolling(window=period, min_periods=1).mean()


def ema(values: pd.Series | np.ndarray, period: int) -> pd.Series:
    """Exponential moving average."""
    s = pd.Series(values)
    return s.ewm(span=period, adjust=False).mean()


def rsi(values: pd.Series | np.ndarray, period: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder)."""
    s = pd.Series(values, dtype=float)
    delta = s.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi_series = 100.0 - (100.0 / (1.0 + rs))
    rsi_series = rsi_series.fillna(100.0).replace(np.inf, 100.0)
    return rsi_series


def macd(values: pd.Series | np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD: (dif, dea, hist)."""
    s = pd.Series(values, dtype=float)
    ema_fast = s.ewm(span=fast, adjust=False).mean()
    ema_slow = s.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2
    return dif, dea, hist
