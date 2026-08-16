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
        df.to_parquet(self._path(symbol))

    @staticmethod
    def merge(old: pd.DataFrame | None, new: pd.DataFrame) -> pd.DataFrame:
        if old is None or old.empty:
            return new.sort_index()
        merged = pd.concat([old, new])
        merged = merged[~merged.index.duplicated(keep="last")]
        return merged.sort_index()
