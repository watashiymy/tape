"""「任务控制台」页（v0.2.0 §4.1）：在本机起三个入口脚本并看进度、日志与结果。

进程管理与日志解析全在 src/quant/runner/（可单测），本模块只负责渲染；
启停三个落点在 app/ui.py（各页顶部的任务条与本页卡片共用同一套闸门）。

**版式（2026-11-16 改版）**：每个任务一张**整幅宽**的卡片，自上而下就是流程的顺序——
全市场扫描 → 人工那一步 → 信号跟踪，「研究工具」区另放回测。卡片上常驻的只有四样：
标题与状态、一句「做什么 / 什么时候跑」、最新结果是哪天的、一个主按钮。
参数、日志、结果表都收进折叠区；只有正在跑或没跑成的时候，进度与日志才摊开。

改之前是 v0.5.0 的两栏并排，毛病有四个，都是真机上看出来的：
- 「开始 / 停止 / 重跑」三个按钮等权重，参数框摆在按钮前面像必填——第一次来的人不知道先点哪个；
- 扫描结果表挤在半幅宽的卡片里，说明页说"最该看"的「放量倍数」一列被挤出了可视区；
- 几个月前那次运行的日志与一条 100% 的进度条一直占着版面，两张卡片高度差出好几屏，
  「研究工具」被推到第三屏；
- 同一条流程在一屏里画了三遍（页顶的流程条、人工步骤那一行、卡片之间的箭头）。
设计与真机验收：docs/superpowers/specs/2026-11-16-console-redesign-design.md。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

import guide
import pool
import theme
import ui
from quant.report import fmt
from quant.runner import jobs, process, progress, steps, view
from quant.strategy import strategy_label


def _param_widgets(job: jobs.Job) -> dict:
    """按 schema 生成控件。控件本身就是第一道白名单（策略是下拉、日期是日历、
    limit 有上下界），值再交给 build_argv 复核一遍。

    每个控件都挂 `help=`（§3.2 就地帮助）：四个控件的值都能留空，而留空的语义
    各不相同（全量 / 最近交易日 / 全部策略）。文案在 app/guide.py，键漏了会 KeyError——
    宁可当场炸，也不要静默渲染一个没有解释的输入框。
    """
    values: dict = {}
    for spec in job.params:
        key = f"{job.name}_{spec.name}"
        tip = guide.PARAM_HELP[(job.name, spec.name)]
        if spec.kind == "int":
            values[spec.name] = st.number_input(
                spec.label, min_value=1, max_value=spec.max_value, value=None,
                step=1, key=key, help=tip)
        elif spec.kind == "date":
            values[spec.name] = st.date_input(spec.label, value=None, key=key,
                                              help=tip)
        elif spec.kind == "choice":
            # 显示中文显示名、底层传内部键（argv 与 REGISTRY 认的是键）。
            # 「全部」与将来非策略的 choice 值不在注册表里，strategy_label
            # 对未知键原样返回，正好原样显示。
            picked = st.selectbox(spec.label, ("全部", *spec.choices), key=key,
                                  format_func=strategy_label, help=tip)
            values[spec.name] = None if picked == "全部" else picked
        elif spec.kind == "flag":
            values[spec.name] = st.checkbox(spec.label, value=False, key=key,
                                            help=tip)
    return values


def _result_table(path_str: str, config: dict, hint: str,
                  color_columns: tuple[str, ...]) -> None:
    """扫描/信号的产物 CSV。symbol 必须按字符串读，否则 000333 变成 333。"""
    try:
        df = pd.read_csv(ui.resolve(path_str), dtype={"symbol": str})
    except (ValueError, OSError):
        st.warning("结果暂时读不出来，可能还在写入；稍后刷新试试。")
        return
    # 策略列只在显示层换中文显示名，磁盘上的 CSV 照旧存键
    ui.data_table(fmt.for_display(df), config, "当次无新信号",
                  hint=hint, color_columns=color_columns)


def _result_scan(path_str: str) -> None:
    """当次扫描的结果表，标题旁带范围徽标（v0.2.4 设计 §2.3）。

    控制台这条路径尤其需要它：试跑按钮就在这张卡片上，一次试跑的结果与一次
    全量扫描的结果长得一模一样——尤其是两者都"无新信号"的时候。
    路径取自日志里的 `已保存:` 行，meta 就在它旁边。
    """
    st.html(theme.section("本次扫描结果", ui.scan_scope_pill(ui.resolve(path_str))))
    _result_table(path_str, fmt.scan_column_config(), guide.TABLE_HINTS["scan"],
                  ui.SCAN_COLOR_COLUMNS)


def _result_signal(path_str: str) -> None:
    """信号跟踪 CSV 的列与扫描**不是**同一套（没有 name/成交额/放量倍数），
    列配置也得各用各的，否则 action / close 连中文标签都没有。"""
    _result_table(path_str, fmt.signal_column_config(),
                  guide.TABLE_HINTS["signal"], ())


def _result_metrics(dir_str: str) -> None:
    """回测产物：每个策略一个报告目录，各出一组指标卡（卡片整幅宽，per_row 用默认的 4）。"""
    try:
        metrics = json.loads((ui.resolve(dir_str) / "metrics.json").read_text(encoding="utf-8"))
        if not isinstance(metrics, dict):
            # 文件被改成了别的形状（一个列表、一个数）：json 读得出来，指标卡却会在取值时出错，
            # 而这里的异常会让整张卡片连同它后面的页面都渲染不出来。当作读不出来处理。
            raise ValueError("指标文件不是一组指标")
    except (ValueError, OSError):
        st.warning("结果暂时读不出来，可能还在写入；稍后刷新试试。")
        return
    # 目录名是给机器看的（策略键 + 时间戳）；这里显示「策略 时间」，同「回测报告」页的下拉框。
    st.caption(fmt.run_label(Path(dir_str).name))
    ui.metric_grid(metrics)


_RESULT_RENDERERS = {"scan_csv": _result_scan, "signal_csv": _result_signal,
                     "backtest_run": _result_metrics}


def _card_title(job: jobs.Job, pill_html: str = "") -> None:
    """卡片标题行：衬线标题 + 帮助圆点 + 状态 pill（§3.2）。

    圆点紧跟标题文字，悬停或 Tab 键选中出说明（2026-09-29 起取代带下拉箭头的
    st.popover——用户看了以为是选择框）。状态文件损坏的那条分支也走这里——那时候没有按钮，
    人却最需要知道这个任务是干什么的，所以说明不能挂在按钮上。
    卡片整幅宽、标题都在左侧，提示框一律向右展开（两栏时代右栏那张要向左，已不需要）。
    """
    st.html(theme.task_head(job.label, pill_html, help=guide.JOB_HELP[job.name],
                            help_wide=True))


def _log_tail(text: str) -> None:
    """摊在卡片上的日志尾部：固定高度，**自动滚到最底**。

    改之前是一个固定高度的 st.code，从头显示——30 行的尾部只看得见开头九行，而要看的
    恰恰是最后一行（运行中是最新进度，没跑成是 traceback 的那句结论），得自己往下滚。
    外层容器给固定高度与自动滚动，里面的 st.code 随内容长。
    """
    with st.container(height=ui.LOG_BOX_HEIGHT, autoscroll=True, border=False):
        st.code(text or guide.CONSOLE_LOG_EMPTY, language="text", height="content")


def _log_full(text: str) -> None:
    """折叠区里的日志全文：固定高度滚动，不给 height 的话几十行日志会把版面顶飞。"""
    st.code(text or guide.CONSOLE_LOG_EMPTY, language="text", height=ui.LOG_BOX_HEIGHT)


def _run_panel(job: jobs.Job, state: process.RunState, log: str,
               prog: progress.Progress) -> None:
    """上一次（或这一次）运行的呈现，按状态分三种——该摊开的摊开，该收起的收起。

    判断与格式化全在 quant.runner.view（可单测），这里只把字符串塞进 st.*。
    status 必须一路带到文案里：解析器只看得见日志，看不见进程死活——被停掉的扫描
    日志最后一行仍是 [1800/3010]，不告诉它状态就会继续喊"扫描中，预计剩余 ~8分钟"。
    """
    head, detail = view.progress_lines(prog, view.elapsed_seconds(state),
                                       status=state.status)
    ratio = view.progress_ratio(prog)
    if state.status == process.SUCCESS:
        # 成功：一行就够。一条 100% 的进度条什么都没说，日志也没人要看——都收起来。
        st.caption(guide.CONSOLE_LAST_RUN.format(
            summary="，".join(x for x in (head, detail) if x)))
        return
    if state.status == process.RUNNING:
        # 进度 + 停止那一行与各页顶部的任务条共用（ui.running_row）。停止按钮在进度条旁，
        # **不占**标题行主按钮的位置：任务恰好跑完的那一刻，光标底下的按钮不许从「停止」
        # 换成「开始」——点下去就是又一趟十几分钟的扫描。
        detail = ui.running_row(state, prog, stop_key=f"stop_{job.name}")
        # "关掉浏览器也不会中断"只在动作现场说：没有任务在跑时这句话没有任何可做的事。
        st.caption("。".join(x for x in (detail, guide.CONSOLE_BACKGROUND_NOTE) if x))
    else:
        # 已停止 / 失败：进度条照画，这时它是"停在哪儿"的存档（文案已由 view 换成终态词、
        # 不带 ETA）。**不存在断点续跑**——重跑从第 1 只开始；存档的价值只是
        # "我上次跑到哪、值不值得再等一轮"。
        if ratio is not None:
            st.progress(ratio, text=head)
        else:
            st.caption(head)
        if detail:
            st.caption(detail)
        # 终止且无产物时补一句（v0.5.0）：detail 那行会写着「信号 43 条」，
        # 而那 43 条一条都没保存——不说清楚，屏幕上唯一的数字就指向一份不存在的文件。
        if note := view.no_output_note(prog, status=state.status, job_name=job.name):
            st.caption(note)
    _log_tail(progress.tail(log, ui.LOG_TAIL_LINES))
    with st.expander(guide.CONSOLE_FULL_LOG, type="compact"):
        _log_full(log)


def _render_card(job_name: str) -> str | None:
    """一张任务卡片。返回当前"谁在跑"（None=全空闲，ui.BUSY_UNKNOWN=读不出来），
    外层据此决定要不要整页重跑。

    边框是原生的 st.container(border=True)：颜色与圆角走 config.toml，不写一行 CSS。
    """
    with st.container(border=True):
        return _card_body(jobs.JOBS[job_name])


def _card_body(job: jobs.Job) -> str | None:
    name = job.name
    try:
        state = process.read_state(name, ui.RUNS_DIR)
        busy = process.any_running(ui.RUNS_DIR)
    except RuntimeError as e:
        # 状态文件损坏必须响亮失败：静默当"空闲"会放开互斥，两个 baostock 会话互踢。
        # 此时一个按钮都不给（fail-safe）——互斥状态不可知，正是最不该让人点开始的时候。
        # 交回「不知道」而不是 None：None 是"全空闲"，外层会据此以为任务跑完了、请求整页重跑，
        # 而重跑之后还是读到同一个坏文件——页面原地打转（见 ui.busy_changed）。
        _card_title(job)
        st.error(str(e))
        return ui.BUSY_UNKNOWN
    running = state is not None and state.status == process.RUNNING
    disabled, notice = view.start_button_state(busy, name)

    # —— 第一行：标题 + 状态 ………… 主按钮。一张卡片一个主按钮（琥珀色）；
    #    全市场扫描多一个「试跑」——说明页一直建议第一次先扫前 300 只，过去得自己把
    #    300 敲进输入框，现在是一个按钮。点击先记下：要等下面的参数控件渲染出来才知道按什么跑。
    title_col, action_col = st.columns([1, 1], vertical_alignment="center")
    with title_col:
        _card_title(job, theme.pill(*view.status_pill(state)))
    trial = False
    with action_col.container(horizontal=True, horizontal_alignment="right"):
        if name == "market_scan":
            trial = st.button(guide.CONSOLE_TRIAL_LABEL, key=f"trial_{name}",
                              disabled=disabled, help=guide.CONSOLE_TRIAL_HELP)
        start = st.button(guide.CONSOLE_START[name], key=f"start_{name}", type="primary",
                          icon=":material/play_arrow:", disabled=disabled)

    # —— 标题下两行：做什么 / 什么时候跑；最新结果是哪天的。
    # **必须接住 RuntimeError**：scan_status 会读扫描的 meta，而 load_meta 对损坏文件是
    # 响亮抛错（那是数据层的设计，不改）。这里不接的话，异常从 fragment 冒到 exec_code
    # 会让整页**提前中止**——这张卡片之后的一切都不再渲染，包括正在跑的那趟扫描的停止按钮。
    # 口径照抄 ui.scan_scope_pill：如实说，但别把页面打没。
    status_text = ""
    try:
        if (status := ui.step_status(name)) is not None:
            status_text = status.text
    except RuntimeError as e:
        st.warning(str(e))                   # 状态读不出来就不写那一行——不许顺手补一句"还没有结果"
    st.html(theme.task_meta(guide.CONSOLE_LEAD[name], guide.CONSOLE_STATUS_KEY, status_text))
    # 点名是谁在跑，否则灰按钮无从解释。正在跑的那张自己就摊着进度，不必对自己再说一遍。
    if notice and not running:
        st.caption(notice)

    # —— 版面上的先后是：按钮下方的提示 → 上一次运行 → 更多选项 → （成功后）日志与结果。
    #    但**处理的先后**不能照这个来：点击必须在"参数一到手"时就处理。初稿是记下点击、
    #    等整张卡片渲染完再起任务——中间任何一处抛错（读日志、解析、渲染结果），这次点击
    #    就无声无息地丢了。所以先把靠上的两块位置占好，内容等参数到手之后再回头填。
    notes = st.container()
    panel = st.container()
    # 参数九成的时候都该留空，摆在按钮前面却像必填，所以收进折叠区；重跑也在里面：
    # 它沿用的是**上一次**的参数，不是主路径。折叠而不是"点了才渲染"：控件一直在页面上，
    # 填过的值不会因为收起而丢。
    options = st.expander(guide.CONSOLE_OPTIONS, type="compact")
    with options:
        params = _param_widgets(job)

    with notes:
        # 折叠区默认收起：填了什么得在卡片明面上写出来，否则主按钮按一组看不见的参数跑
        # （上午试跑时填的只数，下午点「开始扫描」以为是全量）。全留空时这一行不出现。
        if summary := view.params_summary(name, params):
            st.caption(guide.CONSOLE_PARAMS_SET.format(summary=summary))
        # 启动失败的那句话也落在这里（按钮正下方），而不是卡片最底下、摊开的结果表后面。
        if start:
            ui.start_job(name, params)
        if trial:
            # 按钮上写着试跑多少只，扫的就得是多少只：输入框里另填的只数不许悄悄顶掉它；
            # 基准交易日照样带上（补扫历史某天时也想先试跑）。
            ui.start_job(name, {**params, "limit": jobs.TRIAL_LIMIT})

    with options:
        # 要重跑什么直接写出来（v0.5.0 是悬停提示）：上次的参数只存在磁盘那份 JSON 里，
        # 本机那份 argv 就带着 --date，点一下白扫昨天十几分钟。
        st.caption(view.rerun_hint(name, state.argv) if state is not None
                   else guide.CONSOLE_RERUN_NEVER)
        if st.button(guide.CONSOLE_RERUN, key=f"rerun_{name}", type="tertiary",
                     icon=":material/replay:", disabled=disabled or state is None):
            ui.rerun_job(name, state)

    if state is None:
        return busy
    log = process.read_log(state)
    prog = job.parser(log)
    with panel:
        _run_panel(job, state, log, prog)
    if state.status == process.SUCCESS:
        with st.expander(guide.CONSOLE_LOG, type="compact"):
            _log_full(log)
        if prog.outputs:
            # 今天跑完的摊开（人就是来看它的），更早的收起（那是存档）。
            with st.expander(guide.CONSOLE_RESULT, type="compact",
                             expanded=view.finished_on(state, date.today())):
                for out in prog.outputs:
                    _RESULT_RENDERERS[job.result_kind](out)
    return busy


@st.fragment(run_every=ui.AUTO_REFRESH_S)
def _card_live(job_name: str, was_busy: str) -> None:
    """有任务在跑：每 2 秒只重跑这张卡片（整页重跑会把参数控件与滚动位置一起抖没）。"""
    if ui.busy_changed(_render_card(job_name), was_busy):
        st.rerun()   # 跑完了/被停了 → 整页重跑，摘掉轮询并解禁另外两个开始按钮


@st.fragment
def _card_idle(job_name: str, was_busy: str) -> None:
    """全空闲：**不设** run_every，否则面板会每 2 秒无谓地重跑三张卡片。"""
    _render_card(job_name)


def _card_for(busy: str | None):
    return _card_live if busy else _card_idle


# ---------------------------------------------------------------- 排班（v0.5.0 §6）
#
# 真实的闭环是：全市场扫描（发现）→ 人工研究 → 加进信号池 → 信号跟踪（跟踪），
# 其中「加进信号池」不是可运行的任务（2026-09-14 起只占一行，不是卡片）、
# 「回测」根本不在这条链上。
#
# 刻意**不加 ①②③ 编号**：说明页的命令行一节已经在用一套编号，README §2 的
# 「四个入口」是第三套。再引入一套流水线编号，用户会在两页之间看到互相矛盾的数字，
# 而没有任何测试会红——正是本项目最忌讳的静默不一致。顺序由**位置**表达：
# 2026-11-16 起卡片自上而下排，版面本身就是那张流程图。
_PIPELINE = ("market_scan", "daily_signal")     # 链上的两个**可运行**任务
_TOOLS = ("backtest",)                          # 不在链上的研究工具
_ELSEWHERE = ("market_breadth",)                # 控件在别的页——「市场概览」（app/pages_home.py）


def _check_roster() -> None:
    """每个任务都得在某个区里有位置（控制台自己的两区，或者别的页）。

    响亮失败而不是静默漏渲染：将来加了第四个任务却忘了排版，那张卡片会从页面上
    **消失**而没有任何报错——用户只会以为这个功能没做。
    """
    placed = set(_PIPELINE) | set(_TOOLS) | set(_ELSEWHERE)
    if placed != set(jobs.JOBS):
        raise RuntimeError(
            f"控制台排班与 jobs.JOBS 对不上：漏了 {sorted(set(jobs.JOBS) - placed)}，"
            f"多了 {sorted(placed - set(jobs.JOBS))}")


def _first_run_callout() -> None:
    """页顶的首次引导：从没跑过任何任务时，点名先点哪个按钮（2026-11-16）。

    跑过一次就消失——常驻的引导等于噪声（同首页那条去「使用说明」的链接、同侧栏的安全警告）。
    只看有没有状态文件、不读内容（process.has_ever_run）：状态文件坏了也算"跑过"，
    那一刻该显示的是卡片上的错误，不是新手引导。只问**这一页上的任务**：首页那个收盘数据
    任务跑过，不等于这里的任务跑过——在首页点过「开始准备」的新手到了这一页照样需要它。
    """
    if process.has_ever_run(ui.RUNS_DIR, _PIPELINE + _TOOLS):
        return
    tip, link = st.columns([4, 1], vertical_alignment="center")
    tip.html(theme.callout(guide.CONSOLE_FIRST_RUN))
    with link.container(horizontal=True, horizontal_alignment="right"):
        st.page_link(ui.page_ref("guide"), label=guide.CONSOLE_FIRST_RUN_LINK,
                     icon=":material/menu_book:")


def _manual_step_strip() -> None:
    """中间那步「加进信号池」：一行，不是一张卡（2026-09-14）。

    v0.5.0 把它做成与两张任务卡片同形的第三张卡片摆在正中间。用户的话：这一步在
    控制台上进行不了任何操作，却占了相当大的面积、还在页面正中间，整个布局很奇怪。
    它在这一页唯一的价值是三样：说清这是人工动作、现在盯着几只、去哪两页操作——
    一行放得下。它夹在两张任务卡片之间（上下各一个向下的箭头），位置就是它在流程里的位置。
    详细说明挂在「去增删信号池」那个链接的悬停提示上。
    """
    try:
        symbols = pool.current(ui.CONFIG_PATH)
    except pool.CONFIG_ERRORS:
        symbols = None
    status = steps.pool_status(symbols)
    text, links = st.columns([3, 2], vertical_alignment="center")
    text.html(theme.manual_row("人工步骤",
                               f"加进信号池：{status.text}。{guide.PIPELINE_MANUAL_NOTE}"))
    # 两个链接放进一个可换行的横向容器：窄屏时折到下一行，而不是各占一列被截成半个词。
    with links.container(horizontal=True, horizontal_alignment="right"):
        st.page_link(ui.page_ref("signals"), label="去看扫描结果",
                     icon=":material/notifications:", help="到「信号」页看最近一次扫描的结果")
        st.page_link(ui.page_ref("universe"), label="去增删信号池",
                     icon=":material/list:", help=guide.PIPELINE_MANUAL_HELP)


def page_console() -> None:
    _check_roster()
    ui.page_head("任务控制台")
    # 互斥这件事不在这里常驻预告：页头已有一句，而按钮被禁用时 view.start_button_state
    # 的 notice 会当场点名是谁在跑——比预告有用。
    try:
        busy = process.any_running(ui.RUNS_DIR)
    except RuntimeError as e:
        st.error(str(e))
        busy = None
    card = _card_for(busy)
    _first_run_callout()

    # 每日流水线：自上而下就是顺序。fragment 在顶层调用，卡片的边框容器在它里面建。
    st.html(theme.section("每日流水线"))
    card(_PIPELINE[0], busy or "")
    st.html(theme.rail())
    _manual_step_strip()
    st.html(theme.rail())
    card(_PIPELINE[1], busy or "")

    # 研究工具：回测是历史检验，不产生今天的信号，不在上面那条链上——所以单独成区
    # （这句话现在写在回测卡片自己那一句里，区标题下不再重复）。
    st.html(theme.section("研究工具"))
    for name in _TOOLS:
        card(name, busy or "")
