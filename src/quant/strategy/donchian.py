"""唐奇安通道突破（spec §7）：
入场 = 收盘 > 前 entry_n 日（不含当日）最高收盘 且 成交额 > amount_ratio × 前 amount_n 日均额；
出场 = 收盘 < 前 exit_n 日（不含当日）最低收盘。
窗口必须先 shift(1) 再滚动——含当日则"收盘 > N日最高"永不成立（死信号）。"""
from __future__ import annotations

import pandas as pd

from quant.indicators import ma, rolling_high, rolling_low
from quant.strategy.base import Strategy


class Donchian(Strategy):
    name = "donchian"

    def __init__(self, entry_n: int = 20, exit_n: int = 10,
                 amount_n: int = 20, amount_ratio: float = 1.5):
        self.entry_n, self.exit_n = entry_n, exit_n
        self.amount_n, self.amount_ratio = amount_n, amount_ratio

    def generate_positions(self, df: pd.DataFrame) -> pd.Series:
        c, amt = df["adj_close"], df["amount"]
        upper = rolling_high(c.shift(1), self.entry_n)          # 前 N 日，不含当日
        lower = rolling_low(c.shift(1), self.exit_n)
        amt_ok = amt > self.amount_ratio * ma(amt.shift(1), self.amount_n)
        entry = (c > upper) & amt_ok
        exit_ = c < lower

        pos = pd.Series(0, index=df.index, dtype=int)
        holding = False
        for i in range(len(df)):
            if holding and exit_.iloc[i]:
                holding = False
            elif not holding and entry.iloc[i]:
                holding = True
            pos.iloc[i] = int(holding)
        return pos
