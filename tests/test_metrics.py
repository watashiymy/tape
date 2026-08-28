import pandas as pd
import pytest

from quant.backtest.portfolio import Trade
from quant.report.metrics import compute_metrics, trade_stats


def _equity(values, start="2024-01-02"):
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series(values, index=idx, dtype=float)


def test_total_return_and_drawdown():
    m = compute_metrics(_equity([100, 110, 99]), trades=[])
    assert m["total_return"] == pytest.approx(-0.01)
    assert m["max_drawdown"] == pytest.approx(-0.10)   # (99-110)/110


def test_max_drawdown_uses_running_peak_not_global_max():
    # 峰值出现在低谷「之后」：若用全期最高点当基准（未来函数）会算出 -0.55
    m = compute_metrics(_equity([100, 90, 200]), trades=[])
    assert m["max_drawdown"] == pytest.approx(-0.10)   # (90-100)/100


def test_sharpe_value():
    # 日收益 [0.10, 0.20, 0.60]：mean=0.3，std(ddof=1)=sqrt(0.14/2)
    # sharpe = 0.3 / sqrt(0.07) * sqrt(252) = 0.3 * sqrt(3600) = 18.0
    m = compute_metrics(_equity([100, 110, 132, 211.2]), trades=[])
    assert m["sharpe"] == pytest.approx(18.0)


def test_sharpe_sign_follows_returns():
    up = compute_metrics(_equity([100, 110, 132, 211.2]), trades=[])["sharpe"]
    down = compute_metrics(_equity([211.2, 132, 110, 100]), trades=[])["sharpe"]
    assert up > 0 and down < 0


def test_cagr_annualizes_by_calendar_days():
    """CAGR 定义即日历年化：years = 首末日期差天数 / 365.25。
    旧口径 bar数/252 会系统性高估——A 股一年只有约 243 个交易日，
    126 根 bar 按旧口径算 0.5 年，按日历实际跨 175 天 ≈ 0.4791 年。
    期望值用 Decimal 手算：1.21 ** (1 / (175/365.25)) - 1。"""
    m = compute_metrics(_equity([100.0] * 125 + [121.0]), trades=[])
    # bdate_range("2024-01-02", periods=126) 末日 2024-06-25，跨 175 天
    assert m["cagr"] == pytest.approx(0.48862358115820816)


def test_cagr_exact_four_julian_years():
    # 1461 天 = 4 × 365.25 → years 恰为 4.0；100 -> 146.41 = 1.1**4 → cagr = 0.1
    idx = pd.DatetimeIndex([pd.Timestamp("2023-01-01"), pd.Timestamp("2027-01-01")])
    m = compute_metrics(pd.Series([100.0, 146.41], index=idx), trades=[])
    assert m["cagr"] == pytest.approx(0.1)


def test_cagr_single_point_returns_zero():
    # 单点净值 years == 0：不能除零，也不能抛异常
    m = compute_metrics(_equity([100.0]), trades=[])
    assert m["cagr"] == 0.0


def test_trade_stats():
    t = pd.Timestamp("2024-01-05")
    trades = [
        Trade("A", "sell", t, 10, 100, 5, 5, pnl=100.0, holding_days=10),
        Trade("A", "sell", t, 10, 100, 5, 5, pnl=-50.0, holding_days=20),
        Trade("A", "sell", t, 10, 100, 5, 5, pnl=200.0, holding_days=30),
        Trade("A", "buy", t, 10, 100, 5),   # 买入不参与胜率
    ]
    m = compute_metrics(_equity([100, 101, 102]), trades)
    assert m["n_trades"] == 3
    assert m["win_rate"] == pytest.approx(2 / 3)
    assert m["profit_factor"] == pytest.approx(300.0 / 50.0)
    assert m["avg_holding_days"] == pytest.approx(20.0)


