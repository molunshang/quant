"""LLM agent tools: the action surface the model can call."""
from __future__ import annotations

import json

from data.indices import index_constituents, resolve_index
from data.industry import industry_constituents, list_industries as list_sw_industries
from data.registry import _exchange, get_registry
from data.sources import SymbolInfo
from strategies.base import load_strategy_from_source
from strategies.manager import StrategyManager


class AgentToolContext:
    def __init__(self, store, executor, data_layer=None, strategy_manager=None,
                 session_id=None, training_period=None, goal=None, validation_runner=None):
        self.store = store
        self.executor = executor
        self.data_layer = data_layer
        self.strategy_manager = strategy_manager or StrategyManager()
        self.session_id = session_id
        self.training_period = training_period
        self.goal = goal
        self.validation_runner = validation_runner


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def list_symbols(input_: dict, ctx: AgentToolContext) -> str:
    typ = input_.get("type")
    keyword = input_.get("keyword")
    index_code = input_.get("index")
    industry = input_.get("industry")
    items = None
    if index_code:
        code = resolve_index(index_code) or index_code
        items = [SymbolInfo(c["code"], c["name"], "stock", _exchange(c["code"]))
                 for c in index_constituents(code)]
    elif industry:
        items = [SymbolInfo(c["code"], c["name"], "stock", _exchange(c["code"]))
                 for c in industry_constituents(industry)]
    else:
        items = get_registry().list(typ)
    if keyword:
        kw = keyword.strip().lower()
        items = [s for s in items if kw in s.code.lower() or kw in s.name.lower()]
    items = items[:20]
    return _json({"symbols": [{"code": s.code, "name": s.name, "type": s.type} for s in items]})


def run_backtest(input_: dict, ctx: AgentToolContext) -> str:
    strategy_ref = input_.get("strategy_ref", input_.get("strategy", "buy_and_hold"))
    start = input_.get("start", "2020-01-01")
    end = input_.get("end", "2024-12-31")
    tp = getattr(ctx, "training_period", None)
    if tp and not (tp.get("start", "") <= start and end <= tp.get("end", "")):
        raise ValueError(
            f"run_backtest 只能在训练段 {tp.get('start')}~{tp.get('end')} 内回测"
            "（防过拟合：验证段由系统在发布时自动验收，禁止直接运行）")
    job_id = ctx.executor.submit(
        strategy_ref=strategy_ref,
        universe=input_.get("universe"),
        freq=input_.get("freq", "daily"),
        start=start,
        end=end,
        adjust=input_.get("adjust", "qfq"),
    )
    return _json({"job_id": job_id, "status": "running", "strategy": strategy_ref})


def register_strategy(input_: dict, ctx: AgentToolContext) -> str:
    name = input_["name"]
    source = input_["source"]
    load_strategy_from_source(source, name)  # AST sandbox + requires handle_data(ctx) — raises on invalid
    rec = ctx.store.register_draft(name, source, input_.get("description", ""))
    if ctx.session_id:
        ctx.store.link_session_strategy(ctx.session_id, name, rec["version"])
    return _json(rec)


def list_strategies(input_: dict, ctx: AgentToolContext) -> str:
    return _json({"strategies": ctx.store.list_strategies()})


def list_industries(input_: dict, ctx: AgentToolContext) -> str:
    return _json({"industries": list_sw_industries()})


def query_sector_perf(input_: dict, ctx: AgentToolContext) -> str:
    """近 N 日涨跌幅 for an index or SW industry. Best-effort."""
    code = input_.get("code")
    days = int(input_.get("days", 60))
    if not code:
        raise ValueError("code required (index code like 000300, or SW industry like 801080.SI)")
    index_code = resolve_index(code) or str(code).replace(".SI", "").replace(".si", "")
    prefix = "sz" if index_code.startswith("39") else "sh"
    import akshare as ak

    df = ak.stock_zh_index_daily(symbol=f"{prefix}{index_code}")
    df = df.tail(days)
    if df.empty:
        return _json({"code": code, "days": days, "return_pct": None})
    ret = float(df["close"].iloc[-1]) / float(df["close"].iloc[0]) - 1
    return _json({
        "code": code, "days": days,
        "return_pct": round(ret, 6),
        "start": str(df["date"].iloc[0]), "end": str(df["date"].iloc[-1]),
    })


