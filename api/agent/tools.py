"""LLM agent tools: the action surface the model can call."""
from __future__ import annotations

import json

from data.registry import get_registry
from strategies.base import validate_strategy_source
from strategies.manager import StrategyManager


class AgentToolContext:
    def __init__(self, store, executor, data_layer=None, strategy_manager=None):
        self.store = store
        self.executor = executor
        self.data_layer = data_layer
        self.strategy_manager = strategy_manager or StrategyManager()


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def list_symbols(input_: dict, ctx: AgentToolContext) -> str:
    reg = get_registry()
    typ = input_.get("type")
    keyword = input_.get("keyword")
    items = reg.list(typ)
    if keyword:
        kw = keyword.strip().lower()
        items = [s for s in items if kw in s.code.lower() or kw in s.name.lower()]
    items = items[:20]
    return _json({"symbols": [{"code": s.code, "name": s.name, "type": s.type} for s in items]})


def run_backtest(input_: dict, ctx: AgentToolContext) -> str:
    symbol = input_["symbol"]
    strategy_ref = input_.get("strategy_ref", input_.get("strategy", "buy_and_hold"))
    params = input_.get("params", {})
    job_id = ctx.executor.submit(
        symbol=symbol,
        strategy_ref=strategy_ref,
        params=params,
        freq=input_.get("freq", "daily"),
        start=input_.get("start", "2020-01-01"),
        end=input_.get("end", "2024-12-31"),
        adjust=input_.get("adjust", "qfq"),
    )
    return _json({"job_id": job_id, "status": "running", "symbol": symbol})


def register_strategy(input_: dict, ctx: AgentToolContext) -> str:
    name = input_["name"]
    source = input_["source"]
    validate_strategy_source(source)  # AST sandbox — raises on invalid
    rec = ctx.store.register_draft(name, source, input_.get("description", ""))
    return _json(rec)


def list_strategies(input_: dict, ctx: AgentToolContext) -> str:
    return _json({"strategies": ctx.store.list_strategies()})


def publish_strategy(input_: dict, ctx: AgentToolContext) -> str:
    if not input_.get("goal_met"):
        raise ValueError("cannot publish: goal not met")
    name = input_["name"]
    version = input_.get("version")
    g = ctx.store.get_strategy(name)
    if g is None:
        raise KeyError(f"unknown strategy: {name}")
    if version is None:
        version = g["current_version"]
    rec = ctx.store.publish_version(
        name, int(version),
        metrics=input_.get("metrics", {}),
        goal=input_.get("goal", ""),
    )
    return _json(rec)


_DRAWDOWN_KEYS = ("max_drawdown",)


def check_goal(input_: dict, ctx: AgentToolContext) -> str:
    """LLM supplies metrics + constraints; code verifies each constraint."""
    metrics = input_.get("metrics", {})
    constraints = input_.get("constraints", {})
    unmet = []
    for key, threshold in constraints.items():
        val = metrics.get(key)
        if val is None:
            unmet.append(f"{key}: missing")
            continue
        if not isinstance(threshold, (int, float)):
            unmet.append(f"{key}: bad threshold")
            continue
        if key in _DRAWDOWN_KEYS:
            # max_drawdown is a loss metric: a smaller magnitude is better. A
            # strategy meets its drawdown limit when |val| <= |threshold|, so a
            # positive threshold (e.g. 0.15) means the same limit as -0.15.
            if not abs(float(val)) <= abs(float(threshold)):
                unmet.append(f"{key}: |{val}| > |{threshold}|")
        else:
            # return-style metrics (total_return, annual_return, sharpe,
            # win_rate): bigger is better (lower-bound constraint).
            if not float(val) >= float(threshold):
                unmet.append(f"{key}: {val} < {threshold}")
    met = not unmet
    return _json({"met": met, "unmet": unmet, "metrics": metrics})


