import pandas as pd
import pytest

from quant.backtest.portfolio import Trade
from quant.report.charts import equity_chart, kline_chart
from quant.report.metrics import compute_metrics
from tests.conftest import make_bars


def _eq(values, start="2024-01-02"):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)), dtype=float)


def _bars():
    rows = [dict(date=f"2024-01-{d:02d}", open=10 + d, high=11 + d, low=9 + d,
                 close=10.5 + d, volume=1e6, amount=1e7) for d in range(2, 8)]
    return make_bars(rows)


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
    df = _bars()
    trades = [
        Trade("TEST", "buy", pd.Timestamp("2024-01-03"), 13.0, 100, 5),
        Trade("TEST", "sell", pd.Timestamp("2024-01-06"), 16.0, 100, 5, 5, pnl=290.0),
    ]
    fig = kline_chart(df, trades, "TEST")
    # K线 + 买点 + 卖点 = 3 条轨迹
    assert len(fig.data) == 3


def test_equity_and_benchmark_both_start_at_one():
    # 不归一化 / 用最后一天归一化 → 起点不是 1，两条曲线不可比
    eq = _eq([200.0, 220.0, 180.0])
    bench = {"HS300": pd.Series([400.0, 440.0, 400.0], index=eq.index)}
    fig = equity_chart(eq, bench)
    assert fig.data[0].y[0] == pytest.approx(1.0)
    assert fig.data[1].y[0] == pytest.approx(1.0)
    assert list(fig.data[0].y) == pytest.approx([1.0, 1.1, 0.9])


def test_drawdown_uses_running_peak_not_global_max():
    # 峰值在低谷之后：用全期最高当基准（未来函数）会得到 [-0.5, -0.55, 0, -0.25]
    fig = equity_chart(_eq([100.0, 90.0, 200.0, 150.0]), {})
    dd = fig.data[-1]
    assert dd.name == "回撤"
    assert list(dd.y) == pytest.approx([0.0, -0.1, 0.0, -0.25])


def test_drawdown_matches_metrics_max_drawdown():
    eq = _eq([100.0, 130.0, 91.0, 120.0, 60.0, 80.0])
    dd = equity_chart(eq, {}).data[-1]
    assert min(dd.y) == pytest.approx(compute_metrics(eq, [])["max_drawdown"])


def test_equity_on_row1_drawdown_on_row2():
    eq = _eq([100.0, 110.0, 105.0])
    fig = equity_chart(eq, {"HS300": pd.Series([1.0, 2.0, 3.0], index=eq.index)})
    assert [t.yaxis for t in fig.data] == ["y", "y", "y2"]


def test_equity_chart_skips_empty_and_all_nan_benchmark():
    """空 Series / 全 NaN 基准 dropna 后 s.iloc[0] 直接 IndexError 崩整张图。
    该基准应被跳过：策略线 + 回撤线照常画。"""
    eq = _eq([100.0, 110.0, 120.0])
    bench = {
        "空": pd.Series(dtype=float),
        "全NaN": pd.Series([float("nan")] * 3, index=eq.index),
    }
    fig = equity_chart(eq, bench)
    assert [t.name for t in fig.data] == ["策略", "回撤"]


def test_benchmark_nan_head_dropped_not_silently_blank():
    # 少了 dropna：s.iloc[0] 是 NaN → 整条基准线全 NaN，图上什么也没有却不报错
    eq = _eq([100.0, 101.0, 102.0, 103.0])
    bench = pd.Series([float("nan"), 100.0, 110.0, 121.0], index=eq.index)
    b = equity_chart(eq, {"HS300": bench}).data[1]
    assert not pd.isna(list(b.y)).any()
    assert list(b.y) == pytest.approx([1.0, 1.1, 1.21])
    assert pd.Timestamp(b.x[0]) == eq.index[1]


def test_kline_uses_raw_ohlc_in_correct_slots():
    df = _bars()
    k = kline_chart(df, [], "TEST").data[0]
    assert list(k.x) == list(df.index)
    assert list(k.open) == list(df["open"])
    assert list(k.high) == list(df["high"])
    assert list(k.low) == list(df["low"])
    assert list(k.close) == list(df["close"])


def test_kline_ashare_color_convention_red_up_green_down():
    k = kline_chart(_bars(), [], "TEST").data[0]
    assert k.increasing.line.color == "red"
    assert k.decreasing.line.color == "green"


def test_buy_sell_markers_carry_own_date_price_and_shape():
    df = _bars()
    trades = [
        Trade("TEST", "buy", pd.Timestamp("2024-01-03"), 13.0, 100, 5),
        Trade("TEST", "sell", pd.Timestamp("2024-01-06"), 16.0, 100, 5, 5, pnl=290.0),
    ]
    fig = kline_chart(df, trades, "TEST")
    buy, sell = fig.data[1], fig.data[2]
    assert buy.name == "买入" and sell.name == "卖出"
    assert [pd.Timestamp(x) for x in buy.x] == [pd.Timestamp("2024-01-03")]
    assert list(buy.y) == pytest.approx([13.0])
    assert [pd.Timestamp(x) for x in sell.x] == [pd.Timestamp("2024-01-06")]
    assert list(sell.y) == pytest.approx([16.0])
    assert buy.marker.symbol == "triangle-up" and buy.marker.color == "red"
    assert sell.marker.symbol == "triangle-down" and sell.marker.color == "green"


def test_kline_title_and_no_rangeslider():
    fig = kline_chart(_bars(), [], "sh.600519")
    assert fig.layout.title.text == "sh.600519"
    assert fig.layout.xaxis.rangeslider.visible is False
