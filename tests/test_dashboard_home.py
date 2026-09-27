# tests/test_dashboard_home.py — Task 18/21-23：「市场概览」落地页
#
# Task 18 只落地骨架（页头 + 首次引导），这里先钉两条最基本的牵连断言：
#   - 不切页直接渲染，页头就是「市场概览」，不许抛异常；
#   - 打开这一页绝不能起本机进程——它不是控制台，不该有这个副作用。
# Task 21 填行情区（状态条、指数卡片、两市成交额）：判断全在 quant.market.*，
# 这里只钉页面排版与两个 fragment / 两个缓存入口的收敛行为。
# 内容本身（涨跌分布、涨停温度计、趋势宽度）由 Task 22-23 逐块加测试，
# 这个文件到时候继续扩（任务描述原话）。
#
# 时钟：conftest 的 offline_market 把 market.now 冻结在夹具录制日 19:30、取数走离线替身、
# 清 st.cache_data 与 live.STORE。要换时刻就再 monkeypatch 一次 market.now（_freeze）。
import dataclasses
import re
from datetime import datetime, time, timedelta

import pytest
import streamlit as st
from streamlit.runtime.scriptrunner_utils.script_run_context import ScriptRunContext
from streamlit.testing.v1 import AppTest

from quant import market
from quant.market import live, session, sources
from quant.market.sources import FetchError
from quant.runner import process
from tests.conftest import app_module, copy_app, fixture_http_get

HOME = "市场概览"
TENCENT_QUOTES, SINA_QUOTES = "qt.gtimg.cn/q=", "hq.sinajs.cn"   # 离线替身按这两个片段认 URL


def _htmls(at):
    return [b for e in at.get("html") if not (b := e.proto.body).lstrip().startswith("<style>")]


def _status(at) -> str:
    status = [h for h in _htmls(at) if 'class="qd-status"' in h]
    assert status, "状态条没画出来"
    return status[0]


def _captions(at) -> list[str]:
    return [c.value for c in at.caption]


def _turnover_lines(at) -> list[str]:
    return [t for t in _captions(at) + [m.value for m in at.markdown] if t.startswith("两市成交额")]


def _home(tmp_path):
    return AppTest.from_file(str(copy_app(tmp_path)), default_timeout=30).run()


def _fixture_day():
    return market.now().date()           # offline_market 冻结的录制日


def _next_weekday(day):
    d = day + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _freeze(monkeypatch, when: datetime) -> None:
    monkeypatch.setattr(market, "now", lambda: when)


def _kill(*fragments):
    """离线替身，但 URL 含这些片段的请求一律失败。"""
    def http_get(url, headers=None):
        if any(f in url for f in fragments):
            raise FetchError("down")
        return fixture_http_get(url, headers)
    return http_get


def _auto_rerun_spy(monkeypatch) -> list[float]:
    """记下每个 fragment 发给前端的 run_every（AppTest 不会真的按它定时重跑，
    只能在发出去的 ForwardMsg 里看它到底挂的是 30 秒还是 60 秒）。"""
    seen: list[float] = []
    real = ScriptRunContext.enqueue

    def spy(self, msg):
        if msg.WhichOneof("type") == "auto_rerun":
            seen.append(msg.auto_rerun.interval)
        return real(self, msg)
    monkeypatch.setattr(ScriptRunContext, "enqueue", spy)
    return seen


def _fallback_parts() -> tuple[str, str]:
    head, tail = app_module("guide").HOME_FALLBACK.split("{stamp}")
    return head, tail


# ---------------------------------------------------------------- 骨架
def test_the_home_page_head_renders_without_switching_pages(tmp_path):
    """不切页直接渲染：落地页头就是「市场概览」，且不许抛异常
    （落地页身份本身由 tests/test_dashboard_nav.py 逐项对账，这里只守它能渲染）。"""
    at = AppTest.from_file(str(copy_app(tmp_path)), default_timeout=30).run()
    assert not at.exception, at.exception
    heads = [e.proto.body for e in at.get("html") if 'class="qd-head"' in e.proto.body]
    assert len(heads) == 1, heads
    assert f'class="qd-title">{HOME}<' in heads[0], heads[0]


