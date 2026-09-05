import re
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from quant.backtest.portfolio import Trade
from quant.report import charts, palette
from quant.report.charts import (equity_chart, journal_cum_pnl_chart, kline_chart,
                                 position_weights_chart, source_compare_chart)
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


def test_buy_sell_markers_sit_outside_the_candle_and_carry_the_trade():
    """2026-09-05：标注不再画在成交价上（那正好压在 K 线实体中间，把那一根遮没了），
    买 ▲ 挪到最低价下方、卖 ▼ 挪到最高价上方；真实成交价/股数/日期进悬停。"""
    df = _bars()
    trades = [
        Trade("TEST", "buy", pd.Timestamp("2024-01-03"), 13.0, 100, 5),
        Trade("TEST", "sell", pd.Timestamp("2024-01-06"), 16.0, 100, 5, 5, pnl=290.0),
    ]
    fig = kline_chart(df, trades, "TEST")
    buy, sell = fig.data[1], fig.data[2]
    assert buy.name == "买入" and sell.name == "卖出"
    assert [pd.Timestamp(x) for x in buy.x] == [pd.Timestamp("2024-01-03")]
    assert [pd.Timestamp(x) for x in sell.x] == [pd.Timestamp("2024-01-06")]
    assert buy.y[0] < df.loc["2024-01-03", "low"], "买入标注必须在那根 K 线的最低价之下"
    assert sell.y[0] > df.loc["2024-01-06", "high"], "卖出标注必须在那根 K 线的最高价之上"
    assert list(buy.customdata[0]) == ["2024-01-03", 13.0, 100]
    assert list(sell.customdata[0]) == ["2024-01-06", 16.0, 100]
    assert "成交日" in buy.hovertemplate and "股 @" in buy.hovertemplate
    assert buy.marker.symbol == "triangle-up" and buy.marker.color == palette.UP
    assert sell.marker.symbol == "triangle-down" and sell.marker.color == palette.DOWN


# ================================================================ K 线交互（2026-09-05）
# 用户点名的四条：标注遮 K 线、悬停无信息、只能靠工具栏缩放、只有日线。

def _two_weeks():
    """两周共 8 个交易日：第一周二~五，第二周一~四（周五缺，像个假日）。价格逐日 +1。"""
    days = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
            "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11"]
    return make_bars([dict(date=d, open=10 + i, high=11 + i, low=9 + i, close=10.5 + i,
                           volume=1e6 * (i + 1), amount=1e7 * (i + 1))
                      for i, d in enumerate(days)])


def test_resample_weekly_bars_aggregate_ohlcv_and_sit_on_the_last_trading_day():
    """开 = 首日开、高 = 最高、低 = 最低、收 = 末日收、量额求和；bar 标在该周**最后一个
    交易日**（第二周是周四 01-11，不是日历周五）——x 轴上永远是真实交易日。"""
    w = charts.resample_bars(_two_weeks(), "W")
    assert list(w.index) == [pd.Timestamp("2024-01-05"), pd.Timestamp("2024-01-11")]
    first = w.iloc[0]
    assert (first["open"], first["high"], first["low"], first["close"]) == (10, 14, 9, 13.5)
    assert first["volume"] == pytest.approx(1e6 * (1 + 2 + 3 + 4))
    assert first["amount"] == pytest.approx(1e7 * (1 + 2 + 3 + 4))
    second = w.iloc[1]
    assert (second["open"], second["high"], second["low"], second["close"]) == (14, 18, 13, 17.5)


def test_resample_monthly_and_yearly_land_on_the_last_trading_day():
    df = _two_weeks()
    assert list(charts.resample_bars(df, "M").index) == [pd.Timestamp("2024-01-11")]
    assert list(charts.resample_bars(df, "Y").index) == [pd.Timestamp("2024-01-11")]
    y = charts.resample_bars(df, "Y").iloc[0]
    assert (y["open"], y["high"], y["low"], y["close"]) == (10, 18, 9, 17.5)


def test_resample_skips_periods_without_any_trading_day():
    """中间整周停牌/长假：不产生一根全 NaN 的空 bar。"""
    df = make_bars([dict(date=d, open=10, high=11, low=9, close=10.5, volume=1e6, amount=1e7)
                    for d in ("2024-01-02", "2024-01-03", "2024-01-16", "2024-01-17")])
    w = charts.resample_bars(df, "W")
    assert list(w.index) == [pd.Timestamp("2024-01-03"), pd.Timestamp("2024-01-17")]
    assert not w.isna().any().any()


