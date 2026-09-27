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
from numpy.lib.stride_tricks import sliding_window_view

WINDOWS = (20, 60, 200)
HIGH_LOW_WINDOW = 250
REL_TOL = 1e-9


def _columns(windows: tuple[int, ...]) -> list[str]:
    return ["date"] + [c for n in windows for c in (f"above{n}", f"n{n}")] + ["new_high", "new_low", "n_hl"]


#: 指标表（breadth.parquet）的列。store.load_breadth 按它校验：缺哪一列都当「读不出来」，
#: 首页那一块逐块降级（spec §5.5），不许拿半张表去索引。
COLUMNS = _columns(WINDOWS)


def _wide(snaps: dict[date, pd.DataFrame], days: list[date], col: str) -> pd.DataFrame:
    return _wide_frames(snaps, days, (col,))[col]


def _wide_frames(snaps: dict[date, pd.DataFrame], days: list[date],
                  cols: tuple[str, ...]) -> dict[str, pd.DataFrame]:
    """一次性把几个字段都摊成「日期 × 代码」宽表。几张宽表要做的「代码对齐」（跨 300+ 天
    union 5000+ 只股票代码）是同一份活；拆开逐列摊（旧版 `_wide` 那样每个字段调一次）就要把
    这份对齐工作重复算几遍——compute() 一次要摊 4 个字段，是除 rolling 外最贵的一步。"""
    frames, present_days = [], []
    for d in days:
        df = snaps.get(d)
        if df is None:
            continue
        frames.append(df.set_index("code")[list(cols)])
        present_days.append(d)
    if not frames:
        empty = pd.DataFrame(index=days)
        return {c: empty.copy() for c in cols}
    combined = pd.concat(frames, axis=1, keys=present_days)     # 列变 (日期, 字段) 两层
    return {c: combined.xs(c, axis=1, level=1).T.reindex(index=days) for c in cols}


def _listed_mask(close: pd.DataFrame) -> pd.DataFrame:
    """某只股票在首次与最后一次出现之间的日子都算「应有行」；其间的 NaN 就是缺口。"""
    present = close.notna()
    first = present.idxmax()
    last = present[::-1].idxmax()
    idx = np.fromiter((d.toordinal() for d in close.index), dtype=np.int64, count=len(close.index))
    first_ord = np.fromiter((d.toordinal() for d in first.to_numpy()), dtype=np.int64, count=len(first))
    last_ord = np.fromiter((d.toordinal() for d in last.to_numpy()), dtype=np.int64, count=len(last))
    mask_vals = (idx[:, None] >= first_ord[None, :]) & (idx[:, None] <= last_ord[None, :])
    return pd.DataFrame(mask_vals, index=close.index, columns=close.columns)


def _adjusted_close_from_frames(close: pd.DataFrame, pre: pd.DataFrame, tradestatus: pd.DataFrame) -> pd.DataFrame:
    close = close.astype(float)
    pre = pre.astype(float)
    trading = tradestatus.fillna(0).astype(int) == 1
    listed = _listed_mask(close)
    ratio = close / pre
    ratio = ratio.where(trading & (pre > 0), 1.0)          # 停牌 / 昨收无效 → 1
    ratio = ratio.where(~(listed & close.isna()), np.nan)   # 缺行 → NaN
    ratio = ratio.where(listed, 1.0)                        # 未上市/已退市的日子不参与
    adj = ratio.cumprod()
    return adj.where(listed)


def adjusted_close(snaps: dict[date, pd.DataFrame], days: list[date]) -> pd.DataFrame:
    """复权收盘（相对首日 = 1）。停牌行比值 1；缺行 → NaN 并让其后的链断掉（毒 bar）。"""
    frames = _wide_frames(snaps, days, ("close", "preclose", "tradestatus"))
    return _adjusted_close_from_frames(frames["close"], frames["preclose"], frames["tradestatus"])


def bar_index(snaps: dict[date, pd.DataFrame], days: list[date]) -> pd.DataFrame:
    """每只股票到当天为止的**交易根数**（停牌日不加）。"""
    trading = (_wide(snaps, days, "tradestatus").fillna(0).astype(int) == 1)
    return trading.cumsum()


def _rolling_on_bars_reference(adj: pd.DataFrame, trading: pd.DataFrame, n: int, fn: str) -> pd.DataFrame:
    """逐列参考实现（慢）：只给等价性测试用来核对向量化版本，生产路径不再走它。"""
    out = pd.DataFrame(np.nan, index=adj.index, columns=adj.columns)
    for code in adj.columns:
        col = adj[code]
        on_bars = col[trading[code] | col.isna()]          # 交易日 + 毒 bar
        rolled = getattr(on_bars.rolling(n, min_periods=n), fn)()
        out[code] = rolled.reindex(adj.index)
    return out


