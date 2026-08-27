# tests/test_dashboard_nav.py — v0.2.2 M1：热键修复（§1）与 TAPE 命名 / 原生导航（§2）
#
# 三块内容各有各的可测边界，先说清楚为什么这么测：
#
# 1) **热键**（§1.2）：修的是浏览器行为——Streamlit 的 hotkeys-js 过滤器不看修饰键，
#    在表格里选中文字按 Cmd+C 会被当成"清缓存"的裸 `c`。pytest 里没有 DOM 也没有
#    键盘，唯一能守住的是"那段 JS 还在、关键片段没被删"，所以下面按片段断言。
#    **这不等于验证了修复**：真伪只能由用户在自己 Mac 上按一次 Cmd+C 确认。
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

from tests.conftest import PAGE_URL_PATHS, copy_app, goto_page, stub_navigation

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "app" / "dashboard.py"
SOURCE = DASHBOARD.read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")

_SPEC = importlib.util.spec_from_file_location("qd_theme_nav", ROOT / "app" / "theme.py")
theme = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(theme)

# 设计文档 §2.2 的页表：顺序、图标、URL 路径。
# 「任务控制台」从末位提到**第二位**——它是最常用的操作入口。
# 「信号池」是 M3 的活（那时才有内容），本里程碑不放占位页。
EXPECTED_PAGES = [
    ("使用说明", ":material/menu_book:", "guide"),
    ("任务控制台", ":material/play_circle:", "console"),
    ("今日信号", ":material/notifications:", "signals"),
    ("回测报告", ":material/assessment:", "backtest"),
    ("个股K线", ":material/candlestick_chart:", "kline"),
]


# ================================================================ §1.2 Cmd+C 热键修复

def test_hotkey_js_lets_modifier_combos_through():
    """核心一条：带 Cmd/Ctrl/Alt 的组合直接 return false（不进热键系统），
    浏览器自己的复制/粘贴/刷新照旧。少了 metaKey 这一项，Mac 上的 Cmd+C 照样被劫持。"""
    js = theme.HOTKEY_JS
    for key in ("metaKey", "ctrlKey", "altKey"):
        assert key in js, f"热键过滤器没检查 {key}，对应的组合键仍会被 Streamlit 吃掉"
    assert "return false" in js, "带修饰键时必须 return false（让事件回到浏览器）"


def test_hotkey_js_survives_streamlit_reassigning_the_filter():
    """Streamlit 在 useEffect 里**重新赋值** hotkeys.filter，直接赋值会被覆盖。
    必须用 defineProperty 装 setter，让后续每次赋值都自动裹上包装。"""
    js = theme.HOTKEY_JS
    assert "Object.defineProperty" in js, "直接赋值 filter 会被 Streamlit 覆盖"
    assert "configurable: true" in js, "属性不可配置的话，下次注入就再也改不动了"
    assert re.search(r"set:\s*function", js), "缺 setter：Streamlit 重新赋值时不会被包装"


def test_hotkey_js_wraps_instead_of_replacing_the_original_filter():
    """裸按 c / r 必须**照旧**工作：不带修饰键时把判断交回原过滤器，
    而不是一律返回 true（那会让输入框里打字也触发热键）。"""
    assert "orig.apply" in theme.HOTKEY_JS, "没调用原过滤器：输入框里打字会触发热键"


def test_hotkey_js_is_idempotent():
    """每次 rerun 都会重新注入这段脚本，装两次 setter 就会把包装套娃。
    幂等标志必须读一次、写一次。"""
    js = theme.HOTKEY_JS
    assert theme.HOTKEY_FLAG in js, "幂等标志没用上"
    assert js.count(theme.HOTKEY_FLAG) >= 2, "幂等标志要读一次（早退）、写一次（安装后）"
    assert js.index(theme.HOTKEY_FLAG) < js.index("Object.defineProperty"), \
        "幂等检查必须排在安装之前，否则每次 rerun 都会再套一层"


def test_hotkey_js_skips_silently_when_hotkeys_is_disappeared():
    """守卫：将来 Streamlit 不再暴露 window.hotkeys 时静默跳过。
    面板不能因为一个"锦上添花"的修复而整页崩掉。"""
    js = theme.HOTKEY_JS
    assert "window.hotkeys" in js
    guard = js.index("if (!hk)")
    assert guard < js.index("Object.defineProperty"), \
        "window.hotkeys 不存在时必须在动手之前就早退"


