"""「持仓与盈亏」页（v0.3.1 §1，原「交易日志」页的持仓/盈亏/来源那一半）。

「记账」页管**写**，本页管**读**：整本日志算出来的当前持仓、已实现盈亏与来源对比。
页面函数从 app/pages_journal.py 原样搬来（M1 的拆页**逻辑不改只挪**）；排版按
设计 §1：三张表收进 st.tabs（当前持仓 / 平仓明细 / 来源对比），不再纵向全部铺开。
顶部是 M2 的仪表盘（设计 §2）：指标块 2×3 + 累计盈亏曲线 + 持仓占比（各占一整行，
2026-11-18；持仓占比可以在占比条与环形图之间切换），来源对比图在来源 tab 里。数字口径全在 journal_ui.dashboard_metrics 与 quant.journal.analytics
（纯函数，手算单测钉住），本页只做排版与转述。

两条纪律沿自 v0.3.0 §5：

1. **持仓与盈亏永远按整本日志算，不受「记账」页筛选的影响**。拆页把这条从约定
   变成了结构：本页根本不读筛选控件的 session_state。
2. **账上对不上的地方持续可见**（设计 §3）：每轮重算、列在所有数字**之前**，补齐了才消失，
   不是记完就沉没的 toast——卖出找不到买入、读不出来的行，意味着下面的数少算了一块。
   2026-11-18 起它们是一块安静的正文色的字（theme.notice）而不是黄框：用户看着两条黄框问
   「这两句话啥意思？还搞得那么显眼」——句子不说是哪只票、全是算盈亏那一层自己的词，
   框又比下面的数字还抢眼。"持续可见"留着，"显眼"换成"看得懂、知道去哪处理"。

页面上不再有常驻的说明（2026-11-18）：原先页顶那个折叠区「这些数字怎么算的、已知局限」
并进了页名后面的帮助圆点（guide.JOURNAL_REPORT_INTRO）。
"""
from __future__ import annotations

from collections import Counter

import streamlit as st

import guide
import journal_ui
import pool
import theme
import ui
from quant.journal import analytics, pnl, store
from quant.report import charts, minicharts


def page_journal_report() -> None:
    ui.page_head("持仓与盈亏")
    try:
        trades = store.load_trades(ui.JOURNAL_PATH)
    except RuntimeError as e:
        # 日志文件损坏时整页只剩这一句：这份文件不可再生，自愈办法是 git 找回，
        # 绝不是"删掉重建"（store 抛的那句话已经这么写，原样转述）。
        st.error(str(e))
        return

    report = pnl.compute_pnl(trades)   # 永远按整本日志算（见模块 docstring 第 1 条）
    _ledger_notices(report)
    _dashboard_section(report, trades)
    positions_tab, matches_tab, source_tab = st.tabs(["当前持仓", "平仓明细", "来源对比"])
    with positions_tab:
        _positions_section(report)
    with matches_tab:
        _matches_section(report)
    with source_tab:
        _by_source_section(report)


# ---------------------------------------------------------------- 要你看一眼的两种话（2026-11-18）

def _ledger_notices(report: pnl.PnlReport) -> None:
    """账上对不上的地方：一块带边框的区域，左边一条一句、右端一个去「记账」的链接。

    排在所有数字**之前**（模块 docstring 第 2 条）：收进某个 tab 就被藏起来了。
    句子由算盈亏的那一层给（pnl 的一致性标记，点名哪只票、哪一天、怎么办），这里只管摆。
    几条共用一个链接：它们的去处是同一页，一条一个就是一列一模一样的按钮。
    没有对不上的地方时整块不出。

    **一模一样的句子只写一次，但要说有几笔**。句子里已经有票、日期和股数，还一模一样就是
    两笔不同的记录碰巧长得一样（同一天两笔同样股数的卖出）。只写一次而不说几笔的话，
    补上其中一笔之后那句话原样留着，看着像补了没用（独立审查在真机上试出来的）。
    """
    counts = Counter(i.message for i in report.inconsistencies)     # 按第一次出现的先后
    if not counts:
        return
    lines = [message if n == 1 else guide.JOURNAL_SAME_PROBLEM.format(message=message, n=n)
             for message, n in counts.items()]
    with st.container(border=True):
        _notice_row("".join(theme.notice(line) for line in lines), page="journal",
                    label=guide.JOURNAL_FIX_LINK, icon=":material/edit_note:")


