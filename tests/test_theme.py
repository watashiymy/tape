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
    HTML 逃逸口子，而 st.html 就是为插样式加的。

    v0.5.0 起 inject() **只发这一件**：v0.2.2 那段"Cmd+C 热键修复"JS 已删——
    它在 1.61 上是空操作（`c` 键不走 hotkeys-js），而且它是全项目唯一一处
    `unsafe_allow_javascript=True`。真修法是 config.toml 的 toolbarMode。"""
    calls: list[str] = []
    monkeypatch.setattr(theme.st, "html",
                        lambda body, **kwargs: calls.append(body))
    theme.inject()
    styles = [c for c in calls if c.startswith("<style>")]
    assert len(styles) == 1, f"样式应恰好注入一次: {len(styles)}"
    assert len(calls) == 1, f"inject 不该再发第二件东西（热键 JS 已删）: {len(calls)}"
    assert styles[0].endswith("</style>")
    assert theme.FONT_IMPORT in styles[0]


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


def test_pill_carries_an_optional_native_tooltip():
    """v0.2.4 扫描范围徽标：pill 上只放得下四五个字，"范围未知"必须能就地解释。"""
    html = theme.pill("范围未知", "idle", "该文件产生于记录范围之前")
    assert 'title="该文件产生于记录范围之前"' in html


def test_a_pill_without_a_tooltip_has_no_title_attribute():
    """没有 tooltip 就不留空属性（空 title 会在有些浏览器上弹一个空气泡）。"""
    assert "title=" not in theme.pill("空闲", "idle")


def test_pill_escapes_its_tooltip():
    """tooltip 同样来自磁盘上的文件内容，未转义的引号能直接撑破 title 属性
    （`" onmouseover=… x="` 就是这么塞进一个事件处理器的）。"""
    html = theme.pill("范围未知", "idle", '" onmouseover=alert(1) x="')
    assert 'title="&quot; onmouseover=alert(1) x=&quot;"' in html


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
             theme.metric("d", "e"), theme.help_dot("f")]
    for html in blobs:
        for banned in ("st-emotion", "data-testid", "emotion-cache"):
            assert banned not in html


def test_config_toml_pins_the_v050_security_defaults():
    """v0.5.0 把两条**安全/隐私默认**写进了 config.toml，README §3 据此宣称
    「本仓库把安全绑定变成了默认值」。删掉它们，README 当场变成假话——
    而这是全项目唯一一条"说错了就是安全问题"的文档。

    实测依据：不带 --server.address 启动时监听项从 *:8501 变成 127.0.0.1，
    启动日志里的 Network URL / External URL 两行随之消失。
    """
    cfg = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    assert cfg.get("server", {}).get("address") == "127.0.0.1", \
        "回环绑定的默认值没了——面板能执行本机命令，少这一条就是全网卡监听"
    assert cfg.get("browser", {}).get("gatherUsageStats") is False, \
        "使用统计上报又打开了——这台机器上同时存着用户的真实成交记录"


def test_config_toml_turns_off_the_clear_cache_hotkey_via_toolbar_mode():
    """Cmd+C 复制文字弹 "Clear cache?" 的**真修法**（v0.5.0）。

    1.61 的前端把 `c` 键交给 App.handleKeyDown 自己 switch，不经过 hotkeys-js；
    那个 case 被 sT(isOwner, toolbarMode) 守着——viewer 模式下恒为 false，热键与
    菜单项一并消失。真机验证：Deploy 按钮随之消失（同一个判定的可观测副作用）。
    v0.2.2 注入 JS 包 hotkeys.filter 的"修复"是空操作，已删。
    """
    cfg = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    assert cfg.get("client", {}).get("toolbarMode") == "viewer", \
        "toolbarMode 不是 viewer：Cmd+C 又会弹 Clear cache 对话框"


def test_manual_tag_escapes_its_text():
    """它的输出直接喂给 section() 的原始 HTML 通道，这里不转义就没人转义了。
    st.html 不套 iframe，一个未转义的 < 就能把版面撕开。"""
    assert "<img" not in theme.manual_tag('<img src=x onerror=alert(1)>')
    assert "&lt;img" in theme.manual_tag('<img src=x onerror=alert(1)>')


def test_rail_is_a_bare_container_with_the_arrow_in_css():
    """箭头字形放在 CSS 的 ::after 里，不写死在 HTML 里——窄屏媒体查询要把它从
    → 换成 ↓（streamlit 窄屏会把列竖着堆起来，那时横箭头是错的）。"""
    assert theme.rail() == '<div class="qd-rail"></div>'
    assert ".qd-rail::after" in theme.CSS and '"→"' in theme.CSS
    assert '"↓"' in theme.CSS, "窄屏没有换成下箭头"


def test_manual_row_wraps_tag_and_text_and_has_css():
    """一行式人工步骤（2026-09-14）：灰标签 + 说明，两段都转义，类名在 CSS 里有规则。"""
    html = theme.manual_row("人工步骤", "加进信号池：<b>x</b>")
    assert 'class="qd-manual-row"' in html and 'class="qd-manual"' in html
    assert "&lt;b&gt;" in html and "<b>" not in html
    for cls in ("qd-manual-row", "qd-manual-text"):
        assert f".{cls} {{" in theme.CSS


# ================================================================ 首页卡片（Task 20）

def test_stat_card_renders_label_value_and_optional_image():
    html = theme.stat_card("上证指数", "3939.76", sub="-12.37 · -0.31%", color=theme.DOWN,
                           img="data:image/svg+xml;base64,QUJD", note="成交 7767亿")
    assert 'class="qd-card"' in html and "上证指数" in html and "3939.76" in html
    assert f'<div class="qd-card-sub" style="color: {theme.DOWN}">' in html, "有副文时颜色上副文"
    assert '<img class="qd-card-img" src="data:image/svg+xml;base64,QUJD"' in html
    assert "<svg" not in html, "小图必须走 img data URI，st.html 会删内联 svg"


def test_stat_card_colors_the_value_when_there_is_no_sub():
    html = theme.stat_card("涨停", "51", color=theme.UP)
    assert f'<div class="qd-card-value" style="color: {theme.UP}">51</div>' in html


def test_stat_card_can_be_image_only():
    html = theme.stat_card("", "", img="data:image/svg+xml;base64,QUJD")
    assert "qd-card-label" not in html and "qd-card-value" not in html and "qd-card-img" in html


def test_stat_card_rejects_non_data_uri_images_and_off_palette_colors():
    import pytest
    with pytest.raises(ValueError):
        theme.stat_card("x", "1", img="https://evil/x.svg")
    with pytest.raises(ValueError):
        theme.stat_card("x", "1", color="#123456")


def test_stat_card_escapes_everything():
    html = theme.stat_card("<b>", "<i>", sub="<u>", note="<s>")
    assert "<b>" not in html and "<i>" not in html and "<u>" not in html and "<s>" not in html


def test_status_line_only_uses_running_or_idle():
    import pytest
    html = theme.status_line("交易中 · 14:35:03", running=True, extra="数据延迟")
    assert 'qd-pill-running' in html and "数据延迟" in html
    assert 'qd-pill-idle' in theme.status_line("已收盘 · 2026-09-24", running=False)
    for kind in ("success", "failed"):
        assert f"qd-pill-{kind}" not in html


def test_home_css_rules_exist_for_the_new_builders():
    for cls in (".qd-card", ".qd-card-label", ".qd-card-value", ".qd-card-sub", ".qd-card-img",
                ".qd-card-note", ".qd-status"):
        assert cls + " {" in theme.CSS, cls


# ================================================================ 就地帮助圆点（2026-09-29 设计）

_RULE = re.compile(SELECTOR.pattern + r"([^}]*)\}", re.M)


def _css_rule_pairs() -> list[tuple[str, str]]:
    """[(选择器头, 规则体)]，按出现顺序、重复的头也都留着（@media 里会重写同名规则）。"""
    return [(m.group(1).strip(), m.group(2)) for m in _RULE.finditer(theme.CSS)]


def _css_rules() -> dict[str, str]:
    """{选择器头: 规则体}。CSS 约定每条规则的选择器单独一行，所以按行首解析。
    头重复时只剩最后一条——要查的规则先用 `_css_rule_pairs` 确认不重复。"""
    return dict(_css_rule_pairs())


def test_help_dot_structure_and_modifiers():
    plain = theme.help_dot("一句话。")
    assert plain == ('<span class="qd-help-dot" tabindex="0">?'
                     '<span class="qd-help-tip"><p>一句话。</p></span></span>')
    assert 'class="qd-help-dot qd-help-end"' in theme.help_dot("x", align="end")
    assert 'class="qd-help-dot qd-help-wide"' in theme.help_dot("x", wide=True)
    assert 'class="qd-help-dot qd-help-end qd-help-wide"' in theme.help_dot("x", align="end", wide=True)
    assert "qd-help-end" not in theme.help_dot("x", align="middle"), "未知方向按 start"
    assert "<svg" not in plain


def test_help_dot_escapes_before_it_formats():
    html = theme.help_dot('" onmouseover=alert(1) x="<script>x</script>**粗**')
    assert "<script" not in html
    assert 'onmouseover=alert(1) x="' not in html, "引号没转义就能跳出属性"
    assert "&quot; onmouseover=alert(1)" in html
    assert "<b>粗</b>" in html, "转义之后才认加粗"


def test_help_dot_markdown_subset():
    text = ("**信号跟踪**——盯着信号池，这是唯一会提示你**卖出**的任务。\n\n"
            "- **做什么**：只看信号池里的标的，\n  与前一交易日比对。\n"
            "- **多久**：通常十几秒。\n")
    html = theme.help_dot(text)
    assert "<p><b>信号跟踪</b>——盯着信号池，这是唯一会提示你<b>卖出</b>的任务。</p>" in html, html
    assert ("<ul><li><b>做什么</b>：只看信号池里的标的，与前一交易日比对。</li>"
            "<li><b>多久</b>：通常十几秒。</li></ul>") in html, html
    assert "**" not in html and "\n" not in html


def test_help_dot_joins_single_newlines_inside_a_paragraph():
    assert "<p>one two</p><p>next</p>" in theme.help_dot("one\ntwo\n\nnext")
    assert "<p>第一行第二行</p><p>第二段</p>" in theme.help_dot("第一行\n第二行\n\n第二段"), \
        "换行两边都是全角字时直接相接：Chrome 会把软换行渲染成空格，中文里不该冒出空格"
    assert "<p>约 60 只</p>" in theme.help_dot("约 60\n只"), "一边是半角（数字、字母）照样当空格"
    assert "<p><b>已扫出的信号</b>（结果整轮跑完才保存）</p>" in theme.help_dot(
        "**已扫出的信号**\n（结果整轮跑完才保存）"), "看的是 ** 里面的字，不是星号"


def test_help_dot_bold_stays_within_one_line():
    """spec §1.2「同一行内」：这一行落单的 ** 不能和下一行的配成对。"""
    assert theme.help_dot("a ** b\n**c** d").endswith(
        '<span class="qd-help-tip"><p>a ** b <b>c</b> d</p></span></span>')


def test_help_dot_parser_edge_branches():
    assert "<p>indented next</p>" in theme.help_dot("  indented\nnext"), \
        "前面没有列表时，缩进行只是普通段落行（不能去找上一个列表项）"
    assert "<ul><li>甲</li></ul><p>段落</p>" in theme.help_dot("- 甲\n段落"), \
        "列表后面紧跟不缩进的行：先收尾列表、再起段落，顺序不能颠倒"


def test_help_dot_empty_text_gives_no_dot():
    """空说明不出圆点：否则悬停出一个空的描边框。"""
    assert theme.help_dot("") == ""
    assert theme.help_dot(" \n\n  ") == ""


def test_help_dot_css_rules():
    rules = _css_rules()
    dot, tip = rules[".qd-help-dot"], rules[".qd-help-tip"]
    for prop in ("position: relative", "font-family: var(--qd-sans)", "line-height: 1"):
        assert prop in dot, prop
    for prop in ("display: none", "position: absolute", "font-family: var(--qd-sans)",
                 "letter-spacing: normal", "white-space: normal", "background: var(--qd-surface)"):
        assert prop in tip, prop
    assert re.search(r"z-index:\s*[1-9]", tip), "层级要为正，否则被后面的元素容器盖住"
    assert f"--qd-surface: {theme.SURFACE};" in theme.CSS
    heads = list(rules)
    assert ".qd-help-end .qd-help-tip" in heads and ".qd-help-wide .qd-help-tip" in heads
    shown = [h for h in heads if "qd-help-tip" in h and "display: block" in rules[h]]
    assert len(shown) == 1 and ":hover" in shown[0] and ":focus-visible" in shown[0], shown
    help_heads = [h for h in heads if "qd-help" in h]
    assert not [h for h in help_heads if re.search(r":focus(?!-visible)", h)], \
        "只能用 :focus-visible：鼠标点一下也会触发 :focus，提示框就钉住不走了"
    assert ".qd-help-dot .qd-help-tip ul" in heads, "列表缩进要两级类名，压过 st.html 外层的 ul 内边距"
    for tag in ("p", "ul"):
        assert "font-size: inherit" in rules[f".qd-help-dot .qd-help-tip {tag}"], \
            f"Streamlit 全局给 p、ol、ul、dl 定了 1rem，{tag} 不写 inherit 就拿不到提示框的 0.8rem"
    all_help = [h for h, _ in _css_rule_pairs() if "qd-help" in h]
    assert len(all_help) == len(set(all_help)), "同一个 .qd-help 选择器写了两条，上面的查法只看到最后一条"
    assert ".qd-help {" not in theme.CSS, "旧的 .qd-help 规则没人用，删掉免得新类误继承"


def test_stat_card_without_help_is_byte_identical():
    want = ('<div class="qd-card"><div class="qd-card-label">涨停</div>'
            f'<div class="qd-card-value" style="color: {theme.UP}">51</div></div>')
    assert theme.stat_card("涨停", "51", color=theme.UP) == want


def test_stat_card_help_dot_sits_inside_the_label():
    html = theme.stat_card("炸板率", "12.3%", help="炸板 = 盘中碰到过涨停、收盘没封住。", help_align="end")
    dot = theme.help_dot("炸板 = 盘中碰到过涨停、收盘没封住。", align="end")
    assert f'<div class="qd-card-label">炸板率{dot}</div>' in html, html


def test_image_only_card_ignores_help():
    html = theme.stat_card("", "", img="data:image/svg+xml;base64,QUJD", help="x")
    assert "qd-help-dot" not in html and "qd-card-label" not in html
    assert "qd-help-dot" not in theme.stat_card("", "5", help="x"), "没有标签就没有挂圆点的地方"


def test_stat_card_sub_tuple_is_one_line_per_part():
    html = theme.stat_card("深证成指", "13316.97", sub=("-319.10", "-2.34%"), color=theme.DOWN)
    line = f'<div class="qd-card-sub" style="color: {theme.DOWN}">'
    assert html.count(line) == 2, html
    assert f"{line}-319.10</div>{line}-2.34%</div>" in html
    assert '<div class="qd-card-value">13316.97</div>' in html, "有副文时颜色上副文、不上数值"
    escaped = theme.stat_card("x", "1", sub=("<u>", "<i>"))
    assert "<u>" not in escaped and "<i>" not in escaped


def test_stat_card_sub_str_is_not_split_into_characters():
    html = theme.stat_card("站上 20 日线", "41.4%", sub="2070/5004")
    assert html.count('class="qd-card-sub"') == 1 and ">2070/5004<" in html


def test_section_without_help_is_byte_identical():
    p = theme.pill("空闲", "idle")
    assert theme.section("回测", p) == f'<div class="qd-section">回测<span class="qd-section-pill">{p}</span></div>'
    assert theme.section("回测") == '<div class="qd-section">回测</div>'


def test_section_help_dot_follows_the_title_before_the_pill():
    p = theme.pill("空闲", "idle")
    html = theme.section("信号跟踪", p, help="**信号跟踪**——x", help_align="end", help_wide=True)
    dot = theme.help_dot("**信号跟踪**——x", align="end", wide=True)
    assert html == f'<div class="qd-section">信号跟踪{dot}<span class="qd-section-pill">{p}</span></div>'


# ================================================================ 页头右端的「帮助中心」（2026-09-30 设计）

def test_page_head_keeps_title_and_pill_on_one_line_and_lets_the_lead_wrap():
    """页头左栏变窄时（1024 宽、侧边栏展开）只让那句说明折行：不加这几条，页名会被折成「信 / 号」。"""
    heads = [h for h, _ in _css_rule_pairs()]
    for sel in (".qd-title", ".qd-sub", ".qd-head-right"):
        assert heads.count(sel) == 1, sel
    rules = _css_rules()
    assert "white-space: nowrap" in rules[".qd-title"] and "flex-shrink: 0" in rules[".qd-title"]
    assert "white-space: nowrap" in rules[".qd-head-right"] and "flex-shrink: 0" in rules[".qd-head-right"]
    assert "min-width: 0" in rules[".qd-sub"]