def test_opening_the_home_page_starts_no_process(tmp_path, monkeypatch):
    """光是打开落地页绝不能起进程——它只读行情快照，不是控制台的任务卡片。"""
    calls = []
    monkeypatch.setattr(process, "start", lambda *a, **k: calls.append(a))
    at = AppTest.from_file(str(copy_app(tmp_path)), default_timeout=30).run()
    assert not at.exception, at.exception
    assert calls == []


def test_home_opens_without_touching_baostock(tmp_path, monkeypatch):
    from quant.data import baostock_provider
    monkeypatch.setattr(baostock_provider.BaostockProvider, "__enter__",
                        lambda self: pytest.fail("首页打开时连了 baostock"))
    assert not _home(tmp_path).exception


# ---------------------------------------------------------------- 指数卡片
def test_home_renders_six_index_cards_from_the_recording(tmp_path):
    at = _home(tmp_path)
    assert not at.exception, at.exception
    cards = [h for h in _htmls(at) if 'class="qd-card"' in h]
    names = [n for _, n in market.INDEXES]
    assert all(any(n in c for c in cards) for n in names), names
    assert all("<svg" not in h for h in _htmls(at)), "内联 svg 会被 st.html 删掉"
    assert any('qd-card-img' in c for c in cards), "指数卡片要有分时小图"


def test_all_sources_down_shows_unavailable_not_zero(tmp_path, monkeypatch):
    def dead(url, headers=None):
        raise FetchError("down")
    monkeypatch.setattr(live, "HTTP_GET", dead)
    at = _home(tmp_path)
    assert not at.exception
    body = " ".join(_htmls(at)) + " ".join(_captions(at))
    assert "暂不可用" in body and "0.00" not in body


def test_fallback_after_a_day_change_is_stamped_with_the_date(tmp_path, monkeypatch):
    """§3：最近成功值的交易日 ≠ 今天时，时刻一律带日期（「9 月 24 日 16:14」，月日不补零）。
    比较对象是**今天**：失败那轮的 snap.trade_date 是从 store 抄来的，永远等于块的日期。"""
    day = _fixture_day()
    at = _home(tmp_path)                  # 录制日 19:30：最近成功值记下录制日的行情块
    assert not at.exception, at.exception
    st.cache_data.clear()                 # 只清缓存，**不清** live.STORE
    monkeypatch.setattr(live, "HTTP_GET", _kill(TENCENT_QUOTES, SINA_QUOTES))
    _freeze(monkeypatch, datetime.combine(day + timedelta(days=1), time(10, 0)))
    at.run()
    assert not at.exception, at.exception
    head, tail = _fallback_parts()
    notes = [c for c in _captions(at) if c.startswith(head) and c.endswith(tail)]
    assert notes, _captions(at)
    stamp = notes[0][len(head):len(notes[0]) - len(tail)]
    assert re.fullmatch(rf"{day.month} 月 {day.day} 日 \d{{2}}:\d{{2}}", stamp), stamp


def test_trading_hours_never_show_a_previous_days_value(tmp_path, monkeypatch):
    """§3：交易时段里不显示跨交易日的旧值，直接「暂不可用」。
    录制日收盘后存下最近成功值；次日 10:15 分时说今天开市、行情两源都挂 → 不许拿昨天的数顶上。"""
    day = _fixture_day()
    nxt = _next_weekday(day)
    at = _home(tmp_path)
    assert not at.exception, at.exception
    st.cache_data.clear()
    real = sources.parse_tencent_minute
    monkeypatch.setattr(sources, "parse_tencent_minute",
                        lambda raw, code: dataclasses.replace(real(raw, code), trade_date=nxt))
    monkeypatch.setattr(live, "HTTP_GET", _kill(TENCENT_QUOTES, SINA_QUOTES))
    _freeze(monkeypatch, datetime.combine(nxt, time(10, 15)))
    allow_args = []
    real_round = live.fetch_round

    def spy(http_get, now, hint, store, allow_cross_day):
        allow_args.append(allow_cross_day)
        return real_round(http_get, now, hint, store, allow_cross_day)
    monkeypatch.setattr(live, "fetch_round", spy)
    at.run()
    assert not at.exception, at.exception
    assert "交易中" in _status(at)
    # 重跑后按「交易中」取数：请 live 自己就别捡跨日旧值（页面的 without_cross_day 是第二道）
    assert allow_args and allow_args[-1] is False, allow_args
    head, _ = _fallback_parts()
    assert not [c for c in _captions(at) if c.startswith(head)], _captions(at)
    assert "暂不可用" in " ".join(_captions(at))


