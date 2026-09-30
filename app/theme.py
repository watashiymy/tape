"""面板视觉基座（v0.2.1 设计 §2）：色板常量、字体栈、语义化 CSS。

**分工**：基础配色（底色/主色/文字色）全在 `.streamlit/config.toml`——那是官方支持面；
本文件的 CSS 只做 config.toml 表达不了的**排版层级**（衬线标题、等宽放大的数值、
发丝线、状态 pill），而且**只选择我们自己包出来的 `.qd-*` 容器**。

为什么这么死板：Streamlit 的内部 class 名（`.st-emotion-cache-xxxx`）每版都变，
靠它做样式的面板升级必挂——本项目刚被两个过期 API 咬过一次，不在同一个坑里再摔。
纪律由 tests/test_theme.py 钉住：CSS 里出现 Streamlit 内部标识、出现色板之外的
裸十六进制、字体栈少了离线兜底，测试都会红。

本文件另外管品牌块 TAPE（v0.2.2 §2.1，画成 SVG 交给 st.logo）。

**关于 Cmd+C 弹 "Clear cache?"**：v0.2.2 曾在这里注入一段 JS 包 hotkeys.filter，
v0.5.0 删掉了——它从头到尾是空操作。1.61 的前端把 `c` 键交给 App.handleKeyDown
自己 switch，根本不经过 hotkeys-js（window.hotkeys 存在但 _handlers 为空）；
那个 case 被 sT(isOwner, toolbarMode) 守着。真修法是 .streamlit/config.toml 的
`client.toolbarMode = "viewer"`（tests/test_theme.py 钉着），顺带去掉了全项目唯一
一处 `unsafe_allow_javascript=True`。

**色值一个都不在本文件定义**：唯一定义处是 `quant.report.palette`，这里只做转发。
原因是图表（quant.report.charts）也要用同一套配色，而它属于 src/、不能反向
import app/——色板必须下沉到 src/ 才能"改一处、两边同步"。曾经两边各写一套：
面板改暗色后图表还是 plotly 默认浅色模板，内嵌进去中间开一块白。
"""
from __future__ import annotations

import base64
import html as _html
import re
import unicodedata

import streamlit as st
from quant.report import fmt, palette

# ---------------------------------------------------------------- 色板（转发）
# 名字保留在这里是为了让 CSS 那段 f-string 读得顺；值全部来自 palette。
# 前四个必须与 .streamlit/config.toml 一致（测试会比对）。
PRIMARY = palette.PRIMARY        # 琥珀金：可操作元素与选中态
BACKGROUND = palette.BACKGROUND  # 近黑石板
SURFACE = palette.SURFACE        # 卡片/侧栏
TEXT = palette.TEXT              # 暖白

MUTED = fmt.NEUTRAL      # 次级灰：标签、说明、无方向的数字（与"平盘不上色"同一个灰）
HAIRLINE = palette.HAIRLINE   # 发丝线：§2.3 第 2 条——1px 低对比分隔线，不用四面描边
UP = fmt.UP              # 红涨（A 股惯例，与 K 线图一致）
DOWN = fmt.DOWN          # 绿跌
# pill 的底色：压暗到能当底的四个值（不用透明度——8 位十六进制读起来像魔法数）
UP_DIM = palette.UP_DIM
DOWN_DIM = palette.DOWN_DIM
PRIMARY_DIM = palette.PRIMARY_DIM
MUTED_DIM = palette.MUTED_DIM

PALETTE = {
    "PRIMARY": PRIMARY, "BACKGROUND": BACKGROUND, "SURFACE": SURFACE, "TEXT": TEXT,
    "MUTED": MUTED, "HAIRLINE": HAIRLINE, "UP": UP, "DOWN": DOWN,
    "UP_DIM": UP_DIM, "DOWN_DIM": DOWN_DIM, "PRIMARY_DIM": PRIMARY_DIM,
    "MUTED_DIM": MUTED_DIM,
}

