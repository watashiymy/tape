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


# ================================================================ K 线（2026-09-05 交互重做）
#
# 用户点名的四条：买卖标注遮住 K 线、悬停看不到开高低收量额、只能靠工具栏缩放、
# 只有日线。对应的做法：标注挪到影线之外（买在最低价下方、卖在最高价上方）；
# 每根 bar 自带一段 hover 文本、统一按 x 悬停；滚轮/触控板双指缩放 + 拖动平移 +
# 双击复位（前端配置 KLINE_CONFIG，由 st.plotly_chart(config=) 传）；日/周/月/年
# 由 resample_bars 聚合，买卖点映射到所属那根 bar 上。

#: K 线周期：内部键 → (显示名, pandas 重采样频率)。日线不重采样。
#: 键永不改（面板控件的 session_state 认它）；显示名随时可改。
KLINE_FREQS: dict[str, tuple[str, str | None]] = {
    "D": ("日K", None),
    "W": ("周K", "W-FRI"),
    "M": ("月K", "ME"),
    "Y": ("年K", "YE"),
}

#: 交给 st.plotly_chart(config=...) 的前端配置。scrollZoom 让滚轮与触控板双指直接缩放
#: （不必去右上角工具栏找放大镜）；双击复位；plotly 的 logo 去掉。
KLINE_CONFIG: dict = {"scrollZoom": True, "doubleClick": "reset", "displaylogo": False}

#: 买卖标注离影线的距离（占价格的比例）。太小会贴回 K 线上，太大在密集的日线上
#: 会对不上是哪一根；1.2% 在日/周/年三种周期的真机图上都看得出"这根的下方/上方"。
_MARKER_PAD = 0.012

_OHLCV = ("open", "high", "low", "close", "volume", "amount")
_RESAMPLE_HOW = {"open": "first", "high": "max", "low": "min", "close": "last",
                 "volume": "sum", "amount": "sum"}


def resample_bars(df: pd.DataFrame, freq: str) -> pd.DataFrame:
    """把日线聚成周 / 月 / 年 K；freq="D" 原样返回。

    每根 bar **标在该周期内最后一个交易日**（不是日历上的周五/月末）：x 轴上的日期
    永远是真实交易日，买卖点按日期映射时才对得上。开 = 首个交易日的开、高 = 最高、
    低 = 最低、收 = 末个交易日的收、成交量与成交额求和；没有任何交易日的周期
    （长假、停牌）不产生空 bar。只保留 OHLCV 与成交额列——复权列、状态列对 K 线图无用。
    """
    if freq not in KLINE_FREQS:
        raise ValueError(f"未知的 K 线周期 {freq!r}，只能是 {list(KLINE_FREQS)}")
    rule = KLINE_FREQS[freq][1]
    if rule is None:
        return df
    cols = [c for c in _OHLCV if c in df.columns]
    bars = df[cols].resample(rule).agg({c: _RESAMPLE_HOW[c] for c in cols})
    last_day = df.index.to_series().resample(rule).last()
    keep = bars["close"].notna()
    bars = bars[keep]
    bars.index = pd.DatetimeIndex(last_day[keep].to_numpy(), name=df.index.name)
    return bars


def _bar_for(index: pd.DatetimeIndex, day) -> pd.Timestamp:
    """交易日 → 它所属那根 bar 的 x（bar 标在周期末个交易日，所以取第一根不早于它的）。
    超出最后一根（不该发生）就归到最后一根，绝不把标注画到不存在的位置上。"""
    pos = min(int(index.searchsorted(pd.Timestamp(day))), len(index) - 1)
    return index[pos]


def _cn_amount(x: float) -> str:
    """成交额：亿 / 万 / 元。日线一根几亿、年线一根几千亿，固定单位哪头都难读。"""
    if pd.isna(x):
        return "—"
    if abs(x) >= 1e8:
        return f"{x / 1e8:,.2f} 亿"
    if abs(x) >= 1e4:
        return f"{x / 1e4:,.0f} 万"
    return f"{x:,.0f} 元"


