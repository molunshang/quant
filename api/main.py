"""FastAPI application exposing the backtest + agent API and serving the web UI."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from data.precache import manager as precache_manager
from data.registry import get_registry
from strategies.manager import StrategyManager

from .runner import run_backtest, run_optimize
from .schemas import BacktestRequest, OptimizeRequest, RegisterStrategyRequest

app = FastAPI(title="A股回测系统", version="0.1.0")

# Shared singletons
_strategies = StrategyManager()

# Serve web UI (optional; directory may not exist yet)
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if WEB_DIR.exists():
    app.mount("/web", StaticFiles(directory=str(WEB_DIR), html=True), name="web")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(str(WEB_DIR / "index.html"))


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/symbols")
def symbols(type: str | None = None, keyword: str | None = None, limit: int = 200):
    """List tradable symbols (stocks / funds / ETFs). Optional type & keyword filter."""
    reg = get_registry()
    items = reg.list(type)
    if keyword:
        kw = keyword.strip().lower()
        items = [s for s in items if kw in s.code.lower() or kw in s.name.lower()]
    items = items[:limit]
    return {"symbols": [s.__dict__ for s in items], "total": len(items)}


@app.get("/api/strategies")
def list_strategies():
    """List built-in + registered user strategies."""
    return {"strategies": _strategies.list()}


@app.post("/api/strategies")
def register_strategy(req: RegisterStrategyRequest):
    """Register (or overwrite) a user strategy from Python source."""
    try:
        rec = _strategies.register(req.name, req.source, req.description)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"success": True, "strategy": rec.to_dict()}


@app.post("/api/backtest")
def backtest(req: BacktestRequest):
    """Run a backtest. strategy may be a name or {name, source} dict."""
    try:
        return run_backtest(
            symbol=req.symbol,
            strategy_ref=req.strategy,
            params=req.params,
            freq=req.freq,
            start=req.start,
            end=req.end,
            adjust=req.adjust,
            initial_cash=req.initial_cash,
            commission_rate=req.commission_rate,
            stamp_duty=req.stamp_duty,
            lot_size=req.lot_size,
            strategy_manager=_strategies,
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/optimize")
def optimize(req: OptimizeRequest):
    """Grid-search strategy params, ranked by a chosen metric. Agent iteration endpoint."""
    try:
        return run_optimize(
            symbol=req.symbol,
            strategy_ref=req.strategy,
            param_grid=req.param_grid,
            metric=req.metric,
            freq=req.freq,
            start=req.start,
            end=req.end,
            adjust=req.adjust,
            initial_cash=req.initial_cash,
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/api/meta")
def meta():
    """System metadata: frequencies, commission defaults, symbol types."""
    return {
        "frequencies": ["daily", "1", "5", "15", "30", "60"],
        "symbol_types": ["stock", "fund", "etf"],
        "adjustments": ["qfq", "hfq", "none"],
        "default_commission_rate": 0.0003,
        "default_stamp_duty": 0.0005,
        "default_lot_size": 100,
        "metrics_available": ["total_return", "annual_return", "max_drawdown", "volatility", "sharpe", "win_rate", "n_trades"],
    }


@app.post("/api/data/precache")
def precache_submit(body: dict):
    symbols = body.get("symbols") or []
    if not symbols:
        raise HTTPException(status_code=400, detail="symbols required")
    if not isinstance(symbols, list):
        raise HTTPException(status_code=400, detail="symbols must be a list")
    job_ids = precache_manager.submit(
        symbols=symbols,
        freq=body.get("freq", "daily"),
        start=body.get("start", "2020-01-01"),
        end=body.get("end", "2024-12-31"),
        adjust=body.get("adjust", "qfq"),
    )
    return {"job_ids": job_ids}


@app.get("/api/data/precache/jobs")
def precache_jobs():
    return {"jobs": precache_manager.list()}


@app.get("/api/data/precache/{job_id}")
def precache_job(job_id: int):
    job = precache_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return {"job": job}


@app.post("/api/data/precache/refresh")
def precache_refresh():
    precache_manager.refresh_all()
    return {"started": True}


from .agent.api import register_agent_routes

register_agent_routes(app)


# ---- chart data endpoints (agent-friendly, same data as web charts) ----
@app.post("/api/chart/equity")
def chart_equity(req: BacktestRequest):
    """ECharts option for the equity curve — computed from the same run as /api/backtest."""
    res = backtest(req)
    return {"option": _equity_option(res)}


def _equity_option(res: dict) -> dict:
    """Build the equity-curve ECharts option directly from a backtest response dict."""
    eq = res["equity_curve"]
    dates = [r["date"] for r in eq]
    initial = res["metrics"].get("initial_equity", 1) or 1
    equity_norm = [(r["equity"] / initial - 1) * 100 for r in eq]
    bench_init = eq[0]["benchmark"] if eq else 1
    bench_norm = [(r["benchmark"] / bench_init - 1) * 100 for r in eq]
    return {
        "title": {"text": "权益曲线 vs 基准", "left": "center"},
        "tooltip": {"trigger": "axis"},
        "legend": {"data": ["策略", "基准"], "bottom": 0},
        "xAxis": {"type": "category", "data": dates},
        "yAxis": {"type": "value", "name": "收益率 %", "axisLabel": {"formatter": "{value}%"}},
        "series": [
            {"name": "策略", "type": "line", "data": [round(v, 3) for v in equity_norm], "smooth": True, "symbol": "none"},
            {"name": "基准", "type": "line", "data": [round(v, 3) for v in bench_norm], "smooth": True,
             "symbol": "none", "lineStyle": {"type": "dashed"}},
        ],
    }
