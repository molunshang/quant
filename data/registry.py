"""Symbol registry for A-share stocks, funds and ETFs.

The full akshare symbol lists can take ~1min+ to fetch on flaky networks, so
we cache the built registry to a JSON file. On the first run (or if the cache
is missing/stale), we fetch best-effort and merge with curated defaults so the
system is always usable.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

from .sources import SymbolInfo

REGISTRY_DIR = os.path.join(os.path.dirname(__file__), "cache")
REGISTRY_FILE = os.path.join(REGISTRY_DIR, "symbol_registry.json")

# Well-known symbols always available even if list fetch fails.
DEFAULTS: list[SymbolInfo] = [
    SymbolInfo("600519", "贵州茅台", "stock", "sh"),
    SymbolInfo("000001", "平安银行", "stock", "sz"),
    SymbolInfo("601318", "中国平安", "stock", "sh"),
    SymbolInfo("300750", "宁德时代", "stock", "sz"),
    SymbolInfo("510300", "沪深300ETF", "etf", "sh"),
    SymbolInfo("510500", "中证500ETF", "etf", "sh"),
    SymbolInfo("159915", "创业板ETF", "etf", "sz"),
    SymbolInfo("512880", "证券ETF", "etf", "sh"),
    SymbolInfo("161725", "招商中证白酒", "fund", "of"),
    SymbolInfo("110022", "易方达消费行业", "fund", "of"),
    SymbolInfo("006327", "易方达中证海外50", "fund", "of"),
    SymbolInfo("005827", "易方达蓝筹精选", "fund", "of"),
]


@dataclass
class Registry:
    symbols: dict[str, SymbolInfo] = field(default_factory=dict)

    def _load_stock_list(self, timeout_s: float = 60.0) -> dict[str, SymbolInfo]:
        import akshare as ak

        out: dict[str, SymbolInfo] = {}
        df = _with_timeout(ak.stock_info_a_code_name, timeout_s)
        for _, r in df.iterrows():
            code = str(r["code"])
            name = str(r["name"])
            out[code] = SymbolInfo(code=code, name=name, type="stock", exchange=_exchange(code))
        return out

    def _load_fund_list(self, timeout_s: float = 60.0) -> dict[str, SymbolInfo]:
        import akshare as ak

        out: dict[str, SymbolInfo] = {}
        df = _with_timeout(lambda: ak.fund_open_fund_rank_em(symbol="全部"), timeout_s)
        for _, r in df.iterrows():
            code = str(r["基金代码"])
            name = str(r["基金简称"])
            out[code] = SymbolInfo(code=code, name=name, type="fund", exchange="of")
        return out

    def _load_etf_list(self, timeout_s: float = 60.0) -> dict[str, SymbolInfo]:
        import akshare as ak

        out: dict[str, SymbolInfo] = {}
        df = _with_timeout(ak.fund_etf_spot_em, timeout_s)
        for _, r in df.iterrows():
            code = str(r["代码"])
            name = str(r["名称"])
            out[code] = SymbolInfo(code=code, name=name, type="etf", exchange=_exchange(code))
        return out

    def build(self) -> "Registry":
        """Load cached registry, or fetch + cache if unavailable."""
        # Try cached first (fast path).
        loaded = _load_from_file()
        if loaded is not None:
            self.symbols.update(loaded)
            return self

        # Fetch with per-list timeouts, merging defaults so we never end up empty.
        for loader in (self._load_stock_list, self._load_fund_list, self._load_etf_list):
            try:
                self.symbols.update(loader(timeout_s=45.0))
            except Exception:  # noqa: BLE001 - best effort
                pass
        for d in DEFAULTS:
            self.symbols.setdefault(d.code, d)
        _save_to_file(self.symbols)
        return self

    def get(self, code: str) -> SymbolInfo:
        info = self.symbols.get(code)
        if info is None:
            info = SymbolInfo(code=code, name=code, type=_guess_type(code), exchange=_exchange(code))
            self.symbols[code] = info
        return info

    def list(self, typ: str | None = None) -> list[SymbolInfo]:
        items = [s for s in self.symbols.values() if typ is None or s.type == typ]
        items.sort(key=lambda s: s.code)
        return items


def _with_timeout(fn, timeout_s: float):
    """Call fn with a hard wall-clock timeout (runs fn in a thread)."""
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(fn)
        try:
            return fut.result(timeout=timeout_s)
        except concurrent.futures.TimeoutError as e:
            raise TimeoutError(f"timed out after {timeout_s:.0f}s") from e


def _exchange(code: str) -> str:
    if code.startswith("6"):
        return "sh"
    if code.startswith(("0", "3")):
        return "sz"
    if code.startswith(("4", "8")):
        return "bj"
    return "sh"


def _guess_type(code: str) -> str:
    """Best-effort type guess for an unregistered code."""
    if code.startswith(("5", "15", "56", "58")):
        return "etf"
    if code.startswith(("16", "17")):
        return "fund"
    if code.startswith(("6", "0", "3", "4", "8", "68")):
        return "stock"
    return "fund"


def _save_to_file(symbols: dict[str, SymbolInfo]) -> None:
    try:
        os.makedirs(REGISTRY_DIR, exist_ok=True)
        data = {c: {"code": s.code, "name": s.name, "type": s.type, "exchange": s.exchange}
                for c, s in symbols.items()}
        with open(REGISTRY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass


def _load_from_file() -> dict[str, SymbolInfo] | None:
    if not os.path.exists(REGISTRY_FILE):
        return None
    try:
        with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {c: SymbolInfo(**s) for c, s in data.items()}
    except Exception:  # noqa: BLE001
        return None


_registry: Registry | None = None


def get_registry() -> Registry:
    global _registry
    if _registry is None:
        _registry = Registry().build()
    return _registry
