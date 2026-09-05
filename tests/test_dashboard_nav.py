# tests/test_dashboard_nav.py — v0.2.2 M1：热键修复（§1）与 TAPE 命名 / 原生导航（§2）
#
# 三块内容各有各的可测边界，先说清楚为什么这么测：
#
# 1) **热键**（§1.2）：v0.5.0 起不再有注入脚本——真修法是配置项，见文内说明。
# 2) **命名**（§2.1）：TAPE 与副标是纯字符串/纯 SVG，能逐条断言。
# 3) **导航**（§2.2）：st.navigation + st.Page 取代 st.sidebar.radio。页面声明在
#    bare 模式下用替身 st.Page 记录（真 Page 没有 ctx 就是个空壳），渲染则走 AppTest。
import ast
import base64
import importlib.util
import re
import sys
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from tests.conftest import (PAGE_URL_PATHS, app_module, copy_app, goto_page,
                            stub_navigation)

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "app" / "dashboard.py"
SOURCE = DASHBOARD.read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")

_SPEC = importlib.util.spec_from_file_location("qd_theme_nav", ROOT / "app" / "theme.py")
theme = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(theme)

# v0.3.1 M1（设计 §1）的页表：三组八页。「交易日志」拆成「记账」与「持仓与盈亏」，
# 侧栏改用 st.navigation 的 Mapping 分组形态。
# - 主组用空字符串键 ""：**实测**（streamlit 1.61.1，真浏览器截图）它渲染为
#   无标题组——使用说明/任务控制台/信号顶格显示、没有组头占位；
#   命名组「交易日志」「研究」各带组头。所以设计里"不行则三组都起名"的
#   备选方案不必启用。
# - 「记账」沿用老的 journal 路径（一键记账跳的就是它，老书签也不断）；
#   「持仓与盈亏」用新的 positions。
# - 「任务控制台」仍第二位；「使用说明」仍默认落地页。
#
# v0.5.0：说明页拆成四页。第一页留在主组、位置与默认落地页身份都不变
# （侧栏第一项、任务控制台仍是扁平第 2 项）；另外三页单独成「手册」组，
# 紧跟主组之后——它们是侧栏第一项的续页，挨着才读得通。
# 组名刻意不叫「使用说明」：那样侧栏里会出现两处同名，读起来像两个不相干的东西。
# 2026-09-05（用户定）：落地页换成「任务控制台」；主组只留每天用的两页，「使用说明」
# 并进「手册」组（「自定义策略」同日搬去 docs/custom-strategy.md）。
EXPECTED_GROUPS = {
    "": [
        ("任务控制台", ":material/play_circle:", "console"),
        ("信号", ":material/notifications:", "signals"),
    ],
    "手册": [
        ("使用说明", ":material/menu_book:", "guide"),
        ("读懂回测", ":material/insights:", "guide-metrics"),
        ("边界与安全", ":material/shield:", "guide-limits"),
    ],
    "交易日志": [
        ("记账", ":material/edit_note:", "journal"),
        ("持仓与盈亏", ":material/account_balance_wallet:", "positions"),
    ],
    "研究": [
        ("信号池", ":material/list:", "universe"),
        ("回测报告", ":material/assessment:", "backtest"),
        ("个股K线", ":material/candlestick_chart:", "kline"),
    ],
}
EXPECTED_PAGES = [page for group in EXPECTED_GROUPS.values() for page in group]


# ================================================================ §1.2 Cmd+C（v0.5.0 改口）
# v0.2.2 曾注入一段 JS 包 hotkeys.filter 来修「Cmd+C 弹 Clear cache」。**它从头到尾是
# 空操作**：1.61 的前端把 `c` 键交给 App.handleKeyDown 自己 switch，根本不经过 hotkeys-js
# （window.hotkeys 存在但 _handlers 为空）。当年的测试也只钉了"那段 JS 长什么样"，
# 没有任何一条能证明它有效——当时的 docstring 自己也承认"真伪只能由用户按一次确认"。
# 真修法是 .streamlit/config.toml 的 client.toolbarMode="viewer"（见 tests/test_theme.py）。








# ================================================================ §2.1 TAPE 命名

def test_the_brand_is_tape_with_a_chinese_subtitle():
    """§2.1：TAPE 取自 reading the tape（看盘）——纸带上滚动的价与量正是本系统
    唯一的输入。副标说清它是什么，不然只剩一个看不懂的英文词。"""
    assert theme.BRAND == "TAPE"
    assert theme.BRAND_SUB == "A股日线信号"


def test_the_brand_logo_is_a_self_contained_svg_data_uri():
    """品牌块走 st.logo：那是侧栏里**导航之上**唯一的官方位置（普通 st.sidebar.*
    只能排在导航链接下面，品牌落到列表中间就不成体统了）。st.logo 只收图片，
    所以把这行字画成 SVG——内联 data URI，不额外读文件、不请求网络。"""
    assert theme.BRAND_LOGO.startswith("data:image/svg+xml;base64,")
    svg = base64.b64decode(theme.BRAND_LOGO.split(",", 1)[1]).decode("utf-8")
    assert svg == theme.BRAND_SVG
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert f">{theme.BRAND}<" in svg and f">{theme.BRAND_SUB}<" in svg


