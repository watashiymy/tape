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

    @abstractmethod
    def get_all_symbols(self, as_of: date) -> pd.DataFrame:
        """全市场扫描池清单（v0.1.1）。as_of 须为交易日。

        返回 DataFrame，列：symbol(6位str)、name，按 symbol 升序。
        过滤规则：仅沪深主板（sh.60*/sz.00* 前缀，00 含原中小板 002/003）；
        名称含 'ST' 剔除；type=1（股票）且 status=1（在市）；
        上市日期(ipoDate) 距 as_of 不足 400 自然日剔除（暖机不足的新股）。"""
        ...
