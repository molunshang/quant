"""Tests for the precache service (data.precache)."""
from __future__ import annotations

import pandas as pd

from data.precache import PrecacheManager
from data.sources import DataLayer


def test_submit_and_list_jobs(monkeypatch, tmp_path):
    # stub DataLayer so no real network
    def fake_get_bars(self, *a, **k):
        return pd.DataFrame({"date": ["2024-01-02"], "open": [1.0], "high": [1.0],
                             "low": [1.0], "close": [1.0], "volume": [1], "factor": [1.0]})
    monkeypatch.setattr(DataLayer, "get_bars", fake_get_bars)

    mgr = PrecacheManager()
    mgr.cache_dir = str(tmp_path)
    ids = mgr.submit(["600519", "510300"], "daily", "2024-01-01", "2024-01-31", "qfq")
    assert len(ids) == 2
    jobs = mgr.list()
    assert len(jobs) == 2
    assert all(j["symbol"] in ("600519", "510300") for j in jobs)
    mgr.wait_all()
    assert all(j["status"] == "done" for j in mgr.list())


def test_refresh_all_submits_jobs_from_cache_files(monkeypatch, tmp_path):
    """refresh_all scans the cache dir, derives each symbol's start from its
    cached CSV's first date, and force re-downloads up to today."""
    (tmp_path / "stock_600519_daily_qfq.csv").write_text(
        "date,open,high,low,close,volume,factor\n2024-01-02,10,11,9,10.5,1000,1.5\n")
    (tmp_path / "etf_510300_daily_qfq.csv").write_text(
        "date,open,high,low,close,volume,factor\n2023-06-01,3.0,3.1,2.9,3.05,5000,1.0\n")

    def fake_get_bars(self, *a, **k):
        return pd.DataFrame({"date": ["2024-01-02"], "open": [1.0], "high": [1.0],
                             "low": [1.0], "close": [1.0], "volume": [1], "factor": [1.0]})
    monkeypatch.setattr(DataLayer, "get_bars", fake_get_bars)

    mgr = PrecacheManager(cache_dir=str(tmp_path))
    mgr.refresh_all()
    mgr.wait_all()

    jobs = mgr.list()
    assert {j["symbol"] for j in jobs} == {"600519", "510300"}
    assert all(j["freq"] == "daily" for j in jobs)
    assert all(j["adjust"] == "qfq" for j in jobs)
    starts = {j["symbol"]: j["start"] for j in jobs}
    assert starts["600519"] == "2024-01-02"
    assert starts["510300"] == "2023-06-01"


def test_job_error_captured(monkeypatch, tmp_path):
    def fake_get_bars(self, *a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(DataLayer, "get_bars", fake_get_bars)

    mgr = PrecacheManager(cache_dir=str(tmp_path))
    ids = mgr.submit(["600519"], "daily", "2024-01-01", "2024-01-31", "qfq")
    mgr.wait_all()
    j = mgr.get(ids[0])
    assert j["status"] == "error"
    assert "boom" in j["error"]
