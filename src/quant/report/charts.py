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

import unicodedata

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
KLINE_CONFIG: dict = {"scrollZoom": True, "doubleClick": "reset", "displaylogo": False,
                      "responsive": True}    # iframe 里随容器宽度自适应（kline_view）

#: 显示最近多少根：内部键 → (显示名, 根数；None = 全部)。密度由根数决定而不是由时间跨度
#: 决定——同样 120 根，日K是半年、周K是两年多、年K是全部，屏幕上一样疏密。
#: 默认 120 根（页面里定）：一次画十年两千多根，每根不到一个像素宽，正是"太细太密"的来源。
KLINE_SPANS: dict[str, tuple[str, int | None]] = {
    "120": ("120 根", 120),
    "250": ("250 根", 250),
    "500": ("500 根", 500),
    "all": ("全部", None),
}

#: 趋势宽度图表（首页，2026-10-06）上半幅的三条线：(均线天数, 显示名, 线型)。显示名与首页卡片标签同文
#: （tests/test_dashboard_home.py 钉住不漂）；琥珀 / 灰 / 暖白三色之间色差不够大，再用实线 / 虚线 / 点线区分
#: （同 equity_chart 给基准线加点线）。
BREADTH_LINES: tuple[tuple[int, str, str], ...] = (
    (20, "站上 20 日线", "solid"), (60, "站上 60 日线", "dash"), (200, "站上 200 日线", "dot"))

#: 趋势宽度图表显示最近多少个交易日：内部键 → (显示名, 行数；None = 全部)。同 KLINE_SPANS：键永不改。
BREADTH_SPANS: dict[str, tuple[str, int | None]] = {
    "60": ("60 个交易日", 60),
    "120": ("120 个交易日", 120),
    "all": ("全部", None),
}

#: K 线描边宽度。plotly 默认 2px：几百根挤在一起时描边比实体还宽，整根看着像一条线。
_CANDLE_LINE_WIDTH = 1

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


def _cat(day) -> str:
    """x 轴用**类目**而不是日期：日期轴会给周末与节假日留空档，每根 K 线因此被挤瘦
    约三成（一周七格只画五根）。类目轴按交易日紧排，这是所有行情软件的画法。
    类目值就是 ISO 日期串，悬停标题直接显示它，不用再管日期格式。"""
    return pd.Timestamp(day).strftime("%Y-%m-%d")


def _window(bars: pd.DataFrame, span: str) -> pd.DataFrame:
    """只留最近 N 根（KLINE_SPANS）。切的是数据不是坐标范围：plotly 的 y 轴不会跟着
    x 缩放自动重算，只设 x 范围会让最近半年的 K 线挤在十年价格区间的一小段里。"""
    if span not in KLINE_SPANS:
        raise ValueError(f"未知的显示范围 {span!r}，只能是 {list(KLINE_SPANS)}")
    n = KLINE_SPANS[span][1]
    return bars if n is None or len(bars) <= n else bars.iloc[-n:]


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


def _marker_trace(bars: pd.DataFrame, placed: list[tuple[pd.Timestamp, Trade]], *,
                  action: str) -> go.Scatter:
    """一组买（或卖）点。`placed` 是 (所属 bar 的 x, 成交) 对——映射在**切窗口之前**做好
    （见 kline_chart），这里只画：y 在影线之外，悬停显示真实成交日、价、股数。"""
    buying = action == "buy"
    xs, ys, custom = [], [], []
    for x, t in placed:
        bar = bars.loc[x]
        ys.append(bar["low"] * (1 - _MARKER_PAD) if buying else bar["high"] * (1 + _MARKER_PAD))
        xs.append(_cat(x))
        custom.append([_cat(t.date), t.price, t.shares])
    verb = "买入" if buying else "卖出"
    return go.Scatter(
        x=xs, y=ys, mode="markers", name=verb, customdata=custom,
        marker=dict(symbol="triangle-up" if buying else "triangle-down", size=11,
                    color=palette.UP if buying else palette.DOWN),
        hovertemplate=(f"{verb} %{{customdata[2]:,.0f}} 股 @ %{{customdata[1]:.2f}}"
                       "<br>成交日 %{customdata[0]}<extra></extra>"))


