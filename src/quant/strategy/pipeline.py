"""信号流水线：目标仓位的**唯一出口**（v0.4.0 M2 设计 §2）。

在此之前，三个入口各自直调 `strat.generate_positions(df)`：
`scripts/run_backtest.py`、`src/quant/signal/market_scan.py`、`src/quant/signal/scan.py`。
叠加层（止损、趋势过滤）若逐处接入，漏掉任何一处就是"扫描说买、回测按另一套规则
算、每日信号又是第三套"——三边都输出一串合法仓位，谁也不报错，正是本项目一路在防
的"不报错但结论错"。所以先收拢，再叠加：**入口层不许再出现 generate_positions(**
（tests/test_strategy_pipeline.py 有源码级断言钉着）。
"""
from __future__ import annotations

import pandas as pd

from quant.config import OverlaysCfg
from quant.indicators import atr
from quant.strategy.base import Strategy


def target_positions(df: pd.DataFrame, strategy: Strategy,
                     overlays: OverlaysCfg) -> pd.Series:
    """基础信号 → 叠加层 → 最终目标仓位（与 df.index 对齐的 int {0,1}）。

    `overlays` 是**必填**参数，没有"忘了传就当没开"的缺省：那种缺省正是这次要
    消灭的分叉——某个入口漏传，它就悄悄跑着另一套规则。要关就显式传
    `OverlaysCfg()`（全关，且此时本函数是恒等变换）。

    叠加顺序（M3 加入趋势过滤后）：**先 trend_filter 再 atr_stop**——先决定"这个
    环境能不能持有"，再管"持有之后何时认输"。
    """
    pos = strategy.generate_positions(df)
    if overlays.atr_stop.enabled:
        pos = atr_trailing_stop(df, pos, n=overlays.atr_stop.n, k=overlays.atr_stop.k)
    return pos


def atr_trailing_stop(df: pd.DataFrame, base: pd.Series, *, n: int, k: float) -> pd.Series:
    """ATR 追踪止损（海龟法则变体，全部用后复权价）。

    语义三条，逐条都有专测（tests/test_strategy_pipeline.py）：

    1. 持有期间维护 `peak = 入场以来最高 adj_close`；当日
       `adj_close < peak − k × ATR(n)` → 目标仓位归 0。归 0 落在**触发当根**
       （T 收盘），引擎既有的 `desired = positions.shift(1)` 使其 T+1 开盘成交，
       与所有既有信号同一时序——止损没有自己的特权时序，也就没有未来函数。
       判据是严格 `<`：回撤恰好等于阈值不触发。
    2. 止损后**保持空仓，直到基础策略给出新的 0→1 入场**。基础信号一直是 1 不算：
       否则止损次日立刻回补，买回的还是那只刚跌破止损线的票，止损形同虚设。
       这条是本函数必须带状态（而不是逐根重算一个布尔掩码）的全部原因。
    3. ATR 暖机期（`rolling(n)` 还给不出值）不触发——算不出来就不装算得出来。
       这里显式判 `notna` 而不是依赖"NaN 参与比较恒为 False"的副作用：后者一旦
       被改写成等价的否定形式（`not (close >= thr)`）就会静默反转成"暖机期全止损"。

    逐根循环而非向量化：`peak` 依赖"上一次入场在哪儿"，而那又依赖止损是否已发生，
    互相递归。唐奇安的持仓状态机同样是逐根写的（见 donchian.py），口径一致。
    """
    close = df["adj_close"]
    band = k * atr(df, n)
    held = False        # overlay 之后是否持有
    blocked = False     # 止损后的封锁：等基础信号的新 0→1 才解除
    peak = 0.0
    out = [0] * len(df)
    prev_base = 0
    for i in range(len(df)):
        cur_base = int(base.iloc[i])
        if cur_base == 0:
            held, blocked = False, False    # 基础信号自己离场，封锁随之作废
        else:
            if prev_base == 0:              # 基础信号的新入场 → 解除封锁
                blocked = False
            if not blocked:
                c = float(close.iloc[i])
                peak = c if not held else max(peak, c)
                held = True
                b = band.iloc[i]
                if pd.notna(b) and c < peak - b:
                    held, blocked = False, True
        out[i] = int(held)
        prev_base = cur_base
    return pd.Series(out, index=df.index, dtype=int, name=base.name)
