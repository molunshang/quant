"""SW (申万) industry classification, best-effort.

`list_industries` reads the SW first-level list (31 sectors). Per-industry
constituents (`industry_constituents`) depend on an endpoint that may be
unavailable on flaky networks — it degrades to [] and the agent falls back to
`list_symbols(type=...)`.
"""
from __future__ import annotations

import json
import os

INDUSTRY_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "cache")


def list_industries(force: bool = False) -> list[dict]:
    """SW first-level industries: [{code, name, n_stocks}]. Cached to JSON."""
    path = os.path.join(INDUSTRY_DIR, "sw_industries.json")
    if not force and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001 - stale cache -> refetch
            pass
    import akshare as ak

    df = ak.sw_index_first_info()
    out = [{"code": str(r["行业代码"]), "name": str(r["行业名称"]),
            "n_stocks": int(r["成份个数"])}
           for _, r in df.iterrows()]
    try:
        os.makedirs(INDUSTRY_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
    except Exception:  # noqa: BLE001 - cache write is best-effort
        pass
    return out


def industry_constituents(industry_code: str) -> list[dict]:
    """Constituent stocks of a SW industry: [{code, name}]. Best-effort —
    returns [] when the underlying endpoint is unavailable."""
    try:
        import akshare as ak

        df = ak.sw_index_third_cons(symbol=industry_code)
        if df is None or df.empty:
            return []
        return [{"code": str(r["股票代码"]), "name": str(r["股票简称"])}
                for _, r in df.iterrows()]
    except Exception:  # noqa: BLE001 - best-effort, degrade to []
        return []