def kline_chart(df: pd.DataFrame, trades: list[Trade], title: str, *,
                freq: str = "D", span: str = "all") -> go.Figure:
    """K 线 + 买卖点。`df` 是日线（prepare_bars 的输出），`freq` 见 KLINE_FREQS，
    `span` 见 KLINE_SPANS（只画最近 N 根）。

    A股红涨绿跌（铁律）。色值走色板：暗底上纯 red/green 太刺，方向不变、观感协调。
    涨跌幅按**全部** bar 算再切窗口：窗口里第一根的涨跌是对它前一根的，不是 —。
    买卖点先在全部 bar 上映射到所属那根，再只留落在窗口里的——直接在窗口上映射会把
    窗口之前的成交全堆到第一根上（searchsorted 对早于首根的日期返回 0）。
    """
    full = resample_bars(df, freq)
    hover = pd.Series(_bar_hover_text(full), index=full.index) if len(full) else pd.Series(dtype=str)
    bars = _window(full, span)
    fig = go.Figure(go.Candlestick(
        x=[_cat(d) for d in bars.index], open=bars["open"], high=bars["high"],
        low=bars["low"], close=bars["close"], name=title,
        increasing=dict(line=dict(color=palette.UP, width=_CANDLE_LINE_WIDTH)),
        decreasing=dict(line=dict(color=palette.DOWN, width=_CANDLE_LINE_WIDTH)),
        text=list(hover.loc[bars.index]) if len(bars) else [],
        hovertemplate="%{text}<extra></extra>"))
    if len(bars):
        first = bars.index[0]
        placed = [(x, t) for t in trades
                  if (x := _bar_for(full.index, t.date)) >= first]
        buys = [(x, t) for x, t in placed if t.action == "buy"]
        sells = [(x, t) for x, t in placed if t.action == "sell"]
        if buys:
            fig.add_trace(_marker_trace(bars, buys, action="buy"))
        if sells:
            fig.add_trace(_marker_trace(bars, sells, action="sell"))
    fig.update_layout(
        # 标题靠左：这张图在自己的 iframe 里渲染（kline_view），没有 Streamlit 主题替它把
        # 默认居中的标题挪到左边；与面板其余图表保持同一个观感。
        title=dict(text=title, x=0, xanchor="left"), height=550,
        xaxis=dict(type="category", categoryorder="category ascending",
                   rangeslider_visible=False,    # 底部那条缩略滑块又占地方又不好用：滚轮就够
                   nticks=8),                    # 类目轴默认会试着标每一根，8 个刻度够定位
        hovermode="x unified",                    # 悬停一根 bar：开高低收量额与买卖点一起出
        dragmode="pan",                           # 拖动 = 平移；缩放交给滚轮 / 双指（KLINE_CONFIG）
        hoverlabel=dict(align="left"),
        # 图例横排放到绘图区**上方右侧**（标题在左）：竖排在右边会吃掉约一成宽度，
        # 而宽度正是每根 K 线能有几个像素的分母。
        legend=dict(orientation="h", x=1, xanchor="right", y=1.0, yanchor="bottom"),
        # 换标的 / 周期 / 范围才重置视图；别的重跑（页头 pill 刷新等）保留用户缩放到的位置
        uirevision=f"{title}:{freq}:{span}",
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
    # 横轴的日期写成数字（缺省是英文月份缩写），而且要**短**：初稿一律写 2026-09-08，一个刻度 72px，
    # 图一窄就整排斜过来（独立审查在 1024 宽下看到的）。按刻度的疏密换写法，见 _DATE_TICKS。
    fig.update_xaxes(tickformatstops=_DATE_TICKS, nticks=6)
    if points and (span := _padded_span(points[0][0], points[-1][0])) is not None:
        fig.update_xaxes(range=list(span))
    return _apply_dark(fig)


#: 日期轴的刻度怎么写，按相邻两个刻度隔多远分三档（plotly 的 tickformatstops）：
#: 不到一个月 → 「09-08」，年份另起一行、只在第一个刻度和跨年的地方写（格式串里 \n 后面那半截，
#: plotly 只在它变了的时候才画）；按月 → 「2026-09」；按年 → 「2026」。最宽的一档也只有五十来像素。
_DATE_TICKS = [
    dict(dtickrange=[None, "M1"], value="%m-%d\n%Y"),
    dict(dtickrange=["M1", "M12"], value="%Y-%m"),
    dict(dtickrange=["M12", None], value="%Y"),
]

#: 累计盈亏曲线的横轴最少铺开这么多天。
_CUM_MIN_SPAN_DAYS = 14


def _padded_span(first, last):
    """曲线的点挤在两周以内时，横轴向两边各让出一些，凑满两周；本来就够宽返回 None（交给 plotly）。

    只有一个点（刚平掉第一笔）时横轴的自动范围是零宽的，plotly 会把刻度标成
    23:59:59.9995 / 00:00:00.0005 这样的毫秒——看着像坏了（2026-11-18 真机上看到的）。

    两边至少各让一天：范围是我们钉死的，plotly 不会再替点留边，不让的话首尾两个点正好压在图框上。
    用时间戳算而不是日期：日期加减半天会把那半天丢掉（隔 1 天的两个点只凑出 13 天，
    隔 13 天的一点都没让——独立审查指出的）。
    """
    spread = (last - first).days
    if spread >= _CUM_MIN_SPAN_DAYS:
        return None
    pad = pd.Timedelta(days=max(1.0, (_CUM_MIN_SPAN_DAYS - spread) / 2))
    return pd.Timestamp(first) - pad, pd.Timestamp(last) + pad


#: 条外标注与类目标签的字号：没另设时 plotly 用的就是 12px（layout.font.size 的缺省）。
_LABEL_FONT_PX = 12
#: 条的末端到条外标注之间、标注到图的右缘之间各留的一点空（px）。
_LABEL_GAP_PX = 16
#: 类目标签左边到图的左缘留的空（px）：不留的话最长的那个标签顶着图的边。
_LABEL_PAD_PX = 14
#: 类目标签与纵轴之间 plotly 自己留的空（px，量出来的）。
_TICK_GAP_PX = 10
#: 持仓占比图里一只持仓占的高度（px）。
_BAR_ROW_PX = 44


def _text_px(text: str) -> float:
    """一段标注在等宽字体下大约多宽（px）：汉字与全角标点一个字宽，其余 0.6 个字宽。

    用来给横向条形图**留边**：条外的直接标注画在绘图区之外，绘图区多宽要到浏览器里才知道，
    而最长的那根条一定顶到绘图区的右端——它的标注能不能完整显示，只看右边距够不够放下这段字。
    真浏览器里量过两种写法（「百分比 · 金额 元」十五个半角字符加一个汉字：120px；六位代码加四个汉字的名称：98px），
    与这里算的差不到 1px。
    """
    wide = sum(unicodedata.east_asian_width(ch) in ("W", "F") for ch in text)
    return _LABEL_FONT_PX * (wide + 0.6 * (len(text) - wide))


def _bar_margins(categories: list[str], notes: list[str]) -> dict:
    """横向条形图的左右边距：左边放得下最长的类目标签（外侧再留一点空），右边放得下最长的条外标注。

    右边距按**最长的标注**留，不按最长那根条的标注留：短一些的条标注可能更长（多一个「（按成本）」），
    而它离右端有多远取决于图有多宽——按最坏情况留，标注就不会被裁。
    代价是这两段边距是固定的：图太窄时留给条的地方就没了，所以用它的图都要独占一整行。
    左边距只是给外侧留空：算少了 plotly 会自己撑开（标签不会被裁，只是又顶到边上）。
    """
    left = max((_text_px(c) for c in categories), default=0.0)
    right = max((_text_px(n) for n in notes), default=0.0)
    return dict(l=round(left) + _TICK_GAP_PX + _LABEL_PAD_PX, r=round(right) + _LABEL_GAP_PX)


def position_weights_chart(rows: list[dict]) -> go.Figure:
    """持仓占比：横向条按市值降序，单一琥珀 + 右端直接标注百分比与市值。

    不做饼图：持仓通常 3~10 只，条形长度的对比精度远高于扇形角度，且降序
    排完一眼看出集中度。`rows` 来自 analytics.position_weights（已排序）；
    无市价按成本顶上的行带 by_cost=True，条上打「按成本」标——不打标的话，
    一个没有市价的重仓会看着和有市价的一样可信。

    **这张图要独占一整行**（2026-11-18）。类目标签加条外标注合起来要两百多像素的固定边距，
    放进半幅宽的一栏里，1024 宽下留给条的地方只剩十几像素（独立审查量的）。所以高度只按行数给、
    上下边距收紧：整行宽的图里三根条撑不起三百多像素，条与条之间全是空。
    """
    categories = [f"{r['symbol']} {r['name']}".strip() for r in rows]
    notes = [f"{r['weight']:.1%} · {r['value']:,.0f} 元"
             + ("（按成本）" if r["by_cost"] else "") for r in rows]
    fig = go.Figure()
    if rows:
        fig.add_trace(go.Bar(
            x=[r["value"] for r in rows], y=categories,
            orientation="h", marker_color=palette.PRIMARY,
            text=notes, textposition="outside", cliponaxis=False,
            hovertemplate="%{y}<br>%{text}<extra></extra>"))
    # 左右边距按字数留（_bar_margins）：2026-11-18 之前右边只留 40px，最长那根条的标注
    # 有一半画在图外被裁掉（真机截图里只剩前面的百分比，后面的金额看不见）。
    margin = _bar_margins(categories, notes) | dict(t=56, b=40)
    fig.update_layout(
        title="持仓占比（按市值）", showlegend=False,
        height=margin["t"] + margin["b"] + _BAR_ROW_PX * max(len(rows), 1),
        # 首行（最大持仓）在最上面：plotly 横向条默认把第一条画在最下面。
        # 纵轴钉成类目轴：有一只票没有名称时它的标签只剩六位代码，plotly 会把整条轴猜成数值轴——
        # 刻度变成 0.999999M，别的条全部消失（2026-11-18 真机上用一只没有名称的持仓试出来的）。
        yaxis=dict(autorange="reversed", type="category"),
        margin=margin)
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
    categories = [names.get(source, source) or "—" for source, _ in rows]
    values = [summary.get("total_realized") or 0.0 for _, summary in rows]
    notes = [_source_note(summary) for _, summary in rows]
    fig = go.Figure()
    if rows:
        fig.add_trace(go.Bar(
            x=values, y=categories,
            orientation="h", marker_color=palette.PRIMARY,
            # 亏损的条向左伸，条外标注跟着画到条的左边——正好压在类目标签上，两行字叠成一团
            # （2026-11-18 真机上看到的）。所以亏损那几行条上不写字，标注放到零线右侧（下面那几条注记）：
            # 那一行零线右边本来就是空的。悬停读 customdata，两种行说的是同一句话。
            text=[note if value >= 0 else "" for value, note in zip(values, notes)],
            customdata=notes, textposition="outside", cliponaxis=False,
            hovertemplate="%{y}<br>%{customdata}<extra></extra>"))
    for value, category, note in zip(values, categories, notes):
        if value < 0:
            fig.add_annotation(x=0, y=category, text=note, showarrow=False,
                               xanchor="left", xshift=6)
    fig.add_vline(x=0, line_color=palette.HAIRLINE, line_width=1)
    fig.update_layout(title="按来源对比：总已实现（元，含分红）", showlegend=False,
                      height=max(300, 140 + 44 * len(rows)),
                      margin=_bar_margins(categories, notes))
    return _apply_dark(fig)


def _source_note(summary: dict) -> str:
    """条右端那句标注：总已实现 + 胜率。胜率 None（该组没有平仓）→ —。"""
    total = summary.get("total_realized") or 0.0
    money = f"{total:,.0f}" if round(total) == 0 else f"{total:+,.0f}"
    win = summary.get("win_rate")
    return f"{money} 元 · 胜率 {'—' if win is None else f'{win:.0%}'}"


# ================================================================ 趋势宽度（首页，2026-10-06）

def breadth_chart(table: pd.DataFrame, span: int | None) -> go.Figure:
    """首页「趋势宽度」卡片下方那张图：上半幅三条「站上 N 日线」占比线 + 50% 虚线，下半幅 52 周新高红柱向上 /
    新低绿柱向下。`table` 是指标表（按日期升序，只含有快照的日子），`span` 只画最近多少行，None 画全部。

    **单图、一条横轴、两个纵轴区域，不用 make_subplots**：分行子图各有自己的横轴，`hovermode="x unified"` 会上下各弹
    一个提示框，看不到「同一天的三个占比加新高新低」（真浏览器实测）。横轴是交易日的类目轴（同 K 线图），不留周末空档。
    分母为 0 的日子记 NaN——线断开，不画成 0%。下半幅的刻度用 tickvals/ticktext 显示绝对值（tickformat 打不出绝对值），
    两侧各按自己的最大值标一个刻度（新低可达上千、新高只有几百，共用一个最大值会让较小的一侧落在范围外、没有刻度）；
    新低柱 y 取负、悬停用 customdata 显示正数；悬停模板里不写 trace 名（统一悬停自带名字，写了就成「新低 : 新低 87 只」）。
    不缩放（`dragmode=False`）：隐藏工具栏后拖动缩放仍会生效，且每次重跑就复位。
    """
    rows = table if span is None else table.tail(span)
    x = [pd.Timestamp(d).strftime("%Y-%m-%d") for d in rows["date"]]
    fig = go.Figure()
    for n, name, dash in BREADTH_LINES:
        denom = rows[f"n{n}"].astype(float)
        ratio = (rows[f"above{n}"] / denom.where(denom > 0)).tolist()     # 分母 0 → NaN
        fig.add_trace(go.Scatter(x=x, y=ratio, name=name, mode="lines", line=dict(width=2, dash=dash),
                                 hovertemplate="%{y:.1%}"))
    fig.add_hline(y=0.5, line=dict(dash="dot", color=palette.NEUTRAL))
    highs = [int(v) for v in rows["new_high"]]       # int(v) 靠 breadth.compute 写的是 int64 列
    lows = [int(v) for v in rows["new_low"]]
    fig.add_trace(go.Bar(x=x, y=highs, name="新高", marker_color=palette.UP, hovertemplate="%{y:,d} 只", yaxis="y2"))
    fig.add_trace(go.Bar(x=x, y=[-v for v in lows], name="新低", marker_color=palette.DOWN, customdata=lows,
                         hovertemplate="%{customdata:,d} 只", yaxis="y2"))
    hi_top = max(highs, default=0) or 1
    lo_top = max(lows, default=0) or 1
    fig.update_layout(
        xaxis=dict(type="category", categoryorder="category ascending", nticks=8, anchor="y2"),
        yaxis=dict(domain=[0.36, 1.0], range=[0, 1], tickformat=".0%", tickvals=[0, 0.25, 0.5, 0.75, 1]),
        yaxis2=dict(domain=[0.0, 0.30], anchor="x", tickvals=[-lo_top, 0, hi_top],
                    ticktext=[f"{lo_top:,d}", "0", f"{hi_top:,d}"]),
        barmode="relative", hovermode="x unified", hoversubplots="axis", dragmode=False, height=420,
        margin=dict(t=40, b=30, l=50, r=20), legend=dict(orientation="h", y=1.02, yanchor="bottom"))
    return _apply_dark(fig)
