"""Deep diagnosis module tests (pure pandas, no network)."""
from __future__ import annotations

import pandas as pd
import pytest

from engine.diagnose import diagnose


def _eq():
    return pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=40, freq="B").strftime("%Y-%m-%d"),
        "equity": [100.0] * 10 + [90.0] * 10 + [120.0] * 20,
        "benchmark": [100.0] * 40,
        "n_positions": [1] * 40,
        "max_concentration": [1.0] * 40,
    })


def _trades():
    return [
        {"date": "2025-01-02", "symbol": "600519", "side": "buy", "shares": 100,
         "price": 100.0, "amount": 10000.0, "commission": 5.0,
         "stamp_duty": 0.0, "transfer_fee": 0.0},
        {"date": "2025-02-10", "symbol": "600519", "side": "sell", "shares": 100,
         "price": 120.0, "amount": 12000.0, "commission": 5.0,
         "stamp_duty": 6.0, "transfer_fee": 0.1},
    ]


def test_diagnose_keys_present():
    d = diagnose(_eq(), _trades())
    assert set(d) == {"monthly_returns", "drawdown_analysis",
                      "symbol_attribution", "holdings_history", "benchmark_comparison"}


def test_diagnose_drawdown_and_attribution():
    d = diagnose(_eq(), _trades())
    assert d["drawdown_analysis"]["max_drawdown"] < 0
    assert d["drawdown_analysis"]["peak_date"] < d["drawdown_analysis"]["trough_date"]
    sym = next(s for s in d["symbol_attribution"] if s["symbol"] == "600519")
    assert sym["n_trades"] == 2
    assert sym["pnl"] == pytest.approx(12000 - 5 - 6 - 0.1 - (10000 + 5), abs=0.01)
    assert sym["held_days"] == 1


def test_diagnose_accepts_record_list():
    rows = _eq().to_dict("records")
    d = diagnose(rows, _trades())
    assert "monthly_returns" in d


def test_diagnose_empty_equity():
    d = diagnose([], [])
    assert d == {"error": "no equity data"}
