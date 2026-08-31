"""「记账」页（v0.3.1 §1，原「交易日志」页的录入 + 历史 + 导出那一半）。

v0.3.0 的「交易日志」把五块（录入/历史/持仓/盈亏/导出）堆在一屏，拆成两个子页：
本页管**写**（录入表单 + 历史日志表 + 导出），「持仓与盈亏」页管**读**（算出来的
持仓与盈亏）。页面函数从 app/pages_journal.py 原样搬来，**逻辑不改只挪**；
排版按设计 §1：录入表单占一个卡片区，历史表全宽。

这一页与面板其余页面有一个根本差别：**它会写一份不可再生的文件**。其余页面最坏的
后果是显示错了，重跑一次就好；这里最坏的后果是用户真实的交易记录被改错或删错。
所以版面上有三条纪律（沿自 v0.3.0 §5）：

1. **算数与写盘全在 src/quant/journal/**，本文件只做布局与接线（同项目既定分层）；
   编排逻辑（控件 key、预填、校验编排、表格）在 app/journal_ui.py，那份能脱离
   Streamlit 运行时单测。
2. **卖超警告的持仓上下文按整本日志算**（_held 用整本日志的 PnlReport），
   不受筛选影响——筛选只作用于日志表与导出。
3. **警告显眼、持续可见**（设计 §3）：写盘/校验的结论走 journal_ui.flash，
   警告与错误留在页面上，不是记完就沉没的 toast。
"""
from __future__ import annotations

from datetime import date

import streamlit as st

import guide
import journal_ui
import pool
import theme
import ui
from quant.config import load_settings
from quant.journal import export, pnl, schema, store

# 读 settings.yaml 能出的错**沿用 pool 里那一份**（文件不在、YAML 语法坏、缺键、
# 值非法……）：那是同一个文件的同一批坏法，两处各存一份迟早只改一边，
# 而漏掉的那种（第一次就是 yaml.YAMLError）会让整页崩在一屏 traceback 上。
# 本页只用它降级**录入区**：成本模型拿不到就不给记账（详见下面），
# 其余部分（日志表与导出）都不依赖配置。
CONFIG_ERRORS = pool.CONFIG_ERRORS


def page_journal_entry() -> None:
    ui.page_head("记账")
    journal_ui.show_flash()          # 上一轮写盘/校验留下的结论
    with st.expander("这一页记什么、四种记录类型的含义", expanded=False):
        st.markdown(guide.JOURNAL_KINDS)

    try:
        trades = store.load_trades(ui.JOURNAL_PATH)
    except RuntimeError as e:
        # 日志文件损坏时整页只剩这一句：这份文件不可再生，自愈办法是 git 找回，
        # 绝不是"删掉重建"（store 抛的那句话已经这么写，原样转述）。
        st.error(str(e))
        return

    report = pnl.compute_pnl(trades)   # 卖超警告的上下文按整本日志算（见 _held）
    # 录入表单收进一个卡片区（设计 §1 的排版）：与全宽的历史表拉开层次，
    # "填的"与"看的"一眼分得开。历史表留在卡片外，照旧全宽。
    with st.container(border=True):
        _entry_section(report)
    _log_section(trades)


# ---------------------------------------------------------------- 录入（§5.1）

