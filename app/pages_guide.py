"""「使用说明」页（设计 §3.1）：侧栏第一位、默认落地页，纯文档。"""
from __future__ import annotations

import streamlit as st

import guide
import theme
import ui


def page_guide() -> None:
    """八节正文与那张闭环图全在 app/guide.py（可单测的纯字符串），本函数只做组装。

    **刻意不放控制条**：这页没有任何产物可看，也不该让人在读说明时误点一次
    十几分钟的全量扫描。
    """
    ui.page_head("使用说明")
    for sec in guide.SECTIONS:
        st.html(theme.section(sec.title))
        if sec.flow:
            st.html(theme.flow(sec.flow))          # 闭环图：扫描 → 信号池 → 每日信号
        if sec.emphasis:
            st.warning(sec.body)                   # 安全提示单独成块（§3.1 第 7 条）
        else:
            st.markdown(sec.body)
