"""Strategy registry. Tracks built-in + user-registered strategies and
resolves a backtest request's `strategy` reference to a callable."""
from __future__ import annotations

from dataclasses import dataclass, field

from .base import StrategyFunc, load_strategy_from_source
from .builtin import BUILTIN_STRATEGIES


@dataclass
class StrategyRecord:
    name: str
    source: str | None
    description: str = ""
    builtin: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "builtin": self.builtin,
        }


class StrategyManager:
    def __init__(self):
        self._builtins: dict[str, StrategyRecord] = {}
        for key, meta in BUILTIN_STRATEGIES.items():
            self._builtins[key] = StrategyRecord(
                name=key,
                source=meta.get("source"),
                description=meta.get("description", ""),
                builtin=True,
            )
        self._user: dict[str, StrategyRecord] = {}

    # ---- resolution ----
    def get_func(self, name: str) -> StrategyFunc:
        """Return the callable for a built-in or registered user strategy."""
        if name in self._builtins:
            return BUILTIN_STRATEGIES[name]["func"]
        if name in self._user:
            rec = self._user[name]
            return load_strategy_from_source(rec.source, rec.name)
        raise KeyError(f"unknown strategy: {name}")

    def resolve(self, strategy: dict | str) -> tuple[StrategyFunc, str]:
        """Resolve a strategy reference from a backtest request.

        `strategy` may be:
          - a string name (built-in or previously registered user strategy)
          - a dict {"name": ..., "source": "def handle_data(ctx): ..."}
        Returns (func, name).
        """
        if isinstance(strategy, str):
            return self.get_func(strategy), strategy
        if isinstance(strategy, dict):
            name = strategy.get("name", "user_strategy")
            source = strategy.get("source")
            if source:
                self.register(name, source, strategy.get("description", ""))
                return self.get_func(name), name
            if name:
                return self.get_func(name), name
        raise ValueError("strategy must be a name string or dict with 'source'")

    # ---- management ----
    def register(self, name: str, source: str, description: str = "") -> StrategyRecord:
        """Register (or overwrite) a user strategy. Validates source."""
        load_strategy_from_source(source, name)  # raises if invalid
        rec = StrategyRecord(name=name, source=source, description=description, builtin=False)
        self._user[name] = rec
        return rec

    def list(self) -> list[dict]:
        out = [r.to_dict() for r in self._builtins.values()]
        out += [r.to_dict() for r in self._user.values()]
        return out

    def get(self, name: str) -> StrategyRecord | None:
        return self._builtins.get(name) or self._user.get(name)