def test_unknown_to_trading_on_the_same_interval_drops_the_previous_days_fallback(tmp_path, monkeypatch):
    """同间隔改判：新会话、没有日历，上一轮判定是「未知」（30 秒行、允许跨日），本轮分时说
    今天开市 → 交易中（也是 30 秒行，不整页重跑）。本轮是按「允许跨日」取的，行情两源都挂时
    会捡回昨天的最近成功值——改判后必须丢掉它，指数区显示「暂不可用」（§3、§4.2）。"""
    day = _fixture_day()
    nxt = _next_weekday(day)
    assert not _home(tmp_path).exception  # 录制日收盘后：进程里的最近成功值记下录制日的行情块
    st.cache_data.clear()                 # 不清 live.STORE
    real = sources.parse_tencent_minute
    monkeypatch.setattr(sources, "parse_tencent_minute",
                        lambda raw, code: dataclasses.replace(real(raw, code), trade_date=nxt))
    monkeypatch.setattr(live, "HTTP_GET", _kill(TENCENT_QUOTES, SINA_QUOTES))
    _freeze(monkeypatch, datetime.combine(nxt, time(10, 0)))
    calls = {"n": 0}
    real_round = live.fetch_round

    def counting(*a, **k):
        calls["n"] += 1
        return real_round(*a, **k)
    monkeypatch.setattr(live, "fetch_round", counting)
    at = _home(tmp_path)                  # 新会话：session_state 里没有上一轮的判定
    assert not at.exception, at.exception
    assert calls["n"] == 1                # 同间隔，没有整页重跑——丢块必须发生在这一跳里
    assert "交易中" in _status(at)
    head, _ = _fallback_parts()
    assert not [c for c in _captions(at) if c.startswith(head)], _captions(at)
    assert not [h for h in _htmls(at) if 'class="qd-card"' in h]
    assert app_module("guide").HOME_UNAVAILABLE in _captions(at)


# ---------------------------------------------------------------- 状态条
def test_status_line_shows_closed_with_the_data_date(tmp_path):
    at = _home(tmp_path)
    status = _status(at)
    assert "已收盘" in status and _fixture_day().isoformat() in status
    assert "qd-pill-idle" in status        # 收盘后不是琥珀


def test_unknown_state_shows_the_data_date_not_a_clock_time(tmp_path, monkeypatch):
    """次个工作日 9:20、没有日历：源日期还是录制日、没过判休市截止时刻 → 状态未知。
    未知时各板块显示数据自己的日期（§4.1）；收盘后录的行情时间戳跟着请求时刻走，不能当时刻显示。"""
    day = _fixture_day()
    _freeze(monkeypatch, datetime.combine(_next_weekday(day), time(9, 20)))
    at = _home(tmp_path)
    assert not at.exception, at.exception
    status = _status(at)
    assert "状态未知" in status and day.isoformat() in status
    assert not re.search(r"\d{2}:\d{2}:\d{2}", status), status
    assert "qd-pill-idle" in status


