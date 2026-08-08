"""Built-in portfolio strategies.

Each is `initialize(ctx)` (optional) + `handle_data(ctx)`, so users can copy
them into a custom strategy and tweak.
"""
from __future__ import annotations


def buy_and_hold(ctx) -> None:
    """Buy all universe symbols equal-weighted on the first bar; hold to the end."""
    if ctx.bar_index > 0:
        return
    n = len(ctx.universe)
    if n == 0:
        return
    for s in ctx.universe:
        ctx.buy(s, 1.0 / n)


def momentum_rotation(ctx) -> None:
    """Hold the top-N symbols by 60-day momentum; rebalance every 20 bars."""
    window = ctx.state.get("window", 60)
    top_n = ctx.state.get("top_n", 3)
    rebalance_every = ctx.state.get("rebalance_every", 20)
    if ctx.bar_index % rebalance_every != 0:
        return
    momentum = {}
    for s in ctx.universe:
        bars = ctx.history(s)
        if len(bars) < window:
            continue
        ret = bars["close"].iloc[-1] / bars["close"].iloc[-window] - 1
        momentum[s] = ret
    if not momentum:
        return
    top = sorted(momentum, key=momentum.get, reverse=True)[:top_n]
    for s in list(ctx.positions):
        if s not in top:
            ctx.sell(s)
    for s in top:
        if s not in ctx.positions:
            ctx.buy(s, 1.0 / len(top))


BUILTIN_STRATEGIES: dict[str, dict] = {
    "buy_and_hold": {
        "name": "buy_and_hold",
        "description": "买入持有：首日等权买入 universe 全部标的，一直持有",
        "func": buy_and_hold,
        "builtin": True,
    },
    "momentum_rotation": {
        "name": "momentum_rotation",
        "description": "动量轮动：选最近 60 日涨幅最高的 3 个标的持有，每 20 日轮换",
        "func": momentum_rotation,
        "builtin": True,
    },
}
