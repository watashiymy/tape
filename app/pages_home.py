"""「市场概览」落地页（spec §2）。只排版：判断全在 quant.market.*（可单测）。

两个 fragment：行情区按 session 的决定用 30 秒或 60 秒；按钮区有任务在跑时 2 秒。
两个缓存入口（25 秒 / 600 秒）都 show_spinner=False——它默认会把函数名显示出来。
"""
from __future__ import annotations

from datetime import date, datetime

import streamlit as st

import guide
import theme
import ui
from quant import market
from quant.market import live, session, sources, turnover
from quant.market.session import Decision, State, TradeDay
from quant.market.store import MarketStore
from quant.report import fmt, minicharts
from quant.runner import process


def _store() -> MarketStore:
    return MarketStore(ui.ROOT / "data" / "market")


# ---------------------------------------------------------------- 取数（两个缓存入口）
def _round(allow_cross_day: bool, hint: date | None) -> live.LiveSnapshot:
    return live.fetch_round(live.HTTP_GET, market.now(), hint, live.STORE, allow_cross_day)


@st.cache_data(ttl=session.SHORT_TTL_S, show_spinner=False)
def _round_short(allow_cross_day: bool, hint: date | None, _key: str) -> live.LiveSnapshot:
    return _round(allow_cross_day, hint)


@st.cache_data(ttl=session.LONG_TTL_S, show_spinner=False)
def _round_long(allow_cross_day: bool, hint: date | None, _key: str) -> live.LiveSnapshot:
    return _round(allow_cross_day, hint)


def _snapshot(decision: Decision, hint: date | None) -> live.LiveSnapshot:
    allow = decision.state not in (State.AUCTION, State.WAIT_OPEN, State.TRADING)
    fn = _round_short if decision.cache_ttl == session.SHORT_TTL_S else _round_long
    return fn(allow, hint, "v1")


DECISION_KEY = "home_decision"     # session_state：上一轮用真实快照判出的 (state, interval, ttl)


def _remembered_decision() -> Decision:
    """选缓存入口与 allow_cross_day 时优先用上一轮的真实判定：没有日历时 _decide(None) 是
    「未知」，会让交易时段也允许跨日旧值、休市日却走 25 秒缓存。"""
    raw = st.session_state.get(DECISION_KEY)
    if raw:
        try:
            return Decision(State(raw[0]), int(raw[1]), int(raw[2]))
        except (ValueError, TypeError, IndexError):
            pass
    return _decide(None)[0]


# ---------------------------------------------------------------- 判定
def _calendar():
    return _store().load_calendar()


def _decide(snap: live.LiveSnapshot | None) -> tuple[Decision, TradeDay]:
    now = market.now()
    cal = _calendar()
    ok = bool(snap and snap.source_ok)
    src = snap.trade_date if (snap and snap.source_ok) else None
    td = session.trade_day(now.date(), cal, ok, src, now.time())
    return session.decide(now, td), td


def _hint() -> date | None:
    """池子与新浪校验用的「预期交易日」（spec §3）：本轮源日期由 live 自己拿，这里只给
    日历推算 → 上一轮成功的日期 这两级兜底。**不能**把上一轮的日期当 source_date 传，
    否则第一行就 return，日历永远不被咨询。"""
    now = market.now()
    return session.expected_trade_day(None, _calendar(), now.time(),
                                      live.STORE.last_trade_date, today=now.date())


# ---------------------------------------------------------------- 渲染块
def _status(decision: Decision, snap: live.LiveSnapshot) -> None:
    text = guide.HOME_STATE_TEXT[decision.state.value]
    q = snap.quotes
    # 收盘后与盘前都写**日期**：实测收盘后腾讯/新浪的时间戳会跟着请求时刻往前走，盘前显示它是误导
    if decision.state in (State.CLOSED, State.CLOSED_DAY, State.PRE_OPEN) and snap.trade_date:
        text += f" · {snap.trade_date.isoformat()}"
    elif q is not None and q.ts is not None:
        text += f" · {q.ts:%H:%M:%S}"
    last_point = None
    if (m := snap.minutes.get("sh000001")) and m.points:
        # 评审收口后 is_stale 收的是**完整的日期时间**：昨天 15:00 的点在今早必须判成延迟
        last_point = datetime.combine(m.trade_date, m.points[-1][0])
    stale = session.is_stale(market.now(), last_point, q.ts if q else None)
    running = decision.state in (State.AUCTION, State.WAIT_OPEN, State.TRADING)
    st.html(theme.status_line(text, running=running, extra=guide.HOME_STALE if stale else ""))


def _fallback_note(block: live.Block, current_day: date | None) -> str:
    if not block.fallback or block.ts is None:
        return ""
    stamp = block.ts.strftime("%m 月 %d 日 %H:%M") if block.trade_date != current_day else f"{block.ts:%H:%M}"
    return guide.HOME_FALLBACK.format(stamp=stamp)


