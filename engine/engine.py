"""Event-driven backtest engine.

Runs a strategy function `strategy(ctx, params)` bar-by-bar over an OHLCV
DataFrame, enforcing A-share trading rules. Supports daily & minute bars —
the same strategy code works at both frequencies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from .context import Context
from .rules import TradingRules, calc_commission, calc_transfer_fee

StrategyFunc = Callable[[Context, dict], None]


@dataclass
class EngineConfig:
    initial_cash: float = 100_000.0
    commission_rate: float = 0.0003
    min_commission: float = 5.0
    stamp_duty: float = 0.0005
    transfer_fee_rate: float = 0.00001
    lot_size: int = 100
    t_plus_1: bool = True
    price_limit_pct: float = 0.10
    symbol_type: str = "stock"  # "stock" | "etf" | "fund"
    params: dict = field(default_factory=dict)


class BacktestResult:
    def __init__(self, equity_curve: pd.DataFrame, trades: list[dict], ctx: Context, symbol: str, freq: str):
        self.equity_curve = equity_curve
        self.trades = trades
        self.ctx = ctx
        self.symbol = symbol
        self.freq = freq

    @property
    def metrics(self) -> dict:
        return compute_metrics(self.equity_curve, self.trades)


class BacktestEngine:
    def __init__(self, config: EngineConfig | None = None):
        self.cfg = config or EngineConfig()

    def _rules(self) -> TradingRules:
        c = self.cfg
        return TradingRules(
            commission_rate=c.commission_rate,
            min_commission=c.min_commission,
            stamp_duty=c.stamp_duty,
            transfer_fee_rate=c.transfer_fee_rate,
            lot_size=c.lot_size,
            t_plus_1=c.t_plus_1,
            price_limit_pct=c.price_limit_pct,
        )

    def run(self, strategy: StrategyFunc, bars: pd.DataFrame, params: dict | None = None) -> BacktestResult:
        if bars is None or bars.empty:
            raise ValueError("No bars provided to backtest engine")
        bars = bars.reset_index(drop=True)
        self.cfg.params = params or {}

        ctx = Context(self.cfg.initial_cash, engine=self)
        ctx.bars = bars
        equity_rows: list[dict] = []

        for i in range(len(bars)):
            bar = bars.iloc[i]
            ctx.current_bar = bar
            ctx.current_date = str(bar["date"])
            ctx.bar_index = i

            strategy(ctx, self.cfg.params)

            equity_rows.append({
                "date": str(bar["date"]),
                "close": float(bar["close"]),
                "cash": ctx.cash,
                "position": ctx.position,
                "market_value": ctx.market_value,
                "equity": ctx.total_value,
                "benchmark": float(bar["close"]),
            })

        equity_df = pd.DataFrame(equity_rows)
        return BacktestResult(
            equity_curve=equity_df,
            trades=ctx.trades,
            ctx=ctx,
            symbol=self.cfg.params.get("symbol", ""),
            freq=self.cfg.params.get("freq", "daily"),
        )

    # ---- order entry (called via ctx.buy / ctx.sell) ----
    def buy(self, ctx: Context, shares: int | None = None, price: float | None = None) -> bool:
        """Buy `shares` (default: all-in). Returns True if filled."""
        bar = ctx.current_bar
        price = price or float(bar["close"])
        rules = self._rules()
        lot = rules.lot_size

        if rules.price_limit_pct > 0 and self._is_limit_up(bar, rules):
            return False

        shares = shares or int(ctx.cash // (price * lot) * lot)
        shares = int(shares // lot) * lot
        if shares <= 0:
            return False
        amount = shares * price
        commission = calc_commission(amount, rules.commission_rate, rules.min_commission)
        # Step down a lot at a time until the position (incl. commission) fits cash.
        while amount + commission > ctx.cash and shares > 0:
            shares -= lot
            if shares <= 0:
                return False
            amount = shares * price
            commission = calc_commission(amount, rules.commission_rate, rules.min_commission)
        if amount + commission > ctx.cash:
            return False

        ctx.cash -= amount + commission
        prev_cost = ctx.avg_cost * ctx.position
        ctx.position += shares
        ctx.avg_cost = (prev_cost + amount) / ctx.position if ctx.position else 0.0
        if rules.t_plus_1:
            ctx._buy_dates.add(str(bar["date"]))
        trade = {"date": str(bar["date"]), "side": "buy", "shares": shares, "price": price,
                 "amount": amount, "commission": commission, "stamp_duty": 0.0, "transfer_fee": 0.0}
        ctx.trades.append(trade)
        return True

    def sell(self, ctx: Context, shares: int | None = None, price: float | None = None) -> bool:
        """Sell `shares` (default: entire position). Returns True if filled."""
        bar = ctx.current_bar
        price = price or float(bar["close"])
        rules = self._rules()

        if rules.t_plus_1 and str(bar["date"]) in ctx._buy_dates:
            return False  # T+1: can't sell shares bought today

        if rules.price_limit_pct > 0 and self._is_limit_down(bar, rules):
            return False

        shares = shares or ctx.position
        shares = int(shares // rules.lot_size) * rules.lot_size
        if shares <= 0 or shares > ctx.position:
            return False
        amount = shares * price
        commission = calc_commission(amount, rules.commission_rate, rules.min_commission)
        is_etf_fund = rules.is_etf_or_fund(self.cfg.symbol_type)
        stamp = 0.0 if is_etf_fund else amount * rules.stamp_duty
        transfer = calc_transfer_fee(amount, rules.transfer_fee_rate) if not is_etf_fund else 0.0

        ctx.cash += amount - commission - stamp - transfer
        ctx.position -= shares
        if ctx.position == 0:
            ctx.avg_cost = 0.0
        trade = {"date": str(bar["date"]), "side": "sell", "shares": shares, "price": price,
                 "amount": amount, "commission": commission, "stamp_duty": stamp, "transfer_fee": transfer}
        ctx.trades.append(trade)
        return True

    # ---- helpers ----
    def _is_limit_up(self, bar: pd.Series, rules: TradingRules) -> bool:
        prev_close = float(bar.get("prev_close", 0.0)) or float(bar["open"])
        if prev_close <= 0:
            return False
        limit = prev_close * (1 + rules.price_limit_pct)
        return float(bar["close"]) >= limit - 1e-6

    def _is_limit_down(self, bar: pd.Series, rules: TradingRules) -> bool:
        prev_close = float(bar.get("prev_close", 0.0)) or float(bar["open"])
        if prev_close <= 0:
            return False
        limit = prev_close * (1 - rules.price_limit_pct)
        return float(bar["close"]) <= limit + 1e-6


def compute_metrics(equity_curve: pd.DataFrame, trades: list[dict]) -> dict:
    """Compute performance metrics from equity curve + trades."""
    if equity_curve is None or equity_curve.empty:
        return {"error": "no equity data"}

    eq = equity_curve.copy()
    equity = eq["equity"].astype(float).values
    initial = float(equity[0]) if len(equity) else 0.0
    final = float(equity[-1]) if len(equity) else 0.0
    total_return = (final / initial - 1) if initial else 0.0
    n = len(equity)

    # annualization factor based on date span
    annual_factor = 252.0
    if n > 1:
        dates = pd.to_datetime(eq["date"])
        days = (dates.iloc[-1] - dates.iloc[0]).days
        if days > 0:
            annual_factor = 252.0 * n / days

    annual_return = (final / initial) ** (annual_factor / n) - 1 if n > 0 and initial > 0 and final > 0 else 0.0

    # max drawdown
    peak = np.maximum.accumulate(equity)
    drawdown = equity / peak - 1
    max_drawdown = float(np.min(drawdown)) if len(drawdown) else 0.0

    # volatility & sharpe
    vol = sharpe = 0.0
    if n > 2:
        rets = np.diff(equity) / equity[:-1]
        rets = rets[np.isfinite(rets)]
        if len(rets) > 1:
            std = np.std(rets, ddof=1)
            vol = float(std * np.sqrt(annual_factor))
            sharpe = float(np.mean(rets) / std * np.sqrt(annual_factor)) if std > 0 else 0.0

    # win rate: match sells to buys FIFO, compute realized P&L per closed round-trip
    wins = losses = 0
    open_buys: list[dict] = []
    for t in trades:
        if t["side"] == "buy":
            open_buys.append(t)
        else:
            # realize against oldest open buy(s)
            remaining = t["shares"]
            cost = 0.0
            while remaining > 0 and open_buys:
                b = open_buys[0]
                take = min(remaining, b["shares"])
                cost += take * b["price"]
                remaining -= take
                b["shares"] -= take
                if b["shares"] == 0:
                    open_buys.pop(0)
            pnl = t["amount"] - cost - t["commission"] - t.get("stamp_duty", 0.0) - t.get("transfer_fee", 0.0)
            if pnl >= 0:
                wins += 1
            else:
                losses += 1
    n_closed = wins + losses
    win_rate = wins / n_closed if n_closed else 0.0

    return {
        "total_return": total_return,
        "annual_return": annual_return,
        "max_drawdown": max_drawdown,
        "volatility": vol,
        "sharpe": sharpe,
        "n_trades": len(trades),
        "n_buys": sum(1 for t in trades if t["side"] == "buy"),
        "n_sells": sum(1 for t in trades if t["side"] == "sell"),
        "win_rate": win_rate,
        "final_equity": final,
        "initial_equity": initial,
        "equity_curve": eq.to_dict("records"),
        "trades": trades,
    }
