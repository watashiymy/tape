# tests/test_theme.py — v0.2.1 §2 视觉基座：.streamlit/config.toml 的色板、
# app/theme.py 的字体栈与语义化 CSS。
#
# 这些测试守的是两条工程纪律，两条都是踩过坑才写下来的：
#   1. 基础配色走 config.toml（官方支持面），CSS 绝不碰 Streamlit 内部 class
#      （.st-emotion-cache-xxxx 每版都变；本项目刚被两个过期 API 咬过一次）；
#   2. 字体必须有 fallback 链——Google Fonts 连不上时不能变豆腐块。
import importlib.util
import re
import tomllib
from pathlib import Path

import pytest

from quant.report import fmt, palette

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / ".streamlit" / "config.toml"
DASHBOARD = ROOT / "app" / "dashboard.py"
THEME = ROOT / "app" / "theme.py"

_SPEC = importlib.util.spec_from_file_location("qd_theme", ROOT / "app" / "theme.py")
theme = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(theme)

HEX = re.compile(r"#[0-9A-Fa-f]{3,8}\b")
# 规则的选择器：行首到 '{' 之间的内容（每条规则的选择器单独一行，便于这样解析）
SELECTOR = re.compile(r"^\s*([^{}@\n][^{}\n]*?)\s*\{", re.M)


# ---------------------------------------------------------------- config.toml

def test_config_toml_exists_and_parses():
    """面板的基础配色只认这个文件；写坏了 streamlit 会退回出厂浅色主题。"""
    assert CONFIG.exists(), "缺 .streamlit/config.toml：暗色主题不会生效"
    tomllib.loads(CONFIG.read_text(encoding="utf-8"))


def test_config_toml_has_the_four_design_colors():
    """设计 §2.1 定死的四个色值，一个不许改（改了就不是"金融电报"那套了）。"""
    cfg = tomllib.loads(CONFIG.read_text(encoding="utf-8"))["theme"]
    assert cfg["base"] == "dark"
    assert cfg["primaryColor"] == "#C8963E"
    assert cfg["backgroundColor"] == "#12141A"
    assert cfg["secondaryBackgroundColor"] == "#1A1D26"
    assert cfg["textColor"] == "#E8E4DC"


def test_theme_constants_match_config_toml():
    """同一个色值在两处出现是必然的（config.toml 不能 import Python 常量），
    所以必须有测试钉住它们一致——否则改了一处，CSS 与底色就慢慢错开。"""
    cfg = tomllib.loads(CONFIG.read_text(encoding="utf-8"))["theme"]
    assert theme.PRIMARY == cfg["primaryColor"]
    assert theme.BACKGROUND == cfg["backgroundColor"]
    assert theme.SURFACE == cfg["secondaryBackgroundColor"]
    assert theme.TEXT == cfg["textColor"]


# ---------------------------------------------------------------- 色值单一来源

def test_direction_colors_come_from_fmt_not_redefined():
    """红涨绿跌只在一处定义：theme 里再抄一遍，
    改一处漏一处就会出现"表格绿、指标卡红"的对撞。"""
    assert theme.UP is fmt.UP
    assert theme.DOWN is fmt.DOWN


@pytest.mark.parametrize("name", [
    "PRIMARY", "BACKGROUND", "SURFACE", "TEXT", "HAIRLINE",
    "UP", "DOWN", "UP_DIM", "DOWN_DIM", "PRIMARY_DIM", "MUTED_DIM",
])
def test_theme_colors_are_forwarded_from_the_shared_palette(name):
    """色板下沉到 quant.report.palette：面板（app/theme.py）与图表
    （quant.report.charts）从同一处取色，"改一处、两边同步"。

    charts.py 属于 src/，不能反向 import app/theme.py——所以单一事实来源必须
    在 src/ 里。曾经两边各写一套：面板改成暗色后，图表还是 plotly 默认浅色模板，
    内嵌进去中间开一块白，两轮评审都没看出来。

    `is` 而不是 `==`：抄一份字面量照样相等，但改一处就漂移。"""
    assert getattr(theme, name) is getattr(palette, name)


