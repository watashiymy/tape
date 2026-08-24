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
        # 构造期校验，用 raise 而非 assert（-O 下 assert 会被剥除）。
        # 不校验的代价是静默出错：rolling(0) 无警告地返回全 NaN——
        # exit_n=0 入场后永不卖出，entry_n=0/amount_n=0 全程空仓，
        # 回测照常完成零告警；浮点窗口则迟至 generate_positions 才崩且报错误导。
        for pname, v in (("entry_n", entry_n), ("exit_n", exit_n), ("amount_n", amount_n)):
            if not isinstance(v, int) or v < 1:
                raise ValueError(f"参数 {pname} 必须是不小于 1 的整数，实际为 {v!r}")
        if not isinstance(amount_ratio, (int, float)) or amount_ratio <= 0:
            raise ValueError(f"参数 amount_ratio 必须大于 0，实际为 {amount_ratio!r}")
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
