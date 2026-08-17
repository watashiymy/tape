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


def test_ex_dividend_day_is_not_mistaken_for_a_limit_board():
    """10送10 除权日：原始价 100→50，但后复权因子 1.0→2.0，除权参考价就是 50，
    真实涨跌幅 0%，绝不是跌停。拿未除权的昨收 100 去比会误判成跌停并顺延卖出。"""
    df = _df([
        dict(date="2024-01-02", open=100.0, high=100.0, low=100.0, close=100.0,
             volume=1e6, amount=1e8, adj_factor=1.0),
        dict(date="2024-01-03", open=100.0, high=101.0, low=99.0, close=100.0,
             volume=1e6, amount=1e8, adj_factor=1.0),
        dict(date="2024-01-04", open=50.0, high=50.5, low=49.5, close=50.0,
             volume=1e6, amount=1e8, adj_factor=2.0),
        dict(date="2024-01-05", open=50.0, high=51.0, low=49.0, close=50.5,
             volume=1e6, amount=1e8, adj_factor=2.0),
    ])
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 0, 0, 0])},
                     settings(capital=50_000)).run()
    assert res.skipped == []
    sells = [t for t in res.trades if t.action == "sell"]
    assert len(sells) == 1 and sells[0].date == pd.Timestamp("2024-01-04")


def test_ex_dividend_day_one_word_limit_up_still_blocks_buy():
    """除权日叠加一字涨停：除权参考价 50 → 涨停价 55，一手都买不到。
    用未除权昨收 100 比较时 55>=109.5 为 False、close 55>100 也为 False，两个分支同时失效。"""
    df = _df([
        dict(date="2024-01-02", open=100.0, high=100.0, low=100.0, close=100.0,
             volume=1e6, amount=1e8, adj_factor=1.0),
        dict(date="2024-01-03", open=55.0, high=55.0, low=55.0, close=55.0,
             volume=1e3, amount=1e5, adj_factor=2.0),
        dict(date="2024-01-04", open=56.0, high=58.0, low=55.5, close=57.0,
             volume=1e6, amount=1e8, adj_factor=2.0),
    ])
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 1, 1])},
                     settings(capital=50_000)).run()
    assert any("涨停" in r[2] for r in res.skipped)
    buys = [t for t in res.trades if t.action == "buy"]
    assert len(buys) == 1 and buys[0].date == pd.Timestamp("2024-01-04")


def test_ex_dividend_scales_shares_by_factor_ratio_not_by_factor():
    """除权调整必须乘「因子比率」f/last_factor，不是因子本身 f。
    真实的 baostock 后复权因子是自上市累积值，起点从来不是 1.0；这里用 2.0→2.5（10送2.5）
    把两种写法拉开：正确 400×1.25=500 股，错写成 ×f 则是 400×2.5=1000 股，
    净值凭空从 49,949.99 涨到 89,949.99。原除权用例的因子恰好是 1.0→1.25，两种写法数值相同，
    根本分辨不出来。手算：01-03 以 100×1.001=100.1 买 400 股，
    成交额 40,040，佣金 max(10.01,5)=10.01，现金余 9,949.99；
    01-04 除权后 500 股 × 80 = 40,000，净值 49,949.99 与除权前 400×100+9,949.99 相等。"""
    df = _df([
        dict(date="2024-01-02", open=100.0, high=100.0, low=100.0, close=100.0,
             volume=1e6, amount=1e8, adj_factor=2.0),
        dict(date="2024-01-03", open=100.0, high=101.0, low=99.0, close=100.0,
             volume=1e6, amount=1e8, adj_factor=2.0),
        dict(date="2024-01-04", open=80.0, high=81.0, low=79.5, close=80.0,
             volume=1e6, amount=1e8, adj_factor=2.5),
        dict(date="2024-01-05", open=80.0, high=82.0, low=79.0, close=81.0,
             volume=1e6, amount=1e8, adj_factor=2.5),
    ])
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 1, 0, 0])},
                     settings(capital=50_000)).run()
    assert res.equity.loc["2024-01-03"] == pytest.approx(49_949.99)
    assert res.equity.loc["2024-01-04"] == pytest.approx(49_949.99)
    sells = [t for t in res.trades if t.action == "sell"]
    assert len(sells) == 1 and sells[0].shares == 500.0


def test_moderate_gap_up_is_not_a_limit_board():
    """负向用例：+5% 高开离 ±10% 板还远，必须当天就成交。
    少了它，把 LIMIT_UP_RATIO 收紧（例如 1.095→1.045）会让引擎把普通高开当涨停顺延，
    全部正向用例仍然全绿。"""
    df = _df([
        dict(date="2024-01-02", open=10.0, high=10.1, low=9.9, close=10.0, volume=1e6, amount=1e7),
        dict(date="2024-01-03", open=10.5, high=10.8, low=10.4, close=10.7, volume=1e6, amount=1e7),
    ])
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 1])}, settings()).run()
    assert res.skipped == []
    buys = [t for t in res.trades if t.action == "buy"]
    assert len(buys) == 1 and buys[0].date == pd.Timestamp("2024-01-03")


