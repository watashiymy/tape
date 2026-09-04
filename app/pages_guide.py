"""说明手册（v0.5.0 拆成四页；原为单页「使用说明」，设计 §3.1 / v0.5.0 §5）。

拆分的由来：十节 10931 字堆在一页，实测渲染出 9598 字——是第二重那一页
（任务控制台 4059）的 2.4 倍，全站三分之二的文字压在同一屏。

拆的是**呈现**不是内容：分页表 `guide.GUIDE_PAGES` 只是 `guide.SECTIONS` 之上的
一层索引，正文一节没改、一节没重排，并集在 import 期就查过不重不漏。

**刻意不放控制条**：这几页没有任何产物可看，也不该让人在读说明时误点一次
十几分钟的全量扫描。
"""
from __future__ import annotations

import streamlit as st

import guide
import theme
import ui


def _render(page_key: str) -> None:
    """渲染手册的一页：页头 + 属于这一页的那几节。

    循环体与拆分前逐行相同——安全提示仍单独走 st.warning（§3.1 第 7 条），
    闭环图仍跟在有 flow 的那一节标题下面。
    """
    page = guide.guide_page(page_key)
    ui.page_head(page.title)
    for sec in guide.page_sections(page_key):
        st.html(theme.section(sec.title))
        if sec.flow:
            st.html(theme.flow(sec.flow))          # 闭环图：扫描 → 信号池 → 信号跟踪
        if sec.emphasis:
            st.warning(sec.body)                   # 安全提示单独成块
        else:
            st.markdown(sec.body)


def _contents() -> None:
    """落地页页尾的目录：手册还有哪几页、什么时候需要点进去。

    **只在落地页出现**。子页顶部不放面包屑、页尾不放「下一页」：四页全在侧栏里
    看得见，再加一层页内导航就是纯噪声。

    用 `st.page_link` 而不是 `st.button`：说明页不许出现按钮
    （tests/test_dashboard_guide.py 钉着——一个"读文档"的页面上出现按钮，
    在这个面板里意味着"会起进程"）。page_link 只是导航，不触发任何任务。
    """
    st.html(theme.section("手册还有这几页"))
    st.caption("日常操作只需要上面那些。下面几页是「读一次就放着」的参考。")
    for page in guide.GUIDE_PAGES[1:]:
        st.page_link(ui.page_ref(page.key), label=page.title, icon=page.icon,
                     help=page.lead)


def page_guide() -> None:
    """落地页（侧栏第一位、default=True，位置与 v0.2.1 起完全一致）。"""
    _render("guide")
    _contents()


def page_guide_metrics() -> None:
    _render("guide_metrics")


def page_guide_custom() -> None:
    _render("guide_custom")


def page_guide_limits() -> None:
    _render("guide_limits")


#: {页 key: 渲染函数}。dashboard.py 照着 GUIDE_PAGES 的顺序建 st.Page，
#: 漏一个就 KeyError——比"侧栏里静静少一页"强得多。
RENDERERS = {
    "guide": page_guide,
    "guide_metrics": page_guide_metrics,
    "guide_custom": page_guide_custom,
    "guide_limits": page_guide_limits,
}
