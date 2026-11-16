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
    body = " ".join(htmls) + " ".join(_captions(at))
    assert any("涨跌分布" in h for h in htmls)
    assert "上涨" in body and "下跌" in body and "平盘" in body
    assert body.count('qd-card-img') >= 7, "6 张指数小图 + 1 幅分布柱状图（+ 比例条）"
    assert "沪深京 A 股，不含 ST 与停牌股" in body


def _card(at, label: str) -> str:
    """label 那张首页卡片的 html（每张卡片是一个 st.html 元素）。标签后面可能跟着帮助圆点，
    所以按前缀 `<div class="qd-card-label">{label}<` 匹配。"""
    cards = [h for h in _htmls(at) if f'<div class="qd-card-label">{label}<' in h]
    assert len(cards) == 1, (label, cards)
    return cards[0]


def _card_value(card: str) -> str:
    m = re.search(r'<div class="qd-card-value"[^>]*>([^<]*)</div>', card)
    assert m, card
    return m.group(1)


def _card_note(card: str) -> str:
    """卡片注脚那一行的文字（没有注脚则为空串）。"""
    m = re.search(r'<div class="qd-card-note">([^<]*)</div>', card)
    return m.group(1) if m else ""


def test_thermometer_shows_four_readings_and_a_dash_for_zero_division(tmp_path, monkeypatch):
    at = _home(tmp_path)
    body = " ".join(_htmls(at))
    for label in ("涨停", "跌停", "炸板率", "最高连板"):
        assert label in body
    # 涨停/跌停取分布的 ±11 档（与左图同源同轮）：数从录制文件里现读，不在这里抄死
    fenbu = {k: v for item in json.loads((FIXTURES_MARKET / "em_fenbu.bin").read_bytes())["data"]["fenbu"]
             for k, v in item.items()}
    assert fenbu["11"] != fenbu["-11"], "两档相等的话，涨停/跌停对调也看不出来"
    assert _card_value(_card(at, "涨停")) == str(fenbu["11"])
    assert _card_value(_card(at, "跌停")) == str(fenbu["-11"])

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


def test_bucket_note_follows_the_datas_day_before_the_open(tmp_path, monkeypatch):
    """次个工作日 9:00、没有日历：状态是盘前/未知（不是「已收盘」），但分布块是上一交易日
    收盘后的数——那一天早就收盘封住了，不能写成盘中的「封住涨停/跌停」。"""
    day = _fixture_day()
    _freeze(monkeypatch, datetime.combine(_next_weekday(day), time(9, 0)))
    at = _home(tmp_path)
    assert not at.exception, at.exception
    assert "已收盘" not in _status(at)
    notes = [c for c in _captions(at) if c.startswith("区间按涨跌幅绝对值划分")]
    assert notes and "收盘封住" in notes[0], _captions(at)


