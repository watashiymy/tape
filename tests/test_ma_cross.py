from quant.strategy.ma_cross import MaCross
from tests.conftest import make_bars


def _bars(closes):
    rows = [dict(date=f"2024-01-{i+1:02d}", open=c, high=c, low=c, close=c,
                 volume=1000, amount=c * 1000) for i, c in enumerate(closes)]
    df = make_bars(rows)
    for c in ("open", "high", "low", "close"):
        df["adj_" + c] = df[c]
    return df


def test_positions_follow_ma_state():
    # 前 8 天横盘 10，随后连涨 → fast(2) 上穿 slow(4) → 持有；参数用小窗口便于手推
    closes = [10.0] * 8 + [11.0, 12.0, 13.0, 14.0]
    pos = MaCross(fast=2, slow=4).generate_positions(_bars(closes))
    assert set(pos.unique()) <= {0, 1}
    assert pos.iloc[-1] == 1          # 上升段持有
    assert pos.iloc[5] == 0           # 横盘段 fast == slow，不持有
    assert (pos.iloc[:3] == 0).all()  # slow 未成形前必须为 0


def test_positions_exit_on_downtrend():
    closes = [10.0] * 8 + [11.0, 12.0, 13.0, 14.0] + [12.0, 10.0, 8.0, 6.0]
    pos = MaCross(fast=2, slow=4).generate_positions(_bars(closes))
    assert pos.iloc[-1] == 0          # 下跌段 fast 跌回 slow 之下 → 空仓


def test_no_lookahead():
    # 未来函数错位测试（spec §13）：截断未来数据，历史信号不得改变
    closes = [10.0] * 8 + [11.0, 12.0, 13.0, 14.0, 12.0, 10.0]
    full = MaCross(fast=2, slow=4).generate_positions(_bars(closes))
    trunc = MaCross(fast=2, slow=4).generate_positions(_bars(closes[:-3]))
    assert (full.iloc[: len(trunc)].values == trunc.values).all()