def _index_cards(snap: live.LiveSnapshot) -> None:
    st.html(theme.section("指数行情"))
    if snap.quotes is None:
        st.caption(guide.HOME_UNAVAILABLE)
        return
    if note := _fallback_note(snap.quotes, snap.trade_date):
        st.caption(note)
    quotes = snap.quotes.value
    for col, (code, name) in zip(st.columns(len(market.INDEXES)), market.INDEXES):
        q = quotes[code]
        m = snap.minutes.get(code)
        pts = [(i, p) for i, (_, p) in enumerate(m.points)] if m else []
        img = minicharts.sparkline(pts, q.prev_close) if pts else None
        sub = f"{q.change:+.2f} · {q.pct:+.2f}%"
        col.html(theme.stat_card(name, f"{q.price:.2f}", sub=sub, color=fmt.signed_color(q.change),
                                 img=img))


def _turnover(snap: live.LiveSnapshot, decision: Decision) -> None:
    q = snap.quotes
    if q is None:
        return
    quotes = q.value
    now, today = market.now(), market.now().date()
    trade_date = snap.trade_date or today
    current = quotes["sh000001"].amount_wan + quotes["sz399001"].amount_wan
    if quotes["sh000001"].amount_wan <= 0 or quotes["sz399001"].amount_wan <= 0:
        return                          # spec §3「两项都有值且 > 0」：盘前 [37] 为 0，不渲染「0.00 万亿」
    ts = q.ts.time() if q.ts else now.time()
    prev, pct_ok = _prev_day(trade_date, quotes, q.source, now, today)
    st.caption(turnover.line(now, today, trade_date, current, ts, prev, pct_ok),
               help=guide.HOME_TURNOVER_HELP)


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def _klines(trade_date: date, settled: bool, _key: str):
    """缓存键 = (交易日, 是否已定格)：交易日一变就重取，定格后再重取一次做口径检查。"""
    sh = sources.fetch(live.HTTP_GET, sources.tencent_kline_url("sh000001"),
                       lambda raw: sources.parse_tencent_kline(raw, "sh000001"))
    sz = sources.fetch(live.HTTP_GET, sources.tencent_kline_url("sz399001"),
                       lambda raw: sources.parse_tencent_kline(raw, "sz399001"))
    return (sh.value, sz.value) if sh.ok and sz.ok else None


def _prev_day(trade_date: date, quotes, source: str, now: datetime, today: date):
    settled = turnover.settled(now, trade_date, today)
    bars = _klines(trade_date, settled, "v1")
    if bars is None:
        return None, None
    sh, sz = bars
    prev = turnover.previous_day(sh, sz, trade_date)
    pct_ok = None
    if settled and source == "tencent":
        pct_ok = turnover.consistent(sh, sz, trade_date, quotes["sh000001"].amount_wan,
                                     quotes["sz399001"].amount_wan)
    return prev, pct_ok


# ---------------------------------------------------------------- 行情区（两个 fragment）
INTERVAL_KEY = "home_interval"      # session_state：fragment 用真实快照判出的间隔，页面据此选 fragment


def _render_live(expected_interval: int, expected_key: str) -> None:
    hint = _hint()
    snap = _snapshot(_remembered_decision(), hint)   # 缓存入口与 allow_cross_day 用上一轮的真实判定
    decision, _ = _decide(snap)                      # 用真实源日期重判
    st.session_state[DECISION_KEY] = (decision.state.value, decision.interval_s, decision.cache_ttl)
    if decision.interval_s != expected_interval or _job_key() != expected_key:
        # 状态/间隔变了，或跨过了收盘数据就绪时刻 → 记下想要的间隔再整页重跑。
        # 页面按 session_state 选 fragment，所以下一轮两边一致、不会来回 rerun：
        # 没有日历的休市日，_decide(None) 是「未知」（30 秒行）而 _decide(snap) 是「休市」
        # （60 秒行），不记下来的话就会无限重跑。
        st.session_state[INTERVAL_KEY] = decision.interval_s
        st.rerun()
    _status(decision, snap)
    _index_cards(snap)
    _turnover(snap, decision)
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
    return _decide(None)[0].interval_s


def _job_key() -> str:
    """按钮区状态的粗粒度键：跨过 BREADTH_READY 时它会变，行情 fragment 据此整页重跑。"""
    return "ready" if market.now().time() >= market.BREADTH_READY else "early"


def _breadth_and_thermo(snap, decision) -> None:     # Task 22 填
    pass


def _first_run_hint() -> None:
    if not process.has_ever_run(ui.RUNS_DIR):
        st.page_link(ui.page_ref("guide"), label="第一次用？先看「使用说明」",
                     icon=":material/menu_book:", help=guide.guide_page("guide").lead)


def page_home() -> None:
    ui.page_head("市场概览")
    _first_run_hint()
    key = _job_key()
    (_live_30 if _page_interval() == session.LIVE_INTERVAL_S else _live_60)(key)
    st.caption(guide.HOME_FOOTER)
