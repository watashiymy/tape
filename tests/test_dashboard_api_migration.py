# tests/test_dashboard_api_migration.py — v0.2.1 §1 废弃 API 迁移的防回退测试。
#
# 为什么要源码级断言（平时该避免的写法，这里恰好合适）：这两个 API 现版本
# （streamlit 1.61.1）**仍然能跑**，只打一条告警，所以 AppTest 一切正常也证明不了
# 迁移做到位了；而复制粘贴旧代码会让它们静默复活，直到某次 pip install -U 整页崩掉。
#   - st.components.v1.html → st.iframe，官方声明 2026-06-01 后移除（今天已过期）
#   - use_container_width  → width，官方声明 2025-12-31 后移除（今天已过期 8 个月）
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from tests.conftest import copy_app, goto_page, stub_navigation

ROOT = Path(__file__).resolve().parent.parent
APP_FILES = sorted(ROOT.glob("app/*.py"))

DEPRECATED = [
    ("use_container_width", 'width="stretch"（True）/ width="content"（False）'),
    ("components.html", "st.iframe"),
    ("components.v1.html", "st.iframe"),
    ("streamlit.components", "st.iframe（连 import 一起删）"),
]


def test_app_dir_has_python_files():
    """防止上面的 glob 写错时，下面几条断言变成空跑（永远通过）。"""
    assert APP_FILES, "app/ 下没找到 .py 文件，源码级断言会形同虚设"


@pytest.mark.parametrize("banned,replacement", DEPRECATED,
                         ids=[d[0] for d in DEPRECATED])
def test_no_deprecated_streamlit_api_in_app(banned, replacement):
    hits = [f"{p.relative_to(ROOT)}:{i}"
            for p in APP_FILES
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if banned in line]
    assert hits == [], f"仍在用废弃 API {banned}（应改为 {replacement}）: {hits}"


def _make_apptest(tmp_path) -> AppTest:
    return AppTest.from_file(str(copy_app(tmp_path)), default_timeout=30)


def _at_backtest_page(tmp_path) -> AppTest:
    """渲染并**显式**切到回测报告页：默认落地页自 v0.2.1 起是「使用说明」，
    靠默认落地页取页的话，下面两条 iframe / 半截目录的断言就落到一页纯文档上了。"""
    return goto_page(_make_apptest(tmp_path).run(), "回测报告")


def _complete_run(out: Path, name: str) -> Path:
    run = out / name
    run.mkdir(parents=True)
    (run / "metrics.json").write_text('{"n_trades": 3}', encoding="utf-8")
    (run / "report.html").write_text(
        "<html><body><script>1</script>报告</body></html>", encoding="utf-8")
    (run / "trades.csv").write_text(
        "symbol,action,date,price,shares,commission,stamp,pnl,holding_days\n"
        "000333,buy,2016-04-05,10.0,100,5.0,0.0,,\n", encoding="utf-8")
    return run


def test_backtest_page_renders_report_through_iframe(tmp_path, monkeypatch):
    """st.iframe 的 src 直接收 Path（文档明确"HTML 文件会被读取并直接嵌入"）。
    必须传 Path 而不是自己 read_text：report.html 约 5 MB，预读一遍纯属白费。
    st.html 不是替代品——它不套 iframe 且默认忽略 JavaScript，plotly 报告会是空白页。"""
    import importlib.util
    import sys

    run = _complete_run(tmp_path / "output", "ma_cross_20260817_121152")
    dashboard = copy_app(tmp_path)
    # theme 必须由 dashboard.py 自己解析出来：留着上一个测试的 sys.modules["theme"]
    # 缓存，"import theme 能不能解析"这件事就永远测不到（曾经因此漏过一次）。
    monkeypatch.delitem(sys.modules, "theme", raising=False)
    srcs: list[object] = []
    stub_navigation(monkeypatch, "回测报告")   # bare 模式：真 st.Page 什么都不画
    monkeypatch.setattr(st, "iframe", lambda src, **k: srcs.append(src))
    monkeypatch.setattr(st, "dataframe", lambda *a, **k: None)
    spec = importlib.util.spec_from_file_location("dashboard_iframe_probe", dashboard)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert len(srcs) == 1, f"回测报告页应恰好嵌一次 report.html，实际 {srcs}"
    assert isinstance(srcs[0], Path), f"src 应是 Path（别自己 read_text）: {type(srcs[0])}"
    assert srcs[0] == run / "report.html"


def test_report_page_does_not_crash_with_real_iframe(tmp_path):
    """不打桩的真渲染：st.iframe 拿到 Path 后自己读文件，路径/编码错了会在这里炸。"""
    _complete_run(tmp_path / "output", "ma_cross_20260817_121152")
    at = _at_backtest_page(tmp_path)
    assert not at.exception, f"回测报告页崩了: {at.exception}"


def test_run_dir_without_report_html_is_still_excluded(tmp_path):
    """report.html 不再被预读，但它必须留在 _REQUIRED_FILES 的存在性检查里：
    st.iframe 拿到一个不存在的文件同样崩页，那正是 v0.2.0 修过的半截目录缺陷。"""
    run = tmp_path / "output" / "ma_cross_20260824_151600"
    run.mkdir(parents=True)
    (run / "metrics.json").write_text('{"n_trades": 1}', encoding="utf-8")
    (run / "trades.csv").write_text("symbol,action\n", encoding="utf-8")
    at = _at_backtest_page(tmp_path)
    assert not at.exception, f"缺 report.html 的半截目录不该崩页: {at.exception}"
    assert at.info, "半截目录应被排除并显示'暂无回测结果'提示"


def test_width_stretch_is_used_for_tables_and_charts(tmp_path):
    """等价替换要真的替上去：至少有一处 width="stretch"，否则表格会缩成窄窄一条。"""
    src = (ROOT / "app" / "dashboard.py").read_text(encoding="utf-8")
    assert 'width="stretch"' in src
