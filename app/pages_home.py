"""「市场概览」落地页（spec）。本任务只有骨架，Task 21-23 逐块填。"""
from __future__ import annotations

import streamlit as st

import guide
import ui
from quant.runner import process


def _first_run_hint() -> None:
    """从没跑过任何任务时给一条去「使用说明」的链接。原在控制台（2026-09-05），
    落地页换成首页后跟着搬过来：引导要出现在落地的那一页。"""
    if not process.has_ever_run(ui.RUNS_DIR):
        st.page_link(ui.page_ref("guide"), label="第一次用？先看「使用说明」",
                     icon=":material/menu_book:", help=guide.guide_page("guide").lead)


def page_home() -> None:
    ui.page_head("市场概览")
    _first_run_hint()
