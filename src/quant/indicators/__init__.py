"""基础指标：全部纯函数，标准滚动窗口（含当日）。需要"不含当日"时由调用方先 shift(1)（spec §6）。"""
from __future__ import annotations

import pandas as pd


def ma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def rolling_high(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).max()


def rolling_low(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).min()


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    h, l, c = df["adj_high"], df["adj_low"], df["adj_close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()