def _compact_bars(adj: pd.DataFrame, trading: pd.DataFrame):
    """把每只股票自己的「交易根 + 毒 bar」序列压紧成一列，所有股票左边补齐 NaN 对到同一长度，
    这样才能用一次 sliding_window_view 对整张表做 rolling（而不是逐只股票 rolling 一遍）。
    返回 (vals, rows_idx, cols_idx, dest)：vals 是压紧后的矩阵；(rows_idx, cols_idx) 是原表里
    「算作一根 bar」的格子（日历行、股票列）；dest 是这些格子在 vals 里对应的行号，用来把
    rolling 结果原样散回日历行。"""
    use = (trading.to_numpy() | np.isnan(adj.to_numpy()))
    adj_vals = adj.to_numpy()
    n_codes = adj_vals.shape[1]
    cnt = use.sum(axis=0)
    max_len = int(cnt.max()) if cnt.size else 0
    ordinal = use.cumsum(axis=0)                # 到本行为止（含本行）这只股票攒了几根 bar
    pad = max_len - cnt                         # 比最长的那只股票少几根，就在左边补几个 NaN
    dest_row = ordinal - 1 + pad                 # 只在 use=True 的格子上有意义
    rows_idx, cols_idx = np.nonzero(use)
    vals = np.full((max_len, n_codes), np.nan)
    dest = dest_row[rows_idx, cols_idx]
    vals[dest, cols_idx] = adj_vals[rows_idx, cols_idx]
    return vals, rows_idx, cols_idx, dest


def _rolling_on_bars(adj: pd.DataFrame, trading: pd.DataFrame, n: int, fn: str) -> pd.DataFrame:
    """在「交易根」序列上做 rolling，再对齐回日历日。毒 bar（NaN）参与 rolling，让窗口失效。
    向量化：把每只股票的 bar 序列压紧成矩阵（见 `_compact_bars`），用 sliding_window_view
    一次性对全表做 rolling，NaN 会顺着 mean/max/min 自动传染——效果等价于逐列
    `rolling(n, min_periods=n)`，但不用对 5000+ 只股票各跑一遍。"""
    vals, rows_idx, cols_idx, dest = _compact_bars(adj, trading)
    max_len, n_codes = vals.shape
    out = np.full(adj.shape, np.nan)
    if rows_idx.size and max_len >= n:
        windows = sliding_window_view(vals, n, axis=0)      # (max_len-n+1, n_codes, n)
        reduced = getattr(np, fn)(windows, axis=-1)          # 窗口里有一个 NaN 就传染成 NaN
        rolled_full = np.full((max_len, n_codes), np.nan)
        rolled_full[n - 1:] = reduced
        out[rows_idx, cols_idx] = rolled_full[dest, cols_idx]
    return pd.DataFrame(out, index=adj.index, columns=adj.columns)


def compute(snaps: dict[date, pd.DataFrame], days: list[date],
            windows: tuple[int, ...] = WINDOWS, high_low_window: int = HIGH_LOW_WINDOW) -> pd.DataFrame:
    """快照 → 指标表，按日期升序。

    一天一行，只有有快照的日子才有行；缺口不占行，但仍作为毒 bar 参与 rolling。
    `days` 是完整的交易日窗口：缺快照的日子先按 NaN 摊进宽表，让回看期跨过它的窗口失效，
    算完再把这些行丢掉。不丢的话它们是一行全 0——窗口末日的快照还没发布时，末行就是
    那一行，首页会把没有数据的那天写成「收盘数据截至」、四张卡片全变成「—」。"""
    days = sorted(days)
    present = [d in snaps for d in days]
    if not any(present):
        return pd.DataFrame(columns=_columns(windows))
    frames = _wide_frames(snaps, days, ("close", "preclose", "tradestatus", "isST"))
    adj = _adjusted_close_from_frames(frames["close"], frames["preclose"], frames["tradestatus"])
    trading = (frames["tradestatus"].fillna(0).astype(int) == 1)
    st = (frames["isST"].fillna(0).astype(int) == 1)
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
    return pd.DataFrame(rows).loc[present].reset_index(drop=True)


def suspicious_suspended(snaps: dict[date, pd.DataFrame]) -> list[str]:
    """spec §5.3：停牌行本该 close == preclose；不等的列出来给任务日志报警（不改数）。"""
    out = []
    for d, df in sorted(snaps.items()):
        odd = df[(df["tradestatus"] == 0) & (df["preclose"] > 0) & (df["close"] != df["preclose"])]
        for code in odd["code"].head(5):
            out.append(f"{d} {code} 停牌但 close ≠ preclose")
    return out
