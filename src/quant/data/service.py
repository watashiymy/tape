"""DataService：cache 优先、增量拉取、经 prepare_bars 清洗后交付。"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from quant.data.cache import BarCache
from quant.data.pipeline import prepare_bars
from quant.data.provider import DataProvider

OVERLAP_DAYS = 5  # 增量取数时回拉的重叠天数，用于覆盖盘中运行留下的未收盘 bar


class DataService:
    def __init__(self, provider: DataProvider, cache: BarCache):
        self.provider = provider
        self.cache = cache

    def get_bars(self, symbol: str, start: date, end: date | None = None,
                 refresh: bool = False) -> tuple[pd.DataFrame, list[str]]:
        end = end or date.today()
        cached = None if refresh else self.cache.load(symbol)
        if cached is None or cached.empty:
            fetch_start = start
        else:
            # 回拉 OVERLAP_DAYS 天重叠，而不是从"最新日+1天"开始。
            # 原因：若曾在交易日盘中运行过，当天那根**未收盘**的 K 线会被写进缓存；
            # 用"最新日+1天"会永远跳过它，这根错误的 bar 将永久污染此后所有回测且无告警。
            # 重叠重拉让 merge 的 keep="last" 自动修正，代价只是每次多几行网络数据。
            fetch_start = max(start, cached.index.max().date() - timedelta(days=OVERLAP_DAYS))
        if fetch_start <= end:
            new = self.provider.get_daily_bars(symbol, fetch_start, end)
            merged = self.cache.merge(cached, new)  # merge 自己会处理 new 为空
            self.cache.save(symbol, merged)
        else:
            merged = cached
        if merged is None or merged.empty:
            raise ValueError(f"{symbol}: 无可用数据（{start}~{end}）")
        df, warns = prepare_bars(merged)
        return df[df.index.date >= start], warns
