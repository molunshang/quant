"""Strategy interface.

A portfolio strategy is `initialize(ctx)` (optional) + `handle_data(ctx)`
(required). `handle_data` runs once per time step (daily in this release);
`ctx` is portfolio-aware (see engine.context). Built-in strategies live in
this package; user-submitted strategies are loaded and validated before run.
"""
from __future__ import annotations

import ast
import inspect
import math
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from engine.context import Context
from .indicators import ema, macd, rsi, sma

# Indicator helpers injected into every user strategy namespace.
INDICATOR_HELPERS = {"sma": sma, "ema": ema, "rsi": rsi, "macd": macd}

StrategyFunc = Callable[[Context], None]

_ALLOWED_IMPORTS = {"math", "numpy", "pandas"}


@dataclass
class StrategySpec:
    name: str
    description: str
    func: StrategyFunc | None  # None for user-defined (loaded from source)
    source: str | None = None
    builtin: bool = False


def validate_strategy_source(source: str) -> None:
    """Statically check a user strategy source before it runs.

    Allowed imports: math, numpy, pandas. No imports from engine/data/api
    (strategy should only use ctx). Enforced by AST allowlist — not a
    security boundary, just a guardrail for the local agent.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods = {a.name.split(".")[0] for a in node.names}
            if mods - _ALLOWED_IMPORTS:
                raise ValueError("strategy may import only math, numpy, pandas")
            continue
        if isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root not in _ALLOWED_IMPORTS:
                raise ValueError("strategy may import only math, numpy, pandas")
            continue
        if isinstance(node, ast.Global) or isinstance(node, ast.Nonlocal):
            raise ValueError("global/nonlocal not allowed")
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in ("eval", "compile", "__import__", "exec"):
                raise ValueError(f"`{node.func.id}` is not allowed")
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                raise ValueError("access to private members (ctx._*) is not allowed")


def load_strategy_from_source(source: str, name: str = "user_strategy") -> StrategyFunc:
    """Compile a strategy source into `handle_data`, with optional `initialize`
    attached as `__initialize__`.

    The source must define `handle_data(ctx)`; `initialize(ctx)` is optional.
    Indicator helpers (sma/ema/rsi/macd) are injected.
    """
    validate_strategy_source(source)
    ns: dict = {"math": math, "np": np, "pandas": pd, **INDICATOR_HELPERS}
    exec(compile(source, f"<strategy:{name}>", "exec"), ns)
    func = ns.get("handle_data")
    if func is None:
        raise ValueError("strategy source must define a function named `handle_data(ctx)`")
    if not callable(func):
        raise ValueError("`handle_data` must be callable")
    sig = inspect.signature(func)
    if len(sig.parameters) < 1:
        raise ValueError("`handle_data` must accept (ctx)")
    init = ns.get("initialize")
    if init is not None:
        if not callable(init):
            raise ValueError("`initialize` must be callable")
        setattr(func, "__initialize__", init)
    return func
