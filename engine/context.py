"""Strategy context for the portfolio backtest engine.

A strategy is `initialize(ctx)` (optional) + `handle_data(ctx)` (required).
`handle_data` runs once per time step (daily in this release). The context is
portfolio-aware: cash + positions dict over symbols, unified calendar, and a
lazy bar loader for `history()`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from .engine import BacktestEngine


class Context:
    def __init__(self, initial_cash: float, engine: "BacktestEngine | None" = None,
                 universe: list[str] | None = None, calendar: list[str] | None = None,
                 data_layer=None):
        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.positions: dict[str, int] = {}
        self.state: dict = {}
        self.universe: list[str] = list(universe or [])
        self.calendar: list[str] = list(calendar or [])
        self.time: str | None = None
        self.bar_index: int = 0
        self.trades: list[dict] = []
        self._engine = engine
        self._data_layer = data_layer
        self._bars: dict[str, pd.DataFrame] = {}
        self._bar_index_by_date: dict[str, dict[str, int]] = {}
        self._buy_dates: dict[str, set[str]] = {}
        self._avg_cost: dict[str, float] = {}
        self._symbol_type: dict[str, str] = {}
        # defaults so ctx.history works even before the engine wires real values
        self._freq = "daily"
        self._adjust = "qfq"

    # ---- order interface (delegates to engine for A-share rule matching) ----
    def buy(self, symbol: str, pct: float = 1.0) -> bool:
        if self._engine is None:
            raise RuntimeError("Context not attached to an engine")
        return self._engine.buy(self, symbol, pct)

    def sell(self, symbol: str, pct: float = 1.0) -> bool:
        if self._engine is None:
            raise RuntimeError("Context not attached to an engine")
        return self._engine.sell(self, symbol, pct)

    # ---- data access ----
    def history(self, symbol: str, lookback: int = 0) -> pd.DataFrame:
        """Return `symbol` bars up to (inclusive) the current time point.

        Triggers lazy load on first use. lookback=0 -> full history to now;
        lookback=N -> last N+1 bars.
        """
        df = self._ensure_loaded(symbol)
        idx = self._idx_at_current(symbol)
        if lookback <= 0:
            start = 0
        else:
            start = max(0, idx - lookback)
        return df.iloc[start:idx + 1].reset_index(drop=True)

    def price(self, symbol: str) -> float:
        df = self._ensure_loaded(symbol)
        idx = self._idx_at_current(symbol)
        return float(df.iloc[idx]["close"])

    # ---- portfolio helpers ----
    @property
    def market_value(self) -> float:
        return sum(self.position_value(s) for s in self.positions)

    def position_value(self, symbol: str) -> float:
        if symbol not in self.positions:
            return 0.0
        return self.positions[symbol] * self.price(symbol)

    @property
    def total_value(self) -> float:
        return self.cash + self.market_value

    @property
    def shares(self) -> dict[str, int]:
        return dict(self.positions)

    # ---- lazy loader / alignment (called by engine) ----
    def _ensure_loaded(self, symbol: str) -> pd.DataFrame:
        if symbol not in self._bars:
            if self._data_layer is None:
                raise RuntimeError(f"no data layer for lazy-loading {symbol}")
            info = self._data_layer.symbol_info(symbol)
            df = self._data_layer.get_bars(info, freq=self._freq, start=self.calendar[0],
                                           end=self.calendar[-1], adjust=self._adjust)
            if df is None or df.empty:
                raise ValueError(f"no data for {symbol}")
            self._symbol_type[symbol] = info.type
            self._bars[symbol] = df.reset_index(drop=True)
            self._bar_index_by_date[symbol] = {
                str(d): i for i, d in enumerate(self._bars[symbol]["date"])
            }
        return self._bars[symbol]

    def _idx_at_current(self, symbol: str) -> int:
        """Row index of current time point in symbol's bars (searchsorted-free:
        prebuilt date->index map; falls back to 0 if current date not present)."""
        if symbol not in self._bar_index_by_date:
            self._ensure_loaded(symbol)
        m = self._bar_index_by_date[symbol]
        d = str(self.time)
        if d in m:
            return m[d]
        # no bar on current date for this symbol -> use the last bar before it
        import bisect
        dates = list(m.keys())
        i = bisect.bisect_right(dates, d) - 1
        return m[dates[max(0, i)]] if dates else 0

    def __repr__(self):
        return f"<Context cash={self.cash:.0f} pos={self.positions} val={self.total_value:.0f}>"
