"""策略基类：输入清洗后的行情 df（含 adj_* 列），输出目标仓位（spec 决策10）。"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    def generate_positions(self, df: pd.DataFrame) -> pd.Series:
        """返回与 df.index 对齐的 Series，值 ∈ {0, 1}；只能使用当日及以前的数据。"""
