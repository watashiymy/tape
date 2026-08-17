import pandas as pd
import pytest

from quant.backtest.engine import Backtester
from tests.conftest import make_bars
from tests.test_engine_basic import COSTS, settings, _positions


def _df(rows):
    df = make_bars(rows)
    for c in ("open", "high", "low", "close"):
        df["adj_" + c] = df[c] * df["adj_factor"]
    return df


def test_limit_up_blocks_buy_then_fills_next_day():
    """d2 相对 d1 收盘(10.0)高开 10% → 买入顺延；d3 正常开盘成交。"""
    df = _df([
        dict(date="2024-01-02", open=10.0, high=10.0, low=10.0, close=10.0, volume=1e6, amount=1e7),
        dict(date="2024-01-03", open=11.0, high=11.0, low=10.9, close=11.0, volume=1e6, amount=1e7),
        dict(date="2024-01-04", open=11.2, high=11.5, low=11.0, close=11.3, volume=1e6, amount=1e7),
    ])
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 1, 1])}, settings()).run()
    assert any("涨停" in r[2] for r in res.skipped)
    buys = [t for t in res.trades if t.action == "buy"]
    assert len(buys) == 1 and buys[0].date == pd.Timestamp("2024-01-04")


def test_one_word_limit_board_blocks_buy():
    """一字涨停（high==low 且收盘高于昨收）也不可买。"""
    df = _df([
        dict(date="2024-01-02", open=10.0, high=10.0, low=10.0, close=10.0, volume=1e6, amount=1e7),
        dict(date="2024-01-03", open=10.9, high=10.9, low=10.9, close=10.9, volume=1e3, amount=1e4),
        dict(date="2024-01-04", open=11.0, high=11.8, low=11.0, close=11.5, volume=1e6, amount=1e7),
    ])
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 1, 1])}, settings()).run()
    buys = [t for t in res.trades if t.action == "buy"]
    assert len(buys) == 1 and buys[0].date == pd.Timestamp("2024-01-04")


def test_limit_down_blocks_sell_then_fills_next_day():
    """positions=[1,0,0,0] → desired=shift(1)=[0,1,0,0]：
    01-03 买入；01-04 目标转 0 但低开 10%（9.0 ≤ 10.0×0.905）触发跌停顺延；01-05 正常卖出。
    注意不可写成 [1,1,0,0]——那样 01-04 的目标仓位仍是 1，根本不会尝试卖出，测不到跌停分支。"""
    df = _df([
        dict(date="2024-01-02", open=10.0, high=10.0, low=10.0, close=10.0, volume=1e6, amount=1e7),
        dict(date="2024-01-03", open=10.0, high=10.2, low=9.9, close=10.0, volume=1e6, amount=1e7),
        dict(date="2024-01-04", open=9.0, high=9.0, low=8.8, close=8.9, volume=1e6, amount=1e7),   # 低开10%
        dict(date="2024-01-05", open=8.8, high=9.0, low=8.5, close=8.6, volume=1e6, amount=1e7),
    ])
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 0, 0, 0])}, settings()).run()
    assert any("跌停" in r[2] for r in res.skipped)
    sells = [t for t in res.trades if t.action == "sell"]
    assert len(sells) == 1 and sells[0].date == pd.Timestamp("2024-01-05")


def test_suspension_keeps_last_close_valuation():
    """d3 无行情行（停牌）：不交易，净值沿用 d2 收盘估值。"""
    df = _df([
        dict(date="2024-01-02", open=10.0, high=10.0, low=10.0, close=10.0, volume=1e6, amount=1e7),
        dict(date="2024-01-03", open=10.0, high=10.5, low=10.0, close=10.4, volume=1e6, amount=1e7),
        # 01-04 停牌，行已被 pipeline 过滤
        dict(date="2024-01-05", open=10.5, high=10.8, low=10.3, close=10.6, volume=1e6, amount=1e7),
    ])
    two = _df([
        dict(date="2024-01-02", open=5.0, high=5.0, low=5.0, close=5.0, volume=1e6, amount=1e7),
        dict(date="2024-01-03", open=5.0, high=5.1, low=4.9, close=5.0, volume=1e6, amount=1e7),
        dict(date="2024-01-04", open=5.0, high=5.2, low=5.0, close=5.1, volume=1e6, amount=1e7),
        dict(date="2024-01-05", open=5.1, high=5.3, low=5.0, close=5.2, volume=1e6, amount=1e7),
    ])
    res = Backtester(
        {"A": df, "B": two},
        {"A": _positions(df, [1, 1, 1]), "B": _positions(two, [0, 0, 0, 0])},
        settings(universe=("A", "B")),
    ).run()
    # 01-04 出现在日历里（B 有行情），A 停牌持仓按 10.4 估值
    assert "2024-01-04" in res.equity.index.strftime("%Y-%m-%d")
    buys = [t for t in res.trades if t.symbol == "A" and t.action == "buy"]
    qty = buys[0].shares
    cash_a = 250_000 - buys[0].shares * buys[0].price - buys[0].commission
    assert res.equity.loc["2024-01-04"] == pytest.approx(cash_a + qty * 10.4 + 250_000)


def test_ex_dividend_adjusts_shares_and_nav_continuous():
    """除权日（评审问题5 的机制验证）：持有 100 股，因子 1.0→1.25，原始价 100→80。
    调整后株数 125，市值 125×80=10,000 = 调整前 100×100 —— 净值连续。"""
    df = _df([
        dict(date="2024-01-02", open=100.0, high=100.0, low=100.0, close=100.0, volume=1e6, amount=1e8, adj_factor=1.0),
        dict(date="2024-01-03", open=100.0, high=101.0, low=99.0, close=100.0, volume=1e6, amount=1e8, adj_factor=1.0),
        dict(date="2024-01-04", open=80.0, high=81.0, low=79.5, close=80.0, volume=1e6, amount=1e8, adj_factor=1.25),
        dict(date="2024-01-05", open=80.0, high=82.0, low=79.0, close=81.0, volume=1e6, amount=1e8, adj_factor=1.25),
    ])
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 1, 1, 1])},
                     settings(capital=50_000)).run()
    b = [t for t in res.trades if t.action == "buy"][0]
    # d2 买入 400 股 @100.1；d4 除权 → 500 股
    assert b.shares == 400
    eq_before = res.equity.loc["2024-01-03"]   # 400×100 + 现金
    eq_after = res.equity.loc["2024-01-04"]    # 500×80 + 现金 —— 应相等
    assert eq_after == pytest.approx(eq_before)


def test_t_plus_1_guard():
    """引擎的 desired=shift(1) 天然不会当日买当日卖，此测试直接驱动私有方法验证保险丝。"""
    df = _df([
        dict(date="2024-01-02", open=10.0, high=10.0, low=10.0, close=10.0, volume=1e6, amount=1e7),
        dict(date="2024-01-03", open=10.0, high=10.5, low=9.9, close=10.2, volume=1e6, amount=1e7),
    ])
    bt = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 1])}, settings())
    from quant.backtest.portfolio import Slot
    slot = Slot(symbol="TEST", cash=0.0, shares=100.0,
                entry_date=pd.Timestamp("2024-01-03"), cost_basis=1000.0,
                last_close=10.0, last_factor=1.0)
    trades, skipped = [], []
    bt._try_sell(slot, df.loc[pd.Timestamp("2024-01-03")], pd.Timestamp("2024-01-03"), trades, skipped)
    assert trades == [] and any("T+1" in r[2] for r in skipped)
