"""面板视觉基座（v0.2.1 设计 §2）：色板常量、字体栈、语义化 CSS。

**分工**：基础配色（底色/主色/文字色）全在 `.streamlit/config.toml`——那是官方支持面；
本文件的 CSS 只做 config.toml 表达不了的**排版层级**（衬线标题、等宽放大的数值、
发丝线、状态 pill），而且**只选择我们自己包出来的 `.qd-*` 容器**。

为什么这么死板：Streamlit 的内部 class 名（`.st-emotion-cache-xxxx`）每版都变，
靠它做样式的面板升级必挂——本项目刚被两个过期 API 咬过一次，不在同一个坑里再摔。
纪律由 tests/test_theme.py 钉住：CSS 里出现 Streamlit 内部标识、出现色板之外的
裸十六进制、字体栈少了离线兜底，测试都会红。

色值只在这里定义一次（方向色复用 quant.report.fmt，因为表格列配置也要用）。
"""
from __future__ import annotations

import html as _html

import streamlit as st
from quant.report import fmt

# ---------------------------------------------------------------- 色板
# 前四个必须与 .streamlit/config.toml 一致（测试会比对）
PRIMARY = "#C8963E"      # 琥珀金：可操作元素与选中态
BACKGROUND = "#12141A"   # 近黑石板
SURFACE = "#1A1D26"      # 卡片/侧栏
TEXT = "#E8E4DC"         # 暖白

MUTED = fmt.NEUTRAL      # 次级灰：标签、说明、无方向的数字（与"平盘不上色"同一个灰）
HAIRLINE = "#2A2E38"     # 发丝线：§2.3 第 2 条——用 1px 低对比分隔线，不用四面描边
UP = fmt.UP              # 红涨（A 股惯例，与 K 线图一致）
DOWN = fmt.DOWN          # 绿跌
# pill 的底色：不用透明度（8 位十六进制读起来像魔法数），直接给四个压暗到能当底的值
UP_DIM = "#3A1F20"
DOWN_DIM = "#16302A"
PRIMARY_DIM = "#3A2E19"
MUTED_DIM = "#23262E"

PALETTE = {
    "PRIMARY": PRIMARY, "BACKGROUND": BACKGROUND, "SURFACE": SURFACE, "TEXT": TEXT,
    "MUTED": MUTED, "HAIRLINE": HAIRLINE, "UP": UP, "DOWN": DOWN,
    "UP_DIM": UP_DIM, "DOWN_DIM": DOWN_DIM, "PRIMARY_DIM": PRIMARY_DIM,
    "MUTED_DIM": MUTED_DIM,
}

# ---------------------------------------------------------------- 字体
# fallback 链是硬要求：Google Fonts 离线/被墙时拿不到，没有兜底中文直接变豆腐块。
# macOS 自带 Songti SC / PingFang SC / Menlo，最后再落到通用族。
FONT_IMPORT = ("https://fonts.googleapis.com/css2"
               "?family=Noto+Serif+SC:wght@500;700"
               "&family=Noto+Sans+SC:wght@400;500"
               "&family=IBM+Plex+Mono:wght@400;500"
               "&display=swap")

SERIF = '"Noto Serif SC", "Songti SC", "STSong", serif'          # 标题：编辑感、权威感
SANS = '"Noto Sans SC", "PingFang SC", "Hiragino Sans GB", sans-serif'   # 正文
MONO = '"IBM Plex Mono", Menlo, "SF Mono", monospace'            # 数字/代码/日志

# ---------------------------------------------------------------- CSS
# 每条规则的选择器单独一行（测试按行解析选择器做纪律检查）。
# 不写 @keyframes / animation / transition：§7 明确不加装饰性动画。
CSS = f"""@import url("{FONT_IMPORT}");
:root {{
  --qd-primary: {PRIMARY};
  --qd-text: {TEXT};
  --qd-muted: {MUTED};
  --qd-hairline: {HAIRLINE};
  --qd-up: {UP};
  --qd-down: {DOWN};
  --qd-serif: {SERIF};
  --qd-sans: {SANS};
  --qd-mono: {MONO};
}}
.qd-head {{
  display: flex;
  align-items: baseline;
  gap: 0.75rem;
  border-bottom: 1px solid var(--qd-hairline);
  padding-bottom: 0.35rem;
  margin-bottom: 0.9rem;
}}
.qd-title {{
  font-family: var(--qd-serif);
  font-size: 1.45rem;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: var(--qd-text);
  line-height: 1.2;
}}
.qd-sub {{
  font-family: var(--qd-sans);
  font-size: 0.8rem;
  color: var(--qd-muted);
}}
.qd-head-right {{
  margin-left: auto;
}}
.qd-section {{
  font-family: var(--qd-serif);
  font-size: 1.05rem;
  font-weight: 500;
  letter-spacing: 0.03em;
  color: var(--qd-text);
  border-bottom: 1px solid var(--qd-hairline);
  padding-bottom: 0.25rem;
  margin: 1.1rem 0 0.5rem;
}}
.qd-section-pill {{
  margin-left: 0.5rem;
}}
.qd-metric {{
  border-left: 2px solid var(--qd-hairline);
  padding: 0.1rem 0 0.1rem 0.6rem;
}}
.qd-metric-label {{
  font-family: var(--qd-sans);
  font-size: 0.72rem;
  letter-spacing: 0.06em;
  color: var(--qd-muted);
  white-space: nowrap;
}}
.qd-metric-value {{
  font-family: var(--qd-mono);
  font-variant-numeric: tabular-nums;
  font-size: 1.6rem;
  font-weight: 500;
  line-height: 1.25;
  color: var(--qd-text);
}}
.qd-num {{
  font-family: var(--qd-mono);
  font-variant-numeric: tabular-nums;
  text-align: right;
}}
.qd-up {{
  color: var(--qd-up);
}}
.qd-down {{
  color: var(--qd-down);
}}
.qd-pill {{
  display: inline-block;
  font-family: var(--qd-sans);
  font-size: 0.72rem;
  letter-spacing: 0.06em;
  padding: 0.08rem 0.5rem;
  border-radius: 2px;
  white-space: nowrap;
  vertical-align: middle;
}}
.qd-pill-running {{
  color: {PRIMARY};
  background: {PRIMARY_DIM};
}}
.qd-pill-success {{
  color: {DOWN};
  background: {DOWN_DIM};
}}
.qd-pill-failed {{
  color: {UP};
  background: {UP_DIM};
}}
.qd-pill-idle {{
  color: {MUTED};
  background: {MUTED_DIM};
}}
.qd-help {{
  font-family: var(--qd-sans);
  font-size: 0.76rem;
  color: var(--qd-muted);
}}
.qd-flow {{
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.4rem;
  margin: 0.2rem 0 0.9rem;
}}
.qd-flow-step {{
  font-family: var(--qd-sans);
  font-size: 0.78rem;
  color: var(--qd-text);
  background: {MUTED_DIM};
  border-left: 2px solid var(--qd-primary);
  padding: 0.2rem 0.55rem;
}}
.qd-flow-arrow {{
  font-family: var(--qd-mono);
  color: var(--qd-primary);
}}
"""


