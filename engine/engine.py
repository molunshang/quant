"""Portfolio backtest engine: unified calendar, target-weight matching, A-share rules."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from .context import Context
from .rules import TradingRules, calc_commission, calc_transfer_fee

StrategyFunc = Callable[[Context], None]


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


class BacktestResult:
    def __init__(self, equity_curve: pd.DataFrame, trades: list[dict], ctx: Context, freq: str):
        self.equity_curve = equity_curve
        self.trades = trades
        self.ctx = ctx
        self.freq = freq

    @property
    def metrics(self) -> dict:
        return compute_metrics(self.equity_curve, self.trades)


class BacktestEngine:
    def __init__(self, config: EngineConfig | None = None, data_layer=None):
        self.cfg = config or EngineConfig()
        self.data_layer = data_layer

    def _rules(self, symbol_type: str) -> TradingRules:
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

    def run(self, strategy: StrategyFunc, calendar: list[str], universe: list[str],
            freq: str = "daily", start: str = "2020-01-01", end: str = "2024-12-31",
            adjust: str = "qfq") -> BacktestResult:
        if not calendar or not universe:
            raise ValueError("calendar and universe must be non-empty")
        ctx = Context(self.cfg.initial_cash, engine=self, universe=universe,
                      calendar=calendar, data_layer=self.data_layer)
        ctx._freq = freq
        ctx._adjust = adjust
        self._ctx = ctx
        # optional one-time initialization on the SAME ctx the loop uses
        init = getattr(strategy, "__initialize__", None)
        if callable(init):
            init(ctx)
        equity_rows: list[dict] = []
        benchmark = self._load_benchmark(start, end)

        for i, day in enumerate(calendar):
            ctx.time = str(day)
            ctx.bar_index = i
            # corporate-action factor adjustment (per symbol, before strategy sees data)
            self._apply_corporate_actions(ctx)
            # strategy decides target weights; ctx.buy/sell fill IMMEDIATELY at
            # the current bar's close (eager matching) under A-share rules.
            # Built-ins sell-then-buy so cash from sells is available to buys.
            strategy(ctx)
            equity_rows.append({
                "date": str(day),
                "cash": ctx.cash,
                "position": sum(ctx.positions.values()),
                "market_value": ctx.market_value,
                "equity": ctx.total_value,
                "benchmark": benchmark.get(str(day)),
            })

        equity_df = pd.DataFrame(equity_rows)
        return BacktestResult(equity_curve=equity_df, trades=ctx.trades, ctx=ctx, freq=freq)

    # ---- matching ----
    def buy(self, ctx: Context, symbol: str, pct: float = 1.0) -> bool:
        """Buy `symbol` up to pct of current net value. Returns True if filled."""
        target = ctx.total_value * pct
        bar = self._bar_for(ctx, symbol)
        price = float(bar["close"])
        rules = self._rules(ctx._symbol_type.get(symbol, "stock"))
        lot = rules.lot_size
        if rules.price_limit_pct > 0 and self._is_limit_up(bar, rules):
            return False
        shares = int(target // (price * lot)) * lot
        if shares <= 0:
            return False
        # step down a lot until fits cash incl. commission
        while shares > 0:
            amount = shares * price
            commission = calc_commission(amount, rules.commission_rate, rules.min_commission)
            if amount + commission <= ctx.cash:
                break
            shares -= lot
        if shares <= 0:
            return False
        amount = shares * price
        commission = calc_commission(amount, rules.commission_rate, rules.min_commission)
        ctx.cash -= amount + commission
        pos = ctx.positions.get(symbol, 0)
        prev_cost = ctx._avg_cost.get(symbol, 0.0) * pos
        ctx.positions[symbol] = pos + shares
        ctx._avg_cost[symbol] = (prev_cost + amount) / ctx.positions[symbol]
        if rules.t_plus_1:
            ctx._buy_dates.setdefault(symbol, set()).add(str(ctx.time))
        ctx.trades.append({
            "date": str(ctx.time), "symbol": symbol, "side": "buy", "shares": shares,
            "price": price, "amount": amount, "commission": commission,
            "stamp_duty": 0.0, "transfer_fee": 0.0,
        })
        return True

    def sell(self, ctx: Context, symbol: str, pct: float = 1.0) -> bool:
        """Sell pct of `symbol` position (default: entire). Returns True if filled."""
        pos = ctx.positions.get(symbol, 0)
        if pos <= 0:
            return False
        bar = self._bar_for(ctx, symbol)
        price = float(bar["close"])
        rules = self._rules(ctx._symbol_type.get(symbol, "stock"))
        if rules.t_plus_1 and str(ctx.time) in ctx._buy_dates.get(symbol, set()):
            return False
        if rules.price_limit_pct > 0 and self._is_limit_down(bar, rules):
            return False
        shares = int(pos * pct // rules.lot_size) * rules.lot_size
        if shares <= 0:
            return False
        shares = min(shares, pos)
        amount = shares * price
        commission = calc_commission(amount, rules.commission_rate, rules.min_commission)
        is_etf = rules.is_etf_or_fund(ctx._symbol_type.get(symbol, "stock"))
        stamp = 0.0 if is_etf else amount * rules.stamp_duty
        transfer = calc_transfer_fee(amount, rules.transfer_fee_rate) if not is_etf else 0.0
        ctx.cash += amount - commission - stamp - transfer
        ctx.positions[symbol] = pos - shares
        if ctx.positions[symbol] == 0:
            ctx._avg_cost.pop(symbol, None)
        ctx.trades.append({
            "date": str(ctx.time), "symbol": symbol, "side": "sell", "shares": shares,
            "price": price, "amount": amount, "commission": commission,
            "stamp_duty": stamp, "transfer_fee": transfer,
        })
        return True

    # ---- helpers ----
    def _bar_for(self, ctx: Context, symbol: str) -> pd.Series:
        df = ctx._ensure_loaded(symbol)
        idx = ctx._idx_at_current(symbol)
        return df.iloc[idx]

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

    def _apply_corporate_actions(self, ctx: Context) -> None:
        """Scale positions on factor-change days (total-return approx)."""
        for symbol in list(ctx.positions):
            df = ctx._bars.get(symbol)
            if df is None or "factor" not in df.columns:
                continue
            idx = ctx._idx_at_current(symbol)
            if idx <= 0:
                continue
            prev = float(df["factor"].iloc[idx - 1]) or 1.0
            cur = float(df["factor"].iloc[idx]) or 1.0
            if prev <= 0:
                continue
            ratio = cur / prev
            if ratio != 1.0 and ctx.positions[symbol] > 0:
                pos = ctx.positions[symbol]
                new_pos = int(pos * ratio // self.cfg.lot_size) * self.cfg.lot_size
                if new_pos > 0:
                    ctx._avg_cost[symbol] = ctx._avg_cost.get(symbol, 0.0) * pos / new_pos
                    ctx.positions[symbol] = new_pos

    def _load_benchmark(self, start: str, end: str) -> dict[str, float]:
        """Load CSI 300 index (000300) closes for the benchmark series.

        Uses the same DataLayer lazy path; if unavailable, returns {} (equity
        curve rows get None benchmark — metrics treat it as all-1.0).
        """
        if self.data_layer is None:
            return {}
        try:
            from data.sources import SymbolInfo
            info = SymbolInfo("000300", "沪深300", "index", "sh")
            df = self.data_layer.get_bars(info, freq="daily", start=start, end=end, adjust="qfq")
            if df is None or df.empty:
                return {}
            return {str(d): float(c) for d, c in zip(df["date"], df["close"])}
        except Exception:  # noqa: BLE001 - benchmark is optional
            return {}


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
    annual_factor = 252.0
    if n > 1:
        dates = pd.to_datetime(eq["date"])
        days = (dates.iloc[-1] - dates.iloc[0]).days
        if days > 0:
            annual_factor = 252.0 * n / days
    annual_return = (final / initial) ** (annual_factor / n) - 1 if n > 0 and initial > 0 and final > 0 else 0.0
    peak = np.maximum.accumulate(equity)
    drawdown = equity / peak - 1
    max_drawdown = float(np.min(drawdown)) if len(drawdown) else 0.0
    vol = sharpe = 0.0
    if n > 2:
        rets = np.diff(equity) / equity[:-1]
        rets = rets[np.isfinite(rets)]
        if len(rets) > 1:
            std = np.std(rets, ddof=1)
            vol = float(std * np.sqrt(annual_factor))
            sharpe = float(np.mean(rets) / std * np.sqrt(annual_factor)) if std > 0 else 0.0
    wins = losses = 0
    open_buys: list[dict] = []
    for t in trades:
        if t["side"] == "buy":
            open_buys.append(t)
        else:
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
        "total_return": total_return, "annual_return": annual_return,
        "max_drawdown": max_drawdown, "volatility": vol, "sharpe": sharpe,
        "n_trades": len(trades),
        "n_buys": sum(1 for t in trades if t["side"] == "buy"),
        "n_sells": sum(1 for t in trades if t["side"] == "sell"),
        "win_rate": win_rate, "final_equity": final, "initial_equity": initial,
        "equity_curve": eq.to_dict("records"), "trades": trades,
    }
