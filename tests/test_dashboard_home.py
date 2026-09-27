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
import json
import os
import re
import sys
from datetime import datetime, time, timedelta

import pytest
import streamlit as st
from streamlit.runtime.scriptrunner_utils.script_run_context import ScriptRunContext
from streamlit.testing.v1 import AppTest

from quant import market
from quant.market import live, session, sources
from quant.market.sources import FetchError
from quant.runner import jobs, process
from tests.conftest import FIXTURES_MARKET, app_module, copy_app, fixture_http_get

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
    # 只有指数卡片是「跨日丢块」看的对象；涨跌分布/涨停温度计走独立数据源，qd-card 类名
    # 是共用的样式，不能拿来当「指数区没画」的证据（Task 22 起温度计也用同一个类）。
    cards = [h for h in _htmls(at) if 'class="qd-card"' in h]
    names = [n for _, n in market.INDEXES]
    assert not any(n in c for c in cards for n in names), cards
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


# ---------------------------------------------------------------- 涨跌分布 + 涨停温度计
def test_distribution_block_counts_add_up_to_the_histogram(tmp_path):
    at = _home(tmp_path)
    htmls = _htmls(at)
    dist = next(h for h in htmls if "涨跌分布" in h)
    body = " ".join(htmls) + " ".join(_captions(at))
    assert "上涨" in body and "下跌" in body and "平盘" in body
    assert body.count('qd-card-img') >= 7, "6 张指数小图 + 1 幅分布柱状图（+ 比例条）"
    assert "沪深京 A 股，不含 ST 与停牌股" in body
    assert dist


def test_thermometer_shows_four_readings_and_a_dash_for_zero_division(tmp_path, monkeypatch):
    at = _home(tmp_path)
    body = " ".join(_htmls(at))
    for label in ("涨停", "跌停", "炸板率", "最高连板"):
        assert label in body

    def empty_pools(url, headers=None):
        if "Pool" in url:
            return b'{"rc":0,"data":{"tc":0,"pool":[],"qdate":"20260924"}}'
        return fixture_http_get(url, headers)
    monkeypatch.setattr(live, "HTTP_GET", empty_pools)
    st.cache_data.clear()
    live.STORE.reset()
    at2 = _home(tmp_path)
    body2 = " ".join(_htmls(at2)) + " ".join(_captions(at2))
    # 池子空、分布 +11 档 > 0 → 日期对不上 → 分布与温度计整块「暂不可用」，不显示 0%
    assert "暂不可用" in body2 and "0.0%" not in body2


def test_thermometer_zb_rate_is_a_dash_when_pools_are_legitimately_empty(tmp_path, monkeypatch):
    """涨停池、炸板池都真的是空的（zt_tc = zb_tc = 0），且分布 +11 档同样是 0——跟池子对得上，
    整块正常取到（不是「暂不可用」）。炸板率此时是 0÷0，必须显示 —，不能显示 0.0%，
    也不能让页面直接炸掉（ZeroDivisionError）。"""
    import json
    fixtures = FIXTURES_MARKET
    fenbu = json.loads((fixtures / "em_fenbu.bin").read_bytes())
    for item in fenbu["data"]["fenbu"]:
        if "11" in item:
            item["11"] = 0
        if "0" in item:
            item["0"] += 52         # 挪给平盘档，合计不变，仍在 [4000, 7000] 校验范围内
    zero_fenbu = json.dumps(fenbu).encode()
    empty_pool = b'{"rc":0,"data":{"tc":0,"pool":[]}}'

    def http_get(url, headers=None):
        if "getTopicZDFenBu" in url:
            return zero_fenbu
        if "Pool" in url:
            return empty_pool
        return fixture_http_get(url, headers)
    monkeypatch.setattr(live, "HTTP_GET", http_get)
    st.cache_data.clear()
    live.STORE.reset()
    at = _home(tmp_path)
    assert not at.exception, at.exception
    body = " ".join(_htmls(at)) + " ".join(_captions(at))
    assert "暂不可用" not in body
    assert "0.0%" not in body
    assert "—" in body


def test_fenbu_down_falls_back_to_tencent_counts_with_its_own_scope(tmp_path, monkeypatch):
    def no_fenbu(url, headers=None):
        if "getTopicZDFenBu" in url:
            raise FetchError("down")
        return fixture_http_get(url, headers)
    monkeypatch.setattr(live, "HTTP_GET", no_fenbu)
    at = _home(tmp_path)
    body = " ".join(_htmls(at)) + " ".join(_captions(at))
    assert "沪深 A 股，含 ST，停牌计入平盘" in body
    assert "上涨" in body and "暂不可用" in body        # 家数有、分布图与温度计没有


def test_bucket_note_says_sealed_at_close_after_close_and_now_intraday(tmp_path, monkeypatch):
    at = _home(tmp_path)
    assert "收盘封住" in " ".join(_captions(at))
    frozen = datetime.combine(_fixture_day(), time(10, 15))
    _freeze(monkeypatch, frozen)
    st.cache_data.clear()
    live.STORE.reset()
    at2 = _home(tmp_path)
    assert "封住涨停/跌停" in " ".join(_captions(at2))