def test_saturday_is_closed_and_never_marked_stale(tmp_path, monkeypatch):
    """§4.2：「数据延迟」只叠加在「交易中」上。周六 10:15 的分时停在最近交易日 15:00，
    只看时钟判的话会一直挂着「数据延迟」。"""
    day = _fixture_day()
    saturday = day + timedelta(days=(5 - day.weekday()) % 7)
    _freeze(monkeypatch, datetime.combine(saturday, time(10, 15)))
    at = _home(tmp_path)
    assert not at.exception, at.exception
    assert "休市" in _status(at)
    assert "数据延迟" not in " ".join(_htmls(at))


def test_trading_with_a_lagging_minute_series_is_marked_stale(tmp_path, monkeypatch):
    """正例：录制日 10:15、分时只到 10:00（落后 16 分钟）→ 交易中 · 数据延迟。"""
    _freeze(monkeypatch, datetime.combine(_fixture_day(), time(10, 15)))
    real = sources.parse_tencent_minute

    def early(raw, code):
        m = real(raw, code)
        return dataclasses.replace(m, points=tuple(p for p in m.points if p[0] <= time(10, 0)))
    monkeypatch.setattr(sources, "parse_tencent_minute", early)
    at = _home(tmp_path)
    assert not at.exception, at.exception
    status = _status(at)
    assert "交易中" in status and "数据延迟" in status


def test_cold_start_at_noon_shows_the_morning_close_not_unavailable(tmp_path, monkeypatch):
    """spec §8：午休 12:00 冷启动（进程里没有最近成功值）必须显示 11:30 的数值。"""
    _freeze(monkeypatch, datetime.combine(_fixture_day(), time(12, 0)))
    at = _home(tmp_path)
    assert not at.exception
    assert "午间休市" in _status(at)
    body = " ".join(_htmls(at))
    assert "暂不可用" not in body and any('class="qd-card"' in h for h in _htmls(at))


# ---------------------------------------------------------------- 两市成交额
def test_turnover_line_after_close_names_two_full_days(tmp_path):
    """录制日 19:30：已过收盘截止时刻、腾讯行情 → 口径检查做了且通过 → 带百分比。"""
    at = _home(tmp_path)
    line = _turnover_lines(at)[0]
    assert "全天" in line and "截至" not in line
    assert "%" in line


def test_turnover_before_the_close_cutoff_has_no_percentage(tmp_path, monkeypatch):
    """15:10：已收盘但没过截止时刻，当天那根没定格，检查没做 → 不给百分比。"""
    _freeze(monkeypatch, datetime.combine(_fixture_day(), time(15, 10)))
    at = _home(tmp_path)
    assert not at.exception, at.exception
    line = _turnover_lines(at)[0]
    assert "全天" in line and "%" not in line


def test_turnover_from_the_sina_fallback_has_no_percentage(tmp_path, monkeypatch):
    """§3：用新浪兜底时不做口径检查、不给百分比（哪怕数字碰巧对得上）。"""
    monkeypatch.setattr(live, "HTTP_GET", _kill(TENCENT_QUOTES))
    at = _home(tmp_path)
    assert not at.exception, at.exception
    line = _turnover_lines(at)[0]
    assert "全天" in line and "%" not in line


def test_turnover_is_not_rendered_when_one_side_is_zero(tmp_path, monkeypatch):
    """§3「两项都有值且 > 0」：盘前 [37] 为 0，不渲染只剩一半的成交额。"""
    real = sources.parse_tencent_quotes

    def zero_sh(raw, codes):
        quotes = real(raw, codes)
        quotes["sh000001"] = dataclasses.replace(quotes["sh000001"], amount_wan=0)
        return quotes
    monkeypatch.setattr(sources, "parse_tencent_quotes", zero_sh)
    at = _home(tmp_path)
    assert not at.exception, at.exception
    assert _turnover_lines(at) == []


