import pandas as pd

from quant.config import OverlaysCfg
from quant.signal.scan import scan
from quant.strategy.base import Strategy
from tests.conftest import make_bars

# scan 的 overlays 自 v0.4.0 M2 起是必填关键字（防"漏传就当没开"的静默分叉）。
# 本文件钉的是扫描本身的语义（0→1/1→0 判定、字段、边界），叠加层全关 = 恒等变换，
# 各用例的期望值因此一个都不用改；叠加层的语义测试在 tests/test_strategy_pipeline.py。
OFF = OverlaysCfg()


class StubStrategy(Strategy):
    name = "stub"

    def __init__(self, positions_by_symbol):
        self.positions_by_symbol = positions_by_symbol

    def generate_positions(self, df):
        sym = df.attrs["symbol"]
        return pd.Series(self.positions_by_symbol[sym], index=df.index, dtype=int)


def _bars(symbol, n=3):
    # close 与 open 必须逐根不同，否则"取首根收盘/取当日开盘"这类错法测不出来
    rows = [dict(date=f"2024-01-{d:02d}", open=1 + d, high=30, low=1, close=10 * (d - 1),
                 volume=1e6, amount=1e7) for d in range(2, 2 + n)]
    df = make_bars(rows)
    df.attrs["symbol"] = symbol
    return df


def test_scan_detects_new_buy_and_sell():
    bars = {"AAA": _bars("AAA"), "BBB": _bars("BBB"), "CCC": _bars("CCC")}
    strat = StubStrategy({"AAA": [0, 0, 1],    # 最新一日 0→1：新买入信号
                          "BBB": [1, 1, 0],    # 1→0：新卖出信号
                          "CCC": [1, 1, 1]})   # 状态不变：无信号
    signals = scan(bars, [strat], overlays=OFF)
    assert {(s["symbol"], s["action"]) for s in signals} == {("AAA", "BUY"), ("BBB", "SELL")}
    assert all(s["strategy"] == "stub" for s in signals)
    assert all(s["date"] == "2024-01-04" for s in signals)


def test_scan_uses_previous_bar_not_first_bar_and_reports_latest_close():
    """仓位必须与**上一根**比（与首根比会漏掉"昨买今卖"），close 必须是最新一根的收盘价。"""
    df = _bars("AAA")           # close = 10, 20, 30；open = 3, 4, 5
    signals = scan({"AAA": df}, [StubStrategy({"AAA": [0, 1, 0]})], overlays=OFF)
    assert [(s["symbol"], s["action"], s["date"], s["close"]) for s in signals] == [
        ("AAA", "SELL", "2024-01-04", 30.0)]


def test_scan_runs_every_strategy():
    """线上配了 2 个策略；只跑第一个会让另一个的信号静默消失。"""
    df = _bars("AAA")
    a, b = StubStrategy({"AAA": [0, 0, 1]}), StubStrategy({"AAA": [1, 1, 0]})
    a.name, b.name = "s1", "s2"
    assert {(s["strategy"], s["action"]) for s in scan({"AAA": df}, [a, b], overlays=OFF)} == {
        ("s1", "BUY"), ("s2", "SELL")}


def test_scan_skips_symbol_with_single_bar():
    """新股上市首日只有一根K线，取 iloc[-2] 会 IndexError 把整轮扫描炸掉。"""
    df = _bars("AAA", n=1)
    assert scan({"AAA": df}, [StubStrategy({"AAA": [1]})], overlays=OFF) == []