def diagnose_backtest(input_: dict, ctx: AgentToolContext) -> str:
    job_id = int(input_.get("job_id"))
    job = ctx.executor.get_job(job_id)
    if job is None:
        raise KeyError(f"job {job_id} 不在已完成结果中（先 run_backtest 并等它完成）")
    result = job.get("result")
    if not result:
        raise ValueError(f"job {job_id} 无可用结果")
    from engine.diagnose import diagnose

    return _json(diagnose(result.get("equity_curve"), result.get("trades")))


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
    goal = getattr(ctx, "goal", None) or {}
    constraints = goal.get("constraints") or {}
    validation_periods = goal.get("validation_periods") or []
    source = ctx.store.get_source(name, int(version))
    universe = input_.get("universe")
    runner = getattr(ctx, "validation_runner", None) or make_validation_runner(ctx, name, source)
    validation_metrics, failures = validate_strategy_on_periods(
        name, source, constraints, validation_periods,
        runner=runner, universe=universe,
    )
    if failures:
        raise ValueError(
            "验证段不达标，拒绝发布（防过拟合）："
            + json.dumps(failures, ensure_ascii=False)
            + " 请回到训练段继续调优后再试")
    rec = ctx.store.publish_version(
        name, int(version),
        metrics=input_.get("metrics", {}),
        goal=input_.get("goal", ""),
        validation_metrics=validation_metrics,
    )
    return _json(rec)


_DRAWDOWN_KEYS = ("max_drawdown",)


def evaluate_constraints(metrics: dict, constraints: dict) -> list[str]:
    """Return list of unmet constraint descriptions. Empty means all met."""
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
            # max_drawdown is a loss metric: meet when |val| <= |threshold|
            if not abs(float(val)) <= abs(float(threshold)):
                unmet.append(f"{key}: |{val}| > |{threshold}|")
        else:
            if not float(val) >= float(threshold):
                unmet.append(f"{key}: {val} < {threshold}")
    return unmet


def check_goal(input_: dict, ctx: AgentToolContext) -> str:
    """LLM supplies metrics + constraints; code verifies each constraint."""
    metrics = input_.get("metrics", {})
    constraints = input_.get("constraints", {})
    unmet = evaluate_constraints(metrics, constraints)
    return _json({"met": not unmet, "unmet": unmet, "metrics": metrics})


def validate_strategy_on_periods(strategy_name, source, constraints, validation_periods,
                                 runner, universe=None):
    """Run the strategy on each validation period, check constraints.

    runner(period: dict, universe) -> backtest metrics dict.
    Returns (validation_metrics, failures):
      - validation_metrics: [{period, metrics}] for every period that ran
      - failures: [{period, unmet|error}] for every period that failed
    """
    validation_metrics = []
    failures = []
    for vp in validation_periods:
        try:
            metrics = runner(vp, universe)
        except Exception as e:  # noqa: BLE001 - surface per-period error
            failures.append({"period": vp, "error": str(e)})
            continue
        unmet = evaluate_constraints(metrics, constraints)
        validation_metrics.append({"period": vp, "metrics": metrics})
        if unmet:
            failures.append({"period": vp, "unmet": unmet})
    return validation_metrics, failures


def make_validation_runner(ctx, name, source):
    """Real validation runner: registers the strategy source and runs a backtest
    over a period via api.runner. Network-backed — tests inject ctx.validation_runner."""
    def runner(period, universe):
        ctx.strategy_manager.register(name, source)
        from api.runner import run_backtest

        res = run_backtest(
            strategy=name, universe=universe, freq="daily",
            start=period["start"], end=period["end"], adjust="qfq",
            initial_cash=ctx.executor.initial_cash,
            strategy_manager=ctx.strategy_manager,
        )
        return res["metrics"]
    return runner


