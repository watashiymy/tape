"""趋势宽度（spec §5.3）：每日快照 → 复权收盘链 → 站上 N 日线占比、52 周新高/新低。

范围：当日交易中、非 ST、且至少有 N 根历史。「根」= 该股实际交易的日子，停牌日不计。
缺口（整天缺快照，或某只股票在首末出现之间缺一天）当作一根值为 NaN 的「毒 bar」：
rolling 均值/极值一碰到它就是 NaN，回看期跨过缺口的日子自然进不了分子分母——
这正是 §5.3 要的语义，而且不用单独维护缺口表。
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

WINDOWS = (20, 60, 200)
HIGH_LOW_WINDOW = 250
REL_TOL = 1e-9


def _wide(snaps: dict[date, pd.DataFrame], days: list[date], col: str) -> pd.DataFrame:
    frames = []
    for d in days:
        df = snaps.get(d)
        if df is None:
            continue
        s = df.set_index("code")[col]
        s.name = d
        frames.append(s)
    wide = pd.concat(frames, axis=1).T if frames else pd.DataFrame()
    return wide.reindex(index=days)              # 缺的整天成为全 NaN 行


def _listed_mask(close: pd.DataFrame) -> pd.DataFrame:
    """某只股票在首次与最后一次出现之间的日子都算「应有行」；其间的 NaN 就是缺口。"""
    present = close.notna()
    first = present.idxmax()
    last = present[::-1].idxmax()
    idx = np.array(close.index)
    mask = pd.DataFrame(False, index=close.index, columns=close.columns)
    for code in close.columns:
        mask[code] = (idx >= first[code]) & (idx <= last[code])
    return mask


def adjusted_close(snaps: dict[date, pd.DataFrame], days: list[date]) -> pd.DataFrame:
    """复权收盘（相对首日 = 1）。停牌行比值 1；缺行 → NaN 并让其后的链断掉（毒 bar）。"""
    close = _wide(snaps, days, "close").astype(float)
    pre = _wide(snaps, days, "preclose").astype(float)
    trading = _wide(snaps, days, "tradestatus").fillna(0).astype(int) == 1
    listed = _listed_mask(close)
    ratio = close / pre
    ratio = ratio.where(trading & (pre > 0), 1.0)          # 停牌 / 昨收无效 → 1
    ratio = ratio.where(~(listed & close.isna()), np.nan)   # 缺行 → NaN
    ratio = ratio.where(listed, 1.0)                        # 未上市/已退市的日子不参与
    adj = ratio.cumprod()
    return adj.where(listed)


def bar_index(snaps: dict[date, pd.DataFrame], days: list[date]) -> pd.DataFrame:
    """每只股票到当天为止的**交易根数**（停牌日不加）。"""
    trading = (_wide(snaps, days, "tradestatus").fillna(0).astype(int) == 1)
    return trading.cumsum()


def _rolling_on_bars(adj: pd.DataFrame, trading: pd.DataFrame, n: int, fn: str) -> pd.DataFrame:
    """在「交易根」序列上做 rolling，再对齐回日历日。毒 bar（NaN）参与 rolling，让窗口失效。"""
    out = pd.DataFrame(np.nan, index=adj.index, columns=adj.columns)
    for code in adj.columns:
        col = adj[code]
        on_bars = col[trading[code] | col.isna()]          # 交易日 + 毒 bar
        rolled = getattr(on_bars.rolling(n, min_periods=n), fn)()
        out[code] = rolled.reindex(adj.index)
    return out


def compute(snaps: dict[date, pd.DataFrame], days: list[date],
            windows: tuple[int, ...] = WINDOWS, high_low_window: int = HIGH_LOW_WINDOW) -> pd.DataFrame:
    adj = adjusted_close(snaps, days)
    trading = (_wide(snaps, days, "tradestatus").fillna(0).astype(int) == 1)
    st = (_wide(snaps, days, "isST").fillna(0).astype(int) == 1)
    eligible = trading & ~st & adj.notna()
    rows = {"date": list(adj.index)}
    for n in windows:
        ma = _rolling_on_bars(adj, trading, n, "mean")
        ok = eligible & ma.notna()
        above = ok & (adj > ma * (1 + REL_TOL))
        rows[f"above{n}"] = above.sum(axis=1).astype(int).tolist()
        rows[f"n{n}"] = ok.sum(axis=1).astype(int).tolist()
    hi = _rolling_on_bars(adj, trading, high_low_window, "max")
    lo = _rolling_on_bars(adj, trading, high_low_window, "min")
    ok = eligible & hi.notna()
    rows["new_high"] = (ok & (adj >= hi * (1 - REL_TOL))).sum(axis=1).astype(int).tolist()
    rows["new_low"] = (ok & (adj <= lo * (1 + REL_TOL))).sum(axis=1).astype(int).tolist()
    rows["n_hl"] = ok.sum(axis=1).astype(int).tolist()
    return pd.DataFrame(rows)


def suspicious_suspended(snaps: dict[date, pd.DataFrame]) -> list[str]:
    """spec §5.3：停牌行本该 close == preclose；不等的列出来给任务日志报警（不改数）。"""
    out = []
    for d, df in sorted(snaps.items()):
        odd = df[(df["tradestatus"] == 0) & (df["preclose"] > 0) & (df["close"] != df["preclose"])]
        for code in odd["code"].head(5):
            out.append(f"{d} {code} 停牌但 close ≠ preclose")
    return out
