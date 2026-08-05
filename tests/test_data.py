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


def test_sina_source_forwards_adjust(monkeypatch):
    """SinaSource must forward hfq/qfq to akshare and not silently downgrade
    hfq to unadjusted data (the old 'qfq'/'1' alias stripped hfq -> None)."""
    import akshare as ak

    calls = []
    def fake_daily(symbol, start_date, end_date, adjust):
        calls.append((symbol, start_date, end_date, adjust))
        return pd.DataFrame({
            "date": ["2024-01-02"], "open": [10.0], "high": [11.0],
            "low": [9.0], "close": [10.5], "volume": [1000],
        })

    monkeypatch.setattr(ak, "stock_zh_a_daily", fake_daily)

    src = SinaSource()
    info = type("SI", (), {"type": "stock", "code": "600519", "exchange": "sh", "name": "x"})()

    for requested, expected in [("hfq", "hfq"), ("qfq", "qfq"), ("none", ""), ("", "")]:
        calls.clear()
        src.fetch_daily(info, "2024-01-01", "2024-01-05", requested)
        assert calls and calls[0][3] == expected, \
            f"adjust={requested!r} should reach akshare as {expected!r}, got {calls[0][3] if calls else None}"

    # the legacy "1" alias must not be treated as qfq
    calls.clear()
    src.fetch_daily(info, "2024-01-01", "2024-01-05", "1")
    assert calls and calls[0][3] == "", \
        f"legacy '1' should reach akshare as raw, got {calls[0][3] if calls else None}"


def test_fetch_with_factor(monkeypatch):
    import akshare as ak
    from data.sources import DataLayer, SymbolInfo

    none_df = pd.DataFrame({
        "date": ["2024-01-02", "2024-01-03", "2024-01-04"],
        "open": [10.0, 10.5, 9.0], "high": [11.0, 11.0, 9.5],
        "low": [9.8, 10.0, 8.8], "close": [10.5, 9.0, 9.2], "volume": [1000, 2000, 3000],
    })
    qfq_df = none_df.copy()
    qfq_df["close"] = [10.5 * 1.5, 9.0 * 1.5, 9.2 * 1.5]  # 1.5x factor

    calls = []
    def fake_hist(symbol, period, start_date, end_date, adjust):
        calls.append(adjust)
        return qfq_df if adjust == "qfq" else none_df

    monkeypatch.setattr(ak, "stock_zh_a_hist", fake_hist)
    dl = DataLayer(cache=False)
    info = SymbolInfo("600519", "茅台", "stock", "sh")
    df = dl._fetch_with_factor(info, "2024-01-01", "2024-01-31")
    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume", "factor"]
    assert calls == ["", "qfq"] or calls == ["qfq", ""]
    assert abs(df["factor"].iloc[0] - 1.5) < 1e-6
    assert abs(df["close"].iloc[0] - 10.5) < 1e-6  # close is RAW price


def test_get_bars_old_format_redownloads(monkeypatch, tmp_path):
    import akshare as ak
    from data.sources import DataLayer, SymbolInfo

    old_csv = tmp_path / "stock_600519_daily_qfq.csv"
    old_csv.write_text("date,open,high,low,close,volume\n2024-01-02,10,11,9,10.5,1000\n")

    def fake_hist(symbol, period, start_date, end_date, adjust):
        df = pd.DataFrame({
            "date": ["2024-01-02", "2024-01-03"],
            "open": [10.0, 11.0], "high": [11.0, 12.0], "low": [9.0, 10.0],
            "close": [10.5, 11.5], "volume": [1000, 2000],
        })
        return df if adjust == "qfq" else df

    monkeypatch.setattr(ak, "stock_zh_a_hist", fake_hist)
    dl = DataLayer(cache=True)
    dl.cache_dir = str(tmp_path)  # monkeypatch cache dir (self.cache_dir per Task 2)
    info = SymbolInfo("600519", "茅台", "stock", "sh")
    df = dl.get_bars(info, "daily", "2024-01-01", "2024-01-31", "qfq")
    assert "factor" in df.columns
    assert len(df) == 2


