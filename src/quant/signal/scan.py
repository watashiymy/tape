"""每日信号扫描（spec §11）：最新一根K线的目标仓位相对前一根的变化 = 新信号。"""
from __future__ import annotations

import pandas as pd

from quant.strategy.base import Strategy


def scan(bars: dict[str, pd.DataFrame], strategies: list[Strategy]) -> list[dict]:
    signals: list[dict] = []
    for strat in strategies:
        for sym, df in bars.items():
            pos = strat.generate_positions(df)
            if len(pos) < 2:
                continue
            prev_p, cur = int(pos.iloc[-2]), int(pos.iloc[-1])
            if cur == prev_p:
                continue
            signals.append({
                "date": df.index[-1].strftime("%Y-%m-%d"),
                "symbol": sym,
                "strategy": strat.name,
                "action": "BUY" if cur > prev_p else "SELL",
                "close": float(df["close"].iloc[-1]),
            })
    return signals
