"""「市场概览」落地页（spec §2）。只排版：判断全在 quant.market.*（可单测）。

两个 fragment：行情区按 session 的决定用 30 秒或 60 秒；按钮区有任务在跑时 2 秒。
两个缓存入口（25 秒 / 600 秒）都 show_spinner=False——它默认会把函数名显示出来。
失败的一轮（与失败的日 K）不进缓存，见 _Uncached；每一跳至多整页重跑一次，见 RERUN_GUARD。
"""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import streamlit as st

import guide
import theme
import ui
from quant import market
from quant.market import breadth, live, session, sources, trend, turnover
from quant.market.session import Calendar, Decision, State, TradeDay
from quant.market.store import MarketStore
from quant.market.trend import ButtonState as B
from quant.report import charts, fmt, minicharts
from quant.runner import process, progress


def _store() -> MarketStore:
    return MarketStore(ui.ROOT / "data" / "market")


# ---------------------------------------------------------------- 取数（两个缓存入口）
class _Uncached(Exception):
    """缓存函数里抛它 = 这次的结果**不进** st.cache_data（函数抛异常时它什么都不存），
    调用方接住后照常用 `.snap`（日 K 失败时为 None）。

    失败的一轮（分时全挂 / 没有行情 / 行情是兜底的旧值）必须下一跳就重试，不能被钉 25/600 秒；
    也免得 `_decide(snap)` 随「这一跳碰巧命中哪个缓存入口」来回翻——一个入口存着成功轮、另一个
    存着失败轮时，两边判出的间隔不同，整页会无限重跑。"""

    def __init__(self, snap: live.LiveSnapshot | None) -> None:
        super().__init__()
        self.snap = snap


def _round(allow_cross_day: bool, hint: date | None) -> live.LiveSnapshot:
    """两个缓存入口共用的函数体：取一轮，失败就抛 _Uncached（在缓存函数里抛，才不会被存下）。"""
    snap = live.fetch_round(live.HTTP_GET, market.now(), hint, live.STORE, allow_cross_day)
    if not snap.source_ok or snap.quotes is None or snap.quotes.fallback:
        raise _Uncached(snap)
    return snap


@st.cache_data(ttl=session.SHORT_TTL_S, show_spinner=False)
def _round_short(allow_cross_day: bool, hint: date | None) -> live.LiveSnapshot:
    return _round(allow_cross_day, hint)


@st.cache_data(ttl=session.LONG_TTL_S, show_spinner=False)
def _round_long(allow_cross_day: bool, hint: date | None) -> live.LiveSnapshot:
    return _round(allow_cross_day, hint)


def _snapshot(decision: Decision, hint: date | None) -> live.LiveSnapshot:
    fn = _round_short if decision.cache_ttl == session.SHORT_TTL_S else _round_long
    try:
        return fn(session.allow_cross_day(decision.state), hint)
    except _Uncached as e:
        return e.snap


DECISION_KEY = "home_decision"     # session_state：上一轮用真实快照判出的 (state, interval, ttl)


def _remembered_decision(cal: Calendar | None) -> Decision:
    """选缓存入口与 allow_cross_day 时优先用上一轮的真实判定：没有日历时 _decide(None) 是
    「未知」，会让交易时段也允许跨日旧值、休市日却走 25 秒缓存。"""
    raw = st.session_state.get(DECISION_KEY)
    if raw:
        try:
            return Decision(State(raw[0]), int(raw[1]), int(raw[2]))
        except (ValueError, TypeError, IndexError):
            pass
    return _decide(None, cal)[0]


# ---------------------------------------------------------------- 判定
def _calendar() -> Calendar | None:
    return _store().load_calendar()


def _decide(snap: live.LiveSnapshot | None, cal: Calendar | None) -> tuple[Decision, TradeDay]:
    now = market.now()
    ok = bool(snap and snap.source_ok)
    src = snap.trade_date if ok else None
    td = session.trade_day(now.date(), cal, ok, src, now.time())
    return session.decide(now, td), td