# ---------------------------------------------------------------- 字体
# 策略：**中文用系统字体，只有拉丁等宽走网络**。两条理由都是浏览器实测得出的：
#
#   ① 动态注入的 @import 加载不了 CJK 网络字体。Noto Serif/Sans SC 按 ~200 个
#      unicode-range 分片，实测 `document.fonts.check('700 16px "Noto Serif SC"')`
#      是 false、拉丁串宽度与"不存在的字体"逐像素相同（字形根本没被用上）、
#      初始渲染时 202 个分片状态全是 unloaded。标题一直是 macOS 自带 Songti SC
#      在撑——观感"看着对"是兜底救的场，不是策略成功。
#   ② 本项目用户在中国大陆，Google Fonts 常不可达，把可用性押在 CDN 上不合理。
#
# 系统衬线中文（Songti SC）本就是想要的观感，那就把它写在首位：诚实、更快，
# 还省掉 200+ 条永远用不上的 @font-face 声明。
#
# 等宽是唯一的例外：IBM Plex Mono 是纯拉丁小字体，实测宽度 806.4（既非 Menlo 的
# 809.2、也非不存在字体的 733.4）——确实生效，所以保留网络加载。兜底照旧不许省：
# 拿不到时数字会掉到比例字体，一列数就对不齐了。
FONT_IMPORT = ("https://fonts.googleapis.com/css2"
               "?family=IBM+Plex+Mono:wght@400;500"
               "&display=swap")

SERIF = '"Songti SC", STSong, "Noto Serif SC", serif'            # 标题：编辑感、权威感
SANS = '"PingFang SC", "Noto Sans SC", "Helvetica Neue", sans-serif'     # 正文
MONO = palette.MONO                                              # 数字/代码/日志

# ---------------------------------------------------------------- 品牌（v0.2.2 §2.1）
# TAPE 取自 "reading the tape"（看盘）——交易所报价纸带上滚动的价与量，
# 正是本系统唯一的输入。短、英文、衬线大写好看，且与"金融电报"的视觉调性同源。
BRAND = "TAPE"
BRAND_SUB = "A股日线信号"
PAGE_TITLE = f"{BRAND} · {BRAND_SUB}"      # 浏览器标签页；与侧栏品牌同一个来源
BRAND_LOGO_SIZE = "large"                  # st.logo 的三档里最大的那档（实测高 32px）

# 品牌块必须走 `st.logo`：那是侧栏里**导航之上**唯一的官方位置。普通 st.sidebar.*
# 元素一律排在 st.navigation 渲染的链接**下面**（实测），品牌落到链接列表中间就不
# 成体统了。st.logo 只收图片，所以把这行字画成 SVG 并内联成 data URI——不读文件、
# 不请求网络，色值与字体栈仍取自本模块的单一来源。
#
# 属性值一律用单引号：字体栈本身带双引号（"Songti SC"），双引号属性会当场把
# SVG 撕成非法 XML。SVG 里只有拉丁的 TAPE 走衬线网络字体拿不到也无妨——
# 首选 Songti SC 是 macOS 自带的（与 CSS 那套字体策略同一条理由）。
# 尺寸按实测调过：st.logo 把图等比缩到高 32px，viewBox 高 36 时副标约 11.6px 可读；
# 图再高一点副标就会被压到 9px 以下，糊成一条灰线。
BRAND_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="180" height="36" '
    'viewBox="0 0 180 36">'
    f"<text x='1' y='20' font-family='{SERIF}' font-size='24' font-weight='700' "
    f"letter-spacing='6' fill='{TEXT}'>{BRAND}</text>"
    f"<text x='2' y='34' font-family='{SANS}' font-size='13' "
    f"letter-spacing='1' fill='{MUTED}'>{BRAND_SUB}</text>"
    "</svg>"
)
BRAND_LOGO = ("data:image/svg+xml;base64,"
              + base64.b64encode(BRAND_SVG.encode("utf-8")).decode("ascii"))

