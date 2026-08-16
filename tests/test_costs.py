from datetime import date

import pytest

from quant.backtest.costs import commission, stamp_tax
from quant.config import Costs, StampTaxRule

CFG = Costs(
    commission_rate=0.00025, commission_min=5.0, slippage=0.001,
    stamp_tax=(StampTaxRule(rate=0.001, until=date(2023, 8, 27)),
               StampTaxRule(rate=0.0005, frm=date(2023, 8, 28))),
)


def test_commission_normal():
    assert commission(100_000, CFG) == pytest.approx(25.0)   # 10万 × 万2.5


def test_commission_min_5():
    assert commission(10_000, CFG) == pytest.approx(5.0)     # 2.5 元 → 按最低 5 元


def test_stamp_tax_segmented():
    assert stamp_tax(100_000, date(2023, 8, 25), CFG) == pytest.approx(100.0)  # 0.1%
    assert stamp_tax(100_000, date(2023, 8, 29), CFG) == pytest.approx(50.0)   # 0.05%
