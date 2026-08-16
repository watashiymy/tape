"""双均线：MA(fast) 在 MA(slow) 之上 → 持有（状态式，与"上穿买入/下穿卖出"事件式等价）。"""
from __future__ import annotations

import pandas as pd

from quant.indicators import ma
from quant.strategy.base import Strategy


class MaCross(Strategy):
    name = "ma_cross"

    def __init__(self, fast: int = 20, slow: int = 60):
        assert fast < slow, "fast 必须小于 slow"
        self.fast, self.slow = fast, slow

    def generate_positions(self, df: pd.DataFrame) -> pd.Series:
        f = ma(df["adj_close"], self.fast)
        s = ma(df["adj_close"], self.slow)
        return (f > s).astype(int)  # NaN 比较为 False → 暖机期自动为 0
