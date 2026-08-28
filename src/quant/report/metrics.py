"""绩效指标（spec §9）。夏普：rf=0 简化口径（中国无风险利率约 1.5–2%，报告中注明）。"""
from __future__ import annotations

import math
from collections.abc import Sequence

import pandas as pd

from quant.backtest.portfolio import Trade

TRADING_DAYS = 252


def compute_metrics(equity: pd.Series, trades: list[Trade]) -> dict:
    total = float(equity.iloc[-1] / equity.iloc[0] - 1)
    # CAGR 定义即日历年化：用首末日期跨度折算。bar数/252 会系统性高估
    # ——A 股一年只有约 243 个交易日，等于把同样的收益塞进更短的"年"里。
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    cagr = float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) if years > 0 else 0.0
    daily = equity.pct_change().dropna()
    std = float(daily.std())
    sharpe = float(daily.mean() / std * math.sqrt(TRADING_DAYS)) if std > 0 else 0.0
    peak = equity.cummax()
    max_dd = float(((equity - peak) / peak).min())

    return {
        "total_return": total,
        "cagr": cagr,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        **trade_stats([t.pnl for t in trades], [t.holding_days for t in trades]),
    }


def trade_stats(pnls: Sequence[float | None],
                holding_days: Sequence[float | None]) -> dict:
    """平仓交易的统计口径：笔数 / 胜率 / 盈亏比 / 平均持仓天数。

    单独抽成纯函数，是为了让**回测报告与交易日志共用同一把尺子**
    （设计 §4.2 明写要把"你的实际交易"和"策略回测"并排比较）。
    各写一份的话，下面这两处边界迟早只改一边，而那时两张表看着都对：

    - `pnl is None` = 这笔算不出盈亏（回测里的未平仓、日志里配不到买入批次的卖出）。
      它不进分母，更不能被当成 0 记一笔平手。
    - 平手（pnl == 0）归入亏损：没赚就是没赢，胜率不该被"打平"抬高。
    """
    if len(pnls) != len(holding_days):
        # 错位会静默把 A 的盈亏配上 B 的持仓天数，平均持仓天数从此是个错数字。
        raise ValueError(f"pnls 与 holding_days 长度不一致：{len(pnls)} vs {len(holding_days)}")

    closed = [(p, h) for p, h in zip(pnls, holding_days) if p is not None]
    wins = [p for p, _ in closed if p > 0]
    loss_sum = abs(sum(p for p, _ in closed if p <= 0))
    hold = [h for _, h in closed if h is not None]

    return {
        "n_trades": len(closed),
        "win_rate": len(wins) / len(closed) if closed else None,
        # 只看分母：全亏时 PF 是良定义的 0.0；None 只留给"没有亏损单可除"（全胜/零平仓）
        "profit_factor": (sum(wins) / loss_sum) if loss_sum > 0 else None,
        "avg_holding_days": (sum(hold) / len(hold)) if hold else None,
    }
