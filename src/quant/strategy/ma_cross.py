"""双均线：MA(fast) 在 MA(slow) 之上 → 持有（状态式，与"上穿买入/下穿卖出"事件式等价）。"""
from __future__ import annotations

import pandas as pd

from quant.indicators import ma
from quant.strategy.base import Strategy


class MaCross(Strategy):
    name = "ma_cross"

    def __init__(self, fast: int = 20, slow: int = 60):
        # 构造期校验，用 raise 而非 assert（-O 下 assert 会被剥除）。
        # fast=0 时 rolling(0) 无警告地返回全 NaN → 全程静默空仓；
        # 浮点参数迟至 generate_positions 才崩且报错误导。
        for pname, v in (("fast", fast), ("slow", slow)):
            if not isinstance(v, int) or v < 1:
                raise ValueError(f"参数 {pname} 必须是不小于 1 的整数，实际为 {v!r}")
        if fast >= slow:
            raise ValueError(f"参数 fast 必须小于 slow，实际为 fast={fast!r}, slow={slow!r}")
        self.fast, self.slow = fast, slow

    def generate_positions(self, df: pd.DataFrame) -> pd.Series:
        f = ma(df["adj_close"], self.fast)
        s = ma(df["adj_close"], self.slow)
        return (f > s).astype(int)  # NaN 比较为 False → 暖机期自动为 0