def _hint(cal: Calendar | None) -> date | None:
    """池子与新浪校验用的「预期交易日」（spec §3）：本轮源日期由 live 自己拿，这里只给
    日历推算 → 上一轮成功的日期 这两级兜底。**不能**把上一轮的日期当 source_date 传，
    否则第一行就 return，日历永远不被咨询。"""
    now = market.now()
    return session.expected_trade_day(None, cal, now.time(),
                                      live.STORE.last_trade_date, today=now.date())


# ---------------------------------------------------------------- 渲染块
def _status(decision: Decision, snap: live.LiveSnapshot) -> None:
    """戳的种类由 session.stamp_kind 定（日期 / 分时最后一点 / 行情时间戳），页面只拼。"""
    text = guide.HOME_STATE_TEXT[decision.state.value]
    q = snap.quotes
    m = snap.minutes.get("sh000001")
    has_points = bool(m and m.points)
    kind = session.stamp_kind(decision.state)
    if kind == "date":
        if snap.trade_date:
            text += f" · {snap.trade_date.isoformat()}"
    elif kind == "minute":
        if has_points:
            text += f" · {m.points[-1][0]:%H:%M}"
        elif q is not None and q.ts is not None:
            text += f" · {q.ts:%H:%M}"
    elif q is not None and q.ts is not None:
        text += f" · {q.ts:%H:%M:%S}"
    # is_stale 收的是**完整的日期时间**：昨天 15:00 的点在今早必须判成延迟
    last_point = datetime.combine(m.trade_date, m.points[-1][0]) if has_points else None
    stale = session.is_stale(market.now(), decision.state, last_point, q.ts if q else None)
    st.html(theme.status_line(text, running=session.is_live(decision.state),
                              extra=guide.HOME_STALE if stale else ""))


def _fallback_note(block: live.Block) -> str:
    """最近成功值的交易日 ≠ 当前日期（market.now().date()）→ 时刻带日期（§3）。不拿
    snap.trade_date 比：失败那轮它是从 store 抄来的，永远等于块自己的日期。"""
    if not block.fallback or block.ts is None:
        return ""
    ts = block.ts
    if block.trade_date == market.now().date():
        stamp = f"{ts:%H:%M}"
    else:
        stamp = f"{fmt.cn_month_day(ts)} {ts:%H:%M}"      # 与 turnover 同一种日期写法
    return guide.HOME_FALLBACK.format(stamp=stamp)


def _index_cards(snap: live.LiveSnapshot) -> None:
    # 数据来源与延迟：在小标题后面的帮助圆点里（原先是页面最底下常驻的一行灰字）。
    st.html(theme.section(guide.HOME_INDEX_TITLE, help=guide.HOME_SECTION_HELP["index"]))
    if snap.quotes is None:
        st.caption(guide.HOME_UNAVAILABLE)
        return
    if note := _fallback_note(snap.quotes):
        st.caption(note)
    quotes = snap.quotes.value
    for col, (code, name) in zip(st.columns(len(market.INDEXES)), market.INDEXES):
        q = quotes[code]
        m = snap.minutes.get(code)
        pts = [(i, p) for i, (_, p) in enumerate(m.points)] if m else []
        img = minicharts.sparkline(pts, q.prev_close) if pts else None
        sub = (f"{q.change:+.2f}", f"{q.pct:+.2f}%")    # 分两行：合一行时窄卡片有的折有的不折，小图就高低不齐
        col.html(theme.stat_card(name, f"{q.price:.2f}", sub=sub, color=fmt.signed_color(q.change),
                                 img=img))