def _entry_section(report: pnl.PnlReport) -> None:
    st.html(theme.section("记一笔"))
    prefill = journal_ui.apply_prefill()      # 必须在任何控件创建之前
    if prefill:
        st.caption(f"已按「{prefill.get('source')} / {prefill.get('date')}」那条信号预填，"
                   "成交价与股数请填你**实际**成交的数——信号那天的收盘价不是成交价。")
    try:
        costs = load_settings(ui.CONFIG_PATH).costs
    except CONFIG_ERRORS as e:
        # 拿不到成本模型就不给录入：拿 0 顶包会污染一份不可再生的文件，
        # 而 0 元费用会让此后每一笔盈亏都偏高一点，永远不报错。
        st.error(f"读不到成本模型 `{ui.CONFIG_PATH}`（{type(e).__name__}: {e}）。"
                 "费用没法自动算，本页**暂不提供录入**（避免把 0 元费用写进日志）；"
                 "下面的日志与导出不依赖它，照常可用。")
        return

    names = ui.symbol_names()
    today = date.today()
    head = st.columns([1.4, 1, 1, 1, 1], vertical_alignment="bottom")
    kind = head[0].radio("方向", TRADE_OPTIONS, format_func=journal_ui.KIND_LABELS.get,
                         key=journal_ui.KIND_KEY, horizontal=True)
    # max_value 钉成今天：未来日期是设计 §3 里唯一的阻断项，干脆让它选不出来。
    # min_value 见 journal_ui.EARLIEST_DAY（默认值会静默挡住补记十年前的交易）。
    day = head[1].date_input("成交日期", value=today, max_value=today,
                             min_value=journal_ui.EARLIEST_DAY,
                             key=journal_ui.DATE_KEY, format="YYYY-MM-DD")
    symbol = str(head[2].text_input("代码", key=journal_ui.SYMBOL_KEY,
                                    placeholder="600519") or "").strip()
    shares = head[3].number_input("股数", value=None, min_value=0.0, step=100.0,
                                  key=journal_ui.SHARES_KEY, placeholder="1000")
    price = head[4].number_input("成交价", value=None, min_value=0.0, step=0.01,
                                 format="%.3f", key=journal_ui.PRICE_KEY,
                                 placeholder="71.500")

    name = journal_ui.hinted_name(symbol, names)
    span = journal_ui.price_range(symbol, day, ui.CACHE_DIR)
    st.caption(_entry_hint(symbol, name, day, span))

    # 费用必须在两个输入框创建**之前**算好（Streamlit 只允许在实例化前改它们的
    # session_state）。依据一变就重算，没变就一个字不动——见 sync_auto_costs。
    journal_ui.sync_auto_costs(kind=kind, day=day, shares=shares, price=price,
                               costs=costs)
    tail = st.columns([1, 1, 1.4, 1], vertical_alignment="bottom")
    fee = tail[0].number_input("佣金", min_value=0.0, step=0.01, format="%.2f",
                              key=journal_ui.FEE_KEY,
                              help="默认按 config/settings.yaml 的成本模型算"
                                   "（与回测同口径），改成券商实际值即可。")
    tax = tail[1].number_input("印花税", min_value=0.0, step=0.01, format="%.2f",
                              key=journal_ui.TAX_KEY,
                              help="A 股只在卖出时收，按**成交日**的税率算。")
    source = tail[2].selectbox("来源", list(schema.SOURCES),
                               format_func=journal_ui.SOURCE_LABELS.get,
                               index=list(schema.SOURCES).index(
                                   journal_ui.DEFAULT_SOURCE),
                               key=journal_ui.SOURCE_KEY, help=guide.JOURNAL_SOURCE_HELP)
    stop_plan = tail[3].number_input("计划止损价", value=None, min_value=0.0, step=0.01,
                                     format="%.3f", key=journal_ui.STOP_KEY,
                                     help=guide.JOURNAL_STOP_HELP)
    reason = st.text_input("理由（为什么做这一笔）", key=journal_ui.REASON_KEY,
                          placeholder="例如：20日线金叉且放量 1.8 倍")
    extra = st.columns([1, 3], vertical_alignment="bottom")
    trade_time = extra[0].text_input("成交时刻（可空）", key=journal_ui.TIME_KEY,
                                     placeholder="14:35")
    note = extra[1].text_input("备注（可空）", key=journal_ui.NOTE_KEY)

    if st.button("＋ 记这一笔", key=journal_ui.SUBMIT_KEY, type="primary"):
        journal_ui.submit(
            dict(date=day, time=trade_time, symbol=symbol, name=name, kind=kind,
                 shares=shares, price=price, fee=fee, tax=tax, source=source,
                 stop_plan=stop_plan, reason=reason, note=note),
            path=ui.JOURNAL_PATH, costs=costs, names=names, price_range_=span,
            position_shares=_held(report, symbol), today=today)
        st.rerun()      # 成功要让下面的表格跟着变，失败也要让提示显示出来
    _other_records(costs, names, today)


#: 主路径只有买入/卖出（设计 §2.2）。列表现造：传模块级 list 给控件时，
#: 谁不小心改了它就串到下一次渲染。
TRADE_OPTIONS = list(journal_ui.TRADE_KINDS)


