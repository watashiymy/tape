"""plotly 图表（spec §9 + v0.3.1 §2）：净值+回撤、K线+买卖点、交易日志仪表盘三图。
K 线用原始价（所见即真实价位）。

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


# ================================================================ 交易日志仪表盘（v0.3.1 §2）
# 三张图共同的硬约束：**零类别配色**（设计 §2.5）。琥珀 + 灰作类别对经校验脚本
# 实测过不了正常视力可辨阈值（ΔE 12.7 < 15），所以身份一律由位置/轴标签/直接
# 标注承载，条与线只有一个琥珀色；红绿仍专属涨跌方向，不当类别色用。


def journal_cum_pnl_chart(points: list[tuple]) -> go.Figure:
    """累计已实现盈亏（元，含分红）的**阶梯线**。

    - `line_shape="hv"` 不是审美偏好：已实现盈亏只在平仓/分红时点**跳变**，
      平滑连线是在编造两笔平仓之间的过程。点带 marker，让"哪天跳的"看得见。
    - 单序列无图例（标题即名）；零轴用发丝灰显式画一条——全盈利时 plotly 的
      自动 zeroline 根本不在可视范围里，而零轴是"赚/亏"的分界。
    - `points` 为空时返回一张没有轨迹的图（页面空态显示引导文案，不画空图，
      这里只保证不崩）。
    """
    fig = go.Figure()
    if points:
        fig.add_trace(go.Scatter(
            x=[day for day, _ in points], y=[value for _, value in points],
            mode="lines+markers", name="累计已实现",
            line=dict(color=palette.PRIMARY, width=2, shape="hv"),
            marker=dict(color=palette.PRIMARY, size=6),
            hovertemplate="%{x|%Y-%m-%d}<br>累计 %{y:,.2f} 元<extra></extra>"))
    fig.add_hline(y=0, line_color=palette.HAIRLINE, line_width=1)
    fig.update_layout(title="累计已实现盈亏（元，含分红）", showlegend=False,
                      height=340)
    return _apply_dark(fig)


def position_weights_chart(rows: list[dict]) -> go.Figure:
    """持仓占比：横向条按市值降序，单一琥珀 + 右端直接标注百分比与市值。

    不做饼图：持仓通常 3~10 只，条形长度的对比精度远高于扇形角度，且降序
    排完一眼看出集中度。`rows` 来自 analytics.position_weights（已排序）；
    无市价按成本顶上的行带 by_cost=True，条上打「按成本」标——不打标的话，
    一个没有市价的重仓会看着和有市价的一样可信。
    """
    fig = go.Figure()
    if rows:
        fig.add_trace(go.Bar(
            x=[r["value"] for r in rows],
            y=[f"{r['symbol']} {r['name']}".strip() for r in rows],
            orientation="h", marker_color=palette.PRIMARY,
            text=[f"{r['weight']:.1%} · {r['value']:,.0f} 元"
                  + ("（按成本）" if r["by_cost"] else "") for r in rows],
            textposition="outside", cliponaxis=False,
            hovertemplate="%{y}<br>%{text}<extra></extra>"))
    fig.update_layout(
        title="持仓占比（按市值）", showlegend=False,
        height=max(340, 120 + 44 * len(rows)),
        # 首行（最大持仓）在最上面：plotly 横向条默认把第一条画在最下面。
        yaxis=dict(autorange="reversed"),
        # 右侧留白给条外的直接标注；margin 挤掉标注是这种图最常见的翻车点。
        margin=dict(r=40))
    return _apply_dark(fig)


def source_compare_chart(by_source: dict, labels: dict | None = None) -> go.Figure:
    """来源对比（照信号做的 vs 自己拍的）：横向条，值 = 各来源的总已实现（含分红）。

    类目身份由**轴标签**承载（中文名由调用方传入，charts 属于 src/ 不 import
    app 层的标签表）；条一律单色琥珀，右端直接标注盈亏与胜率——某组还没有
    平仓交易时胜率是"算不出来"，标 — 而不是 None/nan。
    """
    names = labels or {}
    rows = sorted(by_source.items(),
                  key=lambda kv: -(kv[1].get("total_realized") or 0.0))
    fig = go.Figure()
    if rows:
        fig.add_trace(go.Bar(
            x=[summary.get("total_realized") or 0.0 for _, summary in rows],
            y=[names.get(source, source) or "—" for source, _ in rows],
            orientation="h", marker_color=palette.PRIMARY,
            text=[_source_note(summary) for _, summary in rows],
            textposition="outside", cliponaxis=False,
            hovertemplate="%{y}<br>%{text}<extra></extra>"))
    fig.add_vline(x=0, line_color=palette.HAIRLINE, line_width=1)
    fig.update_layout(title="按来源对比：总已实现（元，含分红）", showlegend=False,
                      height=max(300, 140 + 44 * len(rows)), margin=dict(r=40))
    return _apply_dark(fig)


def _source_note(summary: dict) -> str:
    """条右端那句标注：总已实现 + 胜率。胜率 None（该组没有平仓）→ —。"""
    total = summary.get("total_realized") or 0.0
    money = f"{total:,.0f}" if round(total) == 0 else f"{total:+,.0f}"
    win = summary.get("win_rate")
    return f"{money} 元 · 胜率 {'—' if win is None else f'{win:.0%}'}"
