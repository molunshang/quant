"""Deep backtest diagnosis: monthly returns, drawdown, symbol attribution.

`diagnose()` produces the breakdown a strategy agent needs to understand WHY a
backtest missed its goal — fed by `diagnose_backtest` (api/agent/tools.py).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _as_frame(equity_curve) -> pd.DataFrame:
    if isinstance(equity_curve, pd.DataFrame):
        return equity_curve.copy()
    return pd.DataFrame(equity_curve or [])


def _monthly_returns(eq: pd.DataFrame) -> dict:
    df = eq.copy()
    df["ym"] = pd.to_datetime(df["date"]).dt.to_period("M")
    ends = df.groupby("ym")["equity"].last()
    rets = ends.pct_change().dropna()
    out: dict = {}
    for ym, r in rets.items():
        out.setdefault(str(ym.year), {})[int(ym.month)] = round(float(r), 6)
    return out


def _drawdown_analysis(eq: pd.DataFrame) -> dict:
    equity = eq["equity"].astype(float).values
    dates = eq["date"].tolist()
    peak = np.maximum.accumulate(equity)
    dd = equity / peak - 1
    max_i = int(np.argmin(dd))
    peak_i = int(np.argmax(equity[:max_i + 1]))
    longest = cur = 0
    for v in dd:
        if v < 0:
            cur += 1
            longest = max(longest, cur)
        else:
            cur = 0
    return {
        "max_drawdown": round(float(dd[max_i]), 6),
        "peak_date": dates[peak_i],
        "trough_date": dates[max_i],
        "longest_drawdown_days": longest,
    }


def _symbol_attribution(trades: list[dict]) -> list[dict]:
    per_sym: dict[str, dict] = {}
    for t in trades:
        sym = t.get("symbol", "?")
        d = per_sym.setdefault(sym, {"buys": [], "pnl": 0.0, "n_trades": 0,
                                     "max_single_loss": 0.0, "buy_date": None,
                                     "held_days": 0.0, "sold_shares": 0})
        d["n_trades"] += 1
        if t.get("side") == "buy":
            buy_fee = (float(t.get("commission", 0)) + float(t.get("stamp_duty", 0))
                       + float(t.get("transfer_fee", 0)))
            d["buys"].append({"shares": int(t["shares"]), "price": float(t["price"]),
                              "fee": buy_fee})
            if d["buy_date"] is None or str(t.get("date", "")) < d["buy_date"]:
                d["buy_date"] = str(t.get("date", ""))
        else:
            remaining = int(t["shares"])
            cost = 0.0
            sold = 0
            while remaining > 0 and d["buys"]:
                b = d["buys"][0]
                take = min(remaining, b["shares"])
                ratio = take / b["shares"] if b["shares"] else 0
                cost += take * b["price"] + b["fee"] * ratio
                sold += take
                remaining -= take
                b["shares"] -= take
                b["fee"] -= b["fee"] * ratio
                if b["shares"] == 0:
                    d["buys"].pop(0)
            fees = (float(t.get("commission", 0)) + float(t.get("stamp_duty", 0))
                    + float(t.get("transfer_fee", 0)))
            pnl = float(t.get("amount", 0)) - cost - fees
            d["pnl"] += pnl
            if pnl < d["max_single_loss"]:
                d["max_single_loss"] = pnl
            if d["buy_date"]:
                from datetime import date as _date
                try:
                    buy = _date.fromisoformat(d["buy_date"])
                    sell = _date.fromisoformat(str(t.get("date", "")))
                    d["held_days"] += (sell - buy).days * (sold / int(t["shares"])) if int(t["shares"]) else 0
                except ValueError:
                    pass
            d["sold_shares"] += sold
    # close any remaining open position with the last trade date
    out = []
    for s, v in sorted(per_sym.items()):
        out.append({
            "symbol": s, "pnl": round(v["pnl"], 2), "n_trades": v["n_trades"],
            "max_single_loss": round(v["max_single_loss"], 2),
            "held_days": int(round(v["held_days"])),
        })
    return out


def _holdings_history(eq: pd.DataFrame) -> list[dict]:
    if "n_positions" not in eq.columns:
        return []
    return [{"date": str(d), "n_positions": int(n)}
            for d, n in zip(eq["date"], eq["n_positions"]) if pd.notna(n)]


def _benchmark_comparison(eq: pd.DataFrame) -> dict:
    df = eq.copy()
    df["year"] = pd.to_datetime(df["date"]).dt.year
    out: dict = {}
    for year, g in df.groupby("year"):
        strat = float(g["equity"].iloc[-1]) / float(g["equity"].iloc[0]) - 1
        row = {"strategy_return": round(strat, 6)}
        if "benchmark" in g.columns:
            b = g["benchmark"].astype(float)
            b = b[b.notna()]
            if len(b) >= 2 and b.iloc[0] > 0:
                row["benchmark_return"] = round(float(b.iloc[-1] / b.iloc[0] - 1), 6)
        out[int(year)] = row
    return out


def diagnose(equity_curve, trades: list[dict] | None = None) -> dict:
    """Deep diagnosis of a backtest result.

    `equity_curve` may be a DataFrame or a list of record dicts (as returned in
    a backtest response). Returns {} style dict with monthly returns, drawdown
    analysis, per-symbol attribution, holdings history and benchmark comparison.
    """
    eq = _as_frame(equity_curve)
    if eq.empty:
        return {"error": "no equity data"}
    trades = trades or []
    return {
        "monthly_returns": _monthly_returns(eq),
        "drawdown_analysis": _drawdown_analysis(eq),
        "symbol_attribution": _symbol_attribution(trades),
        "holdings_history": _holdings_history(eq),
        "benchmark_comparison": _benchmark_comparison(eq),
    }