def _entry_hint(symbol: str, name: str, day, span) -> str:
    """代码下面那行灰字：名称与当日价格区间。

    **拿不到就明说拿不到**，不编。价格区间是本功能最实用的一条校验
    （71.5 打成 715 当场看得出来），但它依赖本地缓存里恰好有那一天——
    没有的时候假装有一个区间，比没有区间危险得多。
    """
    if not symbol:
        return "填入 6 位代码后，这里会带出名称与该日的价格区间（都取自本地数据，离线可用）。"
    parts = [f"**{symbol}**", name or "名称查不到（新股/退市股？照记即可）"]
    if span is None:
        parts.append(f"本地缓存里没有 {day} 的行情，无法核对价格区间")
    else:
        parts.append(f"{day} 区间 [{span[0]:.2f}, {span[1]:.2f}]")
    return " · ".join(parts)


def _held(report: pnl.PnlReport, symbol: str) -> float | None:
    """该标的当前持仓股数；没有持仓记录时返回 None（"不知道"，不是 0）。

    None 与 0 的差别不是抠字眼：给 0 的话，一笔正常的卖出会被报成"卖超"
    （因为日志里还没记过那笔买入），而狼来了喊多了就没人看警告了。
    """
    for position in report.positions:
        if position.symbol == symbol:
            return position.shares
    return None


def _other_records(costs, names, today: date) -> None:
    """送股/转增/拆股与现金分红（设计 §2.2）。

    收在折叠区里：买卖是日常，这两种一年几次。但**必须有入口**——没有它们，
    持仓数会与券商对不上，此后每一笔卖出的 FIFO 配对全错，而且不会报错。
    """
    with st.expander("其他记录：送股/转增/拆股、现金分红", expanded=False):
        st.caption(guide.JOURNAL_OTHER_HINT)
        kind = st.radio("记录类型", OTHER_OPTIONS,
                        format_func=journal_ui.KIND_LABELS.get,
                        key=journal_ui.OTHER_KIND_KEY, horizontal=True)
        cols = st.columns([1, 1, 1, 1.2], vertical_alignment="bottom")
        day = cols[0].date_input("日期", value=today, max_value=today,
                                 min_value=journal_ui.EARLIEST_DAY,
                                 key=journal_ui.OTHER_DATE_KEY, format="YYYY-MM-DD")
        symbol = str(cols[1].text_input("代码", key=journal_ui.OTHER_SYMBOL_KEY,
                                        placeholder="600519") or "").strip()
        shares = cols[2].number_input("股数变化（送股填正、缩股填负）", value=None,
                                      step=100.0, key=journal_ui.OTHER_SHARES_KEY,
                                      disabled=kind != "adjust")
        amount = cols[3].number_input("到账金额（元）", value=None, min_value=0.0,
                                      step=0.01, format="%.2f",
                                      key=journal_ui.OTHER_AMOUNT_KEY,
                                      disabled=kind != "dividend",
                                      help="税后实际到账。红利税记进「佣金」那一栏即可。")
        source = st.selectbox("来源（这笔持仓当初是怎么建的）", list(schema.SOURCES),
                              format_func=journal_ui.SOURCE_LABELS.get,
                              index=list(schema.SOURCES).index(
                                  journal_ui.DEFAULT_SOURCE),
                              key=journal_ui.OTHER_SOURCE_KEY)
        reason = st.text_input("理由/说明", key=journal_ui.OTHER_REASON_KEY,
                               placeholder="例如：10送1 / 2025年度分红")
        if st.button("＋ 记这一笔（其他）", key=journal_ui.OTHER_SUBMIT_KEY):
            journal_ui.submit(
                dict(date=day, symbol=symbol, kind=kind,
                     shares=shares if kind == "adjust" else None,
                     amount=amount if kind == "dividend" else None,
                     source=source, reason=reason),
                path=ui.JOURNAL_PATH, costs=costs, names=names, today=today)
            st.rerun()


OTHER_OPTIONS = list(journal_ui.OTHER_KINDS)


# ---------------------------------------------------------------- 日志表与导出（§5.2 / §5.5）