def _turnover(snap: live.LiveSnapshot) -> None:
    q = snap.quotes
    if q is None:
        return
    quotes = q.value
    sh_wan, sz_wan = quotes["sh000001"].amount_wan, quotes["sz399001"].amount_wan
    if sh_wan <= 0 or sz_wan <= 0:
        return                          # spec §3「两项都有值且 > 0」：盘前 [37] 为 0，不渲染「0.00 万亿」
    now = market.now()
    today = now.date()
    trade_date = snap.trade_date or today
    ts = q.ts.time() if q.ts else None  # 没有行情时刻就不写「截至」，不拿渲染时刻顶替
    prev, pct_ok = _prev_day(trade_date, quotes, q.source, now, today)
    st.caption(turnover.line(now, today, trade_date, sh_wan + sz_wan, ts, prev, pct_ok),
               help=guide.HOME_TURNOVER_HELP)


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def _klines(trade_date: date, settled: bool):
    """缓存键 = (交易日, 是否已定格)：交易日一变就重取，定格后再重取一次做口径检查。
    任一只失败就抛 _Uncached（不进缓存）：否则一次抖动就把「没有上一交易日」钉 24 小时。"""
    sh = sources.fetch(live.HTTP_GET, sources.tencent_kline_url("sh000001"),
                       lambda raw: sources.parse_tencent_kline(raw, "sh000001"))
    sz = sources.fetch(live.HTTP_GET, sources.tencent_kline_url("sz399001"),
                       lambda raw: sources.parse_tencent_kline(raw, "sz399001"))
    if not (sh.ok and sz.ok):
        raise _Uncached(None)
    return sh.value, sz.value


def _prev_day(trade_date: date, quotes, source: str, now: datetime, today: date):
    settled = turnover.settled(now, trade_date, today)
    try:
        sh, sz = _klines(trade_date, settled)
    except _Uncached:
        return None, None
    prev = turnover.previous_day(sh, sz, trade_date)
    pct_ok = None
    if settled and source == "tencent":
        pct_ok = turnover.consistent(sh, sz, trade_date, quotes["sh000001"].amount_wan,
                                     quotes["sz399001"].amount_wan)
    return prev, pct_ok


# ---------------------------------------------------------------- 行情区（两个 fragment）
INTERVAL_KEY = "home_interval"      # session_state：fragment 用真实快照判出的间隔，页面据此选 fragment
RERUN_GUARD = "home_rerun_guard"    # session_state：这一跳已经整页重跑过一次


def _render_live(expected_interval: int, expected_key: str) -> None:
    cal = _calendar()
    hint = _hint(cal)
    snap = _snapshot(_remembered_decision(cal), hint)   # 缓存入口与 allow_cross_day 用上一轮的真实判定
    decision, _ = _decide(snap, cal)                    # 用真实源日期重判
    if not session.allow_cross_day(decision.state):
        # 本轮可能是按上一轮「允许跨日」取的（未知 → 交易中同为 30 秒行，不会整页重跑）
        snap = live.without_cross_day(snap, market.now().date())
    st.session_state[DECISION_KEY] = (decision.state.value, decision.interval_s, decision.cache_ttl)
    # 每轮都记下想要的间隔：页面按它选 fragment。没有日历的休市日，_decide(None) 是「未知」
    # （30 秒行）而 _decide(snap) 是「休市」（60 秒行），不记下来的话整页重跑后又选回 30 秒。
    st.session_state[INTERVAL_KEY] = decision.interval_s
    if decision.interval_s != expected_interval or _job_key() != expected_key:
        # 间隔变了，或跨过了收盘数据就绪时刻 → 整页重跑换 fragment。**每一跳至多一次**：
        # 重跑后那一轮碰巧失败时判定会翻回去，再重跑就是死循环；这一跳先照常画完，下一跳自愈。
        if not st.session_state.get(RERUN_GUARD):
            st.session_state[RERUN_GUARD] = True
            st.rerun()
    st.session_state.pop(RERUN_GUARD, None)
    _status(decision, snap)
    _index_cards(snap)
    _turnover(snap)
    _breadth_and_thermo(snap, decision)          # Task 22