TOOLS: list[dict] = [
    {
        "name": "list_symbols",
        "description": "List tradable symbols (stocks/funds/ETFs). Optional type ('stock'|'etf'|'fund') and keyword filter. Use to choose which symbol(s) to backtest. Returns up to 20 matches, each {code, name, type}.",
        "parameters": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["stock", "etf", "fund"], "description": "Symbol type filter"},
                "keyword": {"type": "string", "description": "Code or name keyword"},
            },
        },
    },
    {
        "name": "run_backtest",
        "description": "Submit a backtest job asynchronously. Returns {job_id, status, symbol}. Submit multiple in one turn to run in parallel; the agent waits for all to finish before continuing. strategy_ref is a strategy NAME (the current draft) — call register_strategy first to create/update the draft.",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Symbol code, e.g. 510300"},
                "strategy_ref": {"type": "string", "description": "Strategy name (registered draft). Omit to use the default buy_and_hold."},
                "params": {"type": "object", "description": "Strategy parameters passed through to strategy(ctx, params), e.g. {\"short\": 20}."},
                "freq": {"type": "string", "enum": ["daily", "1", "5", "15", "30", "60"], "description": "Bar frequency: 'daily' for daily bars, or a minute interval in minutes ('1'|'5'|'15'|'30'|'60')."},
                "start": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "end": {"type": "string", "description": "End date YYYY-MM-DD"},
                "adjust": {"type": "string", "enum": ["qfq", "hfq", "none"], "description": "Price adjustment: 'qfq' forward-adjusted (default), 'hfq' backward-adjusted, 'none' raw."},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "register_strategy",
        "description": "Create or update a strategy draft from Python source. The source must define `def strategy(ctx, params)` and use ctx.buy()/ctx.sell(). Updates the current draft; version increments. Always call this before running a backtest on your own strategy.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Strategy name (draft identifier). Registering an existing name updates that draft."},
                "source": {"type": "string", "description": "Python source defining `def strategy(ctx, params)`, called once per bar. ctx provides: ctx.price/open/high/low/volume (current bar), ctx.position/ctx.shares/ctx.cash, ctx.bars_upto(lookback) for historical bars. Trade via ctx.buy(shares, price) / ctx.sell(shares, price) (both return bool; price defaults to close, shares defaults to all-in / full position). params is the run_backtest params dict. Engine enforces A-share rules (T+1, price limit, 100-share lots). Indicator helpers sma/ema/rsi/macd are pre-injected (call directly, no import); math/numpy/pandas may be imported as math/np/pandas."},
                "description": {"type": "string", "description": "Optional human-readable summary of the strategy's logic."},
            },
            "required": ["name", "source"],
        },
    },
    {
        "name": "list_strategies",
        "description": "List registered strategies (drafts + published), each {name, status, current_version}. Use to confirm a draft name before running a backtest.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "publish_strategy",
        "description": "Publish a strategy version ONLY when the goal is met. Requires goal_met=true. Records the metrics snapshot. Call check_goal first to confirm.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Registered strategy name (the draft to publish)"},
                "version": {"type": "integer", "description": "Optional; defaults to current draft version"},
                "goal_met": {"type": "boolean", "description": "Must be true to publish"},
                "metrics": {"type": "object", "description": "Metrics snapshot at publish time, e.g. the backtest metrics that met the goal"},
                "goal": {"type": "string", "description": "The user goal this version satisfies (recorded for the report)"},
            },
            "required": ["name", "goal_met"],
        },
    },
    {
        "name": "check_goal",
        "description": "Verify whether backtest metrics meet the user's goal constraints. Pass the metrics from the backtest result and the goal constraints. Return-style metrics (total_return, annual_return, sharpe, win_rate) are lower bounds (meet when >= threshold); max_drawdown is compared by magnitude (meet when |max_drawdown| <= |threshold|). Returns met: true/false.",
        "parameters": {
            "type": "object",
            "properties": {
                "metrics": {"type": "object", "description": "Backtest metrics, e.g. {annual_return, total_return, max_drawdown, sharpe}"},
                "constraints": {"type": "object", "description": "Goal thresholds, e.g. {annual_return: 0.10, max_drawdown: -0.15}"},
            },
            "required": ["metrics", "constraints"],
        },
    },
]