def test_get_bars_minute_cache_has_factor(monkeypatch, tmp_path):
    """Minute bars are fetched as before, but the cached CSV gets a factor=1
    column so the new-format detection keeps the minute cache hit path working."""
    from data.sources import DataLayer, SymbolInfo

    def fake_minute(symbol, period, start_date, end_date, adjust="qfq"):
        return pd.DataFrame({
            "date": ["2024-01-02", "2024-01-03"],
            "open": [10.0, 11.0], "high": [11.0, 12.0], "low": [9.0, 10.0],
            "close": [10.5, 11.5], "volume": [1000, 2000],
        })

    class FakeMinuteSource:
        name = "fake_minute"
        def supports(self, symbol):
            return True
        def fetch_minute(self, symbol, start, end, period="5"):
            return fake_minute(symbol, period, start, end)

    dl = DataLayer(cache=True)
    dl.cache_dir = str(tmp_path)
    dl.sources = [FakeMinuteSource()]
    info = SymbolInfo("600519", "茅台", "stock", "sh")

    df = dl.get_bars(info, "5", "2024-01-01", "2024-01-31", "qfq")
    assert "factor" in df.columns

    # cache written with factor=1; a second call hits the cache (no re-fetch)
    calls = {"n": 0}
    class FakeMinuteSource2(FakeMinuteSource):
        def fetch_minute(self, symbol, start, end, period="5"):
            calls["n"] += 1
            return fake_minute(symbol, period, start, end)
    dl.sources = [FakeMinuteSource2()]
    df2 = dl.get_bars(info, "5", "2024-01-01", "2024-01-31", "qfq")
    assert calls["n"] == 0
    assert list(df2.columns) == ["date", "open", "high", "low", "close", "volume", "factor"]


def test_get_bars_daily_failover_cache_old_format(monkeypatch, tmp_path):
    """A daily failover write must NOT annotate factor=1: the cache stays old
    format (no factor column) so a later get_bars re-downloads through the
    factor path instead of serving qfq-adjusted prices as if they were raw."""
    import akshare as ak
    from data.sources import DataLayer, SymbolInfo

    # factor-fetch path fails -> failover loop runs -> sources are tried
    def fake_hist(*args, **kwargs):
        raise RuntimeError("simulate factor-fetch failure")

    monkeypatch.setattr(ak, "stock_zh_a_hist", fake_hist)

    calls = {"n": 0}
    class FakeDailySource:
        name = "fake_daily"
        def supports(self, symbol):
            return True
        def fetch_daily(self, symbol, start, end, adjust="qfq"):
            calls["n"] += 1
            return pd.DataFrame({
                "date": ["2024-01-02", "2024-01-03"],
                "open": [10.0, 11.0], "high": [11.0, 12.0], "low": [9.0, 10.0],
                "close": [10.5, 11.5], "volume": [1000, 2000],
            })

    dl = DataLayer(cache=True)
    dl.cache_dir = str(tmp_path)
    dl.sources = [FakeDailySource()]
    info = SymbolInfo("600519", "茅台", "stock", "sh")

    df = dl.get_bars(info, "daily", "2024-01-01", "2024-01-31", "qfq")
    assert calls["n"] == 1
    assert "factor" not in df.columns

    # cached CSV must stay old format (no factor), so the next call re-downloads
    cache_path = dl._cache_path(info, "daily", "qfq")
    cached = pd.read_csv(cache_path)
    assert "factor" not in cached.columns

    # second call re-downloads rather than serving the stale qfq cache
    df2 = dl.get_bars(info, "daily", "2024-01-01", "2024-01-31", "qfq")
    assert calls["n"] == 2
    assert "factor" not in df2.columns
