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
from quant.indicators import atr, ma
from quant.strategy.base import Strategy


def target_positions(df: pd.DataFrame, strategy: Strategy,
                     overlays: OverlaysCfg) -> pd.Series:
    """基础信号 → 叠加层 → 最终目标仓位（与 df.index 对齐的 int {0,1}）。

    `overlays` 是**必填**参数，没有"忘了传就当没开"的缺省：那种缺省正是这次要
    消灭的分叉——某个入口漏传，它就悄悄跑着另一套规则。要关就显式传
    `OverlaysCfg()`（全关，且此时本函数是恒等变换）。

    叠加顺序：**先 trend_filter 再 atr_stop**——先决定"这个环境能不能持有"，
    再管"持有之后何时认输"。顺序不是口味问题（tests 里有专测钉着）：反过来的话，
    止损的状态机会替一段**本来就不该持有**的下跌认一次输，而"止损后保持空仓直到
    基础策略新的 0→1"这条封锁只看基础信号——基础信号一直是 1 的策略（均线仍多头
    排列）从此被永久拉黑，趋势恢复后 gate 重新放行的那次入场再也不会出现。
    两种顺序都输出一串合法仓位，没有任何一处报错。
    """
    pos = strategy.generate_positions(df)
    if overlays.trend_filter.enabled:
        pos = (pos.astype(bool) & trend_gate(df, overlays.trend_filter.n)).astype(int) \
            .rename(pos.name)
    if overlays.atr_stop.enabled:
        pos = atr_trailing_stop(df, pos, n=overlays.atr_stop.n, k=overlays.atr_stop.k)
    return pos


def trend_gate(df: pd.DataFrame, n: int) -> pd.Series:
    """趋势过滤闸门（v0.4.0 M3 设计 §3.1，Faber 风格）：`adj_close > MA(n)`。

    返回布尔序列，`target_positions` 用它与基础信号取 AND。语义是"价格在长期均线
    下方时不持有多头"——**既挡入场也强制出场**（跌破就离场，不等基础策略反应）。

    三个细节，每个都有专测：

    1. 判据是**严格 >**：收盘恰好等于均线不算站上去。
    2. 一律用后复权价 `adj_close`（与两个策略、与止损同一口径）。均线比较本身是
       尺度不变的，但窗口**跨除权日**时 raw 的几根价来自两个尺度，会凭空判出
       "跌破均线"。
    3. 暖机期（`rolling(n)` 还给不出值的前 n−1 根）gate=False——算不出来就不装
       算得出来，策略自然不入场。这里显式判 `notna` 而不是依赖"NaN 参与比较恒为
       False"的副作用：后者一旦被改写成等价的否定形式（`~(close <= ma)`）就会
       静默反转成"暖机期全程放行"，而那正是历史最短、最不该放行的一段。
    """
    close = df["adj_close"]
    line = ma(close, n)
    return (line.notna() & (close > line)).rename("trend_gate")


def atr_trailing_stop(df: pd.DataFrame, base: pd.Series, *, n: int, k: float) -> pd.Series:
    """ATR 追踪止损（海龟法则变体，全部用后复权价）。

    语义四条，逐条都有专测（tests/test_strategy_pipeline.py）：

    1. 持有期间维护 `peak = 入场以来最高 adj_close`；当日
       `adj_close < peak − k × ATR(n)` → 目标仓位归 0。归 0 落在**触发当根**
       （T 收盘），引擎既有的 `desired = positions.shift(1)` 使其 T+1 开盘成交，
       与所有既有信号同一时序——止损没有自己的特权时序，也就没有未来函数。
       判据是严格 `<`：回撤恰好等于阈值不触发。
    2. 止损后**保持空仓，直到基础策略给出新的 0→1 入场**。基础信号一直是 1 不算：
       否则止损次日立刻回补，买回的还是那只刚跌破止损线的票，止损形同虚设。
       这条是本函数必须带状态（而不是逐根重算一个布尔掩码）的全部原因。
       解锁只写一处：基础信号回 0 时清掉封锁。此后的每个 1 都必然是一次 0→1，
       所以**不需要**再单独判"上一根 base 是 0 就解锁"——那条分支与这里逐位等价
       （穷举 3 条价格路径 × 2^11 基础信号 × 3 组参数 = 18432 组核对，0 组不同），
       而一个任何测试都分不开的分支只会烂在那儿：改坏了没人红。
    3. ATR 暖机期（`rolling(n)` 还给不出值）不触发——算不出来就不装算得出来。
       这里显式判 `notna` 而不是依赖"NaN 参与比较恒为 False"的副作用：后者一旦
       被改写成等价的否定形式（`not (close >= thr)`）就会静默反转成"暖机期全止损"。
    4. peak **每次入场重置**（`c if not held`），绝不沿用被止损那一轮的旧高点、
       也不是"全样本滚动最高"。写错的表现只是"止损好像变紧了一点"：止损线钉在
       早已作废的旧高点上，新一轮刚入场就带着一大截凭空的"回撤"。
       tests 里那条**二次入场价落在旧 peak 之下**的用例是全套件唯一分得开两种口径
       的（入场价高于旧 peak 时 `max(旧peak, c) == c`，两种写法逐位一致）。

    逐根循环而非向量化：`peak` 依赖"上一次入场在哪儿"，而那又依赖止损是否已发生，
    互相递归。唐奇安的持仓状态机同样是逐根写的（见 donchian.py），口径一致。
    """
    close = df["adj_close"]
    band = k * atr(df, n)
    held = False        # overlay 之后是否持有
    blocked = False     # 止损后的封锁：等基础信号的新 0→1 才解除
    peak = 0.0
    out = [0] * len(df)
    for i in range(len(df)):
        if int(base.iloc[i]) == 0:
            # 基础信号自己离场 → 封锁随之作废。解锁只有这一处（见文档串第 2 条）：
            # 此后的每个 1 都必然是一次 0→1，无需再单独判"上一根是 0"。
            held, blocked = False, False
        elif not blocked:
            c = float(close.iloc[i])
            peak = c if not held else max(peak, c)   # 每次入场重置 peak，不跨轮沿用
            held = True
            b = band.iloc[i]
            if pd.notna(b) and c < peak - b:
                held, blocked = False, True
        out[i] = int(held)
    return pd.Series(out, index=df.index, dtype=int, name=base.name)