# ---------------------------------------------------------------- HTML 组件
# 面板里只剩 st.html(theme.xxx(...))，这些构造函数是纯字符串函数（可单测）。
#
# 一律转义：pill 文案里带 state.status、小结里带目录名，都来自磁盘上手工改得动的
# 文件。st.html **不套 iframe**，一个未转义的 '<' 就能把版面撕开。
# 与 quant.runner.view.PILL_* 同一套词（两处各由测试钉住字面量，谁改了都会红）
_PILL_KINDS = ("running", "success", "failed", "idle")
# 允许内联的色值只有色板里那些：否则调用方能把任意 CSS 塞进 style 属性。
_INLINE_COLORS = frozenset(PALETTE.values())


def _esc(text) -> str:
    return _html.escape(str(text), quote=True)


def pill(text: str, kind: str) -> str:
    """状态 pill。kind 取 quant.runner.view.PILL_*（running/success/failed/idle）；
    拼错或将来多出一种状态时退回 idle——绝不渲染出没有底色的裸文字。"""
    css = kind if kind in _PILL_KINDS else "idle"
    return f'<span class="qd-pill qd-pill-{css}">{_esc(text)}</span>'


def page_head(title: str, subtitle: str, pill_html: str = "") -> str:
    """通用页头（§2.4）：页名衬线大字 + 一句话说明灰色小字 + 右侧任务状态 pill。
    `pill_html` 是本模块 pill() 的输出（已转义的可信 HTML），空则不出 pill。"""
    right = f'<span class="qd-head-right">{pill_html}</span>' if pill_html else ""
    return (f'<div class="qd-head"><span class="qd-title">{_esc(title)}</span>'
            f'<span class="qd-sub">{_esc(subtitle)}</span>{right}</div>')


def section(title: str, pill_html: str = "") -> str:
    """区块小标题：衬线 + 发丝线（§2.3 第 2 条，不用四面描边的卡片）。"""
    right = f'<span class="qd-section-pill">{pill_html}</span>' if pill_html else ""
    return f'<div class="qd-section">{_esc(title)}{right}</div>'


def metric(label: str, value: str, color: str | None = None) -> str:
    """指标卡：标签小号灰字 + 数值等宽放大（§2.3 第 1 条）。

    `color` 只接受色板里的值（由 fmt.metric_color / fmt.direction_color 给出）：
    红绿是按值算的，写不进静态 CSS，只能内联；但内联 style 是个注入口子，
    所以在这里白名单挡一道。None = 不上色，且**不留空 style**。
    """
    style = ""
    if color is not None:
        if color not in _INLINE_COLORS:
            raise ValueError(f"内联色值必须来自色板，收到 {color!r}")
        style = f' style="color: {color}"'
    return (f'<div class="qd-metric"><div class="qd-metric-label">{_esc(label)}</div>'
            f'<div class="qd-metric-value"{style}>{_esc(value)}</div></div>')


def flow(steps) -> str:
    """闭环图（设计 §3.1 第 1 节）：一行步骤 + 琥珀箭头，讲"扫描发现 → 加入
    universe → 每日信号跟踪卖出"这个闭环。

    用 flex 而不是画 SVG：步数会变（文案改动比图形改动频繁得多），
    而且 flex-wrap 让它在窄屏上自己折行，SVG 做不到。
    空列表返回空串——`st.html("")` 也会占一个元素位，不能留个空盒子把版面顶开。
    """
    parts = [f'<span class="qd-flow-step">{_esc(s)}</span>' for s in steps]
    if not parts:
        return ""
    arrow = '<span class="qd-flow-arrow">→</span>'
    return f'<div class="qd-flow">{arrow.join(parts)}</div>'


def inject() -> None:
    """把字体与语义化 CSS 注入当前页。**每次 rerun 都要调**：Streamlit 每轮重画整棵
    元素树，上一轮的 <style> 不会留下来。

    用 `st.html` 而不是 `st.markdown(..., unsafe_allow_html=True)`：st.html 就是为
    插样式/静态 HTML 加的，也不必为了一段固定 CSS 给整页开 HTML 逃逸口子。
    """
    st.html(f"<style>{CSS}</style>")
