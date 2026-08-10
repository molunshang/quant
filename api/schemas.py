"""Pydantic request/response schemas for the API."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class UniverseSpec(BaseModel):
    symbols: list[str] | None = Field(None, description="显式标的列表")
    types: list[str] | None = Field(None, description="类型过滤: stock|etf|fund")


class BacktestRequest(BaseModel):
    universe: UniverseSpec | None = Field(None, description="标的池（缺省=已缓存标的集）")
    freq: str = Field("daily", description="bar 频率: daily|1|5|15|30|60")
    start: str = Field("2020-01-01", description="起始日期 YYYY-MM-DD")
    end: str = Field("2024-12-31", description="结束日期 YYYY-MM-DD")
    adjust: str = Field("qfq", description="复权: qfq|hfq|none")
    strategy: Any = Field(..., description="策略名或 {'name','source'} 源码字典")
    initial_cash: float = Field(100_000.0, ge=1000)
    commission_rate: float = Field(0.0003)
    stamp_duty: float = Field(0.0005)
    lot_size: int = Field(100)


class RegisterStrategyRequest(BaseModel):
    name: str = Field(..., description="策略名")
    source: str = Field(..., description="Python 源码，定义 initialize(ctx)（可选）+ handle_data(ctx)")
    description: str = Field("")


class SymbolResponse(BaseModel):
    code: str
    name: str
    type: str
    exchange: str