def test_theme_muted_is_the_palette_neutral():
    """次级灰与"平盘不上色"的中性灰必须是同一个（同屏两种灰就是没有灰）。"""
    assert theme.MUTED is palette.NEUTRAL


def test_theme_module_writes_no_hex_color_of_its_own():
    """theme.py 里一个十六进制都不许有：全部走 palette 常量。
    留一个"就这一个"的裸色值，下次改色板时它就是漂移的那一处。"""
    found = HEX.findall(THEME.read_text(encoding="utf-8"))
    assert found == [], f"theme.py 里有裸写色值: {found}"


def test_every_hex_in_css_comes_from_the_palette():
    """§4：不许裸写十六进制色值散落各处。CSS 里的每个色值都必须来自色板常量。"""
    used = set(HEX.findall(theme.CSS))
    known = {v for v in theme.PALETTE.values() if isinstance(v, str)}
    assert used <= known, f"CSS 里有色板之外的裸色值: {sorted(used - known)}"


def test_palette_is_not_empty_and_all_hex():
    assert theme.PALETTE, "色板不能是空的"
    for name, value in theme.PALETTE.items():
        assert HEX.fullmatch(value), f"{name} 不是十六进制色值: {value}"


def test_dashboard_has_no_hardcoded_hex_colors():
    """面板文件里一个色值都不许有：全部走 config.toml 或 theme 的常量。"""
    found = HEX.findall(DASHBOARD.read_text(encoding="utf-8"))
    assert found == [], f"dashboard.py 里有裸写色值: {found}"


# ---------------------------------------------------------------- CSS 选择器纪律

def test_css_only_targets_our_own_qd_classes():
    """只选自己包出来的 .qd-* 容器。碰 Streamlit 内部 class 或 data-testid
    的样式，下次 pip install -U streamlit 就静默失效。"""
    selectors = [s.strip() for group in SELECTOR.findall(theme.CSS)
                 for s in group.split(",")]
    assert selectors, "CSS 里一条规则都没解析出来（正则或 CSS 排版有问题）"
    for sel in selectors:
        head = sel.split()[0]      # 后代选择器只看最外层
        assert head == ":root" or head.startswith(".qd-"), \
            f"选择器 {sel!r} 不是我们自己的 .qd-* 容器"


def test_css_never_mentions_streamlit_internals():
    for banned in ("st-emotion", "data-testid", "stMarkdown", "emotion-cache"):
        assert banned not in theme.CSS, f"CSS 里出现 Streamlit 内部标识 {banned}"


def test_css_has_no_animation():
    """§7：不加装饰性动画（交易面板上跳动的元素是干扰）。"""
    for banned in ("@keyframes", "animation:", "transition:"):
        assert banned not in theme.CSS, f"CSS 里出现动画声明 {banned}"


# ---------------------------------------------------------------- 字体

# 字体策略：**中文用系统字体，只有拉丁等宽走网络**。两条实测理由：
#   1. 浏览器实测 document.fonts.check('700 16px "Noto Serif SC"') === false，
#      拉丁串宽度与"不存在的字体"逐像素相同（字形根本没被用上），初始渲染时
#      202 个 Noto Serif 分片状态全是 unloaded——CJK 网络字体按 ~200 个
#      unicode-range 分片，动态注入的 @import 不能在首屏可靠触发分片加载，
#      标题实际一直是 macOS 自带 Songti SC 在撑（"看着对"是兜底救的场）；
#   2. 本项目用户在中国大陆，Google Fonts 常不可达，把可用性押在 CDN 上不合理。
# 系统衬线中文本就是想要的观感，直接写在首位：诚实、更快、少 200+ 条无用 font-face。
# IBM Plex Mono 是例外——纯拉丁小字体，实测宽度 806.4（既非 Menlo 的 809.2、
# 也非不存在字体的 733.4），确实生效，保留网络加载 + Menlo 兜底。

