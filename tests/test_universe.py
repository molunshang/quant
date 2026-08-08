"""Universe resolution & calendar alignment tests (no network)."""
from __future__ import annotations

import pandas as pd
import pytest

from engine.universe import resolve_universe, cached_symbols, metadata_calendar


class _FakeDL:
    def __init__(self, cache_dir=None, bars_by_symbol=None):
        self._bars = bars_by_symbol or {}
        self.requested = []
    def get_bars(self, info, freq="daily", start="", end="", adjust="qfq"):
        self.requested.append(info.code)
        return self._bars[info.code]


def test_cached_symbols_parses_filenames(tmp_path, monkeypatch):
    for fn in ("stock_600519_daily_qfq.csv", "etf_510300_daily_qfq.csv",
               "fund_161725_daily_qfq.csv", "stock_000858_daily_qfq.csv"):
        (tmp_path / fn).write_text("date,open,high,low,close,volume\n")
    monkeypatch.setattr("engine.universe.CACHE_DIR", str(tmp_path))
    syms = cached_symbols("daily", "qfq")
    assert set(syms) == {"600519", "510300", "161725", "000858"}
    assert cached_symbols("daily", "qfq", types=["etf"]) == ["510300"]


def test_resolve_explicit_symbols():
    assert resolve_universe({"symbols": ["600519", "000858"]}, "daily", "qfq") == ["600519", "000858"]


def test_resolve_default_is_cached(tmp_path, monkeypatch):
    for fn in ("stock_600519_daily_qfq.csv", "etf_510300_daily_qfq.csv"):
        (tmp_path / fn).write_text("date,open,high,low,close,volume\n")
    monkeypatch.setattr("engine.universe.CACHE_DIR", str(tmp_path))
    assert set(resolve_universe(None, "daily", "qfq")) == {"600519", "510300"}
    assert resolve_universe({"types": ["etf"]}, "daily", "qfq") == ["510300"]


def test_metadata_calendar_reads_cache_dates(tmp_path, monkeypatch):
    # metadata-only: reads date column from cache CSV, does NOT call data_layer
    monkeypatch.setattr("engine.universe.CACHE_DIR", str(tmp_path))
    a_dates = "2023-01-02,2023-01-03,2023-01-04"
    b_dates = "2023-01-03,2023-01-04,2023-01-05"
    (tmp_path / "stock_600519_daily_qfq.csv").write_text(
        "date,open,high,low,close,volume\n" + "\n".join(
            f"{d},1,1,1,1,1" for d in a_dates.split(",")))
    (tmp_path / "stock_000858_daily_qfq.csv").write_text(
        "date,open,high,low,close,volume\n" + "\n".join(
            f"{d},1,1,1,1,1" for d in b_dates.split(",")))
    dl = _FakeDL()  # get_bars would raise if called — proves metadata-only path
    cal = metadata_calendar(["600519", "000858"], "2023-01-01", "2023-12-31", "daily", "qfq", dl)
    assert cal == ["2023-01-02", "2023-01-03", "2023-01-04", "2023-01-05"]
    assert dl.requested == []  # never pulled full bars