def test_breakeven_trade_counts_as_loss():
    t = pd.Timestamp("2024-01-05")
    trades = [
        Trade("A", "sell", t, 10, 100, 5, 5, pnl=100.0, holding_days=10),
        Trade("A", "sell", t, 10, 100, 5, 5, pnl=0.0, holding_days=10),
    ]
    m = compute_metrics(_equity([100, 101]), trades)
    assert m["n_trades"] == 2
    assert m["win_rate"] == pytest.approx(0.5)


def test_profit_factor_none_when_no_losses():
    t = pd.Timestamp("2024-01-05")
    trades = [Trade("A", "sell", t, 10, 100, 5, 5, pnl=100.0, holding_days=10)]
    m = compute_metrics(_equity([100, 101]), trades)
    assert m["profit_factor"] is None
    assert m["win_rate"] == pytest.approx(1.0)


def test_profit_factor_zero_when_all_losses():
    """全亏 PF 是良定义的 0.0（分子为 0），不能与"无平仓交易"共用 None——
    面板上两者都显示 —，"策略每单都亏"这个强信号就被吞掉了。"""
    t = pd.Timestamp("2024-01-05")
    trades = [
        Trade("A", "sell", t, 10, 100, 5, 5, pnl=-30.0, holding_days=10),
        Trade("A", "sell", t, 10, 100, 5, 5, pnl=-70.0, holding_days=20),
    ]
    m = compute_metrics(_equity([100, 99]), trades)
    assert m["profit_factor"] == 0.0
    assert m["win_rate"] == 0.0


def test_profit_factor_none_when_no_closed_trades():
    m = compute_metrics(_equity([100, 101]), trades=[])
    assert m["profit_factor"] is None


def test_flat_equity_no_crash():
    m = compute_metrics(_equity([100, 100, 100]), trades=[])
    assert m["sharpe"] == 0.0
    assert m["max_drawdown"] == 0.0


# ---------------------------------------------------------------- trade_stats（v0.3.0）
# 胜率/盈亏比/平均持仓天数的口径被抽成一个纯函数，回测报告与**交易日志**共用。
# 抽出来而不是各写一份：设计 §4.2 要求两边"并排比较"，
# 而"平手算亏""全亏 PF=0.0 vs 无亏损 PF=None"这类边界一旦在两处各写一遍，
# 迟早只改一边 —— 那时两张表看着都对，比出来的结论却是错的。

def test_trade_stats_matches_compute_metrics():
    t = pd.Timestamp("2024-01-05")
    trades = [
        Trade("A", "sell", t, 10, 100, 5, 5, pnl=100.0, holding_days=10),
        Trade("A", "sell", t, 10, 100, 5, 5, pnl=-50.0, holding_days=20),
    ]
    m = compute_metrics(_equity([100, 101]), trades)
    s = trade_stats([100.0, -50.0], [10, 20])
    assert s == {k: m[k] for k in s}


def test_trade_stats_skips_unclosed_entries():
    """pnl 为 None = 这笔算不出盈亏（回测里的未平仓、日志里的无批次可配的卖出）。
    它不能进胜率的分母，更不能被当成 0 记一笔平手。"""
    s = trade_stats([100.0, None, -50.0], [10, None, 20])
    assert s["n_trades"] == 2
    assert s["win_rate"] == pytest.approx(0.5)
    assert s["avg_holding_days"] == pytest.approx(15.0)


def test_trade_stats_holding_days_may_be_missing():
    s = trade_stats([100.0, -50.0], [None, None])
    assert s["n_trades"] == 2
    assert s["avg_holding_days"] is None


def test_trade_stats_empty():
    s = trade_stats([], [])
    assert s == {"n_trades": 0, "win_rate": None,
                 "profit_factor": None, "avg_holding_days": None}


def test_trade_stats_rejects_misaligned_inputs():
    """两列错位会静默把 A 的盈亏配上 B 的持仓天数 —— 响亮报错。"""
    with pytest.raises(ValueError):
        trade_stats([100.0, -50.0], [10])
