from datetime import date

import pandas as pd
import pytest

from quant.backtest.engine import Backtester
from quant.config import Costs, Settings, StampTaxRule
from tests.conftest import make_bars

COSTS = Costs(
    commission_rate=0.00025, commission_min=5.0, slippage=0.001,
    stamp_tax=(StampTaxRule(rate=0.001, until=date(2023, 8, 27)),
               StampTaxRule(rate=0.0005, frm=date(2023, 8, 28))),
)


def settings(capital=500_000, universe=("TEST",)):
    return Settings(universe=tuple(universe), benchmark="000300",
                    start=date(2024, 1, 1), capital=capital, costs=COSTS, strategies={})


def _bars():
    rows = [
        dict(date="2024-01-02", open=10.0, high=10.5, low=9.8, close=10.0, volume=1e6, amount=1e7),
        dict(date="2024-01-03", open=10.0, high=10.6, low=9.9, close=10.5, volume=1e6, amount=1e7),
        dict(date="2024-01-04", open=11.0, high=11.5, low=10.8, close=11.2, volume=1e6, amount=1e7),
        dict(date="2024-01-05", open=12.0, high=12.2, low=11.7, close=12.0, volume=1e6, amount=1e7),
        dict(date="2024-01-08", open=12.0, high=12.1, low=11.5, close=11.8, volume=1e6, amount=1e7),
    ]
    df = make_bars(rows)
    for c in ("open", "high", "low", "close"):
        df["adj_" + c] = df[c]
    return df


def _positions(df, values):
    return pd.Series(values, index=df.index, dtype=int)


def test_buy_next_open_and_sell_next_open():
    """目标仓位 d1 收盘=1 → d2 开盘买入；d4 收盘=0 → d5 开盘卖出。全程手算：
    买入 d3(2024-01-03) 开盘？——不对：pos=[1,1,1,0,0] 的 desired=shift(1)=[0,1,1,1,0]，
    d2(01-03) 便是首个 want=1 日，以 10.0×1.001=10.01 成交。
    额度 500,000：499 手=49,900 股，成交额 499,499.0，佣金 max(124.87,5)=124.874750，
    现金余 500,000-499,499-124.87475=376.12525。
    d5(01-08) want=0：以 12.0×0.999=11.988 卖出 49,900 股，成交额 598,201.2，
    佣金 149.5503，印花税(2024→0.05%) 299.1006，净得 597,752.5491，
    pnl = 597,752.5491 - 499,623.87475 = 98,128.67435。"""
    df = _bars()
    bt = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 1, 1, 0, 0])}, settings())
    res = bt.run()

    buys = [t for t in res.trades if t.action == "buy"]
    sells = [t for t in res.trades if t.action == "sell"]
    assert len(buys) == 1 and len(sells) == 1

    b = buys[0]
    assert b.date == pd.Timestamp("2024-01-03")
    assert b.price == pytest.approx(10.01)
    assert b.shares == 49_900
    assert b.commission == pytest.approx(124.87475)

    s = sells[0]
    assert s.date == pd.Timestamp("2024-01-08")
    assert s.price == pytest.approx(11.988)
    assert s.stamp == pytest.approx(299.1006)
    assert s.pnl == pytest.approx(98_128.67435)

    # 最终净值 = 初始现金余量 + 卖出净得
    assert res.equity.iloc[-1] == pytest.approx(376.12525 + 597_752.5491)


def test_equity_marks_to_close_daily():
    """d2 买入后，当日净值按收盘价 10.5 估值：
    49,900×10.5 + 376.12525 = 524,326.12525。"""
    df = _bars()
    bt = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 1, 1, 0, 0])}, settings())
    res = bt.run()
    assert res.equity.loc["2024-01-03"] == pytest.approx(524_326.12525)
    # 未建仓日净值 = 全额现金
    assert res.equity.loc["2024-01-02"] == pytest.approx(500_000.0)


def test_insufficient_budget_is_skipped_not_crash():
    """茅台场景（评审问题1）：额度 5 万买不起 1 手 10 元×100 股？能。改成价格 600 元：
    1 手 6 万 > 5 万额度 → 跳过并记录，不崩溃、不部分成交。"""
    rows = [dict(date=f"2024-01-{d:02d}", open=600.0, high=610.0, low=590.0, close=600.0,
                 volume=1e6, amount=6e8) for d in (2, 3, 4)]
    df = make_bars(rows)
    for c in ("open", "high", "low", "close"):
        df["adj_" + c] = df[c]
    bt = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 1, 1])},
                    settings(capital=50_000))
    res = bt.run()
    assert res.trades == []
    assert any("资金不足" in r[2] for r in res.skipped)
    assert (res.equity == 50_000.0).all()