@st.fragment(run_every=f"{session.LIVE_INTERVAL_S}s")
def _live_30(expected_key: str) -> None:
    _render_live(session.LIVE_INTERVAL_S, expected_key)


@st.fragment(run_every=f"{session.TICK_INTERVAL_S}s")
def _live_60(expected_key: str) -> None:
    _render_live(session.TICK_INTERVAL_S, expected_key)


def _page_interval() -> int:
    """页面级选 fragment：优先用 fragment 上一轮用真实快照判出的间隔，没有才按时钟。"""
    remembered = st.session_state.get(INTERVAL_KEY)
    if remembered in (session.LIVE_INTERVAL_S, session.TICK_INTERVAL_S):
        return remembered
    return _decide(None, _calendar())[0].interval_s


def _job_key() -> str:
    """按钮区状态的粗粒度键：跨过 BREADTH_READY 时它会变，行情 fragment 据此整页重跑。"""
    return "ready" if market.breadth_ready(market.now()) else "early"


def _breadth_and_thermo(snap: live.LiveSnapshot, decision: Decision) -> None:
    left, right = st.columns([1.4, 1])
    with left:
        _distribution(snap, decision)
    with right:
        _thermometer(snap)


def _distribution(snap: live.LiveSnapshot, decision: Decision) -> None:
    b = snap.breadth
    scope = b.scope if b is not None else ""
    # 分档规则在小标题后面的帮助圆点里（原先是分布图下面常驻的一行灰字）。只在真画得出
    # 分布图时才有：只有家数的那种兜底数据，圆点里讲二十三档就是指着空气。
    tip = ""
    if b is not None and isinstance(b.value, sources.Distribution):
        # 按数据自己的日期判：盘前/状态未知时显示的是上一交易日的块，那天早已收盘封住
        closed = session.sealed_at_close(decision.state, b.trade_date, market.now().date())
        sealed = guide.HOME_SEALED_CLOSE if closed else guide.HOME_SEALED_INTRADAY
        tip = guide.HOME_SECTION_HELP["dist"].format(sealed=sealed)
    st.html(theme.section(guide.HOME_DIST_TITLE, theme.pill(scope, "idle") if scope else "",
                          help=tip))
    if b is None:
        st.caption(guide.HOME_UNAVAILABLE)
        return
    if note := _fallback_note(b):
        st.caption(note)
    v = b.value
    up_label, flat_label, down_label = guide.HOME_COUNT_LABELS
    up, flat, down = v.up, v.flat, v.down
    st.html(f'<div class="qd-card-sub">'
            f'<span class="qd-up">{up_label} {up}</span> · {flat_label} {flat} · '
            f'<span class="qd-down">{down_label} {down}</span></div>')
    st.html(theme.stat_card("", "", img=minicharts.ratio_bar(up, flat, down)))
    if isinstance(v, sources.Distribution):
        st.html(theme.stat_card("", "", img=minicharts.histogram(v.buckets)))
    else:
        st.caption(guide.HOME_COUNTS_ONLY)        # 腾讯兜底：只有家数，画不了分布图


THERMO_HELP_ALIGN = "end"     # 温度计整块在页面右侧那一栏：提示框向左展开，免得越出视口右缘