TOOLS: list[dict] = [
    {
        "name": "list_symbols",
        "description": "List tradable symbols (stocks/funds/ETFs), or constituents of an index (index=) or SW industry (industry=). Optional type ('stock'|'etf'|'fund') and keyword filter. Use to choose which symbol(s) to backtest. Returns up to 20 matches, each {code, name, type}.",
        "parameters": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["stock", "etf", "fund"], "description": "Symbol type filter"},
                "keyword": {"type": "string", "description": "Code or name keyword"},
                "index": {"type": "string", "description": "Index code or name (e.g. 000300 or 沪深300) to list its constituent stocks."},
                "industry": {"type": "string", "description": "SW industry code (e.g. 801080.SI) to list its constituent stocks."},
            },
        },
    },
    {
        "name": "run_backtest",
        "description": "Submit a portfolio backtest job asynchronously. Returns {job_id, status, strategy}. Submit multiple in one turn to run in parallel; the agent waits for all to finish before continuing. strategy_ref is a strategy NAME (the current draft) — call register_strategy first to create/update the draft. The strategy picks its own symbols from the universe (默认=已缓存标的集); universe is optional to restrict the pool. Backtests are restricted to the training period (防过拟合：验证段由系统在发布时自动验收，禁止直接运行); start/end must lie within it.",
        "parameters": {
            "type": "object",
            "properties": {
                "strategy_ref": {"type": "string", "description": "Strategy name (registered draft)."},
                "universe": {"type": "object", "description": "Optional symbol pool: {\"symbols\": [...]} explicit list, or {\"types\": [...]} type filter. Omit to default to all cached symbols."},
                "freq": {"type": "string", "enum": ["daily", "1", "5", "15", "30", "60"], "description": "Bar frequency: 'daily' for daily bars, or a minute interval in minutes ('1'|'5'|'15'|'30'|'60')."},
                "start": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "end": {"type": "string", "description": "End date YYYY-MM-DD"},
                "adjust": {"type": "string", "enum": ["qfq", "hfq", "none"], "description": "Price adjustment: 'qfq' forward-adjusted (default), 'hfq' backward-adjusted, 'none' raw."},
            },
            "required": ["strategy_ref"],
        },
    },
    {
        "name": "register_strategy",
        "description": "Create or update a strategy draft from Python source. The source must define `def initialize(ctx)` (optional) + `def handle_data(ctx)` and use ctx.history()/ctx.buy()/ctx.sell(). Updates the current draft; version increments. Always call this before running a backtest on your own strategy.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Strategy name (draft identifier). Registering an existing name updates that draft."},
                "source": {"type": "string", "description": "Python source defining `def handle_data(ctx)` (called once per time step = per trading day), plus optional `def initialize(ctx)` for cross-bar constants in ctx.state. ctx provides: ctx.universe (candidate symbols), ctx.state (cross-bar dict), ctx.history(symbol, lookback) for a symbol's history up to the current bar, ctx.price(symbol), ctx.positions (dict symbol->shares), ctx.cash, ctx.total_value. Trade via ctx.buy(symbol, pct) / ctx.sell(symbol, pct) (pct relative to net value 0~1, both return bool; sell default clears the position). Engine enforces A-share rules (T+1, price limit, 100-share lots). Indicator helpers sma/ema/rsi/macd are pre-injected (call directly, no import); math/numpy/pandas may be imported as math/np/pandas."},
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
        "name": "list_industries",
        "description": "List SW (申万) first-level industries, each {code, name, n_stocks}. Use to pick a sector as the backtest universe.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "query_sector_perf",
        "description": "Return the trailing N-day return (%) of an index or SW industry (code: index code like 000300, or SW industry like 801080.SI). Use to see which sectors have been strong recently.",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Index code (000300) or SW industry code (801080.SI)."},
                "days": {"type": "integer", "description": "Trailing days, default 60."},
            },
            "required": ["code"],
        },
    },
    {
        "name": "diagnose_backtest",
        "description": "Deep-diagnose a completed backtest by job_id: monthly returns, drawdown peak/trough, per-symbol profit attribution, holdings history, benchmark comparison. Call when a backtest misses its goal to understand WHY before revising the strategy.",
        "parameters": {
            "type": "object",
            "properties": {
                "job_id": {"type": "integer", "description": "The job_id returned by run_backtest."},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "publish_strategy",
        "description": "Publish a strategy version ONLY when the goal is met AND all hidden validation periods pass. Requires goal_met=true. On publish the system automatically runs the strategy on every validation period (unseen by the agent); if any validation period misses the goal constraints, publish is rejected with the shortfalls. Pass the same universe you used for the winning training backtest.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Registered strategy name (the draft to publish)"},
                "version": {"type": "integer", "description": "Optional; defaults to current draft version"},
                "goal_met": {"type": "boolean", "description": "Must be true to publish"},
                "universe": {"type": "object", "description": "Optional; the universe the winning training backtest used, e.g. {\"symbols\": [...]}. Validation runs on this pool."},
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