# ---------------------------------------------------------------- 趋势宽度四卡 + 按钮区
def _fake_run(root, status, log="", exit_code=None, job="market_breadth"):
    runs = root / "output" / "runs"
    (runs / "logs").mkdir(parents=True, exist_ok=True)
    lp = runs / "logs" / f"{job}_x.log"
    lp.write_text(log, encoding="utf-8")
    (runs / f"{job}.json").write_text(json.dumps({
        "script": job, "run_id": "x", "pid": os.getpid(),
        "argv": [sys.executable, "-u", jobs.JOBS[job].script], "log_path": str(lp),
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


def _market_store(tmp_path):
    from quant.market.store import MarketStore
    return MarketStore(tmp_path / "data" / "market")


def _weekdays_until(last, k):
    """截至 last（含）的最近 k 个工作日，升序。"""
    out, d = [], last
    while len(out) < k:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


#: 小快照里的几只票：(代码, 每天的涨跌幅)。两只天天涨、一只天天跌 → 站上均线 2/3。
TREND_CODES = (("sh.600000", 0.01), ("sh.600001", -0.01), ("sz.000001", 0.02))


def _breadth_fixture(tmp_path, k, *, skip=(), permanent=()):
    """k 个连续交易日（工作日）的小快照，末日 = 夹具日的前一个交易日——夹具日自己的快照
    「还没发布」。日历从首日覆盖到夹具日之后一个月、工作日都是交易日（夹具日也是）。
    skip 里的日子整天缺快照；permanent 里的日子登记成永久缺口（两个不同自然日各失败一次）。
    指标表按任务的做法用 breadth.compute 对整个窗口真算——19:30 已过就绪时刻，任务的窗口
    末日就是夹具日，而它没有快照。返回 (store, 有快照的日子)。"""
    import pandas as pd
    from quant.market import breadth
    from quant.market.store import SNAPSHOT_COLUMNS
    ms = _market_store(tmp_path)
    day = _fixture_day()
    days = _weekdays_until(day - timedelta(days=1), k)
    cal_days = list(pd.date_range(days[0], day + timedelta(days=30)).date)
    ms.save_calendar(cal_days, [d for d in cal_days if d.weekday() < 5])
    snaps = {}
    for i, d in enumerate(days):
        if d in skip:
            continue
        rows = [(code, 10 * (1 + step) ** i, 10 * (1 + step) ** max(i - 1, 0), 1, 0)
                for code, step in TREND_CODES]
        snaps[d] = pd.DataFrame(rows, columns=SNAPSHOT_COLUMNS)
        ms.save_snapshot(d, snaps[d])
    for d in permanent:
        ms.record_failure(d, day - timedelta(days=100))
        ms.record_failure(d, day - timedelta(days=99))
    ms.save_breadth(breadth.compute(snaps, days + [day]))
    return ms, [d for d in days if d not in skip]


def test_missing_days_older_than_the_window_do_not_block_up_to_date(tmp_path, monkeypatch):
    """回归 bug：真实门店的日历比补数窗口早得多——补数任务只留最近 WINDOW_TRADING_DAYS 个
    交易日的快照，窗口之前没快照是预期，不该被 missing_days 当成缺。这里把窗口收到 3 天来
    复现：日历有 10 个交易日（末日 = 夹具日的前一个交易日，夹具日本身不是交易日），只留最后
    3 天的快照——窗口之前的 7 天没快照也不该报「更新收盘数据」，按钮区什么都不该显示。"""
    import pandas as pd
    monkeypatch.setattr(market, "WINDOW_TRADING_DAYS", 3)
    ms = _market_store(tmp_path)
    day = _fixture_day()
    window_days = _weekdays_until(day - timedelta(days=1), 10)
    cal_days = list(pd.date_range(window_days[0] - timedelta(days=10), day + timedelta(days=30)).date)
    ms.save_calendar(cal_days, window_days)      # 交易日只有这 10 天——夹具日本身不是交易日
    for d in window_days[-3:]:
        ms.save_snapshot(d, pd.DataFrame({"code": ["sh.600000"], "close": [1.0], "preclose": [1.0],
                                           "tradestatus": [1], "isST": [0]}))
    ms.save_breadth(pd.DataFrame({"date": [window_days[-1]], "above20": [1], "n20": [1], "above60": [0],
                                  "n60": [0], "above200": [0], "n200": [0], "new_high": [0], "new_low": [0],
                                  "n_hl": [0]}))
    at = _home(tmp_path)
    assert not at.exception, at.exception
    assert not ({"home_breadth_start", "home_breadth_update"} & _keys(at))


def test_trend_cards_date_and_shortfall_come_from_days_with_data(tmp_path):
    """夹具日 19:30、夹具日的快照还没存（任务在发布前跑过，只补到前一天）：标题旁写**有数据的
    最后一天**；20 日卡有数；60 日卡「—」并写还差 60 − k 天；按钮区给「更新收盘数据」。"""
    k = 22
    _, days = _breadth_fixture(tmp_path, k)
    at = _home(tmp_path)
    assert not at.exception, at.exception
    body = " ".join(_htmls(at))
    assert f"收盘数据截至 {days[-1].isoformat()}" in body
    assert f"收盘数据截至 {_fixture_day().isoformat()}" not in body
    card20 = _card(at, "站上 20 日线")
    assert re.fullmatch(r"\d+\.\d%", _card_value(card20)), card20
    assert "2/3" in card20
    card60 = _card(at, "站上 60 日线")
    assert _card_value(card60) == "—"
    assert f"还差约 {60 - k} 天的收盘数据" in card60, card60
    assert f"还差约 {250 - k} 天的收盘数据" in _card(at, "52 周新高 / 新低")
    assert "home_breadth_update" in _keys(at)


def test_trend_card_names_the_permanent_gap_inside_its_lookback(tmp_path):
    """20 日卡的回看期里有永久缺口：写缺的是哪天、还要等几个交易日窗口才滚过它（spec §5.4）。"""
    k = 22
    gap = _weekdays_until(_fixture_day() - timedelta(days=1), k)[-6]   # 之后还有 5 个交易日有数
    _breadth_fixture(tmp_path, k, skip={gap}, permanent={gap})
    at = _home(tmp_path)
    assert not at.exception, at.exception
    card20 = _card(at, "站上 20 日线")
    assert _card_value(card20) == "—"
    assert f"数据源缺 {gap.isoformat()} 的数据，还需约 15 个交易日" in card20, card20


def test_trend_block_has_numbers_only_cards_and_one_chart_with_a_span_switch(tmp_path):
    """2026-10-06：四张卡片只留数字；走势看卡片下方那张两行图表（坐标轴、图例、悬停），带 60/120/全部 切换。"""
    import json
    from quant.report import charts
    _breadth_fixture(tmp_path, 70)
    at = _home(tmp_path)
    assert not at.exception, at.exception
    for label in ("站上 20 日线", "站上 60 日线", "站上 200 日线", "52 周新高 / 新低"):
        assert "qd-card-img" not in _card(at, label), label
    assert "qd-card-note" not in _card(at, "站上 20 日线"), "有数的卡片不再有「近 N 个交易日」注脚"
    assert "还差约" in _card_note(_card(at, "站上 200 日线")), "「—」的卡片仍写缺多少天"
    g = app_module("guide")
    assert g.HOME_HELP["above_ma"].format(n=20) in _card(at, "站上 20 日线")
    assert [name for _, name, _ in charts.BREADTH_LINES] == [g.HOME_ABOVE_MA.format(n=n) for n, _, _ in charts.BREADTH_LINES], \
        "图例名与卡片标签不许漂"
    r = at.radio(key="home_trend_span")
    assert r.options == [v[0] for v in charts.BREADTH_SPANS.values()] and r.value == "60"   # .options 是显示名，.value 是内部键
    el = at.get("plotly_chart")
    assert len(el) == 1, "四张卡片下方恰一张图表"
    assert len(json.loads(el[0].proto.spec)["data"][0]["x"]) == 60
    assert "displayModeBar" in el[0].proto.config
    assert g.HOME_TREND_CHART_NOTE in _captions(at)
    at = r.set_value("120").run()
    assert not at.exception, at.exception
    assert len(json.loads(at.get("plotly_chart")[0].proto.spec)["data"][0]["x"]) == 70    # 表只有 70 行 → 画全部
    assert at.radio(key="home_trend_span").value == "120"


def test_a_breadth_table_with_wrong_columns_degrades_only_that_block(tmp_path):
    """spec §5.5 逐块降级：列不对的指标表当读不出来——标题照画、不画卡片、页面不炸。"""
    import pandas as pd
    ms = _market_store(tmp_path)
    ms.save_breadth(pd.DataFrame({"date": [_fixture_day()], "whatever": [1]}))
    at = _home(tmp_path)
    assert not at.exception, at.exception
    assert any('class="qd-section">趋势宽度' in h for h in _htmls(at))
    assert not [h for h in _htmls(at) if "站上 20 日线" in h]
    assert any(h.startswith('<div class="qd-card">') for h in _htmls(at)), "其余板块照常"
    assert at.get("plotly_chart") == [], "表读不出就没有图表"
    assert "home_trend_span" not in {r.key for r in at.radio}


def test_another_job_running_polls_the_button_area_every_two_seconds(tmp_path, monkeypatch):
    """spec §4.4：有任务在跑时按钮区 2 秒一跳——不只是收盘数据任务自己；别的任务跑完时
    按钮区要自己发现并整页重跑，不能一直挂着「有任务在运行」。"""
    intervals = _auto_rerun_spy(monkeypatch)
    _fake_run(tmp_path, "running", log="开始扫描\n", job="market_scan")
    at = _home(tmp_path)
    assert not at.exception, at.exception
    assert app_module("guide").HOME_TREND_BUSY in _captions(at)
    assert not ({"home_breadth_start", "home_breadth_update", "home_breadth_stop"} & _keys(at))
    assert 2 in intervals, intervals


def test_before_ready_time_on_a_trading_day_says_when_to_update(tmp_path, monkeypatch):
    """交易日、就绪时刻前、已补到上一交易日 → 一行小字（带具体日期），不给按钮。"""
    _breadth_fixture(tmp_path, 3)
    day = _fixture_day()
    _freeze(monkeypatch, datetime.combine(day, time(16, 0)))
    at = _home(tmp_path)
    assert not at.exception, at.exception
    want = app_module("guide").HOME_TREND_NOT_READY.format(day=f"{day.month} 月 {day.day} 日")
    assert want in _captions(at), _captions(at)
    assert not ({"home_breadth_start", "home_breadth_update"} & _keys(at))


def test_a_missing_trading_day_offers_update(tmp_path):
    _breadth_fixture(tmp_path, 3)          # 19:30，夹具日的快照还没有
    at = _home(tmp_path)
    assert not at.exception, at.exception
    assert "home_breadth_update" in _keys(at) and "home_breadth_start" not in _keys(at)


def test_failed_update_shows_the_error_and_offers_update(tmp_path):
    import pandas as pd
    _market_store(tmp_path).save_snapshot(
        _fixture_day() - timedelta(days=1),
        pd.DataFrame({"code": ["sh.600000"], "close": [1.0], "preclose": [1.0], "tradestatus": [1], "isST": [0]}))
    _fake_run(tmp_path, "failed", exit_code=1,
              log="待补 1 天\n[1/1] 2026-09-03 跳过：重试后仍为 0 行\n"
                  "补数失败：2026-09-03 拿不到数据（已记录，再失败一次将不再重试）\n")
    at = _home(tmp_path)
    assert not at.exception, at.exception
    assert "home_breadth_update" in _keys(at) and "home_breadth_start" not in _keys(at)
    assert any("2026-09-03 拿不到数据" in c for c in _captions(at)), _captions(at)


def test_a_broken_state_file_shows_an_error_and_no_buttons(tmp_path):
    runs = tmp_path / "output" / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / "market_breadth.json").write_text("{", encoding="utf-8")
    at = _home(tmp_path)
    assert not at.exception, at.exception
    assert [e.value for e in at.error], "状态文件损坏要报错"
    assert not ({"home_breadth_start", "home_breadth_update", "home_breadth_stop"} & _keys(at))


# ---------------------------------------------------------------- 就地帮助圆点（2026-09-29 设计）
THERMO_HELP = {"涨停": "limit_up", "跌停": "limit_down", "炸板率": "zb_rate", "最高连板": "max_lbc"}
#: 标签 → (HOME_HELP 键, N, 是否向左展开)。右半边的挂点向左展开（spec §1.1）。
TREND_HELP = {"站上 20 日线": ("above_ma", 20, False), "站上 60 日线": ("above_ma", 60, False),
              "站上 200 日线": ("above_ma", 200, True), "52 周新高 / 新低": ("high_low", None, True)}


def test_home_has_no_popover(tmp_path):
    _breadth_fixture(tmp_path, 22)
    at = _home(tmp_path)
    assert not at.exception, at.exception
    assert at.get("popover") == [], "带下拉箭头的「? ⌄」弹出框不该再出现"


def test_each_thermometer_card_explains_only_itself_and_opens_leftward(tmp_path):
    at = _home(tmp_path)
    tips = app_module("guide").HOME_HELP
    for label, key in THERMO_HELP.items():
        card = _card(at, label)
        assert tips[key] in card, label
        assert not [k for k in THERMO_HELP.values() if k != key and tips[k] in card], label
        assert "qd-help-end" in card, f"{label}：温度计整块在右栏，提示框要向左展开"


def test_each_trend_card_explains_only_itself_and_the_right_half_opens_leftward(tmp_path):
    _breadth_fixture(tmp_path, 22)     # 22 天：20 日线有数，60/200/52 周是「—」——两个分支都在这一次渲染里
    at = _home(tmp_path)
    assert not at.exception, at.exception
    help_ = app_module("guide").HOME_HELP
    texts = {label: (help_[key].format(n=n) if n else help_[key])
             for label, (key, n, _) in TREND_HELP.items()}
    for label, (_, _, end) in TREND_HELP.items():
        card = _card(at, label)
        assert texts[label] in card, label
        assert not [o for other, o in texts.items() if other != label and o in card], label
        assert ("qd-help-end" in card) is end, label
    assert "近 60 日均价" in _card(at, "站上 60 日线")
    assert "qd-card-sub" not in _card(at, "52 周新高 / 新低"), "「—」时不出「共 N 只」"


def test_high_low_card_shows_its_denominator_and_help(tmp_path):
    """n_hl > 0 那个分支：补一行「共 N 只」（与左边三张的分母同位置，小图才对齐），圆点照挂。"""
    import pandas as pd
    ms = _market_store(tmp_path)
    day = _fixture_day()
    days = pd.date_range(day.replace(day=1), day + pd.Timedelta(days=40)).date
    ms.save_calendar(days, {day})
    ms.save_snapshot(day, pd.DataFrame({"code": ["sh.600000"], "close": [1.0], "preclose": [1.0],
                                        "tradestatus": [1], "isST": [0]}))
    ms.save_breadth(pd.DataFrame({"date": [day], "above20": [1], "n20": [2], "above60": [0], "n60": [0],
                                  "above200": [0], "n200": [0], "new_high": [36], "new_low": [104],
                                  "n_hl": [4922]}))
    at = _home(tmp_path)
    assert not at.exception, at.exception
    g = app_module("guide")
    card = _card(at, "52 周新高 / 新低")
    assert _card_value(card) == "36 / 104"
    assert g.HOME_HL_TOTAL.format(n=4922) in card
    assert g.HOME_HELP["high_low"] in card and "qd-help-end" in card


def test_index_cards_put_change_and_percent_on_two_lines(tmp_path):
    at = _home(tmp_path)
    for _, name in market.INDEXES:
        card = _card(at, name)
        assert card.count('class="qd-card-sub"') == 2, card
        assert " · " not in card, card


def test_the_start_button_carries_the_job_help(tmp_path):
    at = _home(tmp_path)                        # 从没准备过 → 「开始准备」
    assert at.button(key="home_breadth_start").help == app_module("guide").JOB_HELP["market_breadth"]


def test_the_start_button_is_words_plus_a_material_icon(tmp_path):
    """按钮文字就是 guide 里那几个字，图标走 icon=，不再把 ▶ 拼在前面（2026-11-16）。"""
    at = _home(tmp_path)
    btn = at.button(key="home_breadth_start")
    assert (btn.label, btn.proto.icon) == (app_module("guide").HOME_BTN_START,
                                           ":material/play_arrow:")


def test_the_update_button_carries_the_job_help(tmp_path):
    _breadth_fixture(tmp_path, 22)              # 夹具日自己的快照还没有 → 「更新收盘数据」
    at = _home(tmp_path)
    assert at.button(key="home_breadth_update").help == app_module("guide").JOB_HELP["market_breadth"]