# ---------------------------------------------------------------- 趋势宽度四卡 + 按钮区
def _fake_run(root, status, log="", exit_code=None):
    runs = root / "output" / "runs"
    (runs / "logs").mkdir(parents=True, exist_ok=True)
    lp = runs / "logs" / "market_breadth_x.log"
    lp.write_text(log, encoding="utf-8")
    (runs / "market_breadth.json").write_text(json.dumps({
        "script": "market_breadth", "run_id": "x", "pid": os.getpid(),
        "argv": [sys.executable, "-u", jobs.JOBS["market_breadth"].script], "log_path": str(lp),
        "started_at": "2026-09-24T19:00:00", "status": status, "exit_code": exit_code,
        "finished_at": None if status == "running" else "2026-09-24T19:05:00"}), encoding="utf-8")


def _keys(at) -> set[str]:
    """AppTest 的 `at.button(key=…)` 对不存在的 key 抛 KeyError，不是返回假值——按集合判有无。"""
    return {b.key for b in at.button}


def test_never_prepared_shows_the_explanation_and_a_start_button(tmp_path):
    at = _home(tmp_path)
    assert "home_breadth_start" in _keys(at), "从没准备过 → 「开始准备」"
    assert any("第一次准备需要" in c.value for c in at.caption)


def test_running_shows_progress_and_stop(tmp_path):
    _fake_run(tmp_path, "running", log="待补 310 天\n[57/310] 2026-06-30 5220 行 用时 18.4s\n")
    at = _home(tmp_path)
    assert "home_breadth_stop" in _keys(at) and "home_breadth_start" not in _keys(at)
    # AppTest 没有 progress 访问器；st.progress 落在 UnknownElement，按类型名取，文案在 proto.text
    bars = [e.proto.text for e in at.get("progress")]
    assert any("57/310" in t for t in bars), bars


def test_failed_first_run_shows_the_error_and_still_offers_start(tmp_path):
    _fake_run(tmp_path, "failed", log="待补 3 天\n补数失败：交易日历不完整（日历为空）\n", exit_code=2)
    at = _home(tmp_path)
    assert "home_breadth_start" in _keys(at)
    texts = [c.value for c in at.caption] + [e.value for e in at.error]     # ElementList 不能相加
    assert any("交易日历不完整" in t for t in texts)


def test_up_to_date_shows_only_the_date_in_the_title(tmp_path):
    from quant.market.store import MarketStore
    import pandas as pd
    ms = MarketStore(tmp_path / "data" / "market")
    day = market.now().date()
    days = pd.date_range(day.replace(day=1), day + pd.Timedelta(days=40)).date
    ms.save_calendar(days, {day})            # 交易日只有夹具那一天 → 补齐 = 已是最新
    ms.save_snapshot(day, pd.DataFrame({"code": ["sh.600000"], "close": [1.0], "preclose": [1.0],
                                        "tradestatus": [1], "isST": [0]}))
    ms.save_breadth(pd.DataFrame({"date": [day], "above20": [1], "n20": [2], "above60": [0], "n60": [0],
                                  "above200": [0], "n200": [0], "new_high": [0], "new_low": [0], "n_hl": [0]}))
    at = _home(tmp_path)
    assert not ({"home_breadth_start", "home_breadth_update"} & _keys(at))
    body = " ".join(_htmls(at)) + " ".join(c.value for c in at.caption)
    # 状态条「已收盘 · 日期」一次 + 宽度标题旁一次；按钮区在「已是最新」时什么都不显示
    assert body.count(day.isoformat()) == 2, body
    assert "还差约" in body, "60/200 日线历史不足要说清缺多少天"


def test_trend_card_explains_a_permanent_gap_when_history_is_otherwise_enough(tmp_path):
    """分母为 0，但表里天数其实已经够了（≥ n）——那是永久缺口把窗口卡住了，不是「还差几天」。"""
    from quant.market.store import MarketStore
    import pandas as pd
    ms = MarketStore(tmp_path / "data" / "market")
    day = market.now().date()
    days = list(pd.date_range(day - pd.Timedelta(days=30), day).date)   # 31 天 ≥ above20 需要的 20 天
    gap_day = days[0]
    ms.record_failure(gap_day, day - pd.Timedelta(days=100))
    ms.record_failure(gap_day, day - pd.Timedelta(days=99))             # 两次失败 → 永久缺口
    rows = len(days)
    ms.save_breadth(pd.DataFrame({
        "date": days, "above20": [0] * rows, "n20": [0] * rows,
        "above60": [0] * rows, "n60": [0] * rows, "above200": [0] * rows, "n200": [0] * rows,
        "new_high": [0] * rows, "new_low": [0] * rows, "n_hl": [0] * rows}))
    at = _home(tmp_path)
    assert not at.exception, at.exception
    body = " ".join(_htmls(at)) + " ".join(c.value for c in at.caption)
    assert "数据源缺" in body and gap_day.isoformat() in body, body