# ---------------------------------------------------------------- CSS
# 每条规则的选择器单独一行（测试按行解析选择器做纪律检查）。
# 不写 @keyframes / animation / transition：§7 明确不加装饰性动画。
CSS = f"""@import url("{FONT_IMPORT}");
:root {{
  --qd-primary: {PRIMARY};
  --qd-text: {TEXT};
  --qd-muted: {MUTED};
  --qd-hairline: {HAIRLINE};
  --qd-surface: {SURFACE};
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

/* 流水线卡片之间那节箭头轨（v0.5.0：控制台「每日流水线」区）。
   它落在一个很窄的列里（权重 0.14），要在卡片标题那一行的高度上把箭头居中。
   窄屏时 streamlit 会把列竖着堆起来，那时箭头朝下才读得通——用 ::after 换字形，
   而不是另画一个元素（两个元素总有一个要被藏起来，藏错了就是一片空白）。 */
.qd-rail {{
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 2.1rem;
  font-family: var(--qd-mono);
  font-size: 1.15rem;
  color: var(--qd-primary);
  opacity: 0.75;
}}
.qd-rail::after {{ content: "→"; }}
@media (max-width: 640px) {{
  .qd-rail {{ min-height: 1.2rem; }}
  .qd-rail::after {{ content: "↓"; }}
}}

/* 「人工步骤」标记：这一步没有开始按钮，是你自己动手的一环。
   刻意用灰而不是琥珀——琥珀在本主题里只给**可操作**元素（config.toml 的
   primaryColor 就是这个约定），给一个没有按钮的卡片上琥珀等于在骗手。 */
.qd-manual-row {{
  display: flex;
  align-items: center;
  gap: 0.6rem;
  min-height: 2.1rem;
}}
.qd-manual-row .qd-manual {{ flex: none; white-space: nowrap; }}
.qd-manual-text {{
  font-family: var(--qd-sans);
  font-size: 0.85rem;
  color: var(--qd-muted);
}}
.qd-manual {{
  font-family: var(--qd-sans);
  font-size: 0.72rem;
  letter-spacing: 0.04em;
  color: var(--qd-muted);
  border: 1px solid {MUTED_DIM};
  padding: 0.1rem 0.45rem;
}}

/* 首页卡片（市场概览）：一张卡 = 小标签 + 大数值 + 一行副文 + 一幅小图 + 一行注脚。
   边框沿用 .qd-metric 的左侧发丝线，不四面描边。小图是 img（data URI），不是内联 svg。 */
.qd-card {{
  border-left: 2px solid var(--qd-hairline);
  padding: 0.15rem 0 0.15rem 0.6rem;
  margin-bottom: 0.4rem;
}}
.qd-card-label {{
  font-family: var(--qd-sans);
  font-size: 0.72rem;
  letter-spacing: 0.06em;
  color: var(--qd-muted);
  white-space: nowrap;
}}
.qd-card-value {{
  font-family: var(--qd-mono);
  font-variant-numeric: tabular-nums;
  font-size: 1.35rem;
  font-weight: 500;
  line-height: 1.2;
  color: var(--qd-text);
}}
.qd-card-sub {{
  font-family: var(--qd-mono);
  font-variant-numeric: tabular-nums;
  font-size: 0.8rem;
}}
.qd-card-img {{
  display: block;
  width: 100%;
  height: 32px;
  margin: 0.25rem 0 0.1rem;
}}
.qd-card-note {{
  font-family: var(--qd-sans);
  font-size: 0.7rem;
  color: var(--qd-muted);
}}
.qd-status {{
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin: 0.2rem 0 0.6rem;
}}

/* 就地帮助圆点（2026-09-29）：紧跟在卡片标签或卡片标题后面的小圆圈「?」，悬停或 Tab 键选中
   出提示框。取代带下拉箭头的 st.popover（用户看了以为是选择框）。圆点与提示框都自己定死字体：
   它们挂在 .qd-card-label（0.06em 字距 + nowrap）与 .qd-section（衬线）里，不定死两处长得不一样。
   只用 :focus-visible 不用 :focus——鼠标点一下也会触发 :focus，提示框就钉住不走了。
   提示框里的 p / ul 要写 font-size: inherit：Streamlit 全局样式给 p、ol、ul、dl 一律定了 1rem，
   直接落在元素上、压过从 .qd-help-tip 继承来的 0.8rem（实测不写时段落与列表都是 16px）。 */
.qd-help-dot {{
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  width: 0.95rem;
  height: 0.95rem;
  margin-left: 0.3rem;
  border: 1px solid var(--qd-muted);
  border-radius: 50%;
  font-family: var(--qd-sans);
  font-size: 0.62rem;
  font-weight: 400;
  letter-spacing: normal;
  line-height: 1;
  color: var(--qd-muted);
  vertical-align: middle;
  cursor: help;
}}
.qd-help-dot:hover, .qd-help-dot:focus-visible {{
  color: var(--qd-text);
  border-color: var(--qd-text);
  outline: none;
}}
.qd-help-tip {{
  display: none;
  position: absolute;
  top: 1.35rem;
  left: -0.4rem;
  z-index: 20;
  box-sizing: border-box;
  width: 15rem;
  padding: 0.5rem 0.65rem;
  border: 1px solid var(--qd-hairline);
  border-radius: 6px;
  background: var(--qd-surface);
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.45);
  color: var(--qd-text);
  font-family: var(--qd-sans);
  font-size: 0.8rem;
  font-weight: 400;
  letter-spacing: normal;
  line-height: 1.5;
  text-align: left;
  text-transform: none;
  white-space: normal;
  cursor: auto;
}}
.qd-help-wide .qd-help-tip {{
  width: 22rem;
}}
.qd-help-end .qd-help-tip {{
  left: auto;
  right: -0.4rem;
}}
.qd-help-dot:hover .qd-help-tip, .qd-help-dot:focus-visible .qd-help-tip {{
  display: block;
}}
.qd-help-dot .qd-help-tip p {{
  margin: 0 0 0.4rem;
  font-size: inherit;
}}
.qd-help-dot .qd-help-tip ul {{
  margin: 0 0 0.4rem;
  padding-left: 1.1rem;
  font-size: inherit;
}}
.qd-help-dot .qd-help-tip li {{
  margin: 0 0 0.2rem;
}}
.qd-help-dot .qd-help-tip > :last-child {{
  margin-bottom: 0;
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


def pill(text: str, kind: str, tooltip: str = "") -> str:
    """状态 pill。kind 取 quant.runner.view.PILL_*（running/success/failed/idle）；
    拼错或将来多出一种状态时退回 idle——绝不渲染出没有底色的裸文字。

    `tooltip` 走原生 title 属性（v0.2.4 扫描范围徽标要用）：pill 上只放得下四五个字，
    "范围未知"这种说法必须能就地解释清楚为什么未知，否则用户只会当成又一个 bug。
    与文案同样转义——它同样来自磁盘上的文件内容。
    """
    css = kind if kind in _PILL_KINDS else "idle"
    title = f' title="{_esc(tooltip)}"' if tooltip else ""
    return f'<span class="qd-pill qd-pill-{css}"{title}>{_esc(text)}</span>'


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
    universe → 信号跟踪跟踪卖出"这个闭环。

    用 flex 而不是画 SVG：步数会变（文案改动比图形改动频繁得多），
    而且 flex-wrap 让它在窄屏上自己折行，SVG 做不到。
    空列表返回空串——`st.html("")` 也会占一个元素位，不能留个空盒子把版面顶开。
    """
    parts = [f'<span class="qd-flow-step">{_esc(s)}</span>' for s in steps]
    if not parts:
        return ""
    arrow = '<span class="qd-flow-arrow">→</span>'
    return f'<div class="qd-flow">{arrow.join(parts)}</div>'


