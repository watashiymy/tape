import pandas as pd
import pytest

from quant.backtest.portfolio import Trade
from quant.report.metrics import compute_metrics


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


def test_cagr_annualizes_by_bar_count():
    # 126 根 bar = 0.5 年（252 交易日/年），100 -> 121 → cagr = 1.21**2 - 1
    m = compute_metrics(_equity([100.0] * 125 + [121.0]), trades=[])
    assert m["cagr"] == pytest.approx(0.4641)


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


def test_flat_equity_no_crash():
    m = compute_metrics(_equity([100, 100, 100]), trades=[])
    assert m["sharpe"] == 0.0
    assert m["max_drawdown"] == 0.0
