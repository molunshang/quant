"""Backtest runner: wires data layer -> strategy -> engine into one call."""
from __future__ import annotations

import itertools

import pandas as pd

from data.registry import get_registry
from data.sources import DataLayer
from engine.engine import BacktestEngine, EngineConfig
from strategies.manager import StrategyManager


def run_backtest(
    symbol: str,
    strategy_ref,
    params: dict | None = None,
    freq: str = "daily",
    start: str = "2020-01-01",
    end: str = "2024-12-31",
    adjust: str = "qfq",
    initial_cash: float = 100_000.0,
    commission_rate: float = 0.0003,
    stamp_duty: float = 0.0005,
    lot_size: int = 100,
    strategy_manager: StrategyManager | None = None,
    data_layer: DataLayer | None = None,
) -> dict:
    """Fetch bars, resolve strategy, run engine, return metrics+curves+trades."""
    sm = strategy_manager or StrategyManager()
    dl = data_layer or DataLayer()
    reg = get_registry()

    info = reg.get(symbol)
    bars = dl.get_bars(info, freq=freq, start=start, end=end, adjust=adjust)
    if bars is None or bars.empty:
        raise ValueError(f"no data for {symbol}")

    func, strat_name = sm.resolve(strategy_ref)
    cfg = EngineConfig(
        initial_cash=initial_cash,
        commission_rate=commission_rate,
        stamp_duty=stamp_duty,
        lot_size=lot_size,
        symbol_type=info.type,
    )
    engine = BacktestEngine(cfg)
    result = engine.run(func, bars, params=params)

    metrics = result.metrics
    return {
        "success": True,
        "symbol": symbol,
        "symbol_name": info.name,
        "freq": freq,
        "metrics": {k: v for k, v in metrics.items() if k not in ("equity_curve", "trades")},
        "equity_curve": metrics.get("equity_curve", []),
        "trades": metrics.get("trades", []),
        "strategy": strat_name,
        "params": params or {},
    }


def run_optimize(
    symbol: str,
    strategy_ref,
    param_grid: dict[str, list],
    metric: str = "sharpe",
    freq: str = "daily",
    start: str = "2020-01-01",
    end: str = "2024-12-31",
    adjust: str = "qfq",
    initial_cash: float = 100_000.0,
) -> dict:
    """Grid-search over param_grid, return ranked results."""
    if not param_grid:
        raise ValueError("param_grid must be non-empty")

    keys = list(param_grid.keys())
    combos = list(itertools.product(*[param_grid[k] for k in keys]))
    if not combos:
        raise ValueError("param_grid produced no combinations")

    dl = DataLayer()
    reg = get_registry()
    info = reg.get(symbol)
    bars = dl.get_bars(info, freq=freq, start=start, end=end, adjust=adjust)
    if bars is None or bars.empty:
        raise ValueError(f"no data for {symbol}")

    sm = StrategyManager()
    func, strat_name = sm.resolve(strategy_ref)

    results = []
    for combo in combos:
        p = dict(zip(keys, combo))
        cfg = EngineConfig(initial_cash=initial_cash, symbol_type=info.type)
        engine = BacktestEngine(cfg)
        try:
            r = engine.run(func, bars, params=p)
            m = r.metrics
            m.pop("equity_curve", None)
            m.pop("trades", None)
            results.append({"params": p, "metrics": m})
        except Exception as e:  # noqa: BLE001 - keep scanning
            results.append({"params": p, "metrics": {"error": str(e)}})

    def _metric_val(r: dict) -> float:
        v = r["metrics"].get(metric)
        return float(v) if isinstance(v, (int, float)) else float("-inf")

    results.sort(key=_metric_val, reverse=True)
    return {
        "success": True,
        "symbol": symbol,
        "metric": metric,
        "strategy": strat_name,
        "n_combos": len(combos),
        "best": results[0] if results else None,
        "results": results,
    }
