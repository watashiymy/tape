# tests/test_dashboard_home.py — Task 18/21-23：「市场概览」落地页
#
# Task 18 只落地骨架（页头 + 首次引导），这里先钉两条最基本的牵连断言：
#   - 不切页直接渲染，页头就是「市场概览」，不许抛异常；
#   - 打开这一页绝不能起本机进程——它不是控制台，不该有这个副作用。
# 内容本身（指数卡片、涨跌分布、涨停温度计、趋势宽度）由 Task 21-23 逐块加测试，
# 这个文件到时候继续扩（任务描述原话）。
from streamlit.testing.v1 import AppTest

from quant.runner import process
from tests.conftest import copy_app

HOME = "市场概览"


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