def rail() -> str:
    """流水线卡片之间那节箭头轨（v0.5.0）。

    箭头字形在 CSS 的 ::after 里（窄屏自动从 → 换成 ↓），不写死在 HTML 里：
    streamlit 窄屏会把列竖着堆起来，那时横箭头是错的。
    """
    return '<div class="qd-rail"></div>'


def manual_row(tag: str, text: str) -> str:
    """一行式的人工步骤（控制台「每日流水线」区，2026-09-14）：灰标签 + 一句说明。
    取代 v0.5.0 的整张人工卡片——那张卡在页面正中间却什么都不能操作。"""
    return (f'<div class="qd-manual-row">{manual_tag(tag)}'
            f'<span class="qd-manual-text">{_esc(text)}</span></div>')


def manual_tag(text: str) -> str:
    """「人工步骤」标记：这一步没有开始按钮。灰色，不用琥珀——
    琥珀在本主题里只给可操作元素，给没有按钮的卡片上琥珀等于骗手。"""
    return f'<span class="qd-manual">{_esc(text)}</span>'


# ---------------------------------------------------------------- 就地帮助圆点（2026-09-29）
_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _tip_inline(text: str) -> str:
    return _BOLD.sub(r"<b>\1</b>", text)


def _is_wide(ch: str) -> bool:
    """汉字与中文标点（Unicode 东亚宽度 W / F）。空串不算。"""
    return bool(ch) and unicodedata.east_asian_width(ch) in ("W", "F")


def _tip_lines(lines: list[str]) -> str:
    """一段（或一个列表项）的几行 → 一串 HTML。

    加粗**逐行**认（spec §1.2「同一行内」：这一行落单的 `**` 不会和下一行的配成对）。
    软换行按空格接；但换行两边都是全角字时直接相接——Chrome 把软换行画成空格，
    中文里冒出空格很扎眼（「标的， 与前一交易日」）。看的是 `**` 里面的字，不是星号。"""
    out = _tip_inline(lines[0])
    for prev, nxt in zip(lines, lines[1:]):
        tight = _is_wide(prev.rstrip("*")[-1:]) and _is_wide(nxt.lstrip("*")[:1])
        out += ("" if tight else " ") + _tip_inline(nxt)
    return out


