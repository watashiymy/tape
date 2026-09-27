# tests/test_dashboard_home.py — Task 18/21-23：「市场概览」落地页
#
# Task 18 只落地骨架（页头 + 首次引导），这里先钉两条最基本的牵连断言：
#   - 不切页直接渲染，页头就是「市场概览」，不许抛异常；
#   - 打开这一页绝不能起本机进程——它不是控制台，不该有这个副作用。
# Task 21 填行情区（状态条、指数卡片、两市成交额）：判断全在 quant.market.*，
# 这里只钉页面排版与两个 fragment / 两个缓存入口的收敛行为。
# 内容本身（涨跌分布、涨停温度计、趋势宽度）由 Task 22-23 逐块加测试，
# 这个文件到时候继续扩（任务描述原话）。
from datetime import datetime, time

import pytest
from streamlit.testing.v1 import AppTest

from quant import market
from quant.market import live, session
from quant.runner import process
from tests.conftest import copy_app, fixture_http_get

HOME = "市场概览"


def _htmls(at):
    return [b for e in at.get("html") if not (b := e.proto.body).lstrip().startswith("<style>")]


def _home(tmp_path):
    return AppTest.from_file(str(copy_app(tmp_path)), default_timeout=30).run()


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


def test_home_renders_six_index_cards_from_the_recording(tmp_path):
    at = _home(tmp_path)
    assert not at.exception, at.exception
    cards = [h for h in _htmls(at) if 'class="qd-card"' in h]
    names = [n for _, n in market.INDEXES]
    assert all(any(n in c for c in cards) for n in names), names
    assert all("<svg" not in h for h in _htmls(at)), "内联 svg 会被 st.html 删掉"
    assert any('qd-card-img' in c for c in cards), "指数卡片要有分时小图"


def test_status_line_shows_closed_with_the_data_date(tmp_path):
    at = _home(tmp_path)
    status = [h for h in _htmls(at) if 'class="qd-status"' in h]
    assert status and "已收盘" in status[0] and "2026-09" in status[0]
    assert "qd-pill-idle" in status[0]        # 收盘后不是琥珀


def test_turnover_line_after_close_names_two_full_days(tmp_path):
    at = _home(tmp_path)
    texts = [c.value for c in at.caption] + [m.value for m in at.markdown]
    line = next(t for t in texts if t.startswith("两市成交额"))
    assert "全天" in line and "截至" not in line


def test_home_opens_without_starting_any_process(tmp_path, monkeypatch):
    from quant.runner import process
    calls = []
    monkeypatch.setattr(process, "start", lambda *a, **k: calls.append(a))
    _home(tmp_path)
    assert calls == []


def test_home_opens_without_touching_baostock(tmp_path, monkeypatch):
    from quant.data import baostock_provider
    monkeypatch.setattr(baostock_provider.BaostockProvider, "__enter__",
                        lambda self: pytest.fail("首页打开时连了 baostock"))
    assert not _home(tmp_path).exception


def test_all_sources_down_shows_unavailable_not_zero(tmp_path, monkeypatch):
    from quant.market.sources import FetchError

    def dead(url, headers=None):
        raise FetchError("down")
    monkeypatch.setattr(live, "HTTP_GET", dead)
    at = _home(tmp_path)
    assert not at.exception
    body = " ".join(_htmls(at)) + " ".join(c.value for c in at.caption)
    assert "暂不可用" in body and "0.00" not in body


def test_trading_hours_use_the_30s_fragment(tmp_path, monkeypatch):
    frozen = datetime.combine(market.now().date(), time(10, 15))
    monkeypatch.setattr(market, "now", lambda: frozen)
    at = _home(tmp_path)
    assert not at.exception
    status = [h for h in _htmls(at) if 'class="qd-status"' in h][0]
    assert "交易中" in status and "qd-pill-running" in status
    # 夹具是收盘后录的：分时最后一点 15:00，此刻 10:15 → 不在过期判据里报「延迟」（时间在未来）
    assert "数据延迟" not in status


def test_cold_start_at_noon_shows_the_morning_close_not_unavailable(tmp_path, monkeypatch):
    """spec §8：午休 12:00 冷启动（进程里没有最近成功值）必须显示 11:30 的数值。"""
    frozen = datetime.combine(market.now().date(), time(12, 0))
    monkeypatch.setattr(market, "now", lambda: frozen)
    at = _home(tmp_path)
    assert not at.exception
    status = [h for h in _htmls(at) if 'class="qd-status"' in h][0]
    assert "午间休市" in status
    body = " ".join(_htmls(at))
    assert "暂不可用" not in body and any('class="qd-card"' in h for h in _htmls(at))


def test_holiday_without_calendar_converges_instead_of_looping(tmp_path, monkeypatch):
    """没有日历的工作日休市（9/25 中秋 9:45 后）：_decide(None) 是未知（30 秒），
    _decide(snap) 按源日期判休市（60 秒）。整页重跑一次后必须停下来。"""
    from datetime import timedelta
    frozen = datetime.combine(market.now().date() + timedelta(days=1), time(10, 0))   # 夹具日期的次日
    monkeypatch.setattr(market, "now", lambda: frozen)
    calls = {"n": 0}
    real = live.fetch_round

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)
    monkeypatch.setattr(live, "fetch_round", counting)
    at = _home(tmp_path)
    assert not at.exception
    status = [h for h in _htmls(at) if 'class="qd-status"' in h][0]
    assert "休市" in status and calls["n"] <= 2


def test_cache_entries_have_no_spinner():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "app" / "pages_home.py").read_text("utf-8")
    assert src.count("show_spinner=False") >= 3, "三个缓存入口（25 秒 / 600 秒 / 日 K）都必须关掉函数名转圈"
