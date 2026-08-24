# tests/test_market_scan.py — v0.1.1 §3.3 扫描纯逻辑：五类判定、字段、排序、--date 截断语义
import pandas as pd
import pytest

from quant.data.pipeline import prepare_bars
from quant.signal.market_scan import MIN_BARS, classify_and_scan, sort_signals
from quant.strategy.base import Strategy
from tests.conftest import make_bars

EXPECTED = pd.Timestamp("2024-06-28").date()  # 周五，bdate_range(end=...) 的最后一根


def scan_bars(n=MIN_BARS, end="2024-06-28", closes=None, amounts=None, factors=None,
              symbol="600000", name="浦发银行"):
    """构造 n 根工作日 bar 并过 prepare_bars（classify 吃的是清洗后含 adj_* 的 df，
    与线上编排一致），attrs 携带 symbol/name。"""
    dates = pd.bdate_range(end=end, periods=n)
    closes = closes if closes is not None else [10.0] * n
    amounts = amounts if amounts is not None else [1e8] * n
    factors = factors if factors is not None else [1.0] * n
    rows = [dict(date=d.strftime("%Y-%m-%d"), open=c, high=c * 1.05, low=c * 0.95,
                 close=c, volume=1e6, amount=a, adj_factor=f)
            for d, c, a, f in zip(dates, closes, amounts, factors)]
    df, _warns = prepare_bars(make_bars(rows))
    df.attrs["symbol"], df.attrs["name"] = symbol, name
    return df


class TailStub(Strategy):
    """仓位 = 前导 0 + tail（tail 对齐到最后几根）。签名要求对**截断后**的 df 生成仓位。"""

    def __init__(self, name, tail):
        self.name, self.tail = name, list(tail)

    def generate_positions(self, df):
        vals = [0] * (len(df) - len(self.tail)) + self.tail
        return pd.Series(vals, index=df.index, dtype=int)


BUY = TailStub("buy", [0, 1])          # 0→1：新 BUY
HOLD = TailStub("hold", [1, 1])        # 持有中：非新信号
SELL = TailStub("sell", [1, 0])        # 1→0：卖出，扫描只报 BUY，不得出现


class ExplodingStrategy(Strategy):
    """跳过类判定必须在跑策略之前短路——被调用即失败。"""
    name = "exploding"

    def generate_positions(self, df):
        raise AssertionError("被跳过的标的不应执行策略")


# ---------- 判定 1：stale ----------

def test_stale_when_latest_bar_before_expected():
    bars = scan_bars(end="2024-06-27")  # 最新 bar 是周四，基准日是周五
    assert classify_and_scan(bars, [ExplodingStrategy()], EXPECTED, 5e7) == ([], "stale")


# ---------- --date 截断语义 ----------

def test_date_truncation_scans_as_of_expected_ignoring_later_bars():
    """bars 尾部有 expected 之后的行（缓存里有更新数据）时，必须先截断再判定：
    信号的 date/close 取 expected 当日那根，而不是全量最后一根。"""
    n = MIN_BARS + 10
    dates = pd.bdate_range(end="2024-07-12", periods=n)
    closes = [float(i + 1) for i in range(n)]              # 每根 close 互异，防"取错行"漏测
    bars = scan_bars(n=n, end="2024-07-12", closes=closes)
    expected = dates[MIN_BARS - 1].date()                  # 截断后恰剩 MIN_BARS 根
    signals, skip = classify_and_scan(bars, [BUY], expected, 5e7)
    assert skip is None
    assert [(s["date"], s["close"]) for s in signals] == [
        (str(expected), float(MIN_BARS))]                  # 截断处的 close，非最后一根的 160.0


def test_date_truncation_applies_before_warmup_count():
    """截断发生在暖机计数之前：全量 140 根、截到只剩 100 根 → insufficient_history。"""
    n = MIN_BARS + 10
    dates = pd.bdate_range(end="2024-07-12", periods=n)
    bars = scan_bars(n=n, end="2024-07-12")
    expected = dates[99].date()
    assert classify_and_scan(bars, [BUY], expected, 5e7) == ([], "insufficient_history")


# ---------- 判定 2：insufficient_history ----------

def test_insufficient_history_below_min_bars():
    bars = scan_bars(n=MIN_BARS - 1)
    assert classify_and_scan(bars, [ExplodingStrategy()], EXPECTED, 5e7) == (
        [], "insufficient_history")


def test_exactly_min_bars_passes_warmup():
    bars = scan_bars(n=MIN_BARS)
    signals, skip = classify_and_scan(bars, [HOLD], EXPECTED, 5e7)
    assert (signals, skip) == ([], None)   # no_signal，而非 insufficient_history


# ---------- 判定 3：is_st ----------

def test_st_on_latest_bar_skipped():
    bars = scan_bars()
    bars.loc[bars.index[-1], "is_st"] = 1
    assert classify_and_scan(bars, [ExplodingStrategy()], EXPECTED, 5e7) == ([], "is_st")


def test_historical_st_with_clean_latest_bar_not_skipped():
    """曾经 ST、现已摘帽：最新一根 is_st==0 就该照常扫。"""
    bars = scan_bars()
    bars.loc[bars.index[0], "is_st"] = 1
    signals, skip = classify_and_scan(bars, [BUY], EXPECTED, 5e7)
    assert skip is None and len(signals) == 1


