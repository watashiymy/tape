"""行情本地缓存：每标的一个 parquet 文件（spec 决策9）+ 一个 meta.json 记录已覆盖区间。"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pandas as pd


class BarCache:
    def __init__(self, cache_dir: str | Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, symbol: str) -> Path:
        return self.cache_dir / f"{symbol}.parquet"

    def _meta_path(self, symbol: str) -> Path:
        return self.cache_dir / f"{symbol}.meta.json"

    def load_meta(self, symbol: str) -> dict:
        """已取数区间等元信息。缺文件返回 {}（老缓存自动降级为全量重拉一次后自愈）。"""
        p = self._meta_path(symbol)
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            # 原生报错不带文件名，10 只循环里没法定位删哪个。必须报出 symbol + 路径。
            raise RuntimeError(f"{symbol} 缓存文件损坏: {p}，删除该文件后重跑即可自动重拉") from e

    def save_meta(self, symbol: str, meta: dict) -> None:
        fd, tmp = tempfile.mkstemp(dir=self.cache_dir, prefix=f"{symbol}.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(meta))
        os.replace(tmp, self._meta_path(symbol))

    def load(self, symbol: str) -> pd.DataFrame | None:
        p = self._path(symbol)
        if not p.exists():
            return None
        try:
            return pd.read_parquet(p)
        except Exception as e:
            # 损坏 parquet 的原生报错只有 '<Buffer>'，不含文件名——必须报出 symbol + 路径。
            raise RuntimeError(f"{symbol} 缓存文件损坏: {p}，删除该文件后重跑即可自动重拉") from e

    def save(self, symbol: str, df: pd.DataFrame) -> None:
        # 先写临时文件再原子替换：刷新 10 只标的时按 Ctrl-C 不会留下半截 parquet。
        # 临时名必须进程唯一（mkstemp），不能固定为 <symbol>.parquet.tmp：
        # 回测与面板/信号脚本共用 data/cache，两进程互抢共享 tmp 时一方 FileNotFoundError，
        # 还存在互相截断写入后把混写内容 rename 成正式文件的窗口。
        fd, tmp = tempfile.mkstemp(dir=self.cache_dir, prefix=f"{symbol}.", suffix=".tmp")
        os.close(fd)
        df.to_parquet(tmp)
        os.replace(tmp, self._path(symbol))

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
