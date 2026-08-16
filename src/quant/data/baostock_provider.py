"""baostock 数据源实现（spec §5）。用法：with BaostockProvider() as p: ..."""
from __future__ import annotations

from datetime import date

import baostock as bs
import pandas as pd

from quant.data.provider import DataProvider

_K_FIELDS = "date,open,high,low,close,volume,amount,tradestatus,isST"
_NUM_COLS = ["open", "high", "low", "close", "volume", "amount"]
_EMPTY_COLS = _NUM_COLS + ["adj_factor", "trade_status", "is_st"]


def to_bs_code(symbol: str) -> str:
    """'600519' → 'sh.600519'；'000333' → 'sz.000333'。6 开头沪市，其余(0/3开头)深市。"""
    return ("sh." if symbol.startswith("6") else "sz.") + symbol


def _check(rs) -> None:
    if rs.error_code != "0":
        raise RuntimeError(f"baostock 错误 {rs.error_code}: {rs.error_msg}")


def _fetch(rs) -> pd.DataFrame:
    """把 baostock 结果集读成 DataFrame。

    **必须用这个函数，不要用 rs.get_data()。** baostock 0.9.3 的 get_data() 在翻页分支里
    调用了 pandas 2.0 已删除的 DataFrame.append，任何首页恰好返回 2000 行（分页大小）的
    查询都会抛 AttributeError——10 年日线（约 2579 行）正好命中，实测必崩。
    官方惯用的 next()+get_row_data() 逐行迭代没有这个问题，已用真实数据验证通过。
    """
    _check(rs)
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    return pd.DataFrame(rows, columns=rs.fields)


class BaostockProvider(DataProvider):
    def __enter__(self):
        _check(bs.login())
        return self

    def __exit__(self, *exc):
        bs.logout()
        return False

    def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        code = to_bs_code(symbol)
        rs = bs.query_history_k_data_plus(
            code, _K_FIELDS, start_date=str(start), end_date=str(end),
            frequency="d", adjustflag="3")  # 3 = 不复权（原始价）
        df = _fetch(rs)
        if df.empty:
            return pd.DataFrame(columns=_EMPTY_COLS,
                                index=pd.DatetimeIndex([], name="date"))
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        for c in _NUM_COLS:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["trade_status"] = pd.to_numeric(df.pop("tradestatus"), errors="coerce").fillna(0).astype(int)
        df["is_st"] = pd.to_numeric(df.pop("isST"), errors="coerce").fillna(0).astype(int)
        df["adj_factor"] = self._adj_factor_series(code, end).reindex(df.index, method="ffill").fillna(1.0)
        return df[_EMPTY_COLS]

    @staticmethod
    def _adj_factor_series(code: str, end: date) -> pd.Series:
        # 从上市早期拉全量因子记录（只在除权除息日有记录），ffill 到日频。
        # 探针已确认 backAdjustFactor 是"自上市累积"口径（单调不减、每个除权日一条、日期无重复），
        # 所以直接 ffill 即可，无需累乘。
        rs = bs.query_adjust_factor(code=code, start_date="1990-01-01", end_date=str(end))
        fac = _fetch(rs)
        if fac.empty:
            return pd.Series(dtype=float)
        idx = pd.to_datetime(fac["dividOperateDate"])
        return pd.Series(fac["backAdjustFactor"].astype(float).values, index=idx).sort_index()

    def get_index_daily(self, index_code: str, start: date, end: date) -> pd.DataFrame:
        code = "sh." + index_code if index_code.startswith("0") else index_code
        rs = bs.query_history_k_data_plus(
            code, "date,close", start_date=str(start), end_date=str(end),
            frequency="d", adjustflag="3")
        df = _fetch(rs)
        if df.empty:
            raise ValueError(f"指数 {index_code} 在 {start}~{end} 无数据（检查代码前缀是否为 sh./sz.）")
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        return df

    def get_trade_calendar(self, start: date, end: date) -> list[date]:
        # 注意：query_trade_dates 返回的是**日历日**（含周末节假日），需按 is_trading_day 过滤。
        # 10 年区间约 3879 个日历日 → 2579 个交易日（实测）。
        rs = bs.query_trade_dates(start_date=str(start), end_date=str(end))
        df = _fetch(rs)
        trading = df[df["is_trading_day"] == "1"]
        return [d.date() for d in pd.to_datetime(trading["calendar_date"])]
