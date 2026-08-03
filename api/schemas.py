"""Pydantic request/response schemas for the API."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class BacktestRequest(BaseModel):
    symbol: str = Field(..., description="代码，如 600519 / 510300 / 161725")
    freq: str = Field("daily", description="bar 频率: daily|1|5|15|30|60")
    start: str = Field("2020-01-01", description="起始日期 YYYY-MM-DD")
    end: str = Field("2024-12-31", description="结束日期 YYYY-MM-DD")
    adjust: str = Field("qfq", description="复权: qfq|hfq|none")
    strategy: Any = Field(..., description="策略名或 {'name','source'} 源码字典")
    params: dict = Field(default_factory=dict, description="策略参数")
    initial_cash: float = Field(100_000.0, ge=1000)
    commission_rate: float = Field(0.0003)
    stamp_duty: float = Field(0.0005)
    lot_size: int = Field(100)


class RegisterStrategyRequest(BaseModel):
    name: str = Field(..., description="策略名")
    source: str = Field(..., description="Python 源码，定义 strategy(ctx, params)")
    description: str = Field("")


class OptimizeRequest(BaseModel):
    symbol: str
    freq: str = "daily"
    start: str = "2020-01-01"
    end: str = "2024-12-31"
    adjust: str = "qfq"
    strategy: Any
    param_grid: dict[str, list[Any]] = Field(default_factory=dict, description="参数网格")
    metric: str = Field("sharpe", description="优化目标指标")
    initial_cash: float = 100_000.0


class SymbolResponse(BaseModel):
    code: str
    name: str
    type: str
    exchange: str


class BacktestResponse(BaseModel):
    success: bool
    symbol: str
    freq: str
    metrics: dict
    equity_curve: list[dict]
    trades: list[dict]
    strategy: str
    params: dict
