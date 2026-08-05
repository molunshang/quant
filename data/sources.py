"""Data source abstraction and implementations.

DataLayer provides a single interface to fetch OHLCV bars for any tradable
symbol (stock / fund / ETF) at any frequency (daily / minute). Multiple free
sources are tried in order (eastmoney -> sina) with CSV caching to avoid
re-fetching.
"""
from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "cache")

# Bars must use these column names downstream.
OHLCV_COLS = ["open", "high", "low", "close", "volume"]

# ---- normalize akshare's Chinese column names to OHLCV_COLS ----
_COL_MAP = {
    "日期": "date",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "成交额": "amount",
    "date": "date",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "volume": "volume",
    "amount": "amount",
    "factor": "factor",
}


@dataclass
class SymbolInfo:
    """Metadata for a tradable symbol."""

    code: str  # e.g. "600519"
    name: str  # e.g. "贵州茅台"
    type: str  # "stock" | "fund" | "etf"
    exchange: str  # "sh" | "sz" | "bj" (funds may be "of")


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Rename columns to OHLCV standard, sort by date, reset index.

    A `factor` column is preserved when present in the input (not added when
    absent), so downstream code can rely on the factor's presence to decide
    whether a cache holds the new format.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=OHLCV_COLS + ["date"])
    df = df.rename(columns=_COL_MAP)
    keep = ["date"] + OHLCV_COLS
    if "factor" in df.columns:
        keep = keep + ["factor"]
    for c in keep:
        if c not in df.columns:
            df[c] = None
    df = df[keep]
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df = df.dropna(subset=["close"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


class DataSource(ABC):
    """Base class for a free market-data provider."""

    name = "base"

    @abstractmethod
    def fetch_daily(self, symbol: SymbolInfo, start: str, end: str, adjust: str) -> pd.DataFrame:
        """Return daily OHLCV bars for `symbol` between start..end (inclusive)."""

    @abstractmethod
    def fetch_minute(self, symbol: SymbolInfo, start: str, end: str, period: str) -> pd.DataFrame:
        """Return minute OHLCV bars for `symbol`. period in {"1","5","15","30","60"}."""

    def supports(self, symbol: SymbolInfo) -> bool:
        return True


class EastMoneySource(DataSource):
    """Primary source. Rich coverage incl. ETFs and funds. Needs network to eastmoney."""

    name = "eastmoney"

    def fetch_daily(self, symbol: SymbolInfo, start: str, end: str, adjust: str = "qfq") -> pd.DataFrame:
        import akshare as ak

        df = ak.stock_zh_a_hist(
            symbol=symbol.code, period="daily",
            start_date=start.replace("-", ""), end_date=end.replace("-", ""),
            adjust=adjust,
        )
        return normalize(df)

    def fetch_minute(self, symbol: SymbolInfo, start: str, end: str, period: str = "5") -> pd.DataFrame:
        import akshare as ak

        period_map = {"1": "1", "5": "5", "15": "15", "30": "30", "60": "60"}
        df = ak.stock_zh_a_hist_min_em(
            symbol=symbol.code, period=period_map.get(period, "5"),
            start_date=start.replace("-", ""), end_date=end.replace("-", ""),
            adjust="qfq",
        )
        return normalize(df)


class FundSource(DataSource):
    """Open-end fund (场外基金) source. Funds only publish daily NAV, so OHLC
    all equal NAV and volume is 0. No minute data."""

    name = "fund"

    def supports(self, symbol: SymbolInfo) -> bool:
        return symbol.type == "fund"

    def fetch_daily(self, symbol: SymbolInfo, start: str, end: str, adjust: str = "qfq") -> pd.DataFrame:
        import akshare as ak

        df = ak.fund_open_fund_info_em(symbol=symbol.code, indicator="单位净值走势")
        df = df.rename(columns={"净值日期": "date", "单位净值": "close"})
        df["open"] = df["high"] = df["low"] = df["close"]
        df["volume"] = 0
        return normalize(df)

    def fetch_minute(self, symbol: SymbolInfo, start: str, end: str, period: str = "5") -> pd.DataFrame:
        raise NotImplementedError("fund source has no minute data")


class SinaSource(DataSource):
    """Fallback source. Good for A-share stocks + index ETFs. No minute data."""

    name = "sina"

    def _prefixed(self, symbol: SymbolInfo) -> str:
        return f"{symbol.exchange}{symbol.code}"

    def fetch_daily(self, symbol: SymbolInfo, start: str, end: str, adjust: str = "qfq") -> pd.DataFrame:
        import akshare as ak

        adj = {"qfq": "qfq", "hfq": "hfq", "none": ""}.get(adjust, "")
        # ETFs & indices use the index-daily endpoint; stocks use stock-daily.
        if symbol.type in ("etf", "index"):
            df = ak.stock_zh_index_daily(symbol=self._prefixed(symbol))
            # returns full history; normalize() converts date to string and sorts.
            # Date-range filtering happens in DataLayer.get_bars cache path.
            return normalize(df)
        df = ak.stock_zh_a_daily(
            symbol=self._prefixed(symbol),
            start_date=start, end_date=end, adjust=adj,
        )
        return normalize(df)

    def fetch_minute(self, symbol: SymbolInfo, start: str, end: str, period: str = "5") -> pd.DataFrame:
        raise NotImplementedError("sina source has no minute data")


class DataLayer:
    """Orchestrates sources with cache + failover.

    Usage:
        dl = DataLayer()
        bars = dl.get_bars(SymbolInfo("600519", "贵州茅台", "stock", "sh"), "daily", "2024-01-01", "2024-12-31", "qfq")
    """

    def __init__(self, cache: bool = True):
        self.sources: list[DataSource] = [EastMoneySource(), FundSource(), SinaSource()]
        self.cache = cache
        os.makedirs(CACHE_DIR, exist_ok=True)

    def _cache_path(self, symbol: SymbolInfo, freq: str, adjust: str) -> str:
        key = f"{symbol.type}_{symbol.code}_{freq}_{adjust}.csv"
        return os.path.join(CACHE_DIR, key)

    def _fetch_with_factor(self, symbol, start, end, adjust="qfq") -> pd.DataFrame:
        """Fetch raw + qfq prices for the SAME dates, derive per-share cumulative
        adjustment factor = qfq_close / raw_close. Returns columns
        [date, open, high, low, close, volume, factor] where close is RAW."""
        import akshare as ak

        if adjust == "none":
            return self._fetch_raw(symbol, start, end)
        if adjust == "hfq":
            adjust = "qfq"  # deprecated: map to qfq behavior

        raw = ak.stock_zh_a_hist(
            symbol=symbol.code, period="daily",
            start_date=start.replace("-", ""), end_date=end.replace("-", ""),
            adjust="",
        )
        qfq = ak.stock_zh_a_hist(
            symbol=symbol.code, period="daily",
            start_date=start.replace("-", ""), end_date=end.replace("-", ""),
            adjust="qfq",
        )
        raw = normalize(raw).set_index("date")
        qfq = normalize(qfq).set_index("date")
        joined = raw.join(qfq["close"], rsuffix="_qfq")
        joined["factor"] = joined["close_qfq"] / joined["close"]
        joined.loc[joined["close"] <= 0, "factor"] = None
        joined["factor"] = joined["factor"].ffill()
        joined = joined.dropna(subset=["factor"]).reset_index()
        return joined[["date", "open", "high", "low", "close", "volume", "factor"]]

    def _fetch_raw(self, symbol, start, end) -> pd.DataFrame:
        df = EastMoneySource().fetch_daily(symbol, start, end, adjust="")
        df["factor"] = 1.0
        return df[["date", "open", "high", "low", "close", "volume", "factor"]]

    def get_bars(
        self,
        symbol: SymbolInfo,
        freq: str = "daily",
        start: str = "2020-01-01",
        end: str = "2024-12-31",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        """Fetch bars, using cache when possible, failing over across sources."""
        cache_path = self._cache_path(symbol, freq, adjust)
        if self.cache and os.path.exists(cache_path):
            df = pd.read_csv(cache_path)
            if not df.empty:
                df["date"] = df["date"].astype(str)
                return df[(df["date"] >= start) & (df["date"] <= end)].reset_index(drop=True)

        errors = []
        for src in self.sources:
            if not src.supports(symbol):
                continue
            try:
                if freq == "daily":
                    df = src.fetch_daily(symbol, start, end, adjust)
                else:
                    df = src.fetch_minute(symbol, start, end, freq)
                if df is not None and not df.empty:
                    # apply start/end filter on fresh data too (sources may return full history)
                    df = df[(df["date"] >= start) & (df["date"] <= end)].reset_index(drop=True)
                    if self.cache and not df.empty:
                        df.to_csv(cache_path, index=False)
                    return df
            except Exception as e:  # noqa: BLE001 - failover is the intent
                errors.append(f"{src.name}: {type(e).__name__}: {e}")
                time.sleep(0.3)
        raise RuntimeError(f"No data source available for {symbol.code}: {'; '.join(errors)}")
