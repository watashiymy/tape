# tests/test_tsmom.py — v0.4.0 M3：时序动量（设计 §3.2）
#
# 依据 Moskowitz/Ooi/Pedersen (2012)：跨 58 个品种、上百年数据成立的时序动量效应。
# 规则只有一条——**过去 lookback 根的收益为正就持有**，所以这一组测试要钉的是
# "那一条"到底是哪一条：比的是 lookback 根之前那一根（不是 lookback±1）、用的是
# 后复权价（除权日不许被当成暴跌）、暖机期如实空仓、判据是严格 > （零收益不持有）。
import pandas as pd
import pytest

from quant.strategy.tsmom import TSMomentum
from tests.conftest import make_bars


def _bars(adj_closes, factors=None):
    """按**后复权收盘价路径**造行情，raw = adj / factor。

    factor 必须能出现台阶（除权日）：否则 raw 与 adj_* 恒等，"动量误用原始价"这一
    错法就测不出来。动量比的是两个**相隔 lookback 根**的价格，而除权缺口恰好落在
    这种跨期比较里——10 送 10 之后 raw 腰斩，误用 raw 会把一次分红读成 −50% 的动量
    （spec 决策1，与 test_ma_cross / test_donchian 同一套路）。
    """
    factors = factors or [1.0] * len(adj_closes)
    idx = pd.bdate_range(end="2024-06-28", periods=len(adj_closes))
    rows = [dict(date=d.strftime("%Y-%m-%d"), open=a / f, high=a / f, low=a / f,
                 close=a / f, volume=1000, amount=1e8, adj_factor=f)
            for d, a, f in zip(idx, adj_closes, factors)]
    df = make_bars(rows)
    for c in ("open", "high", "low", "close"):
        df["adj_" + c] = df[c] * df["adj_factor"]
    return df


# 手算基线（lookback=4）。pos[i] = 1 ⟺ adj_close[i] > adj_close[i−4]（严格 >）：
#   idx      0     1     2     3     4     5     6     7     8     9
#   adj    10    20    30    40    11    19    30    44     9    21
#   前 4 根  —     —     —     —    10    20    30    40    11    19
#   收益     —     —     —     —  +10%   −5%    0%  +10%  −18% +10.5%
#   pos      0     0     0     0     1     0     0     1     0     1
# 四种情形各有一根：暖机（0~3）、正收益（4/7/9）、负收益（5/8）、
# **恰好零收益**（6，判据是严格 > 而不是 >=）。
ADJ = [10.0, 20.0, 30.0, 40.0, 11.0, 19.0, 30.0, 44.0, 9.0, 21.0]
#: idx4 起 10 送 10 除权（因子 1→2），raw 价腰斩。除权台阶刻意落在**第一个非暖机根**：
#: 那一根的窗口正好跨过除权日，误用 raw 会把 +10% 的动量读成 −45%。
FAC = [1.0] * 4 + [2.0] * 6
WANT = [0, 0, 0, 0, 1, 0, 0, 1, 0, 1]


def test_positions_follow_the_lookback_return():
    pos = TSMomentum(lookback=4).generate_positions(_bars(ADJ, FAC))
    assert pd.api.types.is_integer_dtype(pos), \
        "契约是 {0,1} int：bool 会让下游 shift(1) 变 object，float 会被引擎静默取整"
    assert set(pos.unique()) <= {0, 1}
    assert pos.index.equals(_bars(ADJ, FAC).index)
    assert list(pos) == WANT


def test_the_warmup_holds_nothing_instead_of_pretending():
    """前 lookback 根算不出 lookback 期收益（shift 出来是 NaN）→ 一律 0。

    前缀刻意取**连涨**（10→40）而不是横盘：横盘时"暖机期为 0"与"暖机期照常判定"
    都给 0，断言会退化成永真。这里连涨，若把 NaN 当成"有收益"（例如 fillna(0)
    之后再比、或改用 min_periods=1），暖机期会变成一串 1。
    """
    pos = TSMomentum(lookback=4).generate_positions(_bars(ADJ, FAC))
    assert list(pos.iloc[:4]) == [0, 0, 0, 0]
    assert ADJ[3] > ADJ[0], "前缀必须是上涨的，否则这条断言没有分辨力"