def _pool_notice(report: pnl.PnlReport) -> None:
    """持仓里有票**不在信号池**时，在持仓表上面提醒一句。

    项目自己把这件事称为"最容易踩的坑"（说明页原话：不加进信号池的票没有任何人管它的卖出），
    而唯一能发现它的页面——这一页知道你持着什么——之前从不看信号池。
    典型剧本：扫描报出买入信号，你买了、记了账，但忘了点 ＋ 加进池子；此后「信号跟踪」
    每天替你盯的是**另一批**标的，而它不会报错。

    **在持仓表上面，不在页顶**（2026-11-18）：它说的是持仓，不影响这一页的任何数字；
    自己拿主意买的票不在池子里是常态，页顶天天亮一条等于没有。为什么与要不要管在圆点里。
    只看**手里真有的**（股数 > 0）：pnl 为了挂一致性标记会留一行 0 股的空壳，那不是持仓。

    配置读不到时**不提示**（不猜、也不在盈亏页上再报一次配置错，与
    pages_signals 里那条降级同一个路子）。跳转用 ui.page_ref 而不是 import
    dashboard —— 后者会把主脚本再执行一遍（见 ui.py 模块 docstring）。
    """
    try:
        in_pool = set(pool.current(ui.CONFIG_PATH))
    except pool.CONFIG_ERRORS:
        return
    outside = [f"{p.name}（{p.symbol}）" if p.name else p.symbol
               for p in report.positions if p.shares > 0 and p.symbol not in in_pool]
    if not outside:
        return
    names = "、".join(outside)
    if not names.endswith("）"):
        names += " "        # 全角括号自己带着空；以代码结尾时与后面的字之间补一个空格
    _notice_row(theme.notice(guide.JOURNAL_OUTSIDE_POOL.format(names=names),
                             help=guide.JOURNAL_OUTSIDE_POOL_HELP),
                page="universe", label=guide.JOURNAL_OUTSIDE_POOL_LINK, icon=":material/list:")


def _notice_row(html: str, *, page: str, label: str, icon: str) -> None:
    """一行「要你看一眼」的话 + 右端去处理它的链接。

    按比例的两栏（与各页顶部任务条的行同一个做法），不用靠右的横向容器：那种容器里文字元素
    按自己的内容要宽度，一句话长到要折行时会独占整行，把链接挤到下一行（真机上看到的）。
    右栏窄屏时只有一百来像素，所以链接的字要短——只说去哪一页，做什么在左边那句话里。
    竖直居中：左边折成两三行时链接不贴着顶。
    """
    body, action = st.columns([4, 1], vertical_alignment="center")
    body.html(html)
    with action.container(horizontal=True, horizontal_alignment="right"):
        st.page_link(ui.page_ref(page), label=label, icon=icon)


# ---------------------------------------------------------------- 仪表盘（v0.3.1 §2）

def _dashboard_section(report: pnl.PnlReport, trades) -> None:
    """指标块 2×3 + 曲线与占比两张图（各占一整行，有哪张画哪张）。留在 tabs 之外：
    这几个数是本页的结论，收进某个 tab 就得多点一下才看得到。

    两条口径纪律（数字本身在 journal_ui / analytics 里，有手算单测）：
    - 无市价的持仓**不按 0 计入**市值与浮动，如实注明有几只没算进来；
    - 曲线是**累计已实现盈亏（元）**，不是收益率（设计 §2.6）：日志不记本金
      与出入金，任何收益率的分母都只能编造——这条取舍就注在图旁边。
    """
    # 口径说明（为什么是元、与回测同口径、没有市价的怎么算）在小标题后面的帮助圆点里：
    # 2026-11-16 第二轮之前它们是指标块上下常驻的三段灰字。
    st.html(theme.section("仪表盘", help=guide.JOURNAL_PNL_HINT, help_wide=True))
    prices = {p.symbol: journal_ui.latest_price(p.symbol, ui.CACHE_DIR)
              for p in report.positions}
    metrics, unpriced = journal_ui.dashboard_metrics(report.summary,
                                                     report.positions, prices)
    for start in (0, 3):
        for col, (label, text, color) in zip(st.columns(3), metrics[start:start + 3]):
            col.html(theme.metric(label, text, color))
    if unpriced:
        # 这一句是事实、会影响上面的数怎么读，所以留在明面上；为什么与怎么办在圆点里。
        st.caption(guide.JOURNAL_UNPRICED.format(n=unpriced))

    # 曲线与持仓占比各占一整行，有哪样画哪样（2026-11-18）。原先固定两栏并排：还没有平仓记录时
    # 左半边只有一句话，下面空着三百多像素——用户截图圈出来问「为什么左边要空出一大块」。
    # 初稿是"两样都有才并排"，独立审查在 1024 宽下量出来不行（半幅宽的一栏里留给条的地方只剩十几像素）。
    # 曲线在上（历史）、占比在下（紧挨着下面的持仓表，说的都是眼下手里有什么）。
    # 空态不画空图（设计 §2.2）：一张空坐标系没有任何信息量，还像出了错。
    # 缺曲线时留一句话（它说的事别处没说）；没有持仓时什么都不留——紧挨着的「当前持仓」标签页里
    # 已经写着，同一屏不说两遍。
    points = analytics.cumulative_realized(trades)
    rows = analytics.position_weights(report.positions, prices)
    if points:
        st.plotly_chart(charts.journal_cum_pnl_chart(points), width="stretch")
    if rows:
        _weights_block(rows)
    if not points:
        st.caption(guide.JOURNAL_NO_CURVE)
    # 「为什么是元而不是收益率」在「仪表盘」小标题的帮助圆点里（guide.JOURNAL_PNL_HINT）；
    # 完整理由留在本函数的 docstring 里，给下一个改代码的人。


