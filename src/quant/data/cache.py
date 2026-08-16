"""行情本地缓存：每标的一个 parquet 文件（spec 决策9）。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd


class BarCache:
    def __init__(self, cache_dir: str | Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, symbol: str) -> Path:
        return self.cache_dir / f"{symbol}.parquet"

    def load(self, symbol: str) -> pd.DataFrame | None:
        p = self._path(symbol)
        if not p.exists():
            return None
        return pd.read_parquet(p)

    def save(self, symbol: str, df: pd.DataFrame) -> None:
        # 先写临时文件再原子替换：刷新 10 只标的时按 Ctrl-C 不会留下半截 parquet
        tmp = self._path(symbol).with_suffix(".parquet.tmp")
        df.to_parquet(tmp)
        tmp.replace(self._path(symbol))

    @staticmethod
    def merge(old: pd.DataFrame | None, new: pd.DataFrame) -> pd.DataFrame:
        if old is None or old.empty:
            return new.sort_index()
        if new is None or new.empty:
            # 必须挡：pandas 3.0 的 concat 不再忽略空块的 dtype，
            # 拼一张空表会把 9 列全部污染成 object，而后续算术照样不报错
            return old.sort_index()
        if set(old.columns) != set(new.columns):
            # 静默合并会让缺失列变 NaN，且 prepare_bars 一条告警都不会发：
            # 指标全 NaN → 策略无信号 → 净值一条直线，全程无异常。必须响亮失败。
            raise ValueError(
                f"缓存列与新数据列不一致，请删除缓存目录或用 refresh=True 重拉；"
                f"缓存独有={set(old.columns) - set(new.columns)}，"
                f"新数据独有={set(new.columns) - set(old.columns)}")
        merged = pd.concat([old, new])
        merged = merged[~merged.index.duplicated(keep="last")]  # 重叠日期以新数据为准
        return merged.sort_index()
