import pandas as pd

from quant.strategy.ma_cross import MaCross
from tests.conftest import make_bars


def _bars(adj_closes, factors=None):
    """按后复权价路径构造行情，raw = adj / factor。
    factor 必须出现台阶（除权日），否则 raw 与 adj_* 恒等、均线比较又是尺度不变的
    （ma(kx,n) > ma(kx,m) ⟺ ma(x,n) > ma(x,m)），
    "信号误用原始价"这一错法就测不出来（spec 决策1）。"""
    factors = factors or [1.0] * len(adj_closes)
    rows = [dict(date=f"2024-01-{i+1:02d}", open=a / f, high=a / f, low=a / f,
                 close=a / f, volume=1000, amount=a / f * 1000, adj_factor=f)
            for i, (a, f) in enumerate(zip(adj_closes, factors))]
    df = make_bars(rows)
    for c in ("open", "high", "low", "close"):
        df["adj_" + c] = df[c] * df["adj_factor"]
    return df


# 复权价：先升(暖机) → 走平 → 除权后继续升；参数用小窗口便于手推
ADJ = [10.0, 11.0, 12.0, 13.0, 13.0, 13.0, 13.0, 13.0, 14.0, 15.0, 16.0, 17.0]
FAC = [1.0] * 8 + [2.0] * 4          # idx8 为 10 送 10 除权日，raw 价腰斩


def test_positions_follow_ma_state():
    pos = MaCross(fast=2, slow=4).generate_positions(_bars(ADJ, FAC))
    assert pd.api.types.is_integer_dtype(pos)      # 契约是 {0,1} int；bool 会让下游 shift(1) 变 object
    assert set(pos.unique()) <= {0, 1}
    # 逐日手推 ma2 vs ma4（后复权价）。整条路径都断言，才能证伪"信号误用原始价"：
    # 除权日 idx8 起 raw 腰斩，改用 raw 会在 idx8~10 空仓，凭空造出一次往返交易
    assert list(pos) == [0, 0, 0, 1, 1, 1, 0, 0, 1, 1, 1, 1]
    # 暖机期：slow 未成形必须为 0。前缀取"上涨"而非横盘——横盘时 f == s，
    # min_periods=1 之类的错法也返回 0，断言会退化成永真
    assert (pos.iloc[:3] == 0).all()
    assert pos.iloc[6] == 0                        # 走平段 fast == slow，判据是 > 而非 >=


# 除权后先涨后跌，检验 fast 跌回 slow 之下能离场
ADJ_DOWN = ADJ + [16.0, 14.0, 12.0, 10.0]
FAC_DOWN = [1.0] * 8 + [2.0] * 8


def test_positions_exit_on_downtrend():
    pos = MaCross(fast=2, slow=4).generate_positions(_bars(ADJ_DOWN, FAC_DOWN))
    assert pos.iloc[11] == 1          # 上升段持有
    assert pos.iloc[-1] == 0          # 下跌段 fast 跌回 slow 之下 → 空仓


def test_no_lookahead():
    # 未来函数错位测试（spec §13）：截断未来数据，历史信号不得改变。
    # 逐个截断点都要查——单一截断点会挑到"盲点"：shift(-1) 这类偷看未来的错法，
    # 恰好在某些 n 上截断前后都是 0，一个点测不出来（实测 n=13 即为盲点）
    full = MaCross(fast=2, slow=4).generate_positions(_bars(ADJ_DOWN, FAC_DOWN))
    for n in range(4, len(ADJ_DOWN)):
        trunc = MaCross(fast=2, slow=4).generate_positions(_bars(ADJ_DOWN[:n], FAC_DOWN[:n]))
        assert list(full.iloc[:n]) == list(trunc), f"截断到第 {n} 天后，历史信号被改写"