def _thermometer(snap: live.LiveSnapshot) -> None:
    st.html(theme.section(guide.HOME_THERMO_TITLE, theme.pill(guide.HOME_THERMO_PILL, "idle")))
    t = snap.thermo
    if t is None:
        st.caption(guide.HOME_UNAVAILABLE)
        return
    if note := _fallback_note(t):
        st.caption(note)
    v: live.Thermo = t.value
    denom = v.zt_tc + v.zb_tc
    rate = fmt.MISSING if denom == 0 else f"{100 * v.zb_tc / denom:.1f}%"
    lbc = fmt.MISSING if v.max_lbc is None else guide.HOME_LBC.format(n=v.max_lbc)
    labels, tips = guide.HOME_THERMO_LABELS, guide.HOME_HELP
    c1, c2 = st.columns(2)
    c1.html(theme.stat_card(labels["limit_up"], str(v.limit_up), color=fmt.UP,     # 没有副文 → 颜色上数值（Task 20）
                            help=tips["limit_up"], help_align=THERMO_HELP_ALIGN))
    c2.html(theme.stat_card(labels["limit_down"], str(v.limit_down), color=fmt.DOWN,
                            help=tips["limit_down"], help_align=THERMO_HELP_ALIGN))
    c3, c4 = st.columns(2)
    c3.html(theme.stat_card(labels["zb_rate"], rate, note=guide.HOME_ZB_COUNT.format(n=v.zb_tc),
                            help=tips["zb_rate"], help_align=THERMO_HELP_ALIGN))
    c4.html(theme.stat_card(labels["max_lbc"], lbc, help=tips["max_lbc"], help_align=THERMO_HELP_ALIGN))


def _first_run_hint() -> None:
    if not process.has_ever_run(ui.RUNS_DIR):
        st.page_link(ui.page_ref("guide"), label="第一次用？先看「使用说明」",
                     icon=":material/menu_book:", help=guide.guide_page("guide").lead)


# ---------------------------------------------------------------- 趋势宽度四卡 + 按钮区
TREND_WINDOWS = ((20, "above20", "n20"), (60, "above60", "n60"), (200, "above200", "n200"))
#: 四张趋势卡片提示框的展开方向（按列序）：右半边两张向左展开，免得越出视口右缘。
TREND_HELP_ALIGN = ("start", "start", "end", "end")


def _trend_cards(ms: MarketStore) -> None:
    table = ms.load_breadth()           # 读不出来、列不全 → None：只这一块降级（§5.5）
    if table is None or table.empty:
        st.html(theme.section(guide.HOME_TREND_TITLE))
        return
    asof = pd.Timestamp(table["date"].iloc[-1]).date()      # 表里只有有快照的日子，末行就是真实的最后一天
    # 小标签只说数据是哪天的；统计范围与下面那张图怎么读，在帮助圆点里
    # （原先一半挤在这个小标签里、一半是图表下面常驻的一行灰字）。
    pill = theme.pill(guide.HOME_TREND_ASOF.format(day=asof), "idle")
    st.html(theme.section(guide.HOME_TREND_TITLE, pill, help=guide.HOME_SECTION_HELP["trend"],
                          help_wide=True))
    last = table.iloc[-1]
    have, cal, gaps = set(ms.snapshot_dates()), ms.load_calendar(), ms.permanent_gaps()
    cols = st.columns(4)
    for i, (col, (n, above, denom)) in enumerate(zip(cols, TREND_WINDOWS)):
        label = guide.HOME_ABOVE_MA.format(n=n)
        tip = dict(help=guide.HOME_HELP["above_ma"].format(n=n), help_align=TREND_HELP_ALIGN[i])
        if last[denom] == 0:
            col.html(theme.stat_card(label, fmt.MISSING, note=_shortfall_note(n, asof, cal, have, gaps), **tip))
            continue
        pct = 100 * last[above] / last[denom]
        col.html(theme.stat_card(label, f"{pct:.1f}%", sub=f"{int(last[above])}/{int(last[denom])}", **tip))
    hl_tip = dict(help=guide.HOME_HELP["high_low"], help_align=TREND_HELP_ALIGN[3])
    if last["n_hl"] == 0:
        note = _shortfall_note(breadth.HIGH_LOW_WINDOW, asof, cal, have, gaps)
        cols[3].html(theme.stat_card(guide.HOME_HIGH_LOW, fmt.MISSING, note=note, **hl_tip))
    else:
        cols[3].html(theme.stat_card(guide.HOME_HIGH_LOW, f"{int(last['new_high'])} / {int(last['new_low'])}",
                                     sub=guide.HOME_HL_TOTAL.format(n=int(last["n_hl"])), **hl_tip))
    _trend_chart(table)


