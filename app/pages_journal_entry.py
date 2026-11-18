"""「记账」页（v0.3.1 §1，原「交易日志」页的录入 + 历史 + 导出那一半）。

v0.3.0 的「交易日志」把五块（录入/历史/持仓/盈亏/导出）堆在一屏，拆成两个子页：
本页管**写**（录入表单 + 历史记录表 + 导出），「持仓与盈亏」页管**读**（算出来的
持仓与盈亏）。录入表单占一个卡片区，历史表全宽。

**版式（2026-11-16 改版）**。改之前真机上看到的毛病：
- 录入区十二个输入框同等权重，分不出哪几样是每一笔都要填的；记之前看不到股数 × 成交价
  是多少钱、费用合计多少；
- 历史表照搬文件的列序：第一列是机器编号，类型与来源显示 buy / sell / discretionary，
  「删除」勾选框在最右边的屏幕外；
- 表格上方常驻六个筛选框（一共才七笔记录），表格下面三段灰字加五个控件堆在一起，
  保存、撤销、导出分不出层次。

现在录入区只摆每一笔都要填的几样，另有一行**核对**（成交金额、费用、最后付出 / 到手
多少——就是会写进去的数）；"为什么做这一笔"另起一行；自动算好的费用与两个可空项收进
「更多」。历史表按人读一笔交易的顺序排列、显示中文；筛选收进折叠区，筛了什么写在表格
上方。设计与真机验收：docs/superpowers/specs/2026-11-16-status-bar-journal-design.md。

**同一天的第二轮**（用户看过上面那版之后提的三条，docs/superpowers/specs/
2026-11-16-declutter-design.md）：
- 页面上常驻的解释性文字拿掉，要看的时候才出来：页名、小标题后面的帮助圆点，输入框与按钮
  自己的悬停说明。留在明面上的只有会变的东西（带出的名称与价格区间、核对的金额、还有几行
  没保存）。
- 「记一笔」重排：方向不再与四个输入框挤在一行（单选框只占半格，后面空着一大截），改成表单
  最上面的一个「买入｜卖出」切换；下面一行四格等宽——代码、成交日期、股数、成交价；
  核对那两行挪到表单底部左侧，紧挨着右下角的主按钮（点之前眼睛正好经过它）。
- **操作按钮一律靠右**：表单的提交按钮在表单右下角；历史表下面一行，从左到右是恢复上一版
  （连同它的确认框）、两个导出、保存修改——主按钮在最右。

**历史记录的删除**（2026-11-18，用户：「"删除"的选择框为什么放在第一列」「先勾选、再点保存
——很奇怪的交互」，docs/superpowers/specs/2026-11-18-journal-delete-design.md）：
- 改之前删除是历史表里的一列勾选框，勾了什么也不发生，要去点「保存修改」才真的删。
  现在每行最前面一个删除按钮：点了弹确认框、写明删的是哪一条，确认即删；「保存修改」只管改动。
- 防误删靠确认框与删之前照旧留下的上一版。确认框里另有两句只在适用时出现的话：
  表里别的行上有没保存的改动（会一起保存）；上一版里还有现在已经没有的记录（这次删除之后
  就换不回来了——上一版只留一版）。

这一页与面板其余页面有一个根本差别：**它会写一份不可再生的文件**。其余页面最坏的
后果是显示错了，重跑一次就好；这里最坏的后果是用户真实的交易记录被改错或删错。
所以版面上有三条纪律（沿自 v0.3.0 §5）：

1. **算数与写盘全在 src/quant/journal/**，本文件只做布局与接线（同项目既定分层）；
   编排逻辑（控件 key、预填、校验编排、表格）在 app/journal_ui.py，那份能脱离
   Streamlit 运行时单测。
2. **卖超警告的持仓上下文按整本日志算**（_held 用整本日志的 PnlReport），
   不受筛选影响——筛选只作用于历史表与导出。
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
from quant.report import fmt

# 读 settings.yaml 能出的错**沿用 pool 里那一份**（文件不在、YAML 语法坏、缺键、
# 值非法……）：那是同一个文件的同一批坏法，两处各存一份迟早只改一边，
# 而漏掉的那种（第一次就是 yaml.YAMLError）会让整页崩在一屏 traceback 上。
# 本页只用它降级**录入区**：成本模型拿不到就不给记账（详见下面），
# 其余部分（历史记录表与导出）都不依赖配置。
CONFIG_ERRORS = pool.CONFIG_ERRORS

# 这一页四个折叠区的 key。**必须给**：页面顶上一冒出报错那一条（journal_ui.show_flash），
# 下面所有元素整体往后挪一位，不带 key 的折叠区会被浏览器当成一个新的、自己合上——
# 正填着「更多」里的佣金、正开着「筛选」，点一下「记这一笔」被拒，面板就在眼前关掉了
# （2026-11-16 真机看到；用一个小页面对照过：带 key 的留着、不带的合上）。带 key 的认 key，不认位置。
BOX_HELP_KEY = "journal_box_help"
BOX_MORE_KEY = "journal_box_more"
BOX_OTHER_KEY = "journal_box_other"
BOX_FILTER_KEY = "journal_box_filter"


def page_journal_entry() -> None:
    ui.page_head("记账")
    journal_ui.show_flash()          # 上一轮写盘/校验留下的结论
    # 上一轮记下了一笔：把那张表单清回空值。必须排在任何录入控件创建之前。
    journal_ui.apply_pending_clear()
    # 刚才若点了历史表某一行的删除按钮，把它取走，等整页画完去问（见 _confirm_delete）。
    # 在最前面取：这一轮若中途返回，它也不会留到以后某一轮再冒出来。
    asked = journal_ui.take_delete_request()
    with st.expander("这一页记什么、四种记录类型的含义", expanded=False, key=BOX_HELP_KEY):
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
        entry, acted = _entry_section(report)
    # 送股与分红在卡片**外面**（2026-11-16）：卡片里只有一张表单、一个主按钮。
    # 成本模型读不到时录入区整个不给（entry 是 None），这两种也一样——它们同样要写费用。
    if entry is not None:
        acted = _other_records(*entry) or acted
    saved, edited = _log_section(trades)
    if acted or saved:
        # 记了一笔 / 保存了修改之后要整页重跑：成功要让表格跟着变，失败也要让提示显示出来。
        # 但**等整页画完再重跑**（2026-11-16 复审后改）。原先是按钮按下的当场就 st.rerun()，
        # 那时它下面的筛选框、历史表还没画——Streamlit 把一次中途重跑当成"这一轮跑完了"，
        # 这一轮没画出来的控件，后台就认为它们不在了、把它们的状态丢掉：筛着「卖出」时点一下
        # 「记这一笔」，表格会变回全部记录一轮（筛选框里却还写着卖出），表里没保存的改动也跟着
        # 少报一轮。画完再重跑，每个控件都在场，状态一个不丢。
        st.rerun()
    if asked:
        # 确认框也排在整页画完之后，理由相同：它里面的「删除」「取消」都要整页重跑一次。
        _confirm_delete(asked, trades, edited)


# ---------------------------------------------------------------- 录入（§5.1）

def _source_label(prefill: dict) -> str:
    """预填提示里的来源用**中文显示名**。

    存进日志的是内部键（`donchian`），那是刻意的、不能动；但把它原样甩到提示里，
    页面上就冒出一个别处从不出现的英文词——同一个策略在表格里叫「唐奇安通道突破」、
    在这句话里叫 donchian。未知键原样返回（strategy_label 的既有语义）。
    """
    src = prefill.get("source")
    return journal_ui.SOURCE_LABELS.get(src, src)


def _entry_section(report: pnl.PnlReport) -> tuple[tuple | None, bool]:
    """录入卡片。返回两样：(成本模型, 名称表, 今天)——给卡片外的「其他记录」用，
    成本模型读不到时是 None（那时整个录入区都不给）；以及这一轮有没有按下「记这一笔」
    （按下了，页面画完之后要整页重跑，见 page_journal_entry）。"""
    # 卡片自己有边框，标题不再带发丝线（同控制台的任务卡片）。
    st.html(theme.task_head("记一笔"))
    prefill = journal_ui.apply_prefill()      # 必须在任何控件创建之前
    if prefill:
        # 这一句留在明面上：预填的日期是信号日，不是成交日，照着记就是一个看着正常的错数。
        st.caption(guide.JOURNAL_PREFILLED.format(source=_source_label(prefill),
                                                  day=prefill.get("date")))
    try:
        costs = load_settings(ui.CONFIG_PATH).costs
    except CONFIG_ERRORS as e:
        # 拿不到成本模型就不给录入：拿 0 顶包会污染一份不可再生的文件，
        # 而 0 元费用会让此后每一笔盈亏都偏高一点，永远不报错。
        st.error(f"读不到成本模型 `{ui.CONFIG_PATH}`（{type(e).__name__}: {e}）。"
                 "费用没法自动算，本页**暂不提供录入**（避免把 0 元费用写进日志）；"
                 "下面的历史记录与导出不依赖它，照常可用。")
        return None, False

    names = ui.symbol_names()
    today = date.today()
    # —— 方向：表单最上面的一个切换，不与输入框挤在一行（2026-11-16 第二轮）。
    # 改之前它是第一行五格里的第一格——单选框只占半格，后面空着一大截，看着别扭（用户原话）。
    # 标题收起：「买入｜卖出」自己就说清了这是什么。required：点已选中的那个不会变成"都没选"。
    # 初值写进 session_state 而不是 default=：预填（apply_prefill）也是往这个键里写，
    # 两头都给值 streamlit 会告警。
    st.session_state.setdefault(journal_ui.KIND_KEY, TRADE_OPTIONS[0])
    kind = st.segmented_control("方向", TRADE_OPTIONS, format_func=journal_ui.KIND_LABELS.get,
                                key=journal_ui.KIND_KEY, required=True,
                                label_visibility="collapsed")
    # —— 第一行：每一笔都要填的四样，四格等宽，别的都不在这一行。
    head = st.columns(4, vertical_alignment="bottom")
    symbol = str(head[0].text_input("代码", key=journal_ui.SYMBOL_KEY, placeholder="600519",
                                    help=guide.JOURNAL_SYMBOL_HELP) or "").strip()
    # max_value 钉成今天：未来日期是设计 §3 里唯一的阻断项，干脆让它选不出来。
    # min_value 见 journal_ui.EARLIEST_DAY（默认值会静默挡住补记十年前的交易）。
    day = head[1].date_input("成交日期", value=today, max_value=today,
                             min_value=journal_ui.EARLIEST_DAY,
                             key=journal_ui.DATE_KEY, format="YYYY-MM-DD")
    shares = head[2].number_input("股数", value=None, min_value=0.0, step=100.0,
                                  key=journal_ui.SHARES_KEY, placeholder="1000")
    price = head[3].number_input("成交价", value=None, min_value=0.0, step=0.01,
                                 format="%.3f", key=journal_ui.PRICE_KEY,
                                 placeholder="71.500")

    name = journal_ui.hinted_name(symbol, names)
    span = journal_ui.price_range(symbol, day, ui.CACHE_DIR)
    # 费用必须在两个输入框创建**之前**算好（Streamlit 只允许在实例化前改它们的
    # session_state）。依据一变就重算，没变就一个字不动——见 sync_auto_costs。
    journal_ui.sync_auto_costs(kind=kind, day=day, shares=shares, price=price,
                               costs=costs)

    # —— 第二行：为什么做这一笔。这是日志之所以是日志的三样，都可空但都摆在明面上。
    # 理由占上面四格里的前两格，来源与计划止损各占一格。**先对半分、右半边再对半分**，
    # 不写成一行 2:1:1：栏与栏之间的空隙是固定宽度，三栏只有两道空隙、四栏有三道，
    # 2:1:1 的竖线会比上面那一行偏出几个像素（真机量过，差 3 px）；嵌套之后逐栏对齐。
    why, rest = st.columns(2, vertical_alignment="bottom")
    reason = why.text_input("理由", key=journal_ui.REASON_KEY,
                            placeholder="例如：20日线金叉且放量 1.8 倍")
    source_col, stop_col = rest.columns(2, vertical_alignment="bottom")
    source = source_col.selectbox("来源", list(schema.SOURCES),
                                  format_func=journal_ui.SOURCE_LABELS.get,
                                  index=list(schema.SOURCES).index(
                                      journal_ui.DEFAULT_SOURCE),
                                  key=journal_ui.SOURCE_KEY, help=guide.JOURNAL_SOURCE_HELP)
    stop_plan = stop_col.number_input("计划止损价", value=None, min_value=0.0, step=0.01,
                                      format="%.3f", key=journal_ui.STOP_KEY,
                                      help=guide.JOURNAL_STOP_HELP)
    # —— 更多：自动算好的费用与两个可空项。折叠而不是"点了才渲染"：控件一直在页面上，
    # 费用照常自动算、改过的值不会因为收起而丢；改没改，下面「核对」那一行都看得见。
    with st.expander(guide.JOURNAL_MORE, type="compact", key=BOX_MORE_KEY):
        more = st.columns(4, vertical_alignment="bottom")
        fee = more[0].number_input("佣金", min_value=0.0, step=0.01, format="%.2f",
                                   key=journal_ui.FEE_KEY,
                                   help="默认按系统的成本模型算（与回测同口径），"
                                        "改成券商实际值即可。")
        tax = more[1].number_input("印花税", min_value=0.0, step=0.01, format="%.2f",
                                   key=journal_ui.TAX_KEY,
                                   help="A 股只在卖出时收，按**成交日**的税率算。")
        trade_time = more[2].text_input("成交时刻", key=journal_ui.TIME_KEY,
                                        placeholder="14:35")
        note = more[3].text_input("备注", key=journal_ui.NOTE_KEY)

    # —— 底部一行：左边核对，右下角主按钮（操作按钮一律靠右）。
    # 核对两行都是会变的东西：代码带出的名称与当日价格区间；会写进去的钱（没填完时不出）。
    # 什么都没填时这一栏是空的——原先这里常驻一句"填入代码后这里会带出……"，拿掉了，
    # 那句话在「代码」输入框自己的悬停说明里。
    # **两栏顶端对齐**：核对那两行是填完最后一格才冒出来的——人正把光标往按钮上移。
    # 底端对齐时按钮会被它顶下去（独立审查量的：1024 宽下核对折成四行，按钮下移 48 px）；
    # 顶端对齐，左边长多高按钮都不动。
    check, action = st.columns([3, 1], vertical_alignment="top")
    meta = theme.task_meta(
        _entry_hint(symbol, name, day, span), guide.JOURNAL_CHECK_KEY,
        journal_ui.entry_summary(kind=kind, day=day, shares=shares, price=price,
                                 fee=fee, tax=tax, costs=costs))
    if meta:
        check.html(meta)
    with action.container(horizontal=True, horizontal_alignment="right"):
        clicked = st.button("记这一笔", key=journal_ui.SUBMIT_KEY, type="primary",
                            icon=":material/add:")
    if clicked:
        journal_ui.submit(
            dict(date=day, time=trade_time, symbol=symbol, name=name, kind=kind,
                 shares=shares, price=price, fee=fee, tax=tax, source=source,
                 stop_plan=stop_plan, reason=reason, note=note),
            path=ui.JOURNAL_PATH, costs=costs, names=names, price_range_=span,
            position_shares=_held(report, symbol), today=today)
    return (costs, names, today), clicked


#: 主路径只有买入/卖出（设计 §2.2）。列表现造：传模块级 list 给控件时，
#: 谁不小心改了它就串到下一次渲染。
TRADE_OPTIONS = list(journal_ui.TRADE_KINDS)


def _entry_hint(symbol: str, name: str, day, span) -> str:
    """表单底部核对区的第一行：代码带出的名称与当日价格区间。没填代码时是空串——不出这一行。

    **拿不到就明说拿不到**，不编。价格区间是本功能最实用的一条校验
    （71.5 打成 715 当场看得出来），但它依赖本地缓存里恰好有那一天——
    没有的时候假装有一个区间，比没有区间危险得多。
    """
    if not symbol:
        return ""
    # 纯文字：这一行经 theme.task_meta 整体转义后渲染，写 ** 会原样显示成两个星号。
    parts = [symbol, name or "查不到名称（可能是新股或退市股，照常记录即可）"]
    if span is None:
        parts.append(f"本地还没有 {day} 的行情数据，无法核对价格区间")
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


def _other_records(costs, names, today: date) -> bool:
    """送股/转增/拆股与现金分红（设计 §2.2）。返回这一轮有没有按下它的「记这一条」。

    收在折叠区里：买卖是日常，这两种一年几次。但**必须有入口**——没有它们，
    持仓数会与券商对不上，此后每一笔卖出的 FIFO 配对全错，而且不会报错。
    2026-11-16 起在录入卡片**下面**而不是里面：卡片里只有一张表单、一个主按钮。
    """
    with st.expander("其他记录：送股/转增/拆股、现金分红", expanded=False, key=BOX_OTHER_KEY):
        # 与上面那张表单同一种排法（2026-11-16 第二轮）：类型是最上面的一个切换，下面一行四格，
        # 按钮在右下角。原先折叠区一打开先是三行灰字，那些话现在在两个输入框自己的悬停说明里。
        st.session_state.setdefault(journal_ui.OTHER_KIND_KEY, OTHER_OPTIONS[0])
        kind = st.segmented_control("记录类型", OTHER_OPTIONS,
                                    format_func=journal_ui.KIND_LABELS.get,
                                    key=journal_ui.OTHER_KIND_KEY, required=True,
                                    label_visibility="collapsed")
        cols = st.columns(4, vertical_alignment="bottom")
        symbol = str(cols[0].text_input("代码", key=journal_ui.OTHER_SYMBOL_KEY,
                                        placeholder="600519") or "").strip()
        day = cols[1].date_input("日期", value=today, max_value=today,
                                 min_value=journal_ui.EARLIEST_DAY,
                                 key=journal_ui.OTHER_DATE_KEY, format="YYYY-MM-DD")
        shares = cols[2].number_input("股数变化", value=None,
                                      step=100.0, key=journal_ui.OTHER_SHARES_KEY,
                                      disabled=kind != "adjust",
                                      placeholder=guide.JOURNAL_ADJUST_PLACEHOLDER,
                                      help=guide.JOURNAL_ADJUST_HELP)
        amount = cols[3].number_input("到账金额", value=None, min_value=0.0,
                                      step=0.01, format="%.2f",
                                      key=journal_ui.OTHER_AMOUNT_KEY,
                                      disabled=kind != "dividend",
                                      help=guide.JOURNAL_DIVIDEND_HELP)
        # 第二行与上面四格逐栏对齐（先对半分、右半边再对半分，理由同上面那张表单）；
        # 最后一格是按钮，落在折叠区的右下角。
        why, rest = st.columns(2, vertical_alignment="bottom")
        reason = why.text_input("理由/说明", key=journal_ui.OTHER_REASON_KEY,
                                placeholder="例如：10送1 / 2025年度分红")
        source_col, action = rest.columns(2, vertical_alignment="bottom")
        source = source_col.selectbox("来源", list(schema.SOURCES),
                                      format_func=journal_ui.SOURCE_LABELS.get,
                                      index=list(schema.SOURCES).index(
                                          journal_ui.DEFAULT_SOURCE),
                                      key=journal_ui.OTHER_SOURCE_KEY,
                                      help=guide.JOURNAL_OTHER_SOURCE_HELP)
        with action.container(horizontal=True, horizontal_alignment="right"):
            clicked = st.button("记这一条", key=journal_ui.OTHER_SUBMIT_KEY,
                                icon=":material/add:")
        if clicked:
            journal_ui.submit(
                dict(date=day, symbol=symbol, kind=kind,
                     shares=shares if kind == "adjust" else None,
                     amount=amount if kind == "dividend" else None,
                     source=source, reason=reason),
                path=ui.JOURNAL_PATH, costs=costs, names=names, today=today)
    return clicked


OTHER_OPTIONS = list(journal_ui.OTHER_KINDS)


# ---------------------------------------------------------------- 历史记录与导出（§5.2 / §5.5）

def _log_section(trades) -> tuple[bool, tuple | None]:
    """历史记录那一区。返回两样：这一轮有没有按下「保存修改」（页面画完之后要整页重跑）；
    以及历史表（_editor 交回的那一对，没有表可改时是 None）——删除的确认框要拿它看
    表里还有没有没保存的改动。"""
    # 三种空态说的是三件不同的事，混成一句"暂无数据"就等于让用户猜：
    # 一笔都没记过（指路到录入区）、筛没了（指路到筛选条件）、有数据（画表）。
    if not len(trades):
        # 一笔都没记过：筛选、表格、导出都没有东西可作用，只留这句话和撤销手段。
        # 「恢复上一版」照留——正要大批量录入的时刻，更需要知道"改错了还有救"。
        st.html(theme.section(guide.JOURNAL_LOG_TITLE.format(n=0)))
        st.write(guide.JOURNAL_EMPTY)
        with st.container(horizontal=True, horizontal_alignment="right",
                          vertical_alignment="center"):
            with _button_group():
                _restore_block()
        return False, None
    # 怎么改、怎么删、改的时候最容易想当然的那一件事：在小标题后面的帮助圆点里
    # （2026-11-16 第二轮之前是表格上方常驻的一行灰字）。
    st.html(theme.section(guide.JOURNAL_LOG_TITLE.format(n=len(trades)),
                          help=guide.TABLE_HINTS["journal"]))
    # 筛选收进折叠区（2026-11-16）：七笔记录上面常驻六个输入框，是这一页最吵的一排。
    # 折叠而不是"点了才渲染"：控件一直在页面上，筛选条件不会因为收起而丢。
    with st.expander(guide.JOURNAL_FILTER, type="compact", key=BOX_FILTER_KEY):
        _filters(trades)
    conditions = journal_ui.current_filter()
    filtered = export.filter_trades(trades, **conditions)
    if summary := journal_ui.filter_summary(conditions):
        # 收起来之后筛了什么必须写在明面上：表里只剩这些行，导出也只导这些行。
        note, clear = st.columns([5, 1], vertical_alignment="center")
        note.caption(guide.JOURNAL_FILTERED.format(shown=len(filtered), total=len(trades),
                                                   summary=summary))
        with clear.container(horizontal=True, horizontal_alignment="right"):
            st.button(guide.JOURNAL_FILTER_CLEAR, key=journal_ui.FILTER_CLEAR_KEY,
                      type="tertiary", icon=":material/filter_alt_off:",
                      on_click=journal_ui.clear_filters)
    # 必须是 if/else **语句**：裸三元会被 streamlit 的 magic 整条包进 st.write。
    edited = None
    if not len(filtered):
        st.write(guide.JOURNAL_FILTER_NONE)
    else:
        edited = _editor(filtered)
    return _log_actions(filtered, edited), edited


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


def _editor(filtered):
    """可编辑的历史表。返回 (喂进去的表, 交回来的表)——「保存修改」在 _log_actions 里，
    它要拿这两张表比出有没有没保存的改动。

    改与删都按 `trade_id` 定位（store.apply_edits 里那三条铁律）：这张表显示的是
    **筛选后**的行，行号与整本日志对不上。列序只管显示（journal_ui.EDITOR_ORDER），
    交回来的表仍带全部字段。没填的格子显示 —（面板别处的缺值都是它），
    不显示出厂的灰字 "None"——那是个英文词，还像是真填了什么。

    **最前面那一列是每行一个删除按钮**（2026-11-18）。改之前它是一列叫「删除」的勾选框：
    勾了什么也不发生，要去点「保存修改」才真的删——用户的话：很奇怪的交互。现在点按钮
    先弹确认框、写明是哪一条，确认即删（_confirm_delete）。它在最前面而不是行尾：这张表
    十六列，行尾在屏幕外；这种表格只能把列钉在左边。按钮交回来的是"第几行"，所以把
    这一轮各行的记录号一起交给它（journal_ui.on_delete_click 说了为什么不能事后再按行号查）。

    **这个编辑器刻意不给 key**（2026-11-16 审查后改）。编辑器记的是"第几行的哪一格改成了
    什么"；带 key 的固定行数编辑器，身份只看表的结构（列、行数、索引），不看里面的值
    （streamlit 1.61 的文档原话：edits are matched by row position）。于是两个**行数相同**
    的筛选结果是同一个控件：在「买入」那两行里改了第一行、没保存，切到「卖出」
    （也是两行），那处改动就落在另一笔交易上，点保存改掉的是它——真实浏览器里复现过
    （当时用的是删除那一列的勾），落盘又是按 trade_id 写的，所以写得"很准"。
    不给 key 时身份取自表的**内容**：
    表里换了一批记录、或者保存 / 恢复之后内容变了，它就是一个新控件，旧改动自然作废；
    还是那几行时（在页面别处点了点什么），没保存的改动照常留着。
    """
    shown = journal_ui.editor_frame(filtered)
    edited = st.data_editor(shown, column_config=journal_ui.editor_columns(
                                [str(t) for t in shown["trade_id"]]),
                            column_order=journal_ui.EDITOR_ORDER,
                            num_rows="fixed", hide_index=True, width="stretch",
                            disabled=["trade_id"], placeholder=fmt.MISSING)
    return shown, edited


def _log_actions(filtered, edited) -> bool:
    """历史表下面的那一行按钮（2026-11-16 第二轮：操作按钮一律靠右，主按钮在最右）。
    返回这一轮有没有按下「保存修改」。

    从左到右：恢复上一版（连同它的确认框）、两个导出、保存修改。改之前它们分三处——
    保存在表格左下，撤销在更下面一行的左边，导出在那一行的右边；中间还夹着一行常驻的灰字，
    那句话现在在「恢复上一版」与导出按钮自己的悬停说明里。
    "还有几行没保存"那句提醒在这一行的**下面**、靠右：它是改了第一格才冒出来的，放在按钮上方
    会把整行按钮顶下去——1024 宽下「恢复上一版」正好落到「保存修改」原来的位置上
    （独立审查量的；确认框若恰好勾着，奔着保存去的那一下就成了恢复）。放在下面，哪个按钮都不动。
    `edited` 是 _editor 交回的 (喂进去的表, 交回来的表)；筛没了、没有表可改时是 None——
    那时没有「保存修改」，撤销与导出照留（导出的是只有表头的空表，与改之前一样）。
    """
    pending = ""
    if edited is not None:
        # 表里改了却没点保存，切到别的页再回来就没了——有没保存的改动时，按钮变成主按钮、
        # 下面一行写明改了几行。没有改动时只有一个安静的按钮。
        # 比的是交回来的表与喂进去的表（值），不是编辑器记的"哪些格子被碰过"。
        pending = journal_ui.pending_note(journal_ui.pending_changes(*edited))
    clicked = False
    # 一行靠右，里面分两组：撤销（确认框 + 按钮）一组，导出与保存一组。分组是为了窄屏：
    # 放不下时整组折到下一行（1024 宽、侧边栏展开时真机量过），而不是把确认框和它的按钮、
    # 或者两个导出拆在两行上。
    with st.container(horizontal=True, horizontal_alignment="right",
                      vertical_alignment="center"):
        with _button_group():
            _restore_block()
        with _button_group():
            _exports(filtered)
            if edited is not None:
                clicked = st.button("保存修改", key=journal_ui.SAVE_KEY, icon=":material/save:",
                                    type="primary" if pending else "secondary",
                                    help=guide.JOURNAL_EDIT_HINT)
    if pending:
        # 靠右对齐用容器来做（不用文字自己的对齐方式）：容器的对齐在元素树里看得见，测得出来。
        with st.container(horizontal=True, horizontal_alignment="right"):
            st.caption(pending, width="content")
    if clicked:
        # 不再传 trades：v0.5.0 起 save_edits 在锁里重新读整本日志，
        # 拿这一轮渲染时的旧快照整表重写会抹掉另一个标签页刚记的那一笔。
        journal_ui.save_edits(edited[1], path=ui.JOURNAL_PATH, today=date.today())
    return clicked


def _confirm_delete(asked: list[str], trades, edited) -> None:
    """点了历史表某一行的删除按钮之后：开确认框。**整页画完之后才调用**。

    `asked` 是点的那一条的记录号（点击那一轮由 journal_ui.on_delete_click 登记，
    page_journal_entry 一开头取走）；`trades` 是这一轮读到的整本日志，`edited` 是历史表。
    要问的东西在这里一次备齐，确认框里只管摆出来：
    - 要删的是哪一条——从这一轮读到的日志里按记录号取。取不到（另一个标签页刚删过）
      就不开框，说一句它已经不在了；
    - 表里**别的行**上没保存的改动——删完表里的内容变了，表格会换成一张新的，它们留不住，
      所以确认之后一起写进去，框里事先说明；
    - 上一版里还有、现在已经没有的记录——这次删除会把上一版换掉，它们就换不回来了。
    """
    rows = trades[trades["trade_id"].isin(asked)].to_dict("records")
    if len(rows) != len(asked):
        st.toast(guide.JOURNAL_DELETE_GONE)
        return
    pending = None
    if edited is not None:
        pending = journal_ui.changed_rows(*edited, skip=asked)
    _delete_dialog(rows, pending, journal_ui.only_in_backup(trades, ui.JOURNAL_PATH))


@st.dialog(guide.JOURNAL_DELETE_TITLE)
def _delete_dialog(rows: list[dict], pending, fading: list[dict]) -> None:
    """删除的确认框（2026-11-18）。写明要删的是**哪一条**——只写一句「确定删除吗」，
    点的人什么都核对不了。

    防误删靠两样：这个确认框，和删之前照旧留下的上一版（「恢复上一版」换得回来）。
    另两句只在适用时出现：表里另有没保存的改动（会一起保存）；上一版里还有现在已经没有的
    记录（这次删除之后就换不回来了——上一版只留一版，连着删第二条时就是这样）。
    两个按钮靠右、主按钮在最右。点哪个都整页重跑一次：重跑时不再调用本函数，框就关了；
    从右上角关掉则什么都不发生。删除本身在 journal_ui.delete_trades 里，结论留给下一轮说。
    """
    st.write(guide.JOURNAL_DELETE_ASK)
    st.html(theme.record_lines([journal_ui.describe_trade(row) for row in rows]))
    if pending is not None and len(pending):
        st.write(guide.JOURNAL_DELETE_ALSO_SAVES.format(n=len(pending)))
    if fading:
        st.write(guide.JOURNAL_DELETE_FADING.format(n=len(fading)))
        st.html(theme.record_lines([journal_ui.describe_trade(row) for row in fading]))
    st.caption(guide.JOURNAL_DELETE_UNDO)
    with st.container(horizontal=True, horizontal_alignment="right"):
        cancel = st.button(guide.JOURNAL_DELETE_CANCEL, key=journal_ui.DELETE_CANCEL_KEY)
        confirm = st.button(guide.JOURNAL_DELETE_OK, key=journal_ui.DELETE_OK_KEY,
                            type="primary", icon=":material/delete:")
    if confirm:
        journal_ui.delete_trades([row["trade_id"] for row in rows], path=ui.JOURNAL_PATH,
                                 today=date.today(), pending=pending)
    if confirm or cancel:
        st.rerun()


def _button_group():
    """按钮行里不拆开的一组：随内容宽、组内不换行地横排（见 _log_actions）。"""
    return st.container(horizontal=True, width="content", vertical_alignment="center")


def _restore_block() -> None:
    """「恢复上一版」：用自动备份换回上一版（2026-09-05）。

    这是"易用的功能"替代"知道路径"的一例：以前这里写着备份文件叫什么、在哪，
    要使用者自己去文件系统里换文件；现在按钮就在旁边。两步（勾确认 + 点按钮）
    与删除同一套纪律——这份记录不可再生。没有备份时按钮禁用并说明为什么。

    落盘走 `on_click` 回调而不是 if 语句：回调里可以顺手把确认框复位
    （运行期间改一个已渲染控件的 session_state 会被 Streamlit 拒绝，回调里不会），
    否则确认框一直勾着，手一抖再点一次就又换回去了。
    """
    has_backup = store.backup_path(ui.JOURNAL_PATH).exists()
    # 确认框在前、按钮在后（先勾再点，从左到右）。两样放进调用方给的那一组里。
    confirmed = bool(st.session_state.get(journal_ui.RESTORE_CONFIRM_KEY))
    st.checkbox("确认用上一版覆盖当前记录", key=journal_ui.RESTORE_CONFIRM_KEY,
                disabled=not has_backup)
    st.button("恢复上一版", key=journal_ui.RESTORE_KEY, icon=":material/undo:",
              disabled=not (has_backup and confirmed),
              help=guide.JOURNAL_RESTORE_HELP if has_backup
              else "还没有上一版备份：第一次保存之后才会有。",
              on_click=journal_ui.restore_backup, kwargs={"path": ui.JOURNAL_PATH})


def _exports(filtered) -> None:
    """导出区：**数据源只有一个**，就是上面那张表（所见即所得，设计 §5.5）。

    两个按钮放进调用方给的那一组里（与「保存修改」同组）；说明在悬停提示里（guide.JOURNAL_EXPORT_HELP）。
    """
    for payload in journal_ui.export_payloads(filtered, date.today()):
        # 一律走顶层 st.*（而不是 容器.download_button）：项目里所有
        # "这个控件真的收到了什么"的测试都靠 monkeypatch 顶层 st.xxx，
        # 而 DeltaGenerator 上的同名方法绕过那一层，断言会静默变成空跑。
        if payload["error"]:
            st.caption(payload["error"], width="content")
        else:
            st.download_button(payload["label"], data=payload["data"],
                               file_name=payload["file_name"],
                               mime=payload["mime"], key=payload["key"],
                               icon=payload["icon"],
                               help=guide.JOURNAL_EXPORT_HELP[payload["kind"]])