def _tip_markup(text: str) -> str:
    """帮助文字 → 提示框 HTML。**先整体转义**，再只认三种写法（spec §1.2）：
    `**加粗**`；空行分段；行首 `- ` 的列表项（紧随其后、以空格缩进的行是它的续行）。
    段内单个换行的接法见 `_tip_lines`。不做通用 Markdown：现有帮助文案只用了这三种，要新写法时再扩。"""
    blocks: list[str] = []
    para: list[str] = []
    items: list[list[str]] = []

    def flush_para() -> None:
        if para:
            blocks.append(f"<p>{_tip_lines(para)}</p>")
            para.clear()

    def flush_list() -> None:
        if items:
            lis = "".join(f"<li>{_tip_lines(i)}</li>" for i in items)
            blocks.append(f"<ul>{lis}</ul>")
            items.clear()

    for raw in _esc(text).split("\n"):
        line = raw.strip()
        if not line:
            flush_para()
            flush_list()
        elif raw.startswith("- "):
            flush_para()
            items.append([line[2:].strip()])
        elif raw[:1].isspace() and items:
            items[-1].append(line)
        else:
            flush_list()
            para.append(line)
    flush_para()
    flush_list()
    return "".join(blocks)


def help_dot(text: str, *, align: str = "start", wide: bool = False) -> str:
    """紧跟在标签或标题后面的小圆圈「?」，悬停或 Tab 键选中出提示框（spec §1）。

    `align="end"`：提示框从圆点右侧向左展开——挂点在页面右半边时用，免得越出视口右缘。
    `wide=True`：宽档，给任务说明这种多段带列表的长文字。
    文字由调用方从 guide 取来传入：本模块**不 import guide**（测试按文件路径单独加载本模块，
    那时 app/ 不在 sys.path 上）。圆点里的「?」是符号，不是文案。
    文字为空（或只有空白）时返回空串：不出一个悬停只见空框的圆点。
    """
    if not text.strip():
        return ""
    cls = "qd-help-dot"
    if align == "end":
        cls += " qd-help-end"
    if wide:
        cls += " qd-help-wide"
    return (f'<span class="{cls}" tabindex="0">?'
            f'<span class="qd-help-tip">{_tip_markup(text)}</span></span>')


def _img(uri: str) -> str:
    if not uri.startswith("data:image/svg+xml;base64,"):
        raise ValueError("小图只接受 SVG 的 data URI（st.html 会删内联 svg，外链更不许）")
    return f'<img class="qd-card-img" src="{uri}" alt="">'


def stat_card(label: str, value: str, *, sub: str = "", color: str | None = None,
              img: str | None = None, note: str = "") -> str:
    """首页卡片。`color` 上在副文那一行（涨跌）；没有副文时上在数值上。白名单同 metric()。
    label 与 value 都为空 = 纯图卡（比例条、柱状图），不输出那两行。"""
    style = ""
    if color is not None:
        if color not in _INLINE_COLORS:
            raise ValueError(f"内联色值必须来自色板，收到 {color!r}")
        style = f' style="color: {color}"'
    parts = ['<div class="qd-card">']
    if label or value:
        parts.append(f'<div class="qd-card-label">{_esc(label)}</div>')
        parts.append(f'<div class="qd-card-value"{"" if sub else style}>{_esc(value)}</div>')
    if sub:
        parts.append(f'<div class="qd-card-sub"{style}>{_esc(sub)}</div>')
    if img:
        parts.append(_img(img))
    if note:
        parts.append(f'<div class="qd-card-note">{_esc(note)}</div>')
    parts.append("</div>")
    return "".join(parts)


def status_line(text: str, *, running: bool, extra: str = "") -> str:
    """首页状态条：只用 running（琥珀）/ idle（灰）两种 pill——满屏红涨绿跌旁边不许出现
    红色的「延迟」或绿色的「成功」。`extra` 是叠加标记（如「数据延迟」），灰 pill 追加。"""
    kind = "running" if running else "idle"
    tail = pill(extra, "idle") if extra else ""
    return f'<div class="qd-status">{pill(text, kind)}{tail}</div>'


def inject() -> None:
    """把字体与语义化 CSS 注入当前页。**每次 rerun 都要调**：
    Streamlit 每轮重画整棵元素树，上一轮的 <style> 不会留下来。

    用 `st.html` 而不是 `st.markdown(..., unsafe_allow_html=True)`：st.html 就是为
    插样式/静态 HTML 加的，也不必为了一段固定 CSS 给整页开 HTML 逃逸口子。
    纯 `<style>` 会被 Streamlit 送进 event 容器，**不占版面**。

    这里**不再**注入任何 `<script>`（v0.5.0）：唯一那段热键 JS 已证实是空操作，
    见模块 docstring。面板从此没有 `unsafe_allow_javascript=True`。
    """
    st.html(f"<style>{CSS}</style>")
