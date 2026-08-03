"""Strategy interface.

A strategy is a plain Python function `strategy(ctx, params)` that reads
indicators from `ctx` and issues orders via `ctx.buy()` / `ctx.sell()`.
Built-in strategies live in this package; user-submitted strategies are
loaded by the API and validated before running.
"""
from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass
from typing import Callable

from engine.context import Context

StrategyFunc = Callable[[Context, dict], None]


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

    Allowed: standard lib + math. No imports from engine/data/api (strategy
    should only use ctx). Enforced by AST allowlist — not a security boundary,
    just a guardrail for the local agent.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            raise ValueError("strategy may not import modules; use ctx helpers only")
        if isinstance(node, ast.ImportFrom):
            raise ValueError("strategy may not import modules; use ctx helpers only")
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
    """
    validate_strategy_source(source)
    ns: dict = {}
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
