"""「持仓与盈亏」页（v0.3.1 §1，原「交易日志」页的持仓/盈亏/来源那一半）。

「记账」页管**写**，本页管**读**：整本日志算出来的当前持仓、已实现盈亏与来源对比。
页面函数从 app/pages_journal.py 原样搬来，**逻辑不改只挪**；排版按设计 §1：
三张表收进 st.tabs（当前持仓 / 平仓明细 / 来源对比），不再纵向全部铺开。
仪表盘（指标块 + 两图）v0.3.1 M2 再加。

两条纪律沿自 v0.3.0 §5：

1. **持仓与盈亏永远按整本日志算，不受「记账」页筛选的影响**。拆页把这条从约定
   变成了结构：本页根本不读筛选控件的 session_state。
2. **警告显眼、持续可见**（设计 §3）：一致性告警每轮重算并列在所有数字**之前**，
   不是记完就沉没的 toast——卖超/读不懂的行意味着下面每个数都可能是错的。
"""
from __future__ import annotations

import streamlit as st

import guide
import journal_ui
import theme
import ui
from quant.journal import pnl, store


def page_journal_report() -> None:
    ui.page_head("持仓与盈亏")
    with st.expander("这些数字怎么算的、已知局限", expanded=False):
        st.markdown(guide.JOURNAL_LIMITS)

    try:
        trades = store.load_trades(ui.JOURNAL_PATH)
    except RuntimeError as e:
        # 日志文件损坏时整页只剩这一句：这份文件不可再生，自愈办法是 git 找回，
        # 绝不是"删掉重建"（store 抛的那句话已经这么写，原样转述）。
        st.error(str(e))
        return

    report = pnl.compute_pnl(trades)   # 永远按整本日志算（见模块 docstring 第 1 条）
    # 告警排在所有数字**之前**：设计 §3 要求"错误必须显眼、持续可见"，
    # 收进某个 tab 就会被藏起来。同一句话去重后只说一次，别刷屏。
    for message in dict.fromkeys(i.message for i in report.inconsistencies):
        st.warning(message)
    _summary_section(report)
    positions_tab, matches_tab, source_tab = st.tabs(["当前持仓", "平仓明细", "来源对比"])
    with positions_tab:
        _positions_section(report)
    with matches_tab:
        _matches_section(report)
    with source_tab:
        _by_source_section(report)


# ---------------------------------------------------------------- 持仓与盈亏（§4.2）

def _summary_section(report: pnl.PnlReport) -> None:
    """已实现盈亏的汇总指标卡。留在 tabs 之外：这几个数是本页的结论，
    收进某个 tab 就得多点一下才看得到。M2 的仪表盘会在这里长出来。"""
    st.html(theme.section("已实现盈亏"))
    metrics = journal_ui.summary_metrics(report.summary)
    for start in range(0, len(metrics), 4):
        for col, (label, text, color) in zip(st.columns(4), metrics[start:start + 4]):
            col.html(theme.metric(label, text, color))
    st.caption(guide.JOURNAL_PNL_HINT)


def _positions_section(report: pnl.PnlReport) -> None:
    ui.data_table(journal_ui.positions_table(report.positions, ui.CACHE_DIR),
                  journal_ui.positions_columns(),
                  "当前没有持仓（还没记过买入，或者都已经卖光了）。",
                  hint=guide.TABLE_HINTS["positions"])


def _matches_section(report: pnl.PnlReport) -> None:
    ui.data_table(journal_ui.matches_table(report.matches),
                  journal_ui.matches_columns(),
                  "还没有已平仓的交易。", hint=guide.TABLE_HINTS["closings"],
                  color_columns=("盈亏",))


def _by_source_section(report: pnl.PnlReport) -> None:
    ui.data_table(journal_ui.by_source_table(report.by_source),
                  journal_ui.by_source_columns(),
                  "还没有已平仓的交易，暂时无从对比。",
                  hint=guide.TABLE_HINTS["by_source"])
