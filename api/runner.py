"""Backtest runner: wires universe -> strategy -> engine into one call."""
from __future__ import annotations

from data.registry import get_registry
from data.sources import DataLayer
from engine.engine import BacktestEngine, EngineConfig
from engine.universe import metadata_calendar, resolve_universe
from strategies.manager import StrategyManager


def run_backtest(
    strategy,
    universe: dict | None = None,
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
    """Resolve universe, align calendar, run engine, return metrics+curves+trades."""
    sm = strategy_manager or StrategyManager()
    dl = data_layer or DataLayer()
    symbols = resolve_universe(universe, freq=freq, adjust=adjust)
    if not symbols:
        raise ValueError("universe is empty: no cached symbols match, pass symbols explicitly")
    calendar = metadata_calendar(symbols, start, end, freq, adjust, dl)
    if not calendar:
        raise ValueError("no data for universe")

    func, strat_name = sm.resolve(strategy) if isinstance(strategy, (str, dict)) else (strategy, getattr(strategy, "__name__", "strategy"))

    cfg = EngineConfig(
        initial_cash=initial_cash,
        commission_rate=commission_rate,
        stamp_duty=stamp_duty,
        lot_size=lot_size,
    )
    engine = BacktestEngine(cfg, data_layer=dl)
    result = engine.run(func, calendar, symbols, freq=freq, start=start, end=end, adjust=adjust)
    metrics = result.metrics
    return {
        "success": True,
        "universe": symbols,
        "symbol": symbols[0] if symbols else "",
        "symbol_name": (lambda info: info.name)(get_registry().get(symbols[0])) if symbols else "",
        "freq": freq,
        "adjust": adjust,
        "metrics": {k: v for k, v in metrics.items() if k not in ("equity_curve", "trades")},
        "equity_curve": metrics.get("equity_curve", []),
        "trades": metrics.get("trades", []),
        "strategy": strat_name,
    }