def test_is_st_checked_before_liquidity():
    """判定顺序按设计表格：ST 且流动性差 → 归 is_st。"""
    bars = scan_bars(amounts=[1e6] * MIN_BARS)
    bars.loc[bars.index[-1], "is_st"] = 1
    assert classify_and_scan(bars, [ExplodingStrategy()], EXPECTED, 5e7) == ([], "is_st")


# ---------- 判定 4：low_liquidity ----------

def test_low_liquidity_skipped():
    bars = scan_bars(amounts=[1e7] * MIN_BARS)
    assert classify_and_scan(bars, [ExplodingStrategy()], EXPECTED, 5e7) == (
        [], "low_liquidity")


def test_liquidity_uses_last_20_bars_mean():
    """均额必须取**最近 20 根**：前面天量、近 20 日缩到 1e7 → 仍属 low_liquidity。"""
    amounts = [1e10] * (MIN_BARS - 20) + [1e7] * 20
    bars = scan_bars(amounts=amounts)
    assert classify_and_scan(bars, [ExplodingStrategy()], EXPECTED, 5e7) == (
        [], "low_liquidity")


def test_liquidity_at_threshold_passes():
    """条件是 < 门槛：恰好等于门槛必须放行（边界别吃掉合格票）。"""
    bars = scan_bars(amounts=[5e7] * MIN_BARS)
    assert classify_and_scan(bars, [HOLD], EXPECTED, 5e7) == ([], None)


# ---------- 判定 5：signal / no_signal ----------

def test_only_new_buy_emits_signal():
    """0→1 出信号；1→1（持有中）与 1→0（卖出）都不出——扫描只报 BUY。"""
    bars = scan_bars()
    signals, skip = classify_and_scan(bars, [BUY, HOLD, SELL], EXPECTED, 5e7)
    assert skip is None
    assert [s["strategy"] for s in signals] == ["buy"]


def test_two_strategies_can_both_signal_same_symbol():
    bars = scan_bars()
    signals, skip = classify_and_scan(bars, [BUY, TailStub("buy2", [0, 1])], EXPECTED, 5e7)
    assert skip is None
    assert [s["strategy"] for s in signals] == ["buy", "buy2"]


def test_no_signal_returns_none_skip_and_empty_list():
    assert classify_and_scan(scan_bars(), [HOLD], EXPECTED, 5e7) == ([], None)


def test_signal_record_fields():
    closes = [10.0] * (MIN_BARS - 1) + [11.0]        # 末两根 10 → 11：+10%
    amounts = [1e8] * (MIN_BARS - 1) + [2e8]         # 近20均额 = (19*1e8+2e8)/20 = 1.05e8
    bars = scan_bars(closes=closes, amounts=amounts, symbol="000333", name="美的集团")
    signals, skip = classify_and_scan(bars, [BUY], EXPECTED, 5e7)
    assert skip is None and len(signals) == 1
    s = signals[0]
    assert s["date"] == "2024-06-28"
    assert s["symbol"] == "000333"
    assert s["name"] == "美的集团"
    assert s["strategy"] == "buy"
    assert s["close"] == 11.0
    assert s["pct_chg"] == pytest.approx(10.0)
    assert s["amount"] == 2e8
    assert s["amount_ratio_20d"] == pytest.approx(2e8 / 1.05e8, rel=1e-3)


def test_pct_chg_uses_adjusted_close_across_ex_div_day():
    """末两根跨除权日（adj_factor 变化）时，pct_chg 必须按后复权口径，
    否则原始价的除权跳空会被当成暴跌。"""
    closes = [10.0] * (MIN_BARS - 1) + [5.05]        # 除权：原始价腰斩
    factors = [1.0] * (MIN_BARS - 1) + [2.0]         # 后复权因子翻倍 → 复权价 10 → 10.1
    bars = scan_bars(closes=closes, factors=factors)
    signals, skip = classify_and_scan(bars, [BUY], EXPECTED, 5e7)
    assert skip is None
    assert signals[0]["pct_chg"] == pytest.approx(1.0)   # +1%，而非 -49.5%
    assert signals[0]["close"] == 5.05                   # close 仍是原始价（撮合口径）


# ---------- 排序 ----------

def test_sort_signals_by_amount_desc():
    sigs = [{"symbol": "a", "strategy": "s", "amount": 1e8},
            {"symbol": "b", "strategy": "s", "amount": 3e8},
            {"symbol": "c", "strategy": "s", "amount": 2e8}]
    assert [s["symbol"] for s in sort_signals(sigs)] == ["b", "c", "a"]


def test_sort_signals_tie_breaks_by_symbol_then_strategy():
    sigs = [{"symbol": "b", "strategy": "s2", "amount": 1e8},
            {"symbol": "b", "strategy": "s1", "amount": 1e8},
            {"symbol": "a", "strategy": "s9", "amount": 1e8}]
    assert [(s["symbol"], s["strategy"]) for s in sort_signals(sigs)] == [
        ("a", "s9"), ("b", "s1"), ("b", "s2")]