def test_the_brand_logo_is_serif_uppercase_with_open_tracking():
    """§2.1 定死的观感：衬线、大写、字距放开；副标小号灰字。
    字体栈与色值都必须来自既有的单一来源（theme.SERIF / palette），不许另起一套。"""
    svg = theme.BRAND_SVG
    assert theme.BRAND.isupper()
    assert theme.SERIF in svg, "标题没用项目的衬线字体栈"
    assert theme.SANS in svg, "副标没用项目的无衬线字体栈"
    assert "letter-spacing" in svg, "字距没放开"
    assert theme.TEXT in svg and theme.MUTED in svg, "色值必须取自色板"


def test_the_browser_tab_is_named_after_the_brand():
    """浏览器标签页也归到 TAPE：留着 "quant_demo v0.2.1" 的话，
    多开几个标签时这个面板还是那个认不出来的那一个。
    标签页名与侧栏品牌同一个来源（theme），不许各写各的。"""
    assert theme.PAGE_TITLE.startswith(theme.BRAND)
    assert theme.BRAND_SUB in theme.PAGE_TITLE
    m = re.search(r"set_page_config\(([^)]*)\)", SOURCE)
    assert m, "面板必须调用 st.set_page_config"
    assert "quant_demo" not in m.group(1), f"标签页还挂着旧名字: {m.group(1)}"
    assert "theme.PAGE_TITLE" in m.group(1), f"标签页名没走 theme: {m.group(1)}"


def test_the_sidebar_brand_replaces_the_old_plain_title():
    assert "st.logo(" in SOURCE, "品牌块必须走 st.logo（导航之上唯一的位置）"
    assert "theme.BRAND_LOGO" in SOURCE, "logo 图必须取自 theme（单一来源）"
    assert "st.sidebar.title(" not in SOURCE, "旧的 quant_demo 侧栏标题应已删掉"


def test_the_readme_calls_the_panel_by_its_name():
    """README 是外部第一入口：面板改了名，那里还叫老名字就是两套说法。"""
    assert theme.BRAND in README, "README 里没提到面板叫 TAPE"



# ================================================================ §2.2 原生导航