def _log_section(trades) -> None:
    st.html(theme.section(f"日志（共 {len(trades)} 条）"))
    _filters(trades)
    filtered = export.filter_trades(trades, **journal_ui.current_filter())
    st.caption(f"筛出 {len(filtered)} 条。{guide.TABLE_HINTS['journal']}")
    # 三种空态说的是三件不同的事，混成一句"暂无数据"就等于让用户猜：
    # 一笔都没记过（指路到录入区）、筛没了（指路到筛选条件）、有数据（画表）。
    # 必须是 if/elif **语句**：裸三元会被 streamlit 的 magic 整条包进 st.write。
    if not len(trades):
        st.write(guide.JOURNAL_EMPTY)
    elif not len(filtered):
        st.write("没有符合条件的记录——把上面的筛选条件放宽些看看。")
    else:
        _editor(trades, filtered)
    # 备份说明**不放在 if/else 里面**：一笔都还没记的时候正是用户即将大批量录入的
    # 时刻，那时更需要知道"改错了还有救"。位置紧跟历史表（设计 §3）。
    st.caption(guide.journal_backup_note(store.backup_path(ui.JOURNAL_PATH)))
    _exports(filtered)


def _filters(trades) -> None:
    cols = st.columns([1, 1, 1.4, 1.2, 1.2, 1.4], vertical_alignment="bottom")
    # 日期两端各一个输入框而不是一个区间控件：区间控件在"只选了一头"的中间态会
    # 交回一个长度 1 的元组，每个调用点都得判一次，早晚漏一处。
    cols[0].date_input("起（含）", value=None, min_value=journal_ui.EARLIEST_DAY,
                       key=journal_ui.FILTER_START_KEY, format="YYYY-MM-DD")
    cols[1].date_input("止（含）", value=None, min_value=journal_ui.EARLIEST_DAY,
                       key=journal_ui.FILTER_END_KEY, format="YYYY-MM-DD")
    cols[2].multiselect("标的", journal_ui.filter_options(trades, "symbol"),
                        key=journal_ui.FILTER_SYMBOL_KEY, placeholder="不限")
    cols[3].multiselect("类型", list(schema.KINDS), key=journal_ui.FILTER_KIND_KEY,
                        format_func=journal_ui.KIND_LABELS.get, placeholder="不限")
    cols[4].multiselect("来源", list(schema.SOURCES), key=journal_ui.FILTER_SOURCE_KEY,
                        format_func=journal_ui.SOURCE_LABELS.get, placeholder="不限")
    cols[5].text_input("理由关键词", key=journal_ui.FILTER_REASON_KEY,
                       placeholder="例如 金叉")


def _editor(trades, filtered) -> None:
    """可编辑的日志表 + 「保存修改」。

    改与删都按 `trade_id` 定位（store.apply_edits 里那三条铁律）：这张表显示的是
    **筛选后**的行，行号与整本日志对不上。
    """
    edited = st.data_editor(journal_ui.editor_frame(filtered), key=journal_ui.EDITOR_KEY,
                            column_config=journal_ui.editor_columns(),
                            num_rows="fixed", hide_index=True, width="stretch",
                            disabled=["trade_id"])
    row = st.columns([1, 4], vertical_alignment="center")
    if row[0].button("✔ 保存修改", key=journal_ui.SAVE_KEY):
        journal_ui.save_edits(edited, trades=trades, path=ui.JOURNAL_PATH,
                              today=date.today())
        st.rerun()
    row[1].caption(guide.JOURNAL_EDIT_HINT)


def _exports(filtered) -> None:
    """导出区：**数据源只有一个**，就是上面那张表（所见即所得，设计 §5.5）。"""
    cols = st.columns([1, 1, 3], vertical_alignment="center")
    for col, payload in zip(cols, journal_ui.export_payloads(filtered, date.today())):
        # 一律走 `with col:` + 顶层 st.*（而不是 col.download_button）：项目里所有
        # "这个控件真的收到了什么"的测试都靠 monkeypatch 顶层 st.xxx，
        # 而 DeltaGenerator 上的同名方法绕过那一层，断言会静默变成空跑。
        with col:
            if payload["error"]:
                st.caption(payload["error"])
            else:
                st.download_button(payload["label"], data=payload["data"],
                                   file_name=payload["file_name"],
                                   mime=payload["mime"], key=payload["key"])
    with cols[2]:
        st.caption(guide.JOURNAL_EXPORT_HINT)
