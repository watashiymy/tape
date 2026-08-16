"""交易成本（spec 决策5）：佣金双边收、印花税仅卖出且按成交日分段。滑点在引擎撮合价中体现。"""
from __future__ import annotations

from datetime import date

from quant.config import Costs


def commission(notional: float, cfg: Costs) -> float:
    return max(notional * cfg.commission_rate, cfg.commission_min)


def stamp_tax(notional: float, d: date, cfg: Costs) -> float:
    return notional * cfg.stamp_rate(d)