def test_inject_sends_the_style_and_the_hotkey_script(monkeypatch):
    """两次注入合在同一个入口（面板只调 theme.inject()）：
    CSS 走纯 <style>（Streamlit 会把它塞进不占版面的 event 容器），
    JS 单独一发且必须显式开 unsafe_allow_javascript——不开的话脚本被静默忽略，
    "注入了"和"没注入"长得一模一样。"""
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(theme.st, "html",
                        lambda body, **kwargs: calls.append((body, kwargs)))
    theme.inject()
    assert len(calls) == 2, f"应恰好两发（样式 + 热键脚本），实际 {len(calls)}"
    style, style_kwargs = calls[0]
    assert style.startswith("<style>") and style.endswith("</style>")
    assert theme.FONT_IMPORT in style
    assert "<script" not in style, "样式那一发里不许夹 JS（它会被当纯样式静默丢掉）"
    assert style_kwargs == {}, "样式不需要任何开关"
    script, script_kwargs = calls[1]
    assert script.startswith("<script>") and script.endswith("</script>")
    assert theme.HOTKEY_JS in script
    assert script_kwargs == {"unsafe_allow_javascript": True}, \
        f"不开这个开关，Streamlit 会静默忽略脚本: {script_kwargs}"


def test_the_hotkey_fix_reaches_every_page():
    """面板只在顶层调一次 theme.inject()，而顶层每次 rerun 都跑——
    修复因此对六页、每一轮都生效。这条钉的是"注入点没被挪进某个页面函数里"。"""
    assert "theme.inject()" in SOURCE
    assert SOURCE.count("theme.inject()") == 1, "注入点只该有一个"


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


def test_the_readme_documents_how_to_verify_the_hotkey_fix():
    """§1.3：热键修复没法用 pytest 验，验收只能人工——那就把步骤写进 README，
    否则"到底修好没有"永远没人能复核。两步缺一不可：
    Cmd+C 应正常复制（修好了），裸按 c 仍应弹清缓存对话框（没把功能整个禁掉）。"""
    assert "Cmd+C" in README, "README 没写 Cmd+C 的验收步骤"
    assert "hotkeys" in README, "README 没交代根因（Streamlit 的 hotkeys 过滤器）"


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
    """§2.2 的页表：顺序 + 图标 + URL 路径，一项都不许漂。"""
    _, pages = _declared_pages(tmp_path, monkeypatch)
    actual = [(p.title, p.icon, p.url_path) for p in pages]
    assert actual == EXPECTED_PAGES


def test_the_console_is_the_second_page(tmp_path, monkeypatch):
    """§2.2：控制台从末位提到第二位——它是最常用的操作入口，
    排在三张只读页后面每次都要多找一遍。"""
    _, pages = _declared_pages(tmp_path, monkeypatch)
    assert pages[1].title == "任务控制台"


def test_the_guide_is_the_only_default_page(tmp_path, monkeypatch):
    """默认落地页仍是「使用说明」（v0.2.1 的决定不变）。
    显式 default=True 而不是靠"排第一"：st.navigation 的落地页由这个标志定。"""
    _, pages = _declared_pages(tmp_path, monkeypatch)
    assert [p.title for p in pages if p.default] == ["使用说明"]


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
    """每页页头那句话来自 PAGE_INTRO，键漏了就是 KeyError 崩页。"""
    mod, pages = _declared_pages(tmp_path, monkeypatch)
    assert set(mod.PAGE_INTRO) == {p.title for p in pages}


def test_the_sidebar_has_no_radio_widget_left(tmp_path):
    at = AppTest.from_file(str(copy_app(tmp_path)), default_timeout=30).run()
    assert not at.exception, at.exception
    assert at.sidebar.radio == [], "侧栏还留着圆点选择器"


def test_the_guide_is_the_landing_page(tmp_path):
    """不切页直接渲染：落地页必须是「使用说明」。"""
    at = AppTest.from_file(str(copy_app(tmp_path)), default_timeout=30).run()
    assert not at.exception, at.exception
    heads = [e.proto.body for e in at.get("html") if 'class="qd-head"' in e.proto.body]
    assert len(heads) == 1, heads
    assert 'class="qd-title">使用说明<' in heads[0], heads[0]


@pytest.mark.parametrize("title", [p[0] for p in EXPECTED_PAGES])
def test_every_page_renders_without_exception(tmp_path, title):
    """五页 × 空 output/：导航改版之后任何一页都不得抛异常。"""
    at = goto_page(AppTest.from_file(str(copy_app(tmp_path)), default_timeout=30).run(),
                   title)
    assert not at.exception, f"页面 {title} 抛异常: {at.exception}"
    heads = [e.proto.body for e in at.get("html") if 'class="qd-head"' in e.proto.body]
    assert any(f'class="qd-title">{title}<' in h for h in heads), \
        f"切到 {title} 却渲染出了别的页: {heads}"