def test_a_flat_lookback_return_is_not_held():
    """收益**恰好为 0** 不持有（判据是 `> 0`，不是 `>= 0`）。

    idx6 的 adj_close 与四根前逐分不差（30 vs 30）。写成 >= 会让"一年白干"的票
    也算动量为正——时序动量的全部依据是"过去涨了的东西继续涨"，零收益不是涨。
    """
    pos = TSMomentum(lookback=4).generate_positions(_bars(ADJ, FAC))
    assert ADJ[6] == ADJ[2], "手算前提：这两根后复权价必须完全相等"
    assert int(pos.iloc[6]) == 0


def test_the_lookback_window_is_exactly_lookback_bars_back():
    """差一位（shift(3) / shift(5)）必须给出不同答案——否则上面那条路径断言
    对"窗口偏一根"这种最常见的错法毫无分辨力。"""
    df = _bars(ADJ, FAC)
    got = list(TSMomentum(lookback=4).generate_positions(df))
    assert got == WANT
    c = df["adj_close"]
    for off in (3, 5):
        neighbour = list((c / c.shift(off) - 1.0 > 0).astype(int))
        assert got != neighbour, f"与 shift({off}) 的结果一样，这条序列分不开窗口偏移"


def test_momentum_uses_the_adjusted_price_so_a_dividend_is_not_a_crash():
    """除权日连续性：动量比的是相隔 lookback 根的两个价格，除权缺口正落在这种
    跨期比较里。用 adj_close 天然连续；误用原始价会把 10 送 10 读成 −45% 的动量。

    手算（FAC 在 idx4 从 1 跳到 2）：
      raw   10    20    30    40   5.5   9.5    15    22   4.5  10.5
      raw 口径 pos     …          0     0     0     0     0     0   ← 除权后全程假空仓
      adj 口径 pos     …          1     0     0     1     0     1   ← 正确答案
    """
    df = _bars(ADJ, FAC)
    assert df["close"].iloc[4] == pytest.approx(5.5), "raw 确实腰斩了"
    pos = TSMomentum(lookback=4).generate_positions(df)
    assert list(pos) == WANT
    c = df["close"]              # 原始价口径（错法）
    assert list((c / c.shift(4) - 1.0 > 0).astype(int)) != WANT, \
        "原始价口径必须与正确答案不同，否则这条测试测不出误用"


def test_no_lookahead():
    """未来函数错位测试（spec §13）：截断未来数据，历史信号不得改变。
    逐个截断点都查——单一截断点会挑到盲点（见 test_ma_cross 的实测记录）。"""
    full = TSMomentum(lookback=4).generate_positions(_bars(ADJ, FAC))
    for n in range(1, len(ADJ) + 1):
        trunc = TSMomentum(lookback=4).generate_positions(_bars(ADJ[:n], FAC[:n]))
        assert list(trunc) == list(full)[:n], f"截断到第 {n} 根后，历史信号被改写"


def test_a_long_flat_then_rising_path_enters_only_after_a_full_year():
    """默认 lookback=250（≈12 个月）的形状：250 根之前没有任何持仓，
    第 251 根起才可能入场。这条钉的是"默认参数不是随手写的"——
    lookback 若被改小，暖机期会整体前移，而回测只表现为"多做了几笔"。
    """
    closes = [100.0] * 250 + [101.0, 99.0]
    pos = TSMomentum().generate_positions(_bars(closes))
    assert list(pos[:250]) == [0] * 250
    assert int(pos.iloc[250]) == 1, "101 > 250 根前的 100 → 持有"
    assert int(pos.iloc[251]) == 0, "99 < 250 根前的 100 → 空仓"


@pytest.mark.parametrize("lookback", [
    0,        # shift(0) 就是拿自己比自己：收益恒为 0，全程静默空仓、零告警
    -1,
    250.0,    # 浮点会迟至 shift() 才崩，报错离病因很远
    "250",
    True,     # isinstance(True, int) 为真 → 静默变成 shift(1)，"12 个月动量"变日内涨跌
])
def test_invalid_params_raise_at_construction(lookback):
    """必须 raise ValueError 而非 assert（-O 下 assert 会被剥除，见 test_ma_cross）。"""
    with pytest.raises(ValueError):
        TSMomentum(lookback=lookback)


def test_invalid_param_error_names_param_and_value():
    """报错要带参数名与实际值，用户才能对着 settings.yaml 定位改哪一行。"""
    with pytest.raises(ValueError, match=r"lookback.*0"):
        TSMomentum(lookback=0)


def test_valid_params_still_construct():
    assert TSMomentum().lookback == 250, "默认 ≈12 个月（设计 §3.2）"
    assert TSMomentum(lookback=1).lookback == 1
