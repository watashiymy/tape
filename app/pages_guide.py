"""说明手册（v0.5.0 拆成多页；原为单页「使用说明」，设计 §3.1 / v0.5.0 §5）。

拆分的由来：十节 10931 字堆在一页，实测渲染出 9598 字——是第二重那一页
（任务控制台 4059）的 2.4 倍，全站三分之二的文字压在同一屏。
2026-09-05 起是三页：「自定义策略」是写代码的说明，搬去了 docs/custom-strategy.md。

拆的是**呈现**不是内容：分页表 `guide.GUIDE_PAGES` 只是 `guide.SECTIONS` 之上的
一层索引，并集在 import 期就查过不重不漏。

**刻意不放任务条**：这几页没有任何产物可看，也不该让人在读说明时误点一次
十几分钟的全量扫描。

2026-09-30 起三页不在侧边栏里（手册不是系统的功能）：各页页头右端的「帮助中心」进第一页，
三页之间靠页头下方的导航行互相跳转，原来第一页页尾的目录随之删掉（同一件事说两遍）。
"""
from __future__ import annotations

import streamlit as st

import guide
import theme
import ui


def _render(page_key: str) -> None:
    """渲染手册的一页：页头 + 手册导航行 + 属于这一页的那几节。

    手册三页不在侧边栏里（2026-09-30 用户定：手册不是系统的功能），所以页头下方放一行导航：
    三页各一个 page_link，当前页灰掉。其余各页从页头右端的「帮助中心」进来；手册页自己不放
    「帮助中心」（help_center=False）——导航行的第一项就是那一页。
    用 page_link 不用 st.button：说明页不许出现按钮（在这个面板里按钮意味着"会起进程"）。

    循环体与拆分前逐行相同——安全提示仍单独走 st.warning（§3.1 第 7 条），
    闭环图仍跟在有 flow 的那一节标题下面。
    """
    page = guide.guide_page(page_key)
    ui.page_head(page.title, help_center=False)
    with st.container(horizontal=True, gap="small"):
        for p in guide.GUIDE_PAGES:
            st.page_link(ui.page_ref(p.key), label=p.title, icon=p.icon, help=p.lead,
                         disabled=(p.key == page_key))
    for sec in guide.page_sections(page_key):
        st.html(theme.section(sec.title))
        if sec.flow:
            st.html(theme.flow(sec.flow))          # 闭环图：扫描 → 信号池 → 信号跟踪
        if sec.emphasis:
            st.warning(sec.body)                   # 安全提示单独成块
        else:
            st.markdown(sec.body)


def page_guide() -> None:
    """手册第一页，也是各页页头「帮助中心」的落点。"""
    _render("guide")


def page_guide_metrics() -> None:
    _render("guide_metrics")


def page_guide_limits() -> None:
    _render("guide_limits")


#: {页 key: 渲染函数}。dashboard.py 照着 GUIDE_PAGES 的顺序建 st.Page，
#: 漏一个就 KeyError——比"手册里静静少一页"强得多。
RENDERERS = {
    "guide": page_guide,
    "guide_metrics": page_guide_metrics,
    "guide_limits": page_guide_limits,
}