def _trend_chart(table: pd.DataFrame) -> None:
    """四张卡片下方的正式图表（2026-10-06）：小图没有坐标轴，用户看不懂；改成带日期横轴、百分比纵轴、图例与悬停的
    两行图。它在盘中 30 秒刷新的 fragment 之外（收盘数据一天一变），一张交互图不影响刷新节奏。切换用 st.radio
    记在 session_state（键 home_trend_span），整页重跑也保留。"""
    span = st.radio(guide.HOME_TREND_SPAN_LABEL, list(charts.BREADTH_SPANS), horizontal=True,
                    key="home_trend_span", format_func=lambda k: charts.BREADTH_SPANS[k][0])
    st.plotly_chart(charts.breadth_chart(table, charts.BREADTH_SPANS[span][1]), width="stretch",
                    config={"displayModeBar": False, "displaylogo": False})


def _shortfall_note(n: int, asof: date, cal: Calendar | None, have: set[date], gaps: set[date]) -> str:
    """分母为 0 的卡片写什么：判断在 trend.shortfall（按这张卡自己的回看期），这里只选文案。"""
    s = trend.shortfall(n, asof, cal, have, gaps)
    if s.kind == "gap":
        return guide.HOME_TREND_GAP.format(days="、".join(d.isoformat() for d in s.gap_days), n=s.count)
    return guide.HOME_TREND_SHORT.format(n=s.count)


def _job_area(ms: MarketStore) -> None:
    state, busy, broken, message = None, None, False, ""
    try:
        state = process.read_state("market_breadth", ui.RUNS_DIR)
        busy = process.any_running(ui.RUNS_DIR)
    except RuntimeError as e:          # 状态文件损坏：由 button_state 判成 BROKEN，报错、不给按钮
        broken, message = True, str(e)
    running = state is not None and state.status == process.RUNNING
    now = market.now()
    data = dict(snapshot_dates=ms.snapshot_dates(), calendar=ms.load_calendar(),
                permanent=ms.permanent_gaps(), now=now, ready=market.BREADTH_READY,
                window=market.WINDOW_TRADING_DAYS)
    bstate = trend.button_state(
        state_broken=broken, running=running,
        busy_other=bool(busy) and busy != "market_breadth",
        last_failed=state is not None and state.status == process.FAILED, **data)
    if bstate is B.BROKEN:
        st.error(message)
        return
    if bstate is B.RUNNING:
        # **一行：进度、停止、然后是那个点不了的开始按钮——开始按钮始终在这一行的最右端。**
        # 任务结束（或被停掉）的那一刻，光标底下的按钮不许从「停止」换成「开始」：这一页上停止是
        # 被鼓励的动作（"可随时停止、下次接着补"），手快再点一下就把刚停的任务又起了一遍。
        # 控制台卡片与任务条靠"上下错开"防这件事，这里不行——这一区在页面**最底下**：
        # 任务一停，进度那一行没了、页面变短，浏览器把滚到底的内容整体往下挪，上面那一行的
        # 开始按钮就落到停止原来的位置（2026-11-16 真机量到：错开成两行之后仍然重合 2 px，
        # 没有下面那行灰字时完全重合；初稿两个按钮都在第一行右端，独立审查量到完全重合）。
        # 放进同一行：运行前后这一行的高度不变，开始按钮横竖都不动；停止在它左边，
        # 任务停下之后那个位置是空的。
        # 停下之后会是哪个按钮，按"没在跑"再判一次：从没准备过 → 开始准备，补到一半 →
        # 更新收盘数据；已经补齐（跑完之后本来就没有按钮）时就不画它。
        idle = trend.button_state(state_broken=False, running=False, busy_other=False,
                                  last_failed=False, **data)
        prog = progress.parse_market_breadth(process.read_log(state))
        detail = ui.running_row(state, prog, stop_key="home_breadth_stop",
                                trailing=lambda: _start_button(idle, disabled=True))
        if detail:
            st.caption(detail)
        return
    if bstate is B.BUSY_OTHER:
        st.caption(guide.HOME_TREND_BUSY)
        return
    if bstate in (B.FAILED_START, B.FAILED_UPDATE):
        log = process.read_log(state)
        st.caption(progress.parse_market_breadth(log).extras.get("错误") or guide.HOME_JOB_FAILED)
        with st.expander(guide.HOME_LOG_TITLE):
            st.code(log or guide.HOME_LOG_EMPTY, language="text", height=ui.LOG_BOX_HEIGHT)
    if bstate is B.NOT_READY:
        st.caption(guide.HOME_TREND_NOT_READY.format(day=fmt.cn_month_day(now)))
        return
    if _start_row(bstate):
        ui.start_job("market_breadth", {})     # 在那一行外面起：启动失败的那句话要占整行


