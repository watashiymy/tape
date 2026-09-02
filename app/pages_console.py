"""「任务控制台」页（v0.2.0 §4.1）：在本机起三个入口脚本并看进度、日志与结果。

进程管理与日志解析全在 src/quant/runner/（可单测），本模块只负责渲染；
启停三个落点在 app/ui.py（控制条与本页卡片共用同一套闸门）。
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
from quant.config import universe_source
from quant.report import fmt
from quant.runner import jobs, process, progress, steps, view
from quant.strategy import strategy_label


def _param_widgets(job: jobs.Job) -> dict:
    """按 schema 生成控件。控件本身就是第一道白名单（策略是下拉、日期是日历、
    limit 有上下界），值再交给 build_argv 复核一遍。

    每个控件都挂 `help=`（§3.2 就地帮助）：四个控件的值都能留空，而留空的语义
    各不相同（全量 / 最近交易日 / 全部策略）。不说清楚，用户第一次点开始就可能
    误跑一次十几分钟的全量扫描。文案在 app/guide.py，键漏了会 KeyError——
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
    except (ValueError, OSError) as e:
        st.warning(f"产物 {path_str} 读取失败（{type(e).__name__}），可能已被删除或仍在写。")
        return
    # 策略列只在显示层换中文显示名，磁盘上的 CSV 照旧存键
    ui.data_table(fmt.map_strategy_labels(df), config, "当次无新信号",
                  hint=hint, color_columns=color_columns)


def _result_scan(path_str: str) -> None:
    """当次扫描的结果表，标题旁带范围徽标（v0.2.4 设计 §2.3）。

    控制台这条路径尤其需要它：`--limit` 就在这一页的控件上，一次试跑的结果与一次
    全量扫描的结果在卡片里长得一模一样——尤其是两者都"无新信号"的时候。
    路径取自日志里的 `已保存:` 行，meta 就在它旁边。
    """
    st.html(theme.section("本次扫描结果", ui.scan_scope_pill(ui.resolve(path_str))))
    _result_table(path_str, fmt.scan_column_config(), guide.TABLE_HINTS["scan"],
                  ui.SCAN_COLOR_COLUMNS)


def _result_signal(path_str: str) -> None:
    """每日信号 CSV 的列与扫描**不是**同一套（没有 name/成交额/放量倍数），
    列配置也得各用各的，否则 action / close 连中文标签都没有。"""
    _result_table(path_str, fmt.signal_column_config(),
                  guide.TABLE_HINTS["signal"], ())


