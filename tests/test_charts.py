import pandas as pd

from quant.backtest.portfolio import Trade
from quant.report.charts import equity_chart, kline_chart
from tests.conftest import make_bars


def test_equity_chart_smoke(tmp_path):
    idx = pd.bdate_range("2024-01-02", periods=10)
    eq = pd.Series(range(100, 110), index=idx, dtype=float)
    bench = {"沪深300": pd.Series(range(100, 120, 2), index=idx, dtype=float)}
    fig = equity_chart(eq, bench)
    # 2 条净值线 + 1 条回撤 = 3 条轨迹
    assert len(fig.data) == 3
    out = tmp_path / "report.html"
    fig.write_html(out)
    assert out.stat().st_size > 0


def test_kline_chart_smoke():
    rows = [dict(date=f"2024-01-{d:02d}", open=10 + d, high=11 + d, low=9 + d,
                 close=10.5 + d, volume=1e6, amount=1e7) for d in range(2, 8)]
    df = make_bars(rows)
    trades = [
        Trade("TEST", "buy", pd.Timestamp("2024-01-03"), 13.0, 100, 5),
        Trade("TEST", "sell", pd.Timestamp("2024-01-06"), 16.0, 100, 5, 5, pnl=290.0),
    ]
    fig = kline_chart(df, trades, "TEST")
    # K线 + 买点 + 卖点 = 3 条轨迹
    assert len(fig.data) == 3
