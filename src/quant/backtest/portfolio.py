"""账本数据结构。资金模型（spec §8）：每标的一个独立 Slot，额度=初始本金/N，互不挪用。"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class Trade:
    symbol: str
    action: str            # "buy" | "sell"
    date: pd.Timestamp
    price: float           # 含滑点的成交价
    shares: float
    commission: float
    stamp: float = 0.0
    pnl: float | None = None          # 仅卖出时结算
    holding_days: int | None = None   # 仅卖出时结算（自然日）


@dataclass
class Slot:
    symbol: str
    cash: float
    budget: float = 0.0        # 固定额度=初始本金/N，不随权益浮动（spec §8）
    shares: float = 0.0
    entry_date: pd.Timestamp | None = None
    cost_basis: float = 0.0     # 本轮持仓累计买入支出（含费用）
    last_close: float | None = None
    last_factor: float | None = None


@dataclass
class BacktestResult:
    equity: pd.Series                  # 日频总净值
    trades: list[Trade]
    skipped: list[tuple] = field(default_factory=list)  # (date, symbol, 原因)
