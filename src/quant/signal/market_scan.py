"""全市场扫描纯逻辑（v0.1.1 设计 §3.3）：单票五类判定 + 新 BUY 信号提取，离线可测。

与 signal/scan.py（固定池，BUY/SELL 双向）的分工：全市场扫描只报 BUY——
SELL 信号对未持仓者无意义；看中的票加进 universe 后由 run_daily_signal 跟踪卖出。
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from quant.strategy.base import Strategy

MIN_BARS = 130       # 暖机阈值 ≈ MA60×2+10：不足则最慢的指标还在暖机期，信号不可信
AMOUNT_WINDOW = 20   # 流动性均额窗口（bar 数）


def classify_and_scan(bars: pd.DataFrame, strategies: list[Strategy], expected: date,
                      min_avg_amount: float) -> tuple[list[dict], str | None]:
    """对单只标的的清洗后 bars 依设计表格顺序判定，返回 (signals, skip_reason)。

    bars：prepare_bars 输出（含 adj_* 列），attrs 携带 symbol/name（编排层填入）。
    expected：基准交易日。先把晚于它的行截掉（--date 语义：缓存可能已含更新数据），
    再要求截断后最新 bar 恰为该日——否则视为数据未更新（停牌/退市中）。

    skip_reason ∈ {"stale", "insufficient_history", "is_st", "low_liquidity", None}；
    None 表示完成了信号判定，signals 为空即 no_signal。
    """
    df = bars[bars.index.date <= expected]
    if df.empty or df.index.max().date() < expected:
        return [], "stale"
    if len(df) < MIN_BARS:
        return [], "insufficient_history"
    if int(df["is_st"].iloc[-1]) == 1:      # 名称过滤（get_all_symbols）的兜底
        return [], "is_st"
    avg_amount = float(df["amount"].iloc[-AMOUNT_WINDOW:].mean())
    if avg_amount < min_avg_amount:
        return [], "low_liquidity"

    # pct_chg 用后复权口径：末两根跨除权日时原始价的跳空不是真实涨跌
    adj_close = df["adj_close"]
    pct_chg = (adj_close.iloc[-1] / adj_close.iloc[-2] - 1.0) * 100.0
    last_amount = float(df["amount"].iloc[-1])
    signals: list[dict] = []
    for strat in strategies:
        pos = strat.generate_positions(df)
        if int(pos.iloc[-1]) == 1 and int(pos.iloc[-2]) == 0:   # 0→1 = 新 BUY
            signals.append({
                "date": df.index[-1].strftime("%Y-%m-%d"),
                "symbol": bars.attrs.get("symbol", ""),
                "name": bars.attrs.get("name", ""),
                "strategy": strat.name,
                "close": float(df["close"].iloc[-1]),   # 原始价（撮合口径）
                "pct_chg": float(pct_chg),
                "amount": last_amount,
                "amount_ratio_20d": last_amount / avg_amount,
            })
    return signals, None


def sort_signals(signals: list[dict]) -> list[dict]:
    """当日成交额降序（流动性优先，设计 §3.3）；同额按 symbol/strategy 升序保证稳定输出。"""
    return sorted(signals, key=lambda s: (-s["amount"], s["symbol"], s["strategy"]))
