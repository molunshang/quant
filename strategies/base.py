"""Strategy interface.

A strategy is a plain Python function `strategy(ctx, params)` that reads
indicators from `ctx` and issues orders via `ctx.buy()` / `ctx.sell()`.
Built-in strategies live in this package; user-submitted strategies are
loaded by the API and validated before running.
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

# Indicator helpers injected into every user strategy namespace (see
# load_strategy_from_source). Kept out of module-level builtins so a strategy
# can still define its own `sma`, etc.
INDICATOR_HELPERS = {"sma": sma, "ema": ema, "rsi": rsi, "macd": macd}

StrategyFunc = Callable[[Context, dict], None]

_ALLOWED_IMPORTS = {"math", "numpy", "pandas"}


@dataclass
class StrategySpec:
    name: str
    description: str
    func: StrategyFunc | None  # None for user-defined (loaded from source)
    source: str | None = None  # Python source for user-defined strategies
    builtin: bool = False
    params_schema: dict | None = None  # optional param hints for UI/agent


def validate_strategy_source(source: str) -> None:
    """Statically check a user strategy source before it runs.

    Allowed imports: math, numpy, pandas (indicators like RSI/EMA still use
    ctx.bars_upto(), so strategies can reference them; built-in indicator
    helpers sma/ema/rsi/macd are pre-injected at load). No imports from
    engine/data/api (strategy should only use ctx). Enforced by AST allowlist —
    not a security boundary, just a guardrail for the local agent.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            # any form of `import x` — reject unless it's one of the allowlist
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
            # disallow direct engine internals via ctx._* access
            if node.attr.startswith("_"):
                raise ValueError("access to private members (ctx._*) is not allowed")


def load_strategy_from_source(source: str, name: str = "user_strategy") -> StrategyFunc:
    """Compile a strategy source string into a callable strategy function.

    The source must define a function named `strategy(ctx, params)`.
    Indicator helpers (sma/ema/rsi/macd) are injected into the namespace so
    strategies can call them without importing anything.
    """
    validate_strategy_source(source)
    ns: dict = {"math": math, "np": np, "pandas": pd, **INDICATOR_HELPERS}
    exec(compile(source, f"<strategy:{name}>", "exec"), ns)
    func = ns.get("strategy")
    if func is None:
        raise ValueError("strategy source must define a function named `strategy(ctx, params)`")
    if not callable(func):
        raise ValueError("`strategy` must be a callable")
    sig = inspect.signature(func)
    n_params = len(sig.parameters)
    if n_params < 2:
        raise ValueError("`strategy` must accept (ctx, params)")
    return func
