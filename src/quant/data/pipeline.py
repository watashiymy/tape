"""原始行情 → 可用行情：去重、停牌过滤、adj_* 派生、质量校验（spec §5、决策1/6）。"""
from __future__ import annotations

import pandas as pd

JUMP_THRESHOLD = 0.11  # 主板池校验阈值（spec §5）


def prepare_bars(raw: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """返回 (清洗后的 df, 告警列表)。df 含原始列 + adj_open/adj_high/adj_low/adj_close。"""
    warns: list[str] = []
    df = raw.sort_index().copy()

    dup = df.index.duplicated(keep="last")
    if dup.any():
        warns.append(f"重复日期 {int(dup.sum())} 行，保留最后一行")
        df = df[~df.index.duplicated(keep="last")]

    df = df[(df["trade_status"] == 1) & (df["volume"] > 0)].copy()

    # NaN（如 baostock 空串经 to_numeric 转换而来）与任何数比较恒为 False：既躲得过下面的
    # OHLC 谓词，又会让 adj_* 全变 NaN、让自身与次日的 pct_change 双双失效（跳变漏报）。
    na_price = df[["open", "high", "low", "close", "amount", "adj_factor"]].isna().any(axis=1)
    if na_price.any():
        warns.append(f"价格/成交额缺失 {int(na_price.sum())} 行，已剔除: "
                     f"{[d.strftime('%Y-%m-%d') for d in df.index[na_price]]}")
        df = df[~na_price].copy()

    bad_ohlc = (df["high"] < df["low"]) | (df["high"] < df[["open", "close"]].max(axis=1)) \
        | (df["low"] > df[["open", "close"]].min(axis=1))
    if bad_ohlc.any():
        warns.append(f"OHLC 逻辑异常 {int(bad_ohlc.sum())} 行，已剔除: "
                     f"{[d.strftime('%Y-%m-%d') for d in df.index[bad_ohlc]]}")
        df = df[~bad_ohlc].copy()

    for c in ("open", "high", "low", "close"):
        df["adj_" + c] = df[c] * df["adj_factor"]

    pct = df["close"].pct_change().abs()
    factor_changed = df["adj_factor"].diff().fillna(0.0) != 0.0
    jump = (pct > JUMP_THRESHOLD) & (~factor_changed)
    if jump.any():
        warns.append(f"异常跳变（|涨跌|>{JUMP_THRESHOLD:.0%} 且非除权日）: "
                     f"{[d.strftime('%Y-%m-%d') for d in df.index[jump]]}")

    st_rows = df["is_st"] == 1
    if st_rows.any():
        # 保留数据但提醒：ST 期间涨跌幅限制为 5%，本引擎按 10% 建模（spec 决策7/8）
        warns.append(f"该标的在 {df.index[st_rows].min():%Y-%m-%d} ~ "
                     f"{df.index[st_rows].max():%Y-%m-%d} 期间为 ST（共 {int(st_rows.sum())} 天），"
                     f"涨跌停建模与实际不符，建议从股票池剔除")
    return df, warns
