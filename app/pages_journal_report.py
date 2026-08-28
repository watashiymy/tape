"""「持仓与盈亏」页（v0.3.1 §1，原「交易日志」页的持仓/盈亏/来源那一半）。

「记账」页管**写**，本页管**读**：整本日志算出来的当前持仓、已实现盈亏与来源对比。
页面函数从 app/pages_journal.py 原样搬来（M1 的拆页**逻辑不改只挪**）；排版按
设计 §1：三张表收进 st.tabs（当前持仓 / 平仓明细 / 来源对比），不再纵向全部铺开。
顶部是 M2 的仪表盘（设计 §2）：指标块 2×3 + 曲线/占比两图并排，来源对比图在
来源 tab 里。数字口径全在 journal_ui.dashboard_metrics 与 quant.journal.analytics
（纯函数，手算单测钉住），本页只做排版与转述。

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
from quant.journal import analytics, pnl, store
from quant.report import charts


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
    _dashboard_section(report, trades)
    positions_tab, matches_tab, source_tab = st.tabs(["当前持仓", "平仓明细", "来源对比"])
    with positions_tab:
        _positions_section(report)
    with matches_tab:
        _matches_section(report)
    with source_tab:
        _by_source_section(report)


# ---------------------------------------------------------------- 仪表盘（v0.3.1 §2）

def _dashboard_section(report: pnl.PnlReport, trades) -> None:
    """指标块 2×3 + 曲线/占比两图并排。留在 tabs 之外：这几个数是本页的结论，
    收进某个 tab 就得多点一下才看得到。

    两条口径纪律（数字本身在 journal_ui / analytics 里，有手算单测）：
    - 无市价的持仓**不按 0 计入**市值与浮动，如实注明有几只没算进来；
    - 曲线是**累计已实现盈亏（元）**，不是收益率（设计 §2.6）：日志不记本金
      与出入金，任何收益率的分母都只能编造——这条取舍就注在图旁边。
    """
    st.html(theme.section("仪表盘"))
    prices = {p.symbol: journal_ui.latest_price(p.symbol, ui.CACHE_DIR)
              for p in report.positions}
    metrics, unpriced = journal_ui.dashboard_metrics(report.summary,
                                                     report.positions, prices)
    for start in (0, 3):
        for col, (label, text, color) in zip(st.columns(3), metrics[start:start + 3]):
            col.html(theme.metric(label, text, color))
    if unpriced:
        st.caption(f"{unpriced} 只持仓无市价未计入市值与浮动盈亏"
                   "（本地缓存 `data/cache/` 里没有它们的日线——"
                   "跑一次回测或每日信号就有了），显示的是可算部分，不按 0 顶包。")
    st.caption(guide.JOURNAL_PNL_HINT)

    curve_col, weights_col = st.columns(2)
    with curve_col:
        # 空态不画空图（设计 §2.2）：一张空坐标系没有任何信息量，还像出了错。
        if points := analytics.cumulative_realized(trades):
            st.plotly_chart(charts.journal_cum_pnl_chart(points), width="stretch")
        else:
            st.caption("还没有平仓记录——第一笔卖出后这里会出现你的已实现盈亏曲线。")
    with weights_col:
        # 占比图里无市价的标的按成本顶上并打「按成本」标（与指标卡刻意相反：
        # 占比漏一只会让其余标的虚高，见 analytics 的模块 docstring）。
        if rows := analytics.position_weights(report.positions, prices):
            st.plotly_chart(charts.position_weights_chart(rows), width="stretch")
        else:
            st.caption("当前没有持仓，暂无占比可画。")
    st.caption("金额一律是**元**而非收益率：日志不记本金与出入金，收益率的分母"
               "只能编造；将来若加「本金/出入金」记录类型再升级成真收益率（设计 §2.6）。")


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
    # 图在表之上：图先回答"哪边赚得多"，表再给全部口径的数。类目身份由轴标签
    # 承载、条一律单色琥珀——零类别配色是设计 §2.5 经校验实测定下的硬约束。
    if report.by_source:
        st.plotly_chart(charts.source_compare_chart(report.by_source,
                                                    journal_ui.SOURCE_LABELS),
                        width="stretch")
    ui.data_table(journal_ui.by_source_table(report.by_source),
                  journal_ui.by_source_columns(),
                  "还没有已平仓的交易，暂时无从对比。",
                  hint=guide.TABLE_HINTS["by_source"])
