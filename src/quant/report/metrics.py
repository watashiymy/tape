"""绩效指标（spec §9）。夏普：rf=0 简化口径（中国无风险利率约 1.5–2%，报告中注明）。"""
from __future__ import annotations

import math

import pandas as pd

from quant.backtest.portfolio import Trade

TRADING_DAYS = 252


def compute_metrics(equity: pd.Series, trades: list[Trade]) -> dict:
    total = float(equity.iloc[-1] / equity.iloc[0] - 1)
    years = len(equity) / TRADING_DAYS
    cagr = float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) if years > 0 else 0.0
    daily = equity.pct_change().dropna()
    std = float(daily.std())
    sharpe = float(daily.mean() / std * math.sqrt(TRADING_DAYS)) if std > 0 else 0.0
    peak = equity.cummax()
    max_dd = float(((equity - peak) / peak).min())

    closed = [t for t in trades if t.pnl is not None]
    wins = [t for t in closed if t.pnl > 0]
    losses = [t for t in closed if t.pnl <= 0]
    loss_sum = abs(sum(t.pnl for t in losses))
    hold = [t.holding_days for t in closed if t.holding_days is not None]

    return {
        "total_return": total,
        "cagr": cagr,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "n_trades": len(closed),
        "win_rate": len(wins) / len(closed) if closed else None,
        "profit_factor": (sum(t.pnl for t in wins) / loss_sum) if wins and loss_sum > 0 else None,
        "avg_holding_days": (sum(hold) / len(hold)) if hold else None,
    }
