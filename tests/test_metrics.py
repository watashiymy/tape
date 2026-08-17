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


def test_flat_equity_no_crash():
    m = compute_metrics(_equity([100, 100, 100]), trades=[])
    assert m["sharpe"] == 0.0
    assert m["max_drawdown"] == 0.0
