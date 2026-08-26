"""plotly 图表（spec §9）：净值+回撤、K线+买卖点。K 线用原始价（所见即真实价位）。

**暗色是硬要求**：这两个函数的产物（report.html / kline_*.html）会被面板内嵌，
而面板是暗色（.streamlit/config.toml）。plotly 的默认模板是浅色（纸底纯白、
绘图区淡蓝灰、网格线白、字色深灰），照默认走就是在暗色页面中间开一块白 ——
浏览器实测抓到的最显眼的视觉缺陷（具体色值记在 tests/test_charts.py 的断言里）。

为什么不用 `template="plotly_dark"`：它的底色是近纯黑一族，与面板那两档暗色
不是一套，嵌进去仍有可见色差。所以逐项显式设定。

色值全部取自 `quant.report.palette`（单一事实来源，面板的 app/theme.py 也从它
导入）。**本文件不许出现裸十六进制**——那就是"面板改了、图表没改"的漂移来源，
由 tests/test_charts.py 钉住（连注释里也不许，规则才守得住）。
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from quant.backtest.portfolio import Trade
from quant.report import palette

# 暗色基线。paper 用卡片底色（与内嵌它的容器同色，边界看不出来），
# plot 用页面底色（同族、下沉一档，绘图区像个凹进去的槽）。
_DARK_LAYOUT = dict(
    paper_bgcolor=palette.SURFACE,
    plot_bgcolor=palette.BACKGROUND,
    font=dict(color=palette.TEXT, family=palette.MONO),   # 等宽：数值轴标签对齐
    colorway=list(palette.CHART_SERIES),
)
# 轴：网格线与零线换成发丝灰。默认模板的白网格是给浅底配的，暗底上比数据还抢眼。
_DARK_AXIS = dict(gridcolor=palette.HAIRLINE, zerolinecolor=palette.HAIRLINE,
                  linecolor=palette.HAIRLINE)


def _apply_dark(fig: go.Figure) -> go.Figure:
    """把暗色基线刷到整张图。必须走 update_xaxes/update_yaxes（复数）：
    净值图是 2 行子图，只设 layout.xaxis 会漏掉第二行那对轴。"""
    fig.update_layout(**_DARK_LAYOUT)
    fig.update_xaxes(**_DARK_AXIS)
    fig.update_yaxes(**_DARK_AXIS)
    return fig


def equity_chart(equity: pd.Series, benchmarks: dict[str, pd.Series]) -> go.Figure:
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3],
                        subplot_titles=("净值（归一化）", "回撤"))
    fig.add_trace(go.Scatter(x=equity.index, y=equity / equity.iloc[0],
                             name="策略", line=dict(width=2)), row=1, col=1)
    for name, series in benchmarks.items():
        s = series.dropna()
        if s.empty:            # 空/全 NaN 基准：跳过，否则 s.iloc[0] IndexError 崩整张图
            continue
        fig.add_trace(go.Scatter(x=s.index, y=s / s.iloc[0], name=name,
                                 line=dict(dash="dot")), row=1, col=1)
    dd = equity / equity.cummax() - 1
    # 回撤恒为负 → 绿（与 fmt.direction_color、面板的"最大回撤"指标卡同一条规则）。
    # 不许因为"回撤是坏事"而标红：那会和"总收益红涨"当面打架。
    fig.add_trace(go.Scatter(x=dd.index, y=dd, name="回撤", fill="tozeroy",
                             line=dict(color=palette.DOWN)), row=2, col=1)
    fig.update_layout(height=600, hovermode="x unified")
    return _apply_dark(fig)


def kline_chart(df: pd.DataFrame, trades: list[Trade], title: str) -> go.Figure:
    # A股红涨绿跌（铁律）。色值走色板：暗底上纯 red/green 太刺，方向不变、观感协调。
    fig = go.Figure(go.Candlestick(
        x=df.index, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
        name=title, increasing_line_color=palette.UP, decreasing_line_color=palette.DOWN))
    buys = [t for t in trades if t.action == "buy"]
    sells = [t for t in trades if t.action == "sell"]
    if buys:
        fig.add_trace(go.Scatter(x=[t.date for t in buys], y=[t.price for t in buys],
                                 mode="markers", name="买入",
                                 marker=dict(symbol="triangle-up", size=12,
                                             color=palette.UP)))
    if sells:
        fig.add_trace(go.Scatter(x=[t.date for t in sells], y=[t.price for t in sells],
                                 mode="markers", name="卖出",
                                 marker=dict(symbol="triangle-down", size=12,
                                             color=palette.DOWN)))
    fig.update_layout(title=title, xaxis_rangeslider_visible=False, height=550)
    return _apply_dark(fig)
