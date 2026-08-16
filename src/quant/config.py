"""配置加载：settings.yaml → 不可变数据类。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import yaml


def _to_date(v) -> date:
    # datetime 必须先于 date 判断：isinstance(datetime_obj, date) 为 True，
    # 漏判会让 datetime 一路流到回测循环里才炸（date 与 datetime 无法比较）。
    if isinstance(v, datetime):
        return v.date()
    return v if isinstance(v, date) else date.fromisoformat(str(v))


@dataclass(frozen=True)
class StampTaxRule:
    rate: float
    until: date | None = None  # 含当日
    frm: date | None = None    # 含当日


@dataclass(frozen=True)
class Costs:
    commission_rate: float
    commission_min: float
    slippage: float
    stamp_tax: tuple[StampTaxRule, ...]

    def stamp_rate(self, d: date) -> float:
        """取 d 当日适用的印花税率。语义是"该日期是否落在本段区间内"（与，不是或）。

        不可写成"满足任一边界就返回"——那样一旦追加第三段（税率再次调整时的
        自然改法），带 frm 的那段会吞掉其后所有日期，静默返回旧税率，
        而错误税率会污染每一次回测且永不报错。当前写法与声明顺序无关。
        """
        for r in self.stamp_tax:
            if r.frm is not None and d < r.frm:
                continue        # 尚未生效
            if r.until is not None and d > r.until:
                continue        # 已经失效
            return r.rate
        raise ValueError(f"没有覆盖 {d} 的印花税规则")


@dataclass(frozen=True)
class Settings:
    universe: tuple[str, ...]  # 用 tuple 而非 list：frozen 只挡重新赋值，挡不住 list 原地修改
    benchmark: str
    start: date
    capital: float
    costs: Costs
    strategies: dict[str, dict]


def load_settings(path: str | Path) -> Settings:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    rules = tuple(
        StampTaxRule(
            rate=float(item["rate"]),
            until=_to_date(item["until"]) if "until" in item else None,
            frm=_to_date(item["from"]) if "from" in item else None,
        )
        for item in raw["costs"]["stamp_tax"]
    )
    costs = Costs(
        commission_rate=float(raw["costs"]["commission_rate"]),
        commission_min=float(raw["costs"]["commission_min"]),
        slippage=float(raw["costs"]["slippage"]),
        stamp_tax=rules,
    )
    return Settings(
        universe=tuple(str(s) for s in raw["universe"]),
        benchmark=str(raw["benchmark"]),
        start=_to_date(raw["backtest"]["start"]),
        capital=float(raw["backtest"]["capital"]),
        costs=costs,
        strategies={k: dict(v) for k, v in (raw.get("strategies") or {}).items()},
    )
