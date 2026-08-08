"""Universe resolution and metadata calendar alignment.

A backtest runs over a *universe* of candidate symbols. The strategy picks
which to trade; the engine only loads bars for symbols the strategy actually
uses (lazy). The unified calendar (union of all symbol trading dates) is
aligned from lightweight metadata before any full bars are pulled.
"""
from __future__ import annotations

import os
import re

from data.sources import CACHE_DIR


def cached_symbols(freq: str = "daily", adjust: str = "qfq",
                   types: list[str] | None = None) -> list[str]:
    """List symbols that have a local cache file for the given freq/adjust.

    Filename pattern: {type}_{code}_{freq}_{adjust}.csv
    """
    out: list[str] = []
    if not os.path.isdir(CACHE_DIR):
        return out
    for fn in sorted(os.listdir(CACHE_DIR)):
        if not fn.endswith(".csv"):
            continue
        parts = fn[:-4].split("_")
        if len(parts) != 4:
            continue
        typ, code, f, adj = parts
        if f != freq or adj != adjust:
            continue
        if types and typ not in types:
            continue
        out.append(code)
    return out


def resolve_universe(spec: dict | None, freq: str = "daily", adjust: str = "qfq") -> list[str]:
    """Resolve a request's universe into a symbol list.

    - spec with 'symbols' -> those symbols
    - spec with 'types' -> cached symbols of those types
    - spec None/empty -> all cached symbols
    """
    spec = spec or {}
    if spec.get("symbols"):
        return list(spec["symbols"])
    types = spec.get("types")
    return cached_symbols(freq=freq, adjust=adjust, types=types)


def _cache_path(symbol: str, freq: str, adjust: str) -> str:
    """Path of the local cache file for a symbol (matches DataLayer._cache_path)."""
    from data.registry import get_registry
    info = get_registry().get(symbol)
    return os.path.join(CACHE_DIR, f"{info.type}_{info.code}_{freq}_{adjust}.csv")


def metadata_calendar(symbols: list[str], start: str, end: str, freq: str,
                      adjust: str, data_layer) -> list[str]:
    """Union of all symbols' trading dates in [start, end], sorted.

    Reads ONLY the date column from each symbol's cache CSV (cheap header
    peek — no full bar load, honoring the metadata-only alignment). Symbols
    without a cache file fall back to a light get_bars call. Full bars for a
    symbol are never loaded here; that stays with the strategy's lazy use.
    """
    date_sets: list[set[str]] = []
    import pandas as pd
    for sym in symbols:
        path = _cache_path(sym, freq, adjust)
        if os.path.exists(path):
            try:
                dates = pd.read_csv(path, usecols=["date"], dtype={"date": str})["date"]
                date_sets.append({str(d) for d in dates if start <= str(d) <= end})
                continue
            except Exception:  # noqa: BLE001 - fall through to data_layer
                pass
        from data.registry import get_registry
        info = get_registry().get(sym)
        df = data_layer.get_bars(info, freq=freq, start=start, end=end, adjust=adjust)
        if df is None or df.empty:
            continue
        date_sets.append({str(d) for d in df["date"] if start <= str(d) <= end})
    if not date_sets:
        return []
    return sorted(set().union(*date_sets))