def _cn_volume(shares: float) -> str:
    """成交量按 A 股习惯显示成**手**（100 股）：万手 / 手。"""
    if pd.isna(shares):
        return "—"
    hands = shares / 100
    return f"{hands / 1e4:,.2f} 万手" if hands >= 1e4 else f"{hands:,.0f} 手"


def _bar_hover_text(bars: pd.DataFrame) -> list[str]:
    """每根 bar 的悬停文本：开高低收、涨跌幅（对上一根 bar 的收盘）、成交量、成交额。
    第一根没有"上一根"，涨跌显示 —（不写 0）。"""
    pct = bars["close"].pct_change()
    lines = []
    for (_, row), p in zip(bars.iterrows(), pct):
        parts = [f"开 {row['open']:.2f}　高 {row['high']:.2f}",
                 f"低 {row['low']:.2f}　收 {row['close']:.2f}",
                 f"涨跌 {'—' if pd.isna(p) else f'{p:+.2%}'}"]
        if "volume" in bars.columns:
            parts.append(f"成交量 {_cn_volume(row['volume'])}")
        if "amount" in bars.columns:
            parts.append(f"成交额 {_cn_amount(row['amount'])}")
        lines.append("<br>".join(parts))
    return lines


def _marker_trace(bars: pd.DataFrame, trades: list[Trade], *, action: str) -> go.Scatter:
    """一组买（或卖）点：x 是所属 bar，y 在影线之外，悬停显示真实成交日、价、股数。"""
    buying = action == "buy"
    xs, ys, custom = [], [], []
    for t in trades:
        x = _bar_for(bars.index, t.date)
        bar = bars.loc[x]
        ys.append(bar["low"] * (1 - _MARKER_PAD) if buying else bar["high"] * (1 + _MARKER_PAD))
        xs.append(x)
        custom.append([pd.Timestamp(t.date).strftime("%Y-%m-%d"), t.price, t.shares])
    verb = "买入" if buying else "卖出"
    return go.Scatter(
        x=xs, y=ys, mode="markers", name=verb, customdata=custom,
        marker=dict(symbol="triangle-up" if buying else "triangle-down", size=11,
                    color=palette.UP if buying else palette.DOWN),
        hovertemplate=(f"{verb} %{{customdata[2]:,.0f}} 股 @ %{{customdata[1]:.2f}}"
                       "<br>成交日 %{customdata[0]}<extra></extra>"))


def kline_chart(df: pd.DataFrame, trades: list[Trade], title: str, *,
                freq: str = "D") -> go.Figure:
    """K 线 + 买卖点。`df` 是日线（prepare_bars 的输出），`freq` 见 KLINE_FREQS。

    A股红涨绿跌（铁律）。色值走色板：暗底上纯 red/green 太刺，方向不变、观感协调。
    """
    bars = resample_bars(df, freq)
    fig = go.Figure(go.Candlestick(
        x=bars.index, open=bars["open"], high=bars["high"], low=bars["low"],
        close=bars["close"], name=title,
        increasing_line_color=palette.UP, decreasing_line_color=palette.DOWN,
        text=_bar_hover_text(bars), hovertemplate="%{text}<extra></extra>"))
    if len(bars):
        buys = [t for t in trades if t.action == "buy"]
        sells = [t for t in trades if t.action == "sell"]
        if buys:
            fig.add_trace(_marker_trace(bars, buys, action="buy"))
        if sells:
            fig.add_trace(_marker_trace(bars, sells, action="sell"))
    fig.update_layout(
        title=title, height=550,
        xaxis_rangeslider_visible=False,      # 底部那条缩略滑块又占地方又不好用：滚轮就够
        hovermode="x unified",                # 悬停一根 bar：开高低收量额与买卖点一起出
        dragmode="pan",                       # 拖动 = 平移；缩放交给滚轮 / 双指（KLINE_CONFIG）
        hoverlabel=dict(align="left"),
        xaxis_hoverformat="%Y-%m-%d",          # 悬停框标题的日期：真机默认是 "Jan 30, 2026"
        # 换标的或换周期才重置视图；别的重跑（页头 pill 刷新等）保留用户缩放到的位置
        uirevision=f"{title}:{freq}",
    )
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