def test_font_import_only_loads_the_latin_mono_family():
    """@import 里只许剩 IBM Plex Mono。两个 CJK 家族留着是纯负担：
    首屏加载不了（见上），却要让浏览器解析 200+ 条 font-face 声明。"""
    assert "fonts.googleapis.com" in theme.FONT_IMPORT
    assert "IBM+Plex+Mono" in theme.FONT_IMPORT
    for cjk in ("Noto+Serif+SC", "Noto+Sans+SC"):
        assert cjk not in theme.FONT_IMPORT, \
            f"@import 还挂着加载不到的 CJK 网络字体 {cjk}"


@pytest.mark.parametrize("stack,first,generic", [
    ("SERIF", "Songti SC", "serif"),
    ("SANS", "PingFang SC", "sans-serif"),
])
def test_cjk_stacks_lead_with_the_system_font(stack, first, generic):
    """中文字体栈的**首位**必须是系统字体：网络 CJK 字体在首屏拿不到，
    把它排在前面只是让浏览器多试一次、然后照样落兜底——写成实际生效的那个。"""
    value = getattr(theme, stack)
    assert value.startswith(f'"{first}"'), f"{stack} 首选应是系统字体 {first}"
    assert value.rstrip().endswith(generic), f"{stack} 必须以通用族 {generic} 收尾"


def test_mono_keeps_the_network_family_with_offline_fallback():
    """等宽是唯一保留网络加载的：纯拉丁、实测生效。但兜底不许省——
    离线/被墙时拿不到，数字列会掉到比例字体，一列数就对不齐了。"""
    assert theme.MONO.startswith('"IBM Plex Mono"'), "MONO 首选应是 IBM Plex Mono"
    assert "Menlo" in theme.MONO, "MONO 缺系统兜底 Menlo"
    assert theme.MONO.rstrip().endswith("monospace"), "MONO 必须以通用族收尾"


def test_mono_stack_is_forwarded_from_the_shared_palette():
    """等宽栈也是面板与图表共用的（图表的轴标签用它对齐数字），同样只定义一次。"""
    assert theme.MONO is palette.MONO


def test_css_uses_the_font_stacks():
    """字体栈定义了但没用上是最容易犯的错（改完自测"没变化"就以为是缓存）。"""
    for stack in ("SERIF", "SANS", "MONO"):
        assert getattr(theme, stack) in theme.CSS, f"CSS 没用到 {stack} 字体栈"


def test_numbers_use_tabular_figures():
    """§2.3：数字等宽才能竖着扫；等宽字体之外还要显式开表格数字。"""
    assert "tabular-nums" in theme.CSS


# ---------------------------------------------------------------- 注入口

def test_inject_is_the_single_entry_and_uses_st_html(monkeypatch):
    """注入必须走 st.html：st.markdown(unsafe_allow_html=True) 会给整页开
    HTML 逃逸口子，而 st.html 就是为插样式加的。"""
    calls: list[str] = []
    monkeypatch.setattr(theme.st, "html", lambda body: calls.append(body))
    theme.inject()
    assert len(calls) == 1
    assert calls[0].startswith("<style>") and calls[0].endswith("</style>")
    assert theme.FONT_IMPORT in calls[0]


def test_dashboard_injects_the_theme():
    """theme.py 写好了但面板没调 inject()，字体与排版一行都不会生效。"""
    src = DASHBOARD.read_text(encoding="utf-8")
    assert "theme.inject()" in src, "dashboard.py 必须调用 theme.inject()"


# ================================================================ v0.2.1 M2：HTML 组件
# 这四个构造函数是纯字符串函数，所以能在这里逐条断言。面板里只剩 st.html(...) 一句。
#
# **必须转义**：pill 的文案里带 state.status（普通 JSON，用户手改得动）、
# 小结里带 run 目录名。st.html 不套 iframe，未转义的 '<' 会把版面撕开。

