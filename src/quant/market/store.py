"""收盘数据的磁盘布局（spec §5.5）：data/market/{daily/YYYY-MM-DD.parquet, breadth.parquet,
calendar.parquet, gaps.json}。全部可再生、不进 git。

写盘一律原子（mkstemp + os.replace，同 data/cache.py）。删除只碰 daily/ 里文件名严格是
日期的 .parquet，且只在日历完整时（由调用方保证）执行。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Iterable
from datetime import date
from pathlib import Path

import pandas as pd

from quant.market.session import Calendar

_DATED = re.compile(r"^(\d{4}-\d{2}-\d{2})\.parquet$")
SNAPSHOT_COLUMNS = ["code", "close", "preclose", "tradestatus", "isST"]


def _atomic_write(path: Path, write) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    os.close(fd)
    try:
        write(tmp)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class MarketStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.daily_dir = self.root / "daily"
        self.breadth_path = self.root / "breadth.parquet"
        self.calendar_path = self.root / "calendar.parquet"
        self.gaps_path = self.root / "gaps.json"

    # —— 快照
    def snapshot_path(self, day: date) -> Path:
        return self.daily_dir / f"{day.isoformat()}.parquet"

    def snapshot_dates(self) -> list[date]:
        if not self.daily_dir.is_dir():
            return []
        out = []
        for p in self.daily_dir.iterdir():
            m = _DATED.match(p.name)
            if m:
                out.append(date.fromisoformat(m.group(1)))
        return sorted(out)

    def save_snapshot(self, day: date, df: pd.DataFrame) -> None:
        _atomic_write(self.snapshot_path(day), lambda tmp: df[SNAPSHOT_COLUMNS].to_parquet(tmp, index=False))

    def load_snapshot(self, day: date) -> pd.DataFrame:
        return pd.read_parquet(self.snapshot_path(day))

    def snapshot_rows(self, day: date) -> int:
        """只读 parquet 元数据拿行数（98% 规则要比相邻快照，不必把整份读进来）。"""
        import pyarrow.parquet as pq
        return pq.ParquetFile(self.snapshot_path(day)).metadata.num_rows

    def neighbour_rows(self, day: date) -> int | None:
        """相邻两份已存快照（前一份、后一份）里行数较小的那个；一份都没有 → None。"""
        dates = self.snapshot_dates()
        before = [d for d in dates if d < day]
        after = [d for d in dates if d > day]
        rows = [self.snapshot_rows(d) for d in ([before[-1]] if before else []) + ([after[0]] if after else [])]
        return min(rows) if rows else None

    def clean_tmp(self) -> int:
        n = 0
        for d in (self.root, self.daily_dir):
            if d.is_dir():
                for p in d.glob("*.tmp"):
                    p.unlink()
                    n += 1
        return n

    # —— 日历
    def save_calendar(self, days: Iterable[date], trading: Iterable[date]) -> None:
        trading = set(trading)
        df = pd.DataFrame({"date": list(days)})
        df["is_trading"] = df["date"].isin(trading)
        _atomic_write(self.calendar_path, lambda tmp: df.to_parquet(tmp, index=False))

    def load_calendar(self) -> Calendar | None:
        if not self.calendar_path.is_file():
            return None
        try:
            df = pd.read_parquet(self.calendar_path)
        except (OSError, ValueError):
            return None
        days = [pd.Timestamp(d).date() for d in df["date"]]
        trading = frozenset(d for d, t in zip(days, df["is_trading"]) if bool(t))
        return Calendar(first=min(days), last=max(days), trading=trading)

    # —— 永久缺口
    def _gaps(self) -> dict:
        if not self.gaps_path.is_file():
            return {}
        try:
            return json.loads(self.gaps_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def record_failure(self, day: date, failed_on: date) -> None:
        doc = self._gaps()
        entry = doc.setdefault(day.isoformat(), {"failed_on": [], "permanent": False})
        if failed_on.isoformat() not in entry["failed_on"]:
            entry["failed_on"].append(failed_on.isoformat())
        entry["permanent"] = len(entry["failed_on"]) >= 2
        _atomic_write(self.gaps_path, lambda tmp: Path(tmp).write_text(
            json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8"))

    def permanent_gaps(self) -> set[date]:
        return {date.fromisoformat(k) for k, v in self._gaps().items() if v.get("permanent")}

    # —— 指标表
    def save_breadth(self, df: pd.DataFrame) -> None:
        _atomic_write(self.breadth_path, lambda tmp: df.to_parquet(tmp, index=False))

    def load_breadth(self) -> pd.DataFrame | None:
        if not self.breadth_path.is_file():
            return None
        try:
            return pd.read_parquet(self.breadth_path)
        except (OSError, ValueError):
            return None

    # —— 保留规则
    def apply_retention(self, window_first: date) -> list[date]:
        """删 daily/ 里早于窗口首日的快照。只碰文件名严格匹配日期的 .parquet。"""
        removed = []
        for day in self.snapshot_dates():
            if day < window_first:
                self.snapshot_path(day).unlink()
                removed.append(day)
        return removed


def calendar_complete(days: list[date], trading: frozenset[date], today: date,
                      last_done: date, need: int) -> str | None:
    """§5.2「日历完整」：连续不缺日、末日 ≥ 今天、last_done 及之前至少 need 个交易日。
    返回 None 表示完整，否则返回原因。"""
    if not days:
        return "日历为空"
    days = sorted(days)
    for a, b in zip(days, days[1:]):
        if (b - a).days != 1:
            return f"日历缺日：{a} 与 {b} 之间"
    if days[-1] < today:
        return f"日历末日 {days[-1]} 早于今天 {today}"
    n = sum(1 for d in trading if days[0] <= d <= last_done)    # 只数落在日历覆盖范围内的
    if n < need:
        return f"截至 {last_done} 只有 {n} 个交易日，不足 {need}"
    return None
