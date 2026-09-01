"""时序动量（v0.4.0 M3 设计 §3.2）：过去 lookback 根的收益为正就持有。

依据 Moskowitz/Ooi/Pedersen (2012)《Time Series Momentum》：跨 58 个品种、上百年
数据成立的效应，是本项目三个策略里**证据最强**的入场信号（双均线与唐奇安是经典
教学样品）。规则只有一条，没有均线、没有通道、没有量能条件——刻意如此：
参数越少，越不容易把"在这段历史上刚好合适"当成"有效"。

与另两个策略的关系：双均线看两条均线的相对位置、唐奇安看突破，两者都是**价格路径**
的形状；时序动量只看**两个时点的价差**（今天 vs 一年前），中间怎么走一概不问。
"""
from __future__ import annotations

import pandas as pd

from quant.strategy.base import Strategy


class TSMomentum(Strategy):
    name = "tsmom"                # 内部键，已进 REGISTRY / SOURCES，永不改
    label = "时序动量"            # 学术界通行译名（time series momentum），不自创

    def __init__(self, lookback: int = 250):
        """lookback 默认 250 根 ≈ 12 个月（A 股一年约 242~250 个交易日）。

        构造期校验，用 raise 而非 assert（-O 下 assert 会被剥除）。不校验的代价
        全是静默的：`lookback=0` 时 `shift(0)` 就是拿自己跟自己比，收益恒为 0，
        判据又是严格 >，于是**全程空仓**、回测零告警；`True` 会因
        `isinstance(True, int)` 通过类型检查并静默变成 `shift(1)`，
        "12 个月动量"悄悄变成"昨天涨没涨"；浮点/字符串则迟至 `shift()` 才崩，
        报错离病因很远。
        """
        if isinstance(lookback, bool) or not isinstance(lookback, int) or lookback < 1:
            raise ValueError(f"参数 lookback 必须是不小于 1 的整数，实际为 {lookback!r}")
        self.lookback = lookback

    def generate_positions(self, df: pd.DataFrame) -> pd.Series:
        """持有 ⟺ `adj_close / adj_close.shift(lookback) − 1 > 0`。

        用**后复权价**：动量比的是相隔 lookback 根的两个价格，除权缺口恰好落在这种
        跨期比较里——10 送 10 之后原始价腰斩，用 raw 会把一次分红读成 −50% 的动量
        （spec 决策1）。adj_close 天然连续，无需特殊处理，但这条有测试钉着。

        暖机期（前 lookback 根 shift 出 NaN）：NaN 参与比较为 False → 0，如实空仓。
        判据是严格 > 0：一年白干不算"涨过"。
        """
        c = df["adj_close"]
        return (c / c.shift(self.lookback) - 1.0 > 0).astype(int)
