"""Strategy context object.

Exposed to strategy functions as `ctx`. Provides live trading state (cash,
position, current bar, helpers) and the primary order interface: `ctx.buy()`
and `ctx.sell()`. Strategies author these calls directly.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from .engine import BacktestEngine


class Context:
    def __init__(self, initial_cash: float, engine: "BacktestEngine" | None = None):
        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.position = 0  # shares held
        self.avg_cost = 0.0
        self.current_bar: pd.Series | None = None
        self.current_date = None
        self.bar_index = 0
        self.bars: pd.DataFrame | None = None
        self.trades: list[dict] = []
        self._engine = engine
        self._buy_dates: set[str] = set()

    # ---- order interface ----
    def buy(self, shares: int | None = None, price: float | None = None) -> bool:
        """Buy `shares` (default: all-in). Returns True if filled."""
        if self._engine is None:
            raise RuntimeError("Context not attached to an engine")
        return self._engine.buy(self, shares, price)

    def sell(self, shares: int | None = None, price: float | None = None) -> bool:
        """Sell `shares` (default: entire position). Returns True if filled."""
        if self._engine is None:
            raise RuntimeError("Context not attached to an engine")
        return self._engine.sell(self, shares, price)

    def value_at(self, date) -> float:
        """Total portfolio value on a given bar date (for equity curve)."""
        return self.cash + self.position * self._close_on(date)

    def _close_on(self, date) -> float:
        if self.current_date == date and self.current_bar is not None:
            return float(self.current_bar["close"])
        row = self.bars[self.bars["date"] == str(date)]
        if row.empty:
            return 0.0
        return float(row.iloc[0]["close"])

    # ---- snapshot helpers for strategies ----
    @property
    def price(self) -> float:
        """Current close price of the symbol being traded."""
        if self.current_bar is None:
            return 0.0
        return float(self.current_bar["close"])

    @property
    def open(self) -> float:
        if self.current_bar is None:
            return 0.0
        return float(self.current_bar["open"])

    @property
    def high(self) -> float:
        if self.current_bar is None:
            return 0.0
        return float(self.current_bar["high"])

    @property
    def low(self) -> float:
        if self.current_bar is None:
            return 0.0
        return float(self.current_bar["low"])

    @property
    def volume(self) -> float:
        if self.current_bar is None:
            return 0.0
        return float(self.current_bar.get("volume", 0.0))

    def bars_upto(self, lookback: int = 0) -> pd.DataFrame:
        """Return bars up to current index (inclusive). lookback=0 -> all history so far.

        With lookback=0 this is bars.iloc[0:bar_index+1] — the full history
        including the current bar. lookback=1 gives the last two bars, etc.
        """
        if lookback <= 0:
            n = 0  # full history from bar 0
        else:
            n = max(0, self.bar_index - lookback + 1)  # last (lookback+1) bars
        return self.bars.iloc[n : self.bar_index + 1]

    # ---- portfolio state ----
    @property
    def market_value(self) -> float:
        return self.position * self.price

    @property
    def total_value(self) -> float:
        return self.cash + self.market_value

    @property
    def shares(self) -> int:
        return int(self.position)

    def __repr__(self):
        return f"<Context cash={self.cash:.0f} pos={self.position} val={self.total_value:.0f}>"
