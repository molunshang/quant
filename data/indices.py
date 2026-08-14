"""Index constituents + cache. Uses the csindex (中证指数) endpoint.

Index names in goals ("沪深300成分") resolve to a canonical index code; the
agent expands an index to its constituent stock list via list_symbols(index=...).
"""
from __future__ import annotations

import json
import math
import os

INDEX_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "cache")

INDEX_NAMES: dict[str, str] = {
    "000300": "沪深300",
    "000905": "中证500",
    "000016": "上证50",
    "399006": "创业板指",
    "000852": "中证1000",
    "000922": "中证红利",
}

INDEX_ALIASES: dict[str, str] = {
    "沪深300": "000300",
    "沪深300成分": "000300",
    "中证500": "000905",
    "中证500成分": "000905",
    "上证50": "000016",
    "创业板指": "399006",
    "创业板": "399006",
    "中证1000": "000852",
    "中证红利": "000922",
}


def resolve_index(name_or_code: str) -> str | None:
    """Map an index name (沪深300) or code (000300) to a canonical code."""
    if name_or_code in INDEX_ALIASES:
        return INDEX_ALIASES[name_or_code]
    if name_or_code in INDEX_NAMES:
        return name_or_code
    return None


def list_indices() -> list[dict]:
    return [{"code": c, "name": n} for c, n in INDEX_NAMES.items()]


def index_constituents(code: str, force: bool = False) -> list[dict]:
    """Constituent stocks of an index: [{code, name, weight}]. Cached to JSON."""
    if code not in INDEX_NAMES:
        raise ValueError(f"unknown index: {code}")
    path = os.path.join(INDEX_DIR, f"index_{code}.json")
    if not force and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001 - stale/broken cache -> refetch
            pass
    import akshare as ak

    df = ak.index_stock_cons_weight_csindex(symbol=code)
    out = []
    for _, r in df.iterrows():
        try:
            w = float(r["权重"])
        except (TypeError, ValueError):
            w = 0.0
        if not math.isfinite(w):
            w = 0.0
        out.append({
            "code": str(r["成分券代码"]),
            "name": str(r["成分券名称"]),
            "weight": w,
        })
    try:
        os.makedirs(INDEX_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
    except Exception:  # noqa: BLE001 - cache write is best-effort
        pass
    return out
