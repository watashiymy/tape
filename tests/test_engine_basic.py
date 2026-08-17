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


def _mk(dates, prices, factor=1.0):
    """按给定原始价造日线；adj_* = 原始价 × factor（factor≠1 时二者可区分）。"""
    df = make_bars([dict(date=d, open=p, high=p * 1.02, low=p * 0.98, close=p,
                         volume=1e6, amount=1e7, adj_factor=factor)
                    for d, p in zip(dates, prices)])
    for c in ("open", "high", "low", "close"):
        df["adj_" + c] = df[c] * factor
    return df


D3 = ["2024-01-02", "2024-01-03", "2024-01-04"]


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
    assert s.holding_days == 5   # 01-03 买入 → 01-08 卖出，自然日

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


def test_budget_is_fixed_and_does_not_compound_after_a_winning_round():
    """spec §8：每只资金上限 = **初始本金**/N，固定额度，不随权益浮动。
    500,000 单标的，价 10 买入 → 价 20 卖出（翻倍），再次建仓时**仍只能动用 500,000**，
    赚到的部分留作闲置现金，不参与下一轮建仓。

    01-03 开 10.0：px=10.01，499 手=49,900 股，成交额 499,499.0，佣金 124.87475，
                   现金余 376.12525。
    01-05 开 20.0：px=19.98，成交额 997,002.0，佣金 249.2505，印花税 498.501，
                   净得 996,254.2485 → 现金 996,630.37375（≈ 本金的 1.99 倍）。
    01-08 开 20.0：px=20.02，可动用额度 = min(996,630.37375, 500,000) = 500,000
                   → 249 手=24,900 股，成交额 498,498.0，佣金 124.6245，
                   现金余 996,630.37375-498,498-124.6245 = 498,007.74925。
    若额度随权益复利（slot 现金全吃），第二次会买成 49,700 股、投入 994,994.0，
    相当于把固定额度放大到 1.99 倍，系统性虚增此后全部收益与回撤。
    末日净值 = 498,007.74925 + 24,900×20.0 = 996,007.74925。"""
    dates = ["2024-01-02", "2024-01-03", "2024-01-04",
             "2024-01-05", "2024-01-08", "2024-01-09"]
    df = _mk(dates, [10.0, 10.0, 10.0, 20.0, 20.0, 20.0])
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 1, 0, 1, 1, 1])},
                     settings(capital=500_000)).run()

    buys = [t for t in res.trades if t.action == "buy"]
    assert len(buys) == 2
    b2 = buys[1]
    assert b2.date == pd.Timestamp("2024-01-08")
    assert b2.shares == 24_900                      # 复利膨胀时会是 49,700
    assert b2.price == pytest.approx(20.02)
    assert b2.commission == pytest.approx(124.6245)
    # 单次建仓投入（含佣金）不得超过固定额度
    assert b2.shares * b2.price + b2.commission <= 500_000
    assert res.equity.iloc[-1] == pytest.approx(996_007.74925)


def test_each_symbol_gets_independent_budget():
    """每标的独立额度 = 本金/N，互不挪用。本金 50 万、2 个标的 → 每腿 25 万。
    A(开 10.0)：px=10.01，25 万够 249 手 → 24,900 股，成交额 249,249.0，佣金 62.31225。
    B(开 20.0)：px=20.02，25 万够 124 手 → 12,400 股，成交额 248,248.0，佣金 62.062。
    若额度错写成全额本金，A 会买成 49,900 股（两腿合计 2 倍杠杆）。"""
    a, b = _mk(D3, [10.0] * 3), _mk(D3, [20.0] * 3)
    bt = Backtester({"A": a, "B": b},
                    {"A": _positions(a, [1, 1, 1]), "B": _positions(b, [1, 1, 1])},
                    settings(capital=500_000, universe=("A", "B")))
    res = bt.run()
    ta = next(t for t in res.trades if t.symbol == "A")
    tb = next(t for t in res.trades if t.symbol == "B")
    assert ta.shares == 24_900 and tb.shares == 12_400
    assert ta.commission == pytest.approx(62.31225)
    assert tb.commission == pytest.approx(62.062)
    # 两腿现金余额独立：A 余 250,000-249,249-62.31225，B 余 250,000-248,248-62.062
    assert res.equity.iloc[-1] == pytest.approx(688.68775 + 24_900 * 10.0
                                                + 1_689.938 + 12_400 * 20.0)


