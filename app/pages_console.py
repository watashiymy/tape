"""「任务控制台」页（v0.2.0 §4.1）：在本机起三个入口脚本并看进度、日志与结果。

进程管理与日志解析全在 src/quant/runner/（可单测），本模块只负责渲染；
启停三个落点在 app/ui.py（控制条与本页卡片共用同一套闸门）。
"""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

import guide
import theme
import ui
from quant.report import fmt
from quant.runner import jobs, process, progress, view
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
    卡片只有三分之一宽，指标网格用 2 列（4 列会挤成一团）。"""
    try:
        metrics = json.loads((ui.resolve(dir_str) / "metrics.json").read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        st.warning(f"产物 {dir_str} 读取失败（{type(e).__name__}），可能已被删除或仍在写。")
        return
    st.caption(dir_str)
    ui.metric_grid(metrics, per_row=2)


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
        # 已终止也照画：进度条这时是"停在哪儿"的存档（文案已由 view 换成终态词、不带 ETA），
        # 用户据此判断要不要从这儿接着补跑。
        st.progress(ratio, text=head)
    elif running:
        st.status(head, state="running")     # 不确定态：转圈 + 阶段
    else:
        st.caption(head)
    if detail:
        st.caption(detail)
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


def page_console() -> None:
    ui.page_head("任务控制台")
    st.caption("任务在独立进程里运行：关掉浏览器、甚至停掉本面板都不会中断它。"
               "同时只允许一个任务（baostock 单会话，并发会互踢下线）。")
    try:
        busy = process.any_running(ui.RUNS_DIR)
    except RuntimeError as e:
        st.error(str(e))
        busy = None
    card = _card_for(busy)
    # 三张卡片等宽并排（§2.4）：纵向堆叠时要滚很久才看得见第三张。
    # fragment 写进列里是允许的（实测过）——列就是它的父容器，局部重跑照旧只动这一列。
    for col, name in zip(st.columns(len(jobs.JOBS)), jobs.JOBS):
        with col:
            card(name, busy or "")
