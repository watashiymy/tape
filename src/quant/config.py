"""配置加载：settings.yaml → 不可变数据类。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml


def _to_date(v) -> date:
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
        for r in self.stamp_tax:
            if r.until is not None and d <= r.until:
                return r.rate
            if r.frm is not None and d >= r.frm:
                return r.rate
        raise ValueError(f"没有覆盖 {d} 的印花税规则")


@dataclass(frozen=True)
class Settings:
    universe: list[str]
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
        universe=[str(s) for s in raw["universe"]],
        benchmark=str(raw["benchmark"]),
        start=_to_date(raw["backtest"]["start"]),
        capital=float(raw["backtest"]["capital"]),
        costs=costs,
        strategies={k: dict(v) for k, v in (raw.get("strategies") or {}).items()},
    )