def test_moderate_gap_down_is_not_a_limit_board():
    """负向用例：-5% 低开不是跌停，卖单必须当天成交，不许顺延。
    对应 LIMIT_DOWN_RATIO 被放宽（0.905→0.95 之类）的错法。"""
    df = _df([
        dict(date="2024-01-02", open=10.0, high=10.1, low=9.9, close=10.0, volume=1e6, amount=1e7),
        dict(date="2024-01-03", open=10.0, high=10.2, low=9.9, close=10.0, volume=1e6, amount=1e7),
        dict(date="2024-01-04", open=9.5, high=9.7, low=9.3, close=9.4, volume=1e6, amount=1e7),
    ])
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 0, 0])}, settings()).run()
    assert res.skipped == []
    sells = [t for t in res.trades if t.action == "sell"]
    assert len(sells) == 1 and sells[0].date == pd.Timestamp("2024-01-04")


def test_limit_thresholds_are_inclusive_at_the_boundary():
    """阈值边界：开盘价恰好等于昨收×1.095（10.95）就算涨停，差一分钱（10.94）就不算。
    钉死 `>=` 不能写成 `>`，也钉死阈值本身不能挪动。
    （10.0×1.095 在 IEEE754 下恰为 10.95，比较无浮点毛刺。）"""
    on = _df([
        dict(date="2024-01-02", open=10.0, high=10.1, low=9.9, close=10.0, volume=1e6, amount=1e7),
        dict(date="2024-01-03", open=10.95, high=11.2, low=10.9, close=11.0, volume=1e6, amount=1e7),
    ])
    res_on = Backtester({"TEST": on}, {"TEST": _positions(on, [1, 1])}, settings()).run()
    assert any("涨停" in r[2] for r in res_on.skipped) and res_on.trades == []

    off = _df([
        dict(date="2024-01-02", open=10.0, high=10.1, low=9.9, close=10.0, volume=1e6, amount=1e7),
        dict(date="2024-01-03", open=10.94, high=11.2, low=10.9, close=11.0, volume=1e6, amount=1e7),
    ])
    res_off = Backtester({"TEST": off}, {"TEST": _positions(off, [1, 1])}, settings()).run()
    assert res_off.skipped == [] and len(res_off.trades) == 1


def test_one_word_limit_down_blocks_sell_then_fills_next_day():
    """一字跌停也不可卖——此前只测了一字涨停，跌停侧的 one_word 分支完全是盲区。
    这里 01-04 跌幅仅 5%（未触及 -9.5% 阈值），开盘价那条判据不会命中，
    只能靠 high==low 且 close<昨收 识别（ST 股的 ±5% 板就是这个形态）。"""
    df = _df([
        dict(date="2024-01-02", open=10.0, high=10.1, low=9.9, close=10.0, volume=1e6, amount=1e7),
        dict(date="2024-01-03", open=10.0, high=10.2, low=9.9, close=10.0, volume=1e6, amount=1e7),
        dict(date="2024-01-04", open=9.5, high=9.5, low=9.5, close=9.5, volume=1e3, amount=1e4),
        dict(date="2024-01-05", open=9.5, high=9.7, low=9.4, close=9.6, volume=1e6, amount=1e7),
    ])
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 0, 0, 0])}, settings()).run()
    assert any("跌停" in r[2] for r in res.skipped)
    sells = [t for t in res.trades if t.action == "sell"]
    assert len(sells) == 1 and sells[0].date == pd.Timestamp("2024-01-05")


def test_flat_one_word_bar_is_not_a_limit_board():
    """一字板还得看方向：全天一个价、但与昨收持平（冷门股无人交易），既非涨停也非跌停，
    买卖都该照常成交。钉死两处方向判据不能写成 `>=` / `<=`。"""
    df = _df([
        dict(date="2024-01-02", open=10.0, high=10.1, low=9.9, close=10.0, volume=1e6, amount=1e7),
        dict(date="2024-01-03", open=10.0, high=10.0, low=10.0, close=10.0, volume=1e3, amount=1e4),
        dict(date="2024-01-04", open=10.0, high=10.3, low=9.8, close=10.0, volume=1e6, amount=1e7),
        dict(date="2024-01-05", open=10.0, high=10.0, low=10.0, close=10.0, volume=1e3, amount=1e4),
    ])
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 1, 0, 0])}, settings()).run()
    assert res.skipped == []
    assert [(t.action, t.date) for t in res.trades] == [
        ("buy", pd.Timestamp("2024-01-03")), ("sell", pd.Timestamp("2024-01-05"))]


def test_limit_down_is_judged_on_raw_price_not_adjusted_price():
    """项目铁律「涨跌停判定用原始价」在跌停侧的证明：因子恒为 5.0（成熟股的累积后复权因子，
    期间无除权），原始价 10.0→9.0 是实打实的跌停，但 adj_open=45.0 远高于昨收 10.0——
    把判据改用 adj_open 就会漏判并当天卖出。"""
    df = _df([
        dict(date="2024-01-02", open=10.0, high=10.1, low=9.9, close=10.0,
             volume=1e6, amount=1e7, adj_factor=5.0),
        dict(date="2024-01-03", open=10.0, high=10.2, low=9.9, close=10.0,
             volume=1e6, amount=1e7, adj_factor=5.0),
        dict(date="2024-01-04", open=9.0, high=9.2, low=8.8, close=9.0,
             volume=1e6, amount=1e7, adj_factor=5.0),
        dict(date="2024-01-05", open=9.0, high=9.2, low=8.9, close=9.1,
             volume=1e6, amount=1e7, adj_factor=5.0),
    ])
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 0, 0, 0])}, settings()).run()
    assert any("跌停" in r[2] for r in res.skipped)
    sells = [t for t in res.trades if t.action == "sell"]
    assert len(sells) == 1 and sells[0].date == pd.Timestamp("2024-01-05")
