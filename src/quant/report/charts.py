"""plotly 图表（spec §9）：净值+回撤、K线+买卖点。K 线用原始价（所见即真实价位）。"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from quant.backtest.portfolio import Trade


def equity_chart(equity: pd.Series, benchmarks: dict[str, pd.Series]) -> go.Figure:
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3],
                        subplot_titles=("净值（归一化）", "回撤"))
    fig.add_trace(go.Scatter(x=equity.index, y=equity / equity.iloc[0],
                             name="策略", line=dict(width=2)), row=1, col=1)
    for name, series in benchmarks.items():
        s = series.dropna()
        fig.add_trace(go.Scatter(x=s.index, y=s / s.iloc[0], name=name,
                                 line=dict(dash="dot")), row=1, col=1)
    dd = equity / equity.cummax() - 1
    fig.add_trace(go.Scatter(x=dd.index, y=dd, name="回撤", fill="tozeroy"), row=2, col=1)
    fig.update_layout(height=600, hovermode="x unified")
    return fig


def kline_chart(df: pd.DataFrame, trades: list[Trade], title: str) -> go.Figure:
    fig = go.Figure(go.Candlestick(
        x=df.index, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
        name=title, increasing_line_color="red", decreasing_line_color="green"))  # A股红涨绿跌
    buys = [t for t in trades if t.action == "buy"]
    sells = [t for t in trades if t.action == "sell"]
    if buys:
        fig.add_trace(go.Scatter(x=[t.date for t in buys], y=[t.price for t in buys],
                                 mode="markers", name="买入",
                                 marker=dict(symbol="triangle-up", size=12, color="red")))
    if sells:
        fig.add_trace(go.Scatter(x=[t.date for t in sells], y=[t.price for t in sells],
                                 mode="markers", name="卖出",
                                 marker=dict(symbol="triangle-down", size=12, color="green")))
    fig.update_layout(title=title, xaxis_rangeslider_visible=False, height=550)
    return fig
