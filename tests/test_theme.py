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

from quant.report import fmt

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / ".streamlit" / "config.toml"
DASHBOARD = ROOT / "app" / "dashboard.py"

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
    """红涨绿跌只在 quant.report.fmt 定义一次：theme 里再抄一遍，
    改一处漏一处就会出现"表格绿、指标卡红"的对撞。"""
    assert theme.UP is fmt.UP
    assert theme.DOWN is fmt.DOWN


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

def test_font_import_points_at_google_fonts_with_three_families():
    families = ("Noto+Serif+SC", "Noto+Sans+SC", "IBM+Plex+Mono")
    assert "fonts.googleapis.com" in theme.FONT_IMPORT
    for fam in families:
        assert fam in theme.FONT_IMPORT, f"@import 少了 {fam}"


@pytest.mark.parametrize("stack,first,generic,fallback", [
    ("SERIF", "Noto Serif SC", "serif", "Songti SC"),
    ("SANS", "Noto Sans SC", "sans-serif", "PingFang SC"),
    ("MONO", "IBM Plex Mono", "monospace", "Menlo"),
])
def test_font_stacks_have_offline_fallbacks(stack, first, generic, fallback):
    """离线/被墙时 Google Fonts 拿不到，必须落到 macOS 自带字体再落到通用族，
    否则中文直接变豆腐块。"""
    value = getattr(theme, stack)
    assert value.startswith(f'"{first}"'), f"{stack} 首选应是 {first}"
    assert fallback in value, f"{stack} 缺系统兜底 {fallback}"
    assert value.rstrip().endswith(generic), f"{stack} 必须以通用族 {generic} 收尾"


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
