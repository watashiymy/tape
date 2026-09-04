"""信号跟踪扫描（spec §11）：最新一根K线的目标仓位相对前一根的变化 = 新信号。

目标仓位一律经 `pipeline.target_positions`（v0.4.0 M2）——自此本页会出现**止损触发
的 SELL**：基础信号还是 1（均线仍多头排列），但 ATR 追踪止损已经认输。
这正是本版给用户补的真缺口（此前系统等权满仓、没有任何出场保护）。
"""
from __future__ import annotations

import pandas as pd

from quant.config import OverlaysCfg
from quant.strategy.base import Strategy
from quant.strategy.pipeline import target_positions


def scan(bars: dict[str, pd.DataFrame], strategies: list[Strategy], *,
         overlays: OverlaysCfg) -> list[dict]:
    """`overlays` 是**必填关键字**：没有"忘了传就当没开"的缺省。
    缺省会让某个调用点悄悄跑着另一套规则，而三个入口的输出各自都合法（设计 §2.1）。"""
    signals: list[dict] = []
    for strat in strategies:
        for sym, df in bars.items():
            pos = target_positions(df, strat, overlays)
            if len(pos) < 2:
                continue
            prev_p, cur = int(pos.iloc[-2]), int(pos.iloc[-1])
            if cur == prev_p:
                continue
            signals.append({
                "date": df.index[-1].strftime("%Y-%m-%d"),
                "symbol": sym,
                "strategy": strat.name,
                "action": "BUY" if cur > prev_p else "SELL",
                "close": float(df["close"].iloc[-1]),
            })
    return signals