def _start_row(bstate: B) -> bool:
    """「开始准备 / 更新收盘数据」那一行；返回这一轮有没有按下。不该有按钮的状态什么都不画。

    一行靠右：左边那句话（只有从没准备过时才有），右端是按钮——操作按钮一律靠右（2026-11-16
    第二轮）。一个横向容器，不用按比例的分栏：1024 宽时按钮那一栏只有一百三十几像素，
    「更新收盘数据」会折成两行（独立审查量的）；这样写按钮永远是它自己的宽度。
    """
    if _start_label(bstate) is None:
        return False
    with st.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center"):
        if bstate is B.NEVER:
            st.caption(guide.HOME_TREND_NEVER)
        return _start_button(bstate)


def _start_label(bstate: B) -> tuple[str, str] | None:
    """这个状态下该出哪个开始按钮：(按钮上的字, key)；不该有按钮时是 None。"""
    if bstate in (B.NEVER, B.FAILED_START):
        return guide.HOME_BTN_START, "home_breadth_start"
    if bstate in (B.UPDATE, B.FAILED_UPDATE):
        return guide.HOME_BTN_UPDATE, "home_breadth_update"
    return None                                  # 已经补齐 / 还没到时刻 / 别的状态：没有按钮


def _start_button(bstate: B, *, disabled: bool = False) -> bool:
    """开始按钮自己（画在调用方给的那一行里）。说明挂在按钮身上（原生按钮提示，支持加粗与列表）。
    `disabled` 给运行中那一轮用：按钮留在这一行的最右端、只是点不了（见 _job_area）。"""
    found = _start_label(bstate)
    if found is None:
        return False
    label, key = found
    return st.button(label, key=key, icon=":material/play_arrow:", disabled=disabled,
                     help=guide.JOB_HELP["market_breadth"])


@st.fragment(run_every=ui.AUTO_REFRESH_S)
def _job_live(was_busy: str) -> None:
    ms = _store()
    _job_area(ms)
    try:
        busy = process.any_running(ui.RUNS_DIR) or ""
    except RuntimeError:
        busy = was_busy
    if busy != was_busy:
        st.rerun()                # 跑完/停了 → 整页重跑，宽度卡片跟着刷新


@st.fragment
def _job_idle(was_busy: str) -> None:
    _job_area(_store())


def page_home() -> None:
    ui.page_head("市场概览")
    _first_run_hint()
    key = _job_key()
    (_live_30 if _page_interval() == session.LIVE_INTERVAL_S else _live_60)(key)
    ms = _store()
    _trend_cards(ms)
    try:
        busy = process.any_running(ui.RUNS_DIR) or ""
    except RuntimeError:
        busy = ""
    (_job_live if busy else _job_idle)(busy)      # 任一任务在跑都 2 秒一跳（§4.4），跑完才能自己发现
