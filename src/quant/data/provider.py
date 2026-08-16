"""数据源抽象接口。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

import pandas as pd


class DataProvider(ABC):
    """返回的 DataFrame：DatetimeIndex(date)，列 open/high/low/close/volume/amount(原始价)
    + adj_factor(后复权因子) + trade_status(1正常/0停牌) + is_st(0/1)。"""

    @abstractmethod
    def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame: ...

    @abstractmethod
    def get_index_daily(self, index_code: str, start: date, end: date) -> pd.DataFrame: ...

    @abstractmethod
    def get_trade_calendar(self, start: date, end: date) -> list[date]: ...