def test_resample_daily_is_the_frame_itself_and_unknown_freq_is_loud():
    df = _two_weeks()
    assert charts.resample_bars(df, "D") is df
    with pytest.raises(ValueError):
        charts.resample_bars(df, "H")


def test_kline_freqs_cover_day_week_month_year_with_display_names():
    assert list(charts.KLINE_FREQS) == ["D", "W", "M", "Y"]
    assert [v[0] for v in charts.KLINE_FREQS.values()] == ["日K", "周K", "月K", "年K"]


def test_weekly_markers_land_on_the_bar_that_contains_the_trade():
    """周三买、下周二卖 → 标在各自那周的 bar 上（x 是该周末个交易日），
    仍在该 bar 的影线之外；悬停里的成交日是真实的周三/周二。"""
    df = _two_weeks()
    trades = [Trade("T", "buy", pd.Timestamp("2024-01-03"), 12.0, 100, 5),
              Trade("T", "sell", pd.Timestamp("2024-01-09"), 16.0, 100, 5, 5, pnl=1.0)]
    fig = kline_chart(df, trades, "T", freq="W")
    bars = charts.resample_bars(df, "W")
    buy, sell = fig.data[1], fig.data[2]
    assert pd.Timestamp(buy.x[0]) == pd.Timestamp("2024-01-05")
    assert buy.y[0] < bars.loc["2024-01-05", "low"]
    assert pd.Timestamp(sell.x[0]) == pd.Timestamp("2024-01-11")
    assert sell.y[0] > bars.loc["2024-01-11", "high"]
    assert buy.customdata[0][0] == "2024-01-03" and sell.customdata[0][0] == "2024-01-09"


def test_candlestick_hover_text_has_ohlc_change_volume_and_amount():
    """悬停看得到开高低收、涨跌幅（对上一根 bar 的收盘）、成交量（手）、成交额（亿/万）。
    第一根没有上一根，涨跌显示 — 而不是 0。"""
    fig = kline_chart(_two_weeks(), [], "T")
    k = fig.data[0]
    assert k.hovertemplate == "%{text}<extra></extra>"
    first, second = k.text[0], k.text[1]
    for word in ("开 10.00", "高 11.00", "低 9.00", "收 10.50", "涨跌 —", "成交量", "成交额"):
        assert word in first, first
    assert "涨跌 +9.52%" in second, second          # 11.5 / 10.5 − 1
    assert "成交量 2.00 万手" in second and "成交额 2,000 万" in second, second


def test_cn_units_for_amount_and_volume():
    assert charts._cn_amount(1.234e9) == "12.34 亿"
    assert charts._cn_amount(5.2e7) == "5,200 万"
    assert charts._cn_amount(999.0) == "999 元"
    assert charts._cn_volume(1.5e6) == "1.50 万手"      # 150 万股 = 1.5 万手
    assert charts._cn_volume(35_000) == "350 手"
    assert charts._cn_amount(float("nan")) == "—" and charts._cn_volume(float("nan")) == "—"


def test_kline_layout_pans_by_drag_hovers_unified_and_zooms_by_scroll():
    """拖动 = 平移，滚轮 / 双指 = 缩放（前端配置），双击复位；统一按 x 悬停。"""
    fig = kline_chart(_bars(), [], "T")
    assert fig.layout.dragmode == "pan"
    assert fig.layout.hovermode == "x unified"
    assert fig.layout.xaxis.rangeslider.visible is False
    assert fig.layout.xaxis.hoverformat == "%Y-%m-%d"     # 悬停标题别是 "Jan 30, 2026"
    assert charts.KLINE_CONFIG["scrollZoom"] is True
    assert charts.KLINE_CONFIG["doubleClick"] == "reset"


def test_kline_keeps_the_view_across_reruns_but_resets_on_symbol_or_period_change():
    """uirevision 随标的与周期变：换了图才重置缩放，别的重跑保留用户缩放到的位置。"""
    a = kline_chart(_bars(), [], "600519", freq="D").layout.uirevision
    b = kline_chart(_bars(), [], "600519", freq="W").layout.uirevision
    c = kline_chart(_bars(), [], "000333", freq="D").layout.uirevision
    assert a == kline_chart(_bars(), [], "600519", freq="D").layout.uirevision
    assert len({a, b, c}) == 3


def test_kline_with_trades_but_empty_bars_does_not_crash():
    """全部日线都被过滤掉（极端：整段停牌）：不画标注，也不崩。"""
    empty = _bars().iloc[0:0]
    fig = kline_chart(empty, [Trade("T", "buy", pd.Timestamp("2024-01-03"), 1.0, 1, 0)], "T")
    assert len(fig.data) == 1


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