def test_a_failed_kline_fetch_is_retried_on_the_next_run(tmp_path, monkeypatch):
    """日 K 失败不进缓存：否则一次抖动就把「没有上一交易日」钉 24 小时。"""
    monkeypatch.setattr(live, "HTTP_GET", _kill("newfqkline"))
    at = _home(tmp_path)
    assert not at.exception, at.exception
    line = _turnover_lines(at)[0]
    assert " · " not in line and "%" not in line          # 只剩当前值
    monkeypatch.setattr(live, "HTTP_GET", fixture_http_get)
    at.run()                               # 不清缓存：日 K 必须重取
    assert not at.exception, at.exception
    assert "%" in _turnover_lines(at)[0]


# ---------------------------------------------------------------- fragment 与缓存入口
def test_trading_hours_use_the_30s_fragment(tmp_path, monkeypatch):
    intervals = _auto_rerun_spy(monkeypatch)
    _freeze(monkeypatch, datetime.combine(_fixture_day(), time(10, 15)))
    at = _home(tmp_path)
    assert not at.exception
    status = _status(at)
    assert "交易中" in status and "qd-pill-running" in status
    # 夹具是收盘后录的：分时最后一点 15:00，此刻 10:15 → 不在过期判据里报「延迟」（时间在未来）
    assert "数据延迟" not in status
    assert at.session_state["home_interval"] == session.LIVE_INTERVAL_S
    assert intervals and intervals[-1] == session.LIVE_INTERVAL_S


def test_holiday_without_calendar_converges_instead_of_looping(tmp_path, monkeypatch):
    """没有日历的工作日休市（9/25 中秋 9:45 后）：_decide(None) 是未知（30 秒），
    _decide(snap) 按源日期判休市（60 秒）。整页重跑一次后必须停下来。"""
    intervals = _auto_rerun_spy(monkeypatch)
    _freeze(monkeypatch, datetime.combine(_fixture_day() + timedelta(days=1), time(10, 0)))
    calls = {"n": 0}
    real = live.fetch_round

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)
    monkeypatch.setattr(live, "fetch_round", counting)
    at = _home(tmp_path)
    assert not at.exception
    assert "休市" in _status(at)
    assert calls["n"] == 2                 # 30 秒行取一次 → 整页重跑一次 → 60 秒行（长缓存入口）取一次
    assert at.session_state["home_interval"] == session.TICK_INTERVAL_S
    assert intervals and intervals[-1] == session.TICK_INTERVAL_S


def test_a_failed_round_after_the_rerun_does_not_loop(tmp_path, monkeypatch):
    """重跑后那一轮碰巧失败（分时全挂、没有行情）：判定翻回「未知」，与刚换上的 60 秒 fragment
    又对不上。失败轮不进缓存 + 每跳至多一次整页重跑 → 这一跳照常画完，下一跳自愈。
    （旧实现把失败轮钉在 600 秒缓存里，两个缓存入口一成一败，整页无限重跑。）"""
    _freeze(monkeypatch, datetime.combine(_fixture_day() + timedelta(days=1), time(10, 0)))
    calls = {"n": 0}
    real = live.fetch_round

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            return live.LiveSnapshot(fetched_at=market.now(), trade_date=None, source_ok=False,
                                     sh_status=None)
        return real(*a, **k)
    monkeypatch.setattr(live, "fetch_round", flaky)
    at = _home(tmp_path)
    assert not at.exception, at.exception
    assert "状态未知" in _status(at)       # 失败轮照常画出状态条
    # 30 秒行取一次 → 整页重跑 → 60 秒行取到失败轮、判定翻回 30 秒：**不再重跑第二次**
    assert calls["n"] == 2
    at.run()                               # 下一次整页加载：失败那轮没进缓存，重取即恢复
    assert not at.exception, at.exception
    assert "休市" in _status(at) and calls["n"] <= 4


def test_cache_entries_have_no_spinner():
    """st.cache_data 默认把函数名当转圈文案显示出来，违反文案规则（spec §3）。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "app" / "pages_home.py").read_text("utf-8")
    decorators = [ln for ln in src.splitlines() if "@st.cache_data(" in ln]
    assert len(decorators) >= 3, decorators        # 25 秒 / 600 秒 / 日 K
    assert all("show_spinner=False" in ln for ln in decorators), decorators
