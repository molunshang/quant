"""Tests for data layer — normalization and symbol registry."""
from __future__ import annotations

import pandas as pd

from data.sources import DataLayer, EastMoneySource, SinaSource, normalize
from data.registry import _exchange, _guess_type


def test_normalize_akshare_columns():
    raw = pd.DataFrame({
        "日期": ["2024-01-02", "2024-01-03"],
        "开盘": [10, 11], "收盘": [11, 12], "最高": [11.5, 12.5],
        "最低": [9.5, 10.5], "成交量": [1000, 2000], "成交额": [10000, 20000],
    })
    out = normalize(raw)
    assert list(out.columns) == ["date", "open", "high", "low", "close", "volume"]
    assert out["date"].iloc[0] == "2024-01-02"
    assert out["close"].iloc[-1] == 12


def test_normalize_english_columns():
    raw = pd.DataFrame({
        "date": ["2024-01-02"], "open": [1.0], "high": [1.2], "low": [0.9],
        "close": [1.1], "volume": [100],
    })
    out = normalize(raw)
    assert out["close"].iloc[0] == 1.1


def test_normalize_sorts_and_drops_bad():
    raw = pd.DataFrame({
        "date": ["2024-01-03", "2024-01-02"],
        "open": [1, 1], "high": [2, 2], "low": [0, 0], "close": [1.5, None], "volume": [1, 1],
    })
    out = normalize(raw)
    # the row with None close is dropped first, leaving only 2024-01-03
    assert len(out) == 1
    assert out["date"].iloc[0] == "2024-01-03"


def test_exchange_and_type():
    assert _exchange("600519") == "sh"
    assert _exchange("000001") == "sz"
    assert _exchange("300750") == "sz"
    assert _guess_type("510300") == "etf"
    assert _guess_type("600519") == "stock"
    assert _guess_type("161725") == "fund"


def test_sources_present():
    dl = DataLayer()
    assert any(isinstance(s, EastMoneySource) for s in dl.sources)
    assert any(isinstance(s, SinaSource) for s in dl.sources)