# v0.3.1 仪表盘三图的样例输入（与 test_journal_analytics.py 的手算夹具同形）。
_CUM_POINTS = [(date(2026, 3, 2), 1988.10), (date(2026, 4, 15), 72.05),
               (date(2026, 5, 20), 144.05)]
_WEIGHT_ROWS = [
    {"symbol": "000333", "name": "美的集团", "value": 12000.0,
     "weight": 12000.0 / 18005.0, "by_cost": False},
    {"symbol": "600519", "name": "贵州茅台", "value": 6005.0,
     "weight": 6005.0 / 18005.0, "by_cost": True},
]
_BY_SOURCE = {
    "ma_cross": {"total_realized": 1988.10, "win_rate": 0.5},
    "discretionary": {"total_realized": -1916.05, "win_rate": None},
}


def _figs() -> dict:
    """每个图表函数各来一张。暗色是**图表基线**，不是某个函数的特性——
    只改一个函数、另一个仍白底，是这次缺陷最可能的复发形态。"""
    return {"equity": equity_chart(_eq([100.0, 110.0, 105.0]),
                                   {"HS300": _eq([200.0, 210.0, 190.0])}),
            "kline": kline_chart(_bars(), [], "TEST"),
            "journal_cum": journal_cum_pnl_chart(_CUM_POINTS),
            "weights": position_weights_chart(_WEIGHT_ROWS),
            "source": source_compare_chart(_BY_SOURCE)}


@pytest.mark.parametrize("kind", ["equity", "kline", "journal_cum",
                                  "weights", "source"])
def test_chart_surfaces_come_from_the_palette(kind):
    """paper 用卡片底色（与面板内嵌它的容器同色），plot 用页面底色（同族、下沉一档）。
    默认模板下这两个值是 white / #E5ECF6。"""
    layout = _figs()[kind].layout
    assert layout.paper_bgcolor == palette.SURFACE
    assert layout.plot_bgcolor == palette.BACKGROUND


@pytest.mark.parametrize("kind", ["equity", "kline", "journal_cum",
                                  "weights", "source"])
def test_chart_font_is_palette_text_in_mono(kind):
    """暗底上必须显式给字色：默认模板是给浅底配的深灰（#444），暗底上几乎看不见。
    等宽字体是为了数值轴标签对齐（与面板的数字同一套字体栈）。"""
    layout = _figs()[kind].layout
    assert layout.font.color == palette.TEXT
    assert layout.font.family == palette.MONO


@pytest.mark.parametrize("kind", ["equity", "kline", "journal_cum",
                                  "weights", "source"])
def test_chart_grid_lines_are_the_low_contrast_hairline(kind):
    """默认模板的网格线是白色（浅底上才成立）。暗底上必须换成低对比发丝灰，
    否则一屏白格子比数据还抢眼。两个子图的四条轴都要覆盖到。"""
    fig = _figs()[kind]
    axes = list(fig.select_xaxes()) + list(fig.select_yaxes())
    assert len(axes) >= 2, "至少 x/y 各一条轴"
    for ax in axes:
        assert ax.gridcolor == palette.HAIRLINE, ax
        assert ax.zerolinecolor == palette.HAIRLINE, ax


@pytest.mark.parametrize("kind", ["equity", "kline", "journal_cum",
                                  "weights", "source"])
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


# ================================================================ 仪表盘三图（v0.3.1 §2）
# 硬约束：**零类别配色**（设计 §2.5）。琥珀+灰作类别对实测过不了可辨阈值
# （ΔE 12.7 < 15），所以身份一律由位置/轴标签/直接标注承载，条与线一律单色琥珀。

def test_cum_pnl_curve_is_a_step_line_in_primary():
    """已实现盈亏在平仓时点**跳变**，平滑连线是在编造两笔平仓之间的过程——
    必须是阶梯线（hv：先横到下一时点再竖跳）。单序列琥珀，无图例（标题即名）。"""
    fig = journal_cum_pnl_chart(_CUM_POINTS)
    assert len(fig.data) == 1
    trace = fig.data[0]
    assert trace.line.shape == "hv"
    assert trace.line.color == palette.PRIMARY
    assert fig.layout.showlegend is False


def test_cum_pnl_curve_carries_the_points_verbatim():
    """点就是手算的累计值（test_journal_analytics 的同一组数），一个不多一个不少
    ——多出来的插值点等于编造了不存在的平仓。"""
    trace = journal_cum_pnl_chart(_CUM_POINTS).data[0]
    assert [pd.Timestamp(x) for x in trace.x] == \
        [pd.Timestamp(d) for d, _ in _CUM_POINTS]
    assert list(trace.y) == pytest.approx([v for _, v in _CUM_POINTS])