def _weights_block(rows: list[dict]) -> None:
    """持仓占比：小标题 + 右端的画法切换，下面是占比条或环形图（2026-11-18）。

    用户先问「用饼图会不会更直观」。对"看占比"来说确实是：原先那张条形图的条长是按最大的那只比的，
    最长的一根永远顶满整行，占 45% 的那只看着像 100%。但环形图持仓一多就不好读，所以两种都留、让人自己选：
    - **占比条**（默认）：一行一只，条的满格是全部持仓；持仓再多也只是多几行。
    - **环形图**：整圈是全部持仓；最多单独画五只（同一个琥珀只分得出五档深浅），排在后面的并成「其他」。

    两种都是页面自己的 HTML（theme.weight_bars / weight_donut），不是 plotly——字是页面的字、窄了自己让，
    不会再有"标注画到图外被裁"。无市价的持仓按成本顶上并标「按成本」（与指标卡刻意相反：
    占比漏一只会让其余标的虚高，见 analytics 的模块 docstring）。

    选了哪种记在这次会话里（`persist_state="session"`）：去别的页转一圈回来还是它，
    刷新页面才回到默认。标题与切换是一个横向容器：标题的字短，占剩下的地方，切换是它自己的宽度、靠右。
    """
    forms = guide.JOURNAL_WEIGHTS_FORMS
    with st.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center"):
        st.html(theme.subhead(guide.JOURNAL_WEIGHTS_TITLE, help=guide.JOURNAL_WEIGHTS_HELP))
        form = st.radio(guide.JOURNAL_WEIGHTS_FORM_LABEL, list(forms), format_func=forms.get,
                        horizontal=True, label_visibility="collapsed", width="content",
                        key=journal_ui.WEIGHTS_FORM_KEY, persist_state="session")
    # 必须是 if/else 语句：裸三元会被 streamlit 的 magic 整条包进 st.write。
    if form == "donut":
        ramp = theme.PRIMARY_RAMP
        slices = analytics.fold_weights(rows, journal_ui.WEIGHTS_DONUT_KEEP)
        # 颜色档 = 按大小排在第几；并出来的「其他」与排到色板之外的那一只交回 None，画成灰。
        steps = journal_ui.weight_steps(slices, len(ramp))
        uri = minicharts.donut([(s["weight"], theme.MUTED if step is None else ramp[step])
                                for s, step in zip(slices, steps)])
        st.html(theme.weight_donut(uri, [(step, *_weight_texts(s)) for s, step in zip(slices, steps)]))
    else:
        lines = []
        for row in rows:
            name, pct, value, flag = _weight_texts(row)
            lines.append((name, row["weight"], pct, value, flag))
        st.html(theme.weight_bars(lines))


def _weight_texts(row: dict) -> tuple[str, str, str, str]:
    """占比的一行写成字：名称、百分比、金额、标记。并出来的那一块叫「其他 n 只」；
    按成本顶上的（或者并进去的里面有这样的）标「按成本」，否则标记是空串。"""
    if row.get("others"):
        name = guide.JOURNAL_WEIGHTS_OTHERS.format(n=row["others"])
    else:
        name = f"{row['symbol']} {row['name']}".strip()
    flag = guide.JOURNAL_WEIGHTS_BY_COST if row["by_cost"] else ""
    return name, f"{row['weight']:.1%}", f"{row['value']:,.0f} 元", flag


def _positions_section(report: pnl.PnlReport) -> None:
    _pool_notice(report)
    ui.data_table(journal_ui.positions_table(report.positions, ui.CACHE_DIR),
                  journal_ui.positions_columns(),
                  "当前没有持仓（还没记过买入，或者都已经卖光了）。")


def _matches_section(report: pnl.PnlReport) -> None:
    # 标签页里的表没有小标题可挂帮助圆点：表格上方一行很短的「读表说明 ?」（ui.data_table 的 help）。
    ui.data_table(journal_ui.matches_table(report.matches),
                  journal_ui.matches_columns(),
                  "还没有已平仓的交易。", help=guide.TABLE_HINTS["closings"],
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
                  "还没有已平仓的交易，暂时无从对比。", help=guide.TABLE_HINTS["by_source"])
