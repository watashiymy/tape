import pandas as pd

from quant.signal.scan import scan
from quant.strategy.base import Strategy
from tests.conftest import make_bars


class StubStrategy(Strategy):
    name = "stub"

    def __init__(self, positions_by_symbol):
        self.positions_by_symbol = positions_by_symbol

    def generate_positions(self, df):
        sym = df.attrs["symbol"]
        return pd.Series(self.positions_by_symbol[sym], index=df.index, dtype=int)


def _bars(symbol, n=3):
    rows = [dict(date=f"2024-01-{d:02d}", open=10, high=11, low=9, close=10,
                 volume=1e6, amount=1e7) for d in range(2, 2 + n)]
    df = make_bars(rows)
    df.attrs["symbol"] = symbol
    return df


def test_scan_detects_new_buy_and_sell():
    bars = {"AAA": _bars("AAA"), "BBB": _bars("BBB"), "CCC": _bars("CCC")}
    strat = StubStrategy({"AAA": [0, 0, 1],    # 最新一日 0→1：新买入信号
                          "BBB": [1, 1, 0],    # 1→0：新卖出信号
                          "CCC": [1, 1, 1]})   # 状态不变：无信号
    signals = scan(bars, [strat])
    assert {(s["symbol"], s["action"]) for s in signals} == {("AAA", "BUY"), ("BBB", "SELL")}
    assert all(s["strategy"] == "stub" for s in signals)
    assert all(s["date"] == "2024-01-04" for s in signals)