def _result_metrics(dir_str: str) -> None:
    """回测产物：每个策略一个报告目录，各出一组指标卡。

    per_row 用默认的 4：v0.5.0 起回测卡片在「研究工具」区**整幅宽**渲染
    （不再挤在三分之一宽的列里），2 列会让右半边空着、八个指标拉成长长一条。
    """
    try:
        metrics = json.loads((ui.resolve(dir_str) / "metrics.json").read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        st.warning(f"产物 {dir_str} 读取失败（{type(e).__name__}），可能已被删除或仍在写。")
        return
    st.caption(dir_str)
    ui.metric_grid(metrics)


_RESULT_RENDERERS = {"scan_csv": _result_scan, "signal_csv": _result_signal,
                     "backtest_run": _result_metrics}


def _card_head(job: jobs.Job, pill_html: str = "") -> None:
    """卡片标题行：衬线小标题 + 状态 pill，**右上角**一个 `?` 就地帮助（§3.2）。

    帮助必须与标题同一行才算"右上角"：塞到参数控件下面就排在按钮后面，
    正好在"不知道这个按钮会干什么"的那一刻看不见。
    状态文件损坏的那条分支也走这里——那时候人最需要知道这个任务是干什么的。
    """
    head, help_col = st.columns([5, 1], vertical_alignment="center")
    head.html(theme.section(job.label, pill_html))
    with help_col.popover("?"):
        st.markdown(guide.JOB_HELP[job.name])


def _render_card(job_name: str) -> str | None:
    """一张任务卡片。返回当前"谁在跑"（None=全空闲），外层据此决定要不要继续轮询。

    判断与格式化全在 quant.runner.view（可单测），这里只把字符串塞进 st.*。
    """
    job = jobs.JOBS[job_name]
    try:
        state = process.read_state(job_name, ui.RUNS_DIR)
        busy = process.any_running(ui.RUNS_DIR)
    except RuntimeError as e:
        # 状态文件损坏必须响亮失败：静默当"空闲"会放开互斥，两个 baostock 会话互踢。
        _card_head(job)
        st.error(str(e))
        return None
    _card_head(job, theme.pill(*view.status_pill(state)))
    # 就绪状态紧跟标题（v0.5.0）：摆在标题**上面**的话，三列的状态行长短不一，
    # 三张卡片的标题就会各在一个高度上（实测 490 / 429 / 467 px），中间的箭头
    # 轨也就没有一条可对齐的基线。
    #
    # **必须接住 RuntimeError**：scan_status 会读扫描的 meta，而 load_meta 对损坏
    # 文件是响亮抛错（那是数据层的设计，不改）。这里不接的话，异常从 fragment 冒到
    # exec_code 会让整页**提前中止**——第一张卡片之后的一切都不再渲染，包括正在跑的
    # 那趟全量扫描的 ⏹ 停止 按钮。口径照抄 ui.scan_scope_pill：如实说，但别把页面打没。
    try:
        if (status := _step_status(job_name)) is not None:
            st.caption(status.text)
    except RuntimeError as e:
        st.warning(str(e))
    params = _param_widgets(job)
    disabled, notice = view.start_button_state(busy, job_name)
    running = state is not None and state.status == process.RUNNING
    left, mid, right = st.columns(3)
    if left.button("▶ 开始", key=f"start_{job_name}", disabled=disabled):
        ui.start_job(job_name, params)
    if mid.button("⏹ 停止", key=f"stop_{job_name}", disabled=not running):
        ui.stop_job(state)                # SIGTERM 进程组 → 10s → SIGKILL，然后整页重跑
    if right.button("↻ 重跑", key=f"rerun_{job_name}", disabled=disabled or state is None):
        ui.rerun_job(job_name, state)
    if notice:
        st.caption(notice)
    if state is None:
        st.caption("尚未运行过")
        st.divider()
        return busy
    log = process.read_log(state)
    prog = job.parser(log)
    # status 必须一路带到文案里：解析器只看得见日志，看不见进程死活——被停掉的扫描
    # 日志最后一行仍是 [1800/3010]，不告诉它状态就会继续喊"扫描中，预计剩余 ~8分钟"。
    # 两行（§2.4）：主状态进进度条，细节（已用/ETA/信号条数）单独一行灰字。
    head, detail = view.progress_lines(prog, view.elapsed_seconds(state),
                                       status=state.status)
    ratio = view.progress_ratio(prog)
    if ratio is not None:
        # 已终止也照画：进度条这时是"停在哪儿"的存档（文案已由 view 换成终态词、不带 ETA）。
        # **不存在断点续跑**——v0.2.0 的注释原本写着"用户据此判断要不要接着补跑"，
        # 那是误解：重跑从第 1 只开始。存档的价值只是"我上次跑到哪、值不值得再等一轮"。
        st.progress(ratio, text=head)
    elif running:
        st.status(head, state="running")     # 不确定态：转圈 + 阶段
    else:
        st.caption(head)
    if detail:
        st.caption(detail)
    # 终止且无产物时补一句（v0.5.0）：detail 那行会写着「信号 43 条」，
    # 而那 43 条一条都没落盘——不说清楚，屏幕上唯一的数字就指向一份不存在的文件。
    if note := view.no_output_note(prog, status=state.status):
        st.caption(note)
    # 固定高度滚动：不给 height 的话几十行日志会把三张卡片顶得错开老远。
    st.code(progress.tail(log, ui.LOG_TAIL_LINES) or "（暂无输出）", language="text",
            height=ui.LOG_BOX_HEIGHT)
    with st.expander("完整日志"):
        st.code(log or "（暂无输出）", language="text", height=ui.LOG_BOX_HEIGHT)
    if state.status == process.SUCCESS:
        for out in prog.outputs:
            _RESULT_RENDERERS[job.result_kind](out)
    st.divider()
    return busy


@st.fragment(run_every=ui.AUTO_REFRESH_S)
def _card_live(job_name: str, was_busy: str) -> None:
    """有任务在跑：每 2 秒只重跑这张卡片（整页重跑会把参数控件与滚动位置一起抖没）。"""
    busy = _render_card(job_name)
    if (busy or "") != was_busy:
        st.rerun()   # 跑完了/被停了 → 整页重跑，摘掉轮询并解禁另外两个开始按钮


@st.fragment
def _card_idle(job_name: str, was_busy: str) -> None:
    """全空闲：**不设** run_every，否则面板会每 2 秒无谓地重跑三张卡片。"""
    _render_card(job_name)


def _card_for(busy: str | None):
    return _card_live if busy else _card_idle


# ---------------------------------------------------------------- 排班（v0.5.0 §6）
#
# 拆分前三张卡片等宽并排，顺序就是 jobs.JOBS 的字典序——**语义为零**。
# 而真实的闭环是：全市场扫描（发现）→ 人工研究 → 加进信号池 → 每日信号（跟踪），
# 其中「加进信号池」不是可运行的任务、「回测」根本不在这条链上。
#
# 刻意**不加 ①②③ 编号**：说明页的命令行一节已经在用一套编号，README §2 的
# 「四个入口」是第三套。再引入一套流水线编号，用户会在两页之间看到互相矛盾的数字，
# 而没有任何测试会红——正是本项目最忌讳的静默不一致。顺序由箭头与位置表达就够了。
_PIPELINE = ("market_scan", "daily_signal")     # 链上的两个**可运行**任务
_TOOLS = ("backtest",)                          # 不在链上的研究工具


def _check_roster() -> None:
    """每个任务都得在某个区里有位置。

    响亮失败而不是静默漏渲染：将来加了第四个任务却忘了排版，那张卡片会从页面上
    **消失**而没有任何报错——用户只会以为这个功能没做。
    """
    placed = set(_PIPELINE) | set(_TOOLS)
    if placed != set(jobs.JOBS):
        raise RuntimeError(
            f"控制台排班与 jobs.JOBS 对不上：漏了 {sorted(set(jobs.JOBS) - placed)}，"
            f"多了 {sorted(placed - set(jobs.JOBS))}")


def _short_path(path) -> str:
    """路径显示成**相对仓库根**的样子。

    绝对路径在这种一栏宽的卡片里会折成三行，而且把机器上的用户名一起摊在屏幕上
    ——面板截图发出去就带着它。相对路径同时也正是文档里到处写的那个写法。
    """
    if path is None:
        return ""
    try:
        return str(Path(path).relative_to(ui.ROOT))
    except ValueError:                      # 不在仓库里（测试夹具、别处的配置）
        return Path(path).name


def _step_status(job_name: str):
    """链上那两个任务的「就绪状态」。返回 None 表示这一步不需要状态行。

    在卡片内部现算而不是由 page_console 传进来：卡片是 fragment，运行中每 2 秒
    只重跑自己那一列，传进来的值会停在开跑那一刻——扫描跑完了状态行还写着
    「最近一次是上周」。
    """
    if job_name == "market_scan":
        return steps.scan_status(ui.OUTPUT / "scan", date.today())
    if job_name == "daily_signal":
        return steps.signal_status(ui.OUTPUT / "signals", date.today())
    return None                     # 回测不在链上，没有"就绪"这回事


def _manual_step_card() -> None:
    """第二步：加进信号池。**没有开始按钮**——这一步是你自己动手的一环。

    卡片形状与另外两张一致（标题行 + 状态行 + 一块内容），否则并排看过去像
    缺了一块。灰色「人工步骤」标记代替状态 pill：琥珀在本主题里只给可操作元素。
    """
    head, help_col = st.columns([5, 1], vertical_alignment="center")
    head.html(theme.section("加进信号池", theme.manual_tag("人工步骤")))
    with help_col.popover("?"):
        st.markdown(guide.PIPELINE_MANUAL_HELP)
    try:
        symbols = pool.current(ui.CONFIG_PATH)
        source = universe_source(ui.CONFIG_PATH)
    except pool.CONFIG_ERRORS:
        symbols, source = None, None
    st.caption(steps.pool_status(symbols, _short_path(source)).text)
    st.caption(guide.PIPELINE_MANUAL_NOTE)
    st.page_link(ui.page_ref("signals"), label="去「今日信号」看扫描结果",
                 icon=":material/notifications:")
    st.page_link(ui.page_ref("universe"), label="去「信号池」增删标的",
                 icon=":material/list:")
    st.divider()


def page_console() -> None:
    _check_roster()
    ui.page_head("任务控制台")
    # 互斥这件事不在这里常驻预告：页头已有一句，而按钮被禁用时 view.start_button_state
    # 的 notice 会当场点名是谁在跑——比预告有用。这里只留"独立进程"这条动作现场的信息。
    st.caption("任务在独立进程里运行：关掉浏览器、甚至停掉本面板都不会中断它。")
    try:
        busy = process.any_running(ui.RUNS_DIR)
    except RuntimeError as e:
        st.error(str(e))
        busy = None
    card = _card_for(busy)

    # 页顶的闭环图：无论列怎么塌，顺序总还读得懂（窄屏 flex-wrap 自动折行）。
    st.html(theme.section("每日流水线"))
    st.html(theme.flow(guide.CONSOLE_FLOW))
    st.caption(guide.CONSOLE_FLOW_NOTE)
    # 三卡两轨。箭头列很窄（0.14）；窄屏 streamlit 会竖着堆，那时 CSS 把箭头换成 ↓。
    # fragment 写进列里是允许的（实测过）——列就是它的父容器，局部重跑只动这一列。
    scan_col, rail1, manual_col, rail2, signal_col = st.columns(
        [1, 0.14, 1, 0.14, 1], vertical_alignment="top")
    with scan_col:
        card(_PIPELINE[0], busy or "")
    rail1.html(theme.rail())
    with manual_col:
        _manual_step_card()
    rail2.html(theme.rail())
    with signal_col:
        card(_PIPELINE[1], busy or "")

    st.html(theme.section("研究工具"))
    st.caption(guide.CONSOLE_TOOLS_NOTE)
    for name in _TOOLS:
        card(name, busy or "")