def _declared_pages(tmp_path, monkeypatch, title: str = "使用说明"):
    """bare 模式 exec 一遍面板，拿回它声明的页表（顺序即侧栏顺序）。

    app/ 整目录复制进 tmp_path：ROOT/OUTPUT/RUNS_DIR 全落在临时目录，
    与仓库真实产物隔离（否则探针会读到、甚至停掉真任务）。
    """
    pages = stub_navigation(monkeypatch, title)
    monkeypatch.delitem(sys.modules, "theme", raising=False)
    spec = importlib.util.spec_from_file_location(
        f"dashboard_nav_{title}", copy_app(tmp_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, pages


def test_pages_are_declared_in_the_designed_order_with_icons(tmp_path, monkeypatch):
    """页表（v0.3.1 §1）：扁平顺序 + 图标 + URL 路径，一项都不许漂。"""
    _, pages = _declared_pages(tmp_path, monkeypatch)
    actual = [(p.title, p.icon, p.url_path) for p in pages]
    assert actual == EXPECTED_PAGES


def test_navigation_groups_match_the_design(tmp_path, monkeypatch):
    """v0.3.1 §1 的分组结构：组名与组内顺序都要对上。

    主组的键必须是 ""（实测渲染为无标题组，见 EXPECTED_GROUPS 上的注释）；
    「交易日志」组恰好两个子页且「记账」在前——侧栏里"记 → 看"的顺序
    就是使用顺序。dict 的插入序就是侧栏组序，所以整个 Mapping 一次对账。
    """
    _, pages = _declared_pages(tmp_path, monkeypatch)
    actual = {name: [(p.title, p.icon, p.url_path) for p in members]
              for name, members in pages.groups.items()}
    assert actual == EXPECTED_GROUPS


def test_the_journal_group_holds_exactly_the_two_split_pages(tmp_path, monkeypatch):
    """拆页的验收面：原「交易日志」一页消失，取而代之的是组里的两个子页。"""
    _, pages = _declared_pages(tmp_path, monkeypatch)
    assert [p.title for p in pages.groups["交易日志"]] == ["记账", "持仓与盈亏"]
    assert "交易日志" not in {p.title for p in pages}, "老的整页不该还挂在侧栏上"


def test_the_record_jump_targets_the_entry_subpage(tmp_path, monkeypatch):
    """「＋ 记一笔」的 st.switch_page 目标钉住指向「记账」子页（v0.3.1 §1）。

    dashboard.py 把跳转目标声明成 ENTRY_PAGE 再交给 journal_ui.jump_if_requested；
    这里对账那个变量真的是「记账」那页（真跳转 + 预填由
    tests/test_dashboard_journal.py 的 AppTest 端到端验证）。
    """
    mod, pages = _declared_pages(tmp_path, monkeypatch)
    assert mod.ENTRY_PAGE.title == "记账"
    assert mod.ENTRY_PAGE is pages.groups["交易日志"][0], \
        "跳转目标必须就是侧栏里那一页（另造一个同名 Page 会跳到未注册的页上）"
    assert "journal_ui.jump_if_requested(ENTRY_PAGE)" in SOURCE, \
        "跳页钩子没接到 ENTRY_PAGE 上"


def test_the_console_is_the_first_page(tmp_path, monkeypatch):
    """2026-09-05：控制台排第一——它是每天的操作入口（v0.2.2 曾从末位提到第二位，
    那时第一位留给手册）。"""
    _, pages = _declared_pages(tmp_path, monkeypatch)
    assert pages[0].title == "任务控制台"


def test_the_console_is_the_only_default_page(tmp_path, monkeypatch):
    """默认落地页是「任务控制台」（2026-09-05 用户定；v0.2.1～v0.5.0 是「使用说明」）。
    显式 default=True 而不是靠"排第一"：st.navigation 的落地页由这个标志定。"""
    _, pages = _declared_pages(tmp_path, monkeypatch)
    assert [p.title for p in pages if p.default] == ["任务控制台"]


def test_url_paths_are_unique_and_match_the_test_helper(tmp_path, monkeypatch):
    """URL 路径重复的话 st.navigation 直接抛异常（页哈希由它算）；
    另外测试侧的切页助手拿的是同一张表，两边错开就会静默测错页面。"""
    _, pages = _declared_pages(tmp_path, monkeypatch)
    paths = [p.url_path for p in pages]
    assert len(set(paths)) == len(paths), f"URL 路径有重复: {paths}"
    assert {p.title: p.url_path for p in pages} == PAGE_URL_PATHS


def test_navigation_uses_the_native_api_not_a_radio():
    """圆点单选器是 v0.1 的临时做法：不是链接、没有图标、没有 URL。

    走 AST 而不是子串匹配：注释里回顾一句"取代了 st.sidebar.radio"是好事，
    不该因此把测试逼红——要禁的是**代码**里还留着那个控件。
    """
    assert "st.navigation(" in SOURCE and "st.Page(" in SOURCE
    radios = [n for n in ast.walk(ast.parse(SOURCE))
              if isinstance(n, ast.Attribute) and n.attr == "radio"]
    assert radios == [], "侧栏圆点选择器应已删掉"


def test_page_intro_covers_every_page(tmp_path, monkeypatch):
    """每页页头那句话来自 PAGE_INTRO，键漏了就是 KeyError 崩页。
    自 v0.2.2 M3 起它与页头一起住在共享件 app/ui.py（页面模块都要用）。"""
    _, pages = _declared_pages(tmp_path, monkeypatch)
    assert set(app_module("ui").PAGE_INTRO) == {p.title for p in pages}


def test_the_sidebar_has_no_radio_widget_left(tmp_path):
    at = AppTest.from_file(str(copy_app(tmp_path)), default_timeout=30).run()
    assert not at.exception, at.exception
    assert at.sidebar.radio == [], "侧栏还留着圆点选择器"


def test_the_console_is_the_landing_page(tmp_path):
    """不切页直接渲染：落地页必须是「任务控制台」。"""
    at = AppTest.from_file(str(copy_app(tmp_path)), default_timeout=30).run()
    assert not at.exception, at.exception
    heads = [e.proto.body for e in at.get("html") if 'class="qd-head"' in e.proto.body]
    assert len(heads) == 1, heads
    assert 'class="qd-title">任务控制台<' in heads[0], heads[0]


@pytest.mark.parametrize("title", [p[0] for p in EXPECTED_PAGES])
def test_every_page_renders_without_exception(tmp_path, title):
    """八页 × 空 output/：导航分组改版之后任何一页都不得抛异常。"""
    at = goto_page(AppTest.from_file(str(copy_app(tmp_path)), default_timeout=30).run(),
                   title)
    assert not at.exception, f"页面 {title} 抛异常: {at.exception}"
    heads = [e.proto.body for e in at.get("html") if 'class="qd-head"' in e.proto.body]
    assert any(f'class="qd-title">{title}<' in h for h in heads), \
        f"切到 {title} 却渲染出了别的页: {heads}"


def test_page_ref_fails_loudly_on_an_unregistered_key(tmp_path, monkeypatch):
    """`ui.page_ref` 拿不到已登记的页时必须 KeyError，不许返回 None。

    `st.page_link(None)` 会渲染出一个**点了没反应**的链接——它长得和正常链接
    一模一样，用户只会以为是自己点歪了，谁也不会报上来。
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "qd_ui_pageref", Path(__file__).resolve().parent.parent / "app" / "ui.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["qd_ui_pageref"] = mod
    spec.loader.exec_module(mod)

    with pytest.raises(KeyError, match="还没登记"):
        mod.page_ref("no-such-page")

    sentinel = object()
    mod.bind_pages({"guide": sentinel})
    assert mod.page_ref("guide") is sentinel
    mod.bind_pages({})                       # 每轮 rerun 整体覆盖，不残留上一轮
    with pytest.raises(KeyError):
        mod.page_ref("guide")