def test_cum_pnl_curve_draws_the_zero_baseline_in_hairline():
    """零轴是"赚/亏"的分界，必须有一条浅灰参考线；全盈利时 plotly 的自动
    zeroline 可能压根不在可视范围里，所以要显式画。"""
    fig = journal_cum_pnl_chart(_CUM_POINTS)
    zero = [s for s in fig.layout.shapes if s.y0 == 0 and s.y1 == 0]
    assert zero and zero[0].line.color == palette.HAIRLINE


def test_cum_pnl_chart_survives_an_empty_journal():
    fig = journal_cum_pnl_chart([])
    assert len(fig.data) == 0     # 页面空态显示引导文案，这里只保证不崩


def test_weights_chart_is_horizontal_single_amber_desc():
    """横向条 + 单一琥珀：持仓 3~10 只时条形对比精度远高于饼图扇形角度；
    颜色不承载身份（§2.5），身份在 y 轴标签上。首行（最大持仓）在最上面。"""
    fig = position_weights_chart(_WEIGHT_ROWS)
    assert len(fig.data) == 1
    bar = fig.data[0]
    assert bar.orientation == "h"
    assert bar.marker.color == palette.PRIMARY
    assert fig.layout.yaxis.autorange == "reversed"
    assert list(bar.x) == pytest.approx([12000.0, 6005.0])
    assert list(bar.y) == ["000333 美的集团", "600519 贵州茅台"]
    assert fig.layout.showlegend is False


def test_weights_chart_labels_carry_percent_value_and_cost_flag():
    """右端直接标注百分比+市值；按成本顶上的那条必须打「按成本」标——
    不打标的话，一个没有市价的重仓会看着和有市价的一样可信。"""
    texts = list(position_weights_chart(_WEIGHT_ROWS).data[0].text)
    assert "66.6%" in texts[0] and "12,000" in texts[0]
    assert "按成本" not in texts[0]
    assert "33.4%" in texts[1] and "6,005" in texts[1] and "按成本" in texts[1]


def test_weights_chart_survives_no_positions():
    assert len(position_weights_chart([]).data) == 0


def test_source_compare_bars_are_single_amber_with_axis_labels():
    """类目（来源）由**轴标签**承载身份，条一律单色琥珀——琥珀+灰的类别对
    实测过不了可辨阈值（设计 §2.5），干脆不用颜色分类别。"""
    fig = source_compare_chart(_BY_SOURCE, {"ma_cross": "双均线信号",
                                            "discretionary": "自主决策"})
    assert len(fig.data) == 1
    bar = fig.data[0]
    assert bar.orientation == "h"
    assert bar.marker.color == palette.PRIMARY
    assert set(bar.y) == {"双均线信号", "自主决策"}
    assert fig.layout.showlegend is False


def test_source_compare_annotates_pnl_and_win_rate_without_nan():
    """右端直接标注已实现盈亏与胜率；某组还没有平仓交易时胜率是"算不出来"，
    必须显示 —，渲染出 None/nan 是甩到用户脸上的乱码。"""
    fig = source_compare_chart(_BY_SOURCE)
    by_label = dict(zip(fig.data[0].y, fig.data[0].text))
    assert "1,988" in by_label["ma_cross"] and "50%" in by_label["ma_cross"]
    assert "-1,916" in by_label["discretionary"]
    assert "—" in by_label["discretionary"]
    blob = "".join(by_label.values())
    assert "None" not in blob and "nan" not in blob.lower()


def test_source_compare_values_are_the_total_realized():
    fig = source_compare_chart(_BY_SOURCE)
    by_label = dict(zip(fig.data[0].y, fig.data[0].x))
    assert by_label["ma_cross"] == pytest.approx(1988.10)
    assert by_label["discretionary"] == pytest.approx(-1916.05)


def test_source_compare_survives_an_empty_grouping():
    assert len(source_compare_chart({}).data) == 0


@pytest.mark.parametrize("kind", ["journal_cum", "weights", "source"])
def test_dashboard_charts_never_color_by_category(kind):
    """零类别配色的总闸：新图任何轨迹的主色都只能是琥珀，尤其不许把红/绿
    当类别色用——那两个色在本项目里专门表示涨跌方向。"""
    for trace in _figs()[kind].data:
        color = (trace.marker.color if trace.type == "bar" else trace.line.color)
        assert color == palette.PRIMARY, (kind, trace)