def test_fill_uses_raw_price_not_adjusted():
    """撮合/整手/成本一律用原始价，后复权价只喂信号。
    adj_factor=3 时 adj_open=30.0，但成交必须是 10.0×1.001=10.01、49,900 股；
    若误用 adj_open 则会变成 30.03 元、16,600 股。"""
    df = _mk(D3, [10.0] * 3, factor=3.0)
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 0, 0])}, settings()).run()
    b, s = res.trades[0], res.trades[1]
    assert b.action == "buy" and s.action == "sell"
    assert b.price == pytest.approx(10.01)
    assert b.shares == 49_900
    # 卖出同样用原始价：10.0×0.999=9.99（误用 adj 则为 29.97）
    assert s.price == pytest.approx(9.99)
    assert s.commission == pytest.approx(124.62525)
    assert s.stamp == pytest.approx(249.2505)


def test_commission_never_overdraws_the_budget():
    """额度 100,100：整手向下取整得 100 手(10,000 股)，成交额 100,100.0，
    但加佣金 25.025 就透支了 → 必须退到 99 手(9,900 股)，成交额 99,099.0、佣金 24.77475。
    少了这一步现金会变成 -25.025（净值凭空多算一笔钱）。"""
    df = _mk(D3, [10.0] * 3)
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 1, 1])},
                     settings(capital=100_100)).run()
    b = res.trades[0]
    assert b.shares == 9_900
    assert b.commission == pytest.approx(24.77475)
    # 现金余 100,100-99,099-24.77475 = 976.22525，绝不为负
    assert res.equity.iloc[-1] == pytest.approx(976.22525 + 9_900 * 10.0)


def test_engine_charges_the_minimum_commission_on_both_sides():
    """小额成交双边都按 5 元最低佣金收，而非按费率。额度 2,100、价 10.0：
    买 200 股，成交额 2,002.0，费率佣金仅 0.5005 → 实收 5.0，现金余 93.0；
    卖 200 股 px=9.99，成交额 1,998.0，费率佣金 0.4995 → 实收 5.0，印花税 0.999，
    净得 1,992.001，pnl = 1,992.001 - 2,007.0 = -14.999，末日净值 2,085.001。
    去掉最低佣金后净值会虚高到 2,094.001。"""
    df = _mk(D3, [10.0] * 3)
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 0, 0])},
                     settings(capital=2_100)).run()
    b, s = res.trades[0], res.trades[1]
    assert b.shares == 200
    assert b.commission == pytest.approx(5.0)
    assert s.commission == pytest.approx(5.0)
    assert s.stamp == pytest.approx(0.999)
    assert s.pnl == pytest.approx(-14.999)
    assert res.equity.iloc[-1] == pytest.approx(2_085.001)


def test_existing_position_is_never_rebought():
    """已持仓时 want=1 必须什么都不做。若去掉 `slot.shares == 0` 守卫，
    价格从 100 跌到 40 会用剩余现金再买 200 股并把 slot.shares 从 4,900 **覆盖**掉，
    账面凭空蒸发 4,700 股。
    01-03 开 100.0：px=100.1，49 手=4,900 股，成交额 490,490.0，佣金 122.6225，
                    现金余 9,387.3775。
    01-04 开 40.0：已持仓 → 不动。末日净值 = 9,387.3775 + 4,900×40.0 = 205,387.3775
                   （无守卫时只剩 374.3775 + 200×40 = 8,374.3775）。"""
    df = _mk(D3, [100.0, 100.0, 40.0])
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 1, 1])},
                     settings(capital=500_000)).run()
    assert len(res.trades) == 1
    b = res.trades[0]
    assert b.date == pd.Timestamp("2024-01-03")
    assert b.shares == 4_900
    assert b.commission == pytest.approx(122.6225)
    assert res.equity.iloc[-1] == pytest.approx(9_387.3775 + 4_900 * 40.0)
