import re
from pathlib import Path

import pandas as pd
import pytest

from quant.backtest.portfolio import Trade
from quant.report import charts, palette
from quant.report.charts import equity_chart, kline_chart
from quant.report.metrics import compute_metrics
from tests.conftest import make_bars

HEX = re.compile(r"#[0-9A-Fa-f]{3,8}\b")


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
    """红涨绿跌是铁律；色值走色板（暗底上 red/green 纯色太刺，但方向不许反）。"""
    k = kline_chart(_bars(), [], "TEST").data[0]
    assert k.increasing.line.color == palette.UP
    assert k.decreasing.line.color == palette.DOWN


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
    assert buy.marker.symbol == "triangle-up" and buy.marker.color == palette.UP
    assert sell.marker.symbol == "triangle-down" and sell.marker.color == palette.DOWN


def test_kline_title_and_no_rangeslider():
    fig = kline_chart(_bars(), [], "sh.600519")
    assert fig.layout.title.text == "sh.600519"
    assert fig.layout.xaxis.rangeslider.visible is False


# ================================================================ 暗色（实测缺陷一）
# 面板是暗色（config.toml: #12141A），但 plotly 的**默认模板是浅色**
# （paper_bgcolor=white / plot_bgcolor=#E5ECF6）。内嵌的 report.html、kline_*.html
# 于是在暗色页面中间开一块白 —— 浏览器实测量到的最显眼的视觉缺陷。
#
# 色值一律断言"取自 quant.report.palette"，不在测试里裸写十六进制：色板是单一事实
# 来源（面板的 app/theme.py 也从它导入），裸写就等于在测试里埋第三份定义。

def _figs() -> dict:
    """两个图表函数各来一张。暗色是**图表基线**，不是某个函数的特性——
    只改一个函数、另一个仍白底，是这次缺陷最可能的复发形态。"""
    return {"equity": equity_chart(_eq([100.0, 110.0, 105.0]),
                                   {"HS300": _eq([200.0, 210.0, 190.0])}),
            "kline": kline_chart(_bars(), [], "TEST")}


@pytest.mark.parametrize("kind", ["equity", "kline"])
def test_chart_surfaces_come_from_the_palette(kind):
    """paper 用卡片底色（与面板内嵌它的容器同色），plot 用页面底色（同族、下沉一档）。
    默认模板下这两个值是 white / #E5ECF6。"""
    layout = _figs()[kind].layout
    assert layout.paper_bgcolor == palette.SURFACE
    assert layout.plot_bgcolor == palette.BACKGROUND


@pytest.mark.parametrize("kind", ["equity", "kline"])
def test_chart_font_is_palette_text_in_mono(kind):
    """暗底上必须显式给字色：默认模板是给浅底配的深灰（#444），暗底上几乎看不见。
    等宽字体是为了数值轴标签对齐（与面板的数字同一套字体栈）。"""
    layout = _figs()[kind].layout
    assert layout.font.color == palette.TEXT
    assert layout.font.family == palette.MONO


@pytest.mark.parametrize("kind", ["equity", "kline"])
def test_chart_grid_lines_are_the_low_contrast_hairline(kind):
    """默认模板的网格线是白色（浅底上才成立）。暗底上必须换成低对比发丝灰，
    否则一屏白格子比数据还抢眼。两个子图的四条轴都要覆盖到。"""
    fig = _figs()[kind]
    axes = list(fig.select_xaxes()) + list(fig.select_yaxes())
    assert len(axes) >= 2, "至少 x/y 各一条轴"
    for ax in axes:
        assert ax.gridcolor == palette.HAIRLINE, ax
        assert ax.zerolinecolor == palette.HAIRLINE, ax


@pytest.mark.parametrize("kind", ["equity", "kline"])
def test_chart_colorway_avoids_the_direction_colors(kind):
    """线条自动配色不许落到红/绿：那两个色在本项目里专门表示涨跌，
    一条红色的基准线就是个假信号（plotly 默认 colorway 的第 2、3 位正是红和绿）。"""
    colorway = _figs()[kind].layout.colorway
    assert colorway == palette.CHART_SERIES
    assert palette.UP not in colorway and palette.DOWN not in colorway


def test_equity_drawdown_is_green_like_the_metric_card():
    """最大回撤恒为负 → 绿（fmt.direction_color 的规则）。回撤区块标红会和
    "总收益红涨"当面打架，同屏两种红没人看得懂。"""
    dd = equity_chart(_eq([100.0, 90.0, 120.0]), {}).data[-1]
    assert dd.name == "回撤"
    assert dd.line.color == palette.DOWN


def test_charts_module_never_writes_a_bare_hex_color():
    """charts.py 属于 src/，不该反向依赖 app/theme.py；但也不许因此在这里
    抄一份十六进制——那正是"面板改了、图表没改"的配色漂移来源。"""
    src = Path(charts.__file__).read_text(encoding="utf-8")
    assert HEX.findall(src) == [], "charts.py 里有裸写色值，应从 palette 取"
