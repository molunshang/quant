"""A-share trading rules: commissions, stamp duty, price limits, lot size, T+1."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TradingRules:
    commission_rate: float = 0.0003       # 万3, min 5 yuan
    min_commission: float = 5.0
    stamp_duty: float = 0.0005            # 0.05% on SELL for stocks; 0 for ETF/fund
    transfer_fee_rate: float = 0.00001    # 过户费 (沪市)
    lot_size: int = 100                   # 一手 100 股
    t_plus_1: bool = True                 # A股 T+1
    price_limit_pct: float = 0.10         # 主板 ±10%

    def is_etf_or_fund(self, symbol_type: str) -> bool:
        return symbol_type in ("etf", "fund")


@dataclass
class Fill:
    """Result of an order attempt."""

    order_date: str
    shares: int
    price: float
    commission: float
    stamp_duty: float
    transfer_fee: float
    amount: float
    side: str  # "buy" | "sell"


def calc_commission(amount: float, rate: float, min_c: float) -> float:
    return max(min_c, amount * rate)


def calc_transfer_fee(amount: float, rate: float) -> float:
    return amount * rate