def test_pill_wraps_text_in_the_two_classes():
    html = theme.pill("运行中", "running")
    assert 'class="qd-pill qd-pill-running"' in html
    assert ">运行中<" in html


def test_pill_escapes_its_text():
    """状态文件是手工改得动的 JSON：status: "<b>x" 不能变成真的标签。"""
    html = theme.pill("<img src=x onerror=1>", "idle")
    assert "<img" not in html
    assert "&lt;img" in html


def test_pill_of_unknown_kind_falls_back_to_idle():
    """类目拼错（或将来加了新状态）不能渲染出没有底色的裸文字。"""
    assert "qd-pill-idle" in theme.pill("怪状态", "no-such-kind")


@pytest.mark.parametrize("kind", ["running", "success", "failed", "idle"])
def test_every_pill_kind_has_a_css_rule(kind):
    """构造出来的类名必须在 CSS 里真有对应规则，否则 pill 就是没底色的白字。"""
    assert f".qd-pill-{kind} {{" in theme.CSS


def test_page_head_has_title_subtitle_and_pill():
    """§2.4 通用页头：页名（衬线大字）+ 一句话说明（灰色小字）+ 右侧状态 pill。"""
    html = theme.page_head("回测报告", "看历史检验结果", theme.pill("空闲", "idle"))
    assert 'class="qd-head"' in html
    assert 'class="qd-title"' in html and ">回测报告<" in html
    assert 'class="qd-sub"' in html and "看历史检验结果" in html
    assert "qd-pill-idle" in html


def test_page_head_without_a_pill_still_renders():
    html = theme.page_head("回测报告", "看历史检验结果")
    assert "qd-pill" not in html
    assert "回测报告" in html


def test_page_head_escapes_title_and_subtitle():
    html = theme.page_head("<b>x</b>", "<i>y</i>")
    assert "<b>" not in html and "<i>" not in html


def test_section_is_a_serif_hairline_heading():
    """§2.3 第 2 条：发丝线代替方框——小标题靠 1px 分隔线，不用四面描边。"""
    html = theme.section("交易明细")
    assert 'class="qd-section"' in html and ">交易明细<" in html
    assert "border-bottom" in theme.CSS.split(".qd-section {")[1].split("}")[0]


def test_section_escapes_its_title():
    assert "<script" not in theme.section("<script>x</script>")


def test_metric_puts_the_value_in_a_mono_class():
    """§2.3 第 1 条：数值最大最亮（等宽放大），标签降为小号灰字。"""
    html = theme.metric("总收益率", "+116.10%")
    assert 'class="qd-metric-label"' in html and "总收益率" in html
    assert 'class="qd-metric-value"' in html and "+116.10%" in html


def test_metric_color_is_applied_inline_when_given():
    """红绿是**按值**定的，写不进静态 CSS，只能内联；色值由 fmt 给（单一来源）。"""
    html = theme.metric("总收益率", "+116.10%", fmt.UP)
    assert f"color: {fmt.UP}" in html


def test_metric_without_color_has_no_inline_style():
    """不上色时不许留一个空 style（也不许悄悄塞灰色：那会让正常数字比标题还暗）。"""
    html = theme.metric("夏普比率(rf=0)", "0.96")
    assert "style=" not in html


def test_metric_escapes_label_and_value():
    html = theme.metric("<b>l</b>", "<b>v</b>")
    assert "<b>" not in html


def test_metric_color_only_takes_palette_values():
    """内联色值必须来自色板/方向色，不许调用方随便塞字符串（那就是 CSS 注入口子）。"""
    with pytest.raises(ValueError):
        theme.metric("总收益率", "+1%", "red; background: url(x)")


def test_html_builders_never_emit_streamlit_internals():
    """与 CSS 同一条纪律：我们只包自己的 .qd-* 容器。"""
    blobs = [theme.pill("x", "idle"), theme.page_head("a", "b"), theme.section("c"),
             theme.metric("d", "e")]
    for html in blobs:
        for banned in ("st-emotion", "data-testid", "emotion-cache"):
            assert banned not in html
