"""Streamlit 本地面板：streamlit run app/dashboard.py
四页面：回测报告 / 个股K线 / 今日信号 / 任务控制台。
前三页只读 output/ 与 data/cache/；控制台页可在**本机**起三个入口脚本
（进程管理与解析全在 src/quant/runner/，本文件只负责渲染）。
面板能执行本机命令，只许 localhost，切勿 --server.address 0.0.0.0 暴露到局域网。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components  # 显式导入：部分版本下 st.components 不自动可用

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant.backtest.portfolio import Trade  # noqa: E402
from quant.data.cache import BarCache       # noqa: E402
from quant.data.pipeline import prepare_bars  # noqa: E402
from quant.report.charts import kline_chart   # noqa: E402
from quant.runner import jobs, process, progress, view  # noqa: E402

OUTPUT = ROOT / "output"
RUNS_DIR = OUTPUT / "runs"

AUTO_REFRESH_S = "2s"     # 运行中卡片的局部刷新间隔（空闲时不设，避免面板空转）
LOG_TAIL_LINES = 30

METRIC_LABELS = {
    "total_return": "总收益率", "cagr": "年化收益率", "max_drawdown": "最大回撤",
    "sharpe": "夏普比率(rf=0)", "n_trades": "交易次数", "win_rate": "胜率",
    "profit_factor": "盈亏比", "avg_holding_days": "平均持仓天数",
}


RUN_STAMP = re.compile(r"_(\d{8}_\d{6})$")   # run_backtest.py 的 {策略}_{YYYYMMDD}_{HHMMSS}

# 一次可展示的回测最少要有这三件；缺任何一件都是被 Ctrl-C 打断留下的半截目录。
# run_backtest.py 已把 metrics.json 挪到最后写作为完成标记，但老目录仍可能残缺。
_REQUIRED_FILES = ("metrics.json", "report.html", "trades.csv")


def _fmt_metric(key: str, value) -> str:
    """指标卡数值格式化。int 必须原样 str()：一律 f"{v:.2f}" 会把交易次数
    渲染成 '243.00'。None（无平仓交易等）显示 —，比率类显示百分号。"""
    if value is None:
        return "—"
    if isinstance(value, int):
        return str(value)
    if key in ("total_return", "cagr", "max_drawdown", "win_rate"):
        return f"{value:.2%}"
    return f"{value:.2f}"


def _run_key(p: Path) -> tuple[str, str]:
    """按目录名尾部的时间戳排序。直接 sorted(paths, reverse=True) 比的是整条路径字符串，
    策略名会压过时间戳（"ma_cross_" > "donchian_"），默认选中的就不是最新那次回测。
    没有时间戳的目录归到最后（reverse=True 下空串最小）。"""
    m = RUN_STAMP.search(p.name)
    return (m.group(1) if m else "", p.name)


def list_runs() -> list[Path]:
    if not OUTPUT.exists():
        return []
    return sorted((p for p in OUTPUT.iterdir()
                   if p.is_dir() and all((p / f).exists() for f in _REQUIRED_FILES)),
                  key=_run_key, reverse=True)


def page_backtest() -> None:
    runs = list_runs()
    if not runs:
        st.info("暂无回测结果。先运行: python scripts/run_backtest.py")
        return
    run = st.selectbox("选择回测", runs, format_func=lambda p: p.name)
    # 所有文件读取集中在 try 里：即便三件套都在，metrics.json 仍可能只写了一半
    # （JSONDecodeError）。JSONDecodeError / EmptyDataError 都是 ValueError 子类，
    # FileNotFoundError 是 OSError 子类。崩页不如明说：提示删除残缺目录。
    try:
        metrics = json.loads((run / "metrics.json").read_text(encoding="utf-8"))
        report_html = (run / "report.html").read_text(encoding="utf-8")
        # dtype 必须显式给：symbol 写出去是字符串 "000333"，pd.read_csv 会推断成 int64
        # 吃掉前导零，表里就显示成不存在的股票代码 333（所有深市 000xxx 都中招）
        trades = pd.read_csv(run / "trades.csv", dtype={"symbol": str})
        skipped_path = run / "skipped.csv"
        skipped = (pd.read_csv(skipped_path, dtype={"symbol": str})
                   if skipped_path.exists() else None)
    except (ValueError, OSError) as e:
        st.error(f"回测目录 {run.name} 数据残缺（{type(e).__name__}），"
                 f"多半是回测中途被打断；请删除该目录后刷新页面。")
        return
    cols = st.columns(4)
    for i, (k, label) in enumerate(METRIC_LABELS.items()):
        cols[i % 4].metric(label, _fmt_metric(k, metrics.get(k)))
    components.html(report_html, height=650, scrolling=True)
    st.subheader("交易明细")
    st.dataframe(trades, use_container_width=True)
    if skipped is not None:
        st.subheader("被跳过的订单（涨跌停/资金不足等）")
        st.dataframe(skipped, use_container_width=True)


def page_kline() -> None:
    runs = list_runs()
    if not runs:
        st.info("暂无回测结果。先运行: python scripts/run_backtest.py")
        return
    run = st.selectbox("选择回测", runs, format_func=lambda p: p.name)
    try:
        trades_df = pd.read_csv(run / "trades.csv", dtype={"symbol": str})
    except (ValueError, OSError):
        st.error(f"回测目录 {run.name} 的 trades.csv 读取失败，"
                 f"多半是回测中途被打断；请删除该目录后刷新页面。")
        return
    symbols = sorted({p.stem.replace("kline_", "") for p in run.glob("kline_*.html")})
    sym = st.selectbox("选择标的", symbols)
    raw = BarCache(ROOT / "data" / "cache").load(sym)
    if raw is None:
        st.error(f"缓存中无 {sym} 行情")
        return
    df, _ = prepare_bars(raw)
    sym_trades = [
        Trade(r.symbol, r.action, pd.Timestamp(r.date), r.price, r.shares, r.commission)
        for r in trades_df[trades_df["symbol"].astype(str).str.zfill(6) == sym].itertuples()
    ]
    st.plotly_chart(kline_chart(df, sym_trades, sym), use_container_width=True)


def scan_section() -> None:
    """全市场扫描区块（v0.1.1）：读 output/scan/ 最新 CSV，只读展示，无按钮。"""
    scan_dir = OUTPUT / "scan"
    # 文件名是 YYYY-MM-DD.csv，ISO 日期字典序即时间序，reverse 后 [0] 就是最新
    files = sorted(scan_dir.glob("*.csv"), reverse=True) if scan_dir.exists() else []
    if not files:
        st.info("暂无全市场扫描结果。收盘后运行: python scripts/run_market_scan.py")
        return
    latest = files[0]
    # 标题必须带扫描日期（文件名 stem）：停牌日/忘跑的日子，别让人把旧扫描当今天的
    st.subheader(f"全市场扫描（{latest.stem}）")
    # 扫描被 Ctrl-C 打断可能留下零字节/半截 CSV：EmptyDataError / ParserError
    # 都是 ValueError 子类，与回测页同一套容错口径，崩页不如明说
    try:
        df = pd.read_csv(latest, dtype={"symbol": str})
    except (ValueError, OSError) as e:
        st.error(f"扫描文件 {latest.name} 读取失败（{type(e).__name__}），"
                 f"多半是扫描中途被打断；请删除该文件后重新运行扫描。")
        return
    if len(df):
        st.dataframe(df, use_container_width=True)
    else:
        st.write("当日无新信号")


def page_signals() -> None:
    sig_dir = OUTPUT / "signals"
    files = sorted(sig_dir.glob("*.csv"), reverse=True) if sig_dir.exists() else []
    # 无信号记录不能 return 早退：全市场扫描区块在页尾，早退会把它一并吞掉
    if not files:
        st.info("暂无信号记录。收盘后运行: python scripts/run_daily_signal.py")
    else:
        latest = files[0]
        st.subheader(f"最新信号（{latest.stem}）")
        df = pd.read_csv(latest, dtype={"symbol": str})
        # 必须写成 if/else 语句：streamlit 的 magic 会把函数体内**裸的三元表达式**
        # （ast.IfExp，不属于它豁免的 ast.Call）整个包进 st.write()，
        # 于是 st.dataframe() 的返回值 DeltaGenerator 被 st.write 当对象内省，
        # 把整份 Streamlit API 手册糊在信号表下面；无信号那天则渲染出一个 `None`。
        if len(df):
            st.dataframe(df, use_container_width=True)
        else:
            st.write("当日无新信号")
        if len(files) > 1:
            st.subheader("历史信号")
            hist = pd.concat([pd.read_csv(f, dtype={"symbol": str}) for f in files[1:]],
                             ignore_index=True)
            if len(hist):
                st.dataframe(hist, use_container_width=True)
            else:
                st.write("无")
    scan_section()


# ---------------------------------------------------------------- 任务控制台（v0.2.0 §4.1）

def _resolve(path_str: str) -> Path:
    """脚本打进日志的产物路径是相对仓库根的（"output/scan/2026-08-24.csv"）。
    面板的 cwd 未必是仓库根，一律按 ROOT 兜底，否则完成后的结果区永远是"读取失败"。"""
    p = Path(path_str)
    return p if p.is_absolute() else ROOT / p


def _param_widgets(job: jobs.Job) -> dict:
    """按 schema 生成控件。控件本身就是第一道白名单（策略是下拉、日期是日历、
    limit 有上下界），值再交给 build_argv 复核一遍。"""
    values: dict = {}
    for spec in job.params:
        key = f"{job.name}_{spec.name}"
        if spec.kind == "int":
            values[spec.name] = st.number_input(
                spec.label, min_value=1, max_value=spec.max_value, value=None,
                step=1, key=key)
        elif spec.kind == "date":
            values[spec.name] = st.date_input(spec.label, value=None, key=key)
        elif spec.kind == "choice":
            picked = st.selectbox(spec.label, ("全部", *spec.choices), key=key)
            values[spec.name] = None if picked == "全部" else picked
        elif spec.kind == "flag":
            values[spec.name] = st.checkbox(spec.label, value=False, key=key)
    return values


def _start(job_name: str, params: dict) -> None:
    """开始/重跑唯一的落点：argv 一律由 build_argv 现造（列表 + shell=False）。
    start() 自己还会再查一次互斥——按钮禁用只是 UI 层，抢跑要在 runner 层挡死。"""
    try:
        argv = jobs.build_argv(job_name, params)
        process.start(job_name, argv, RUNS_DIR)
    except (ValueError, RuntimeError, OSError) as e:
        # RuntimeError 是抢跑（start() 的互斥）抛的，漏接就是整页 traceback 而不是这句提示。
        st.error(f"启动失败: {e}")
        return
    # 整页重跑：另外两张卡片的"开始"要被互斥禁用、三张卡片要切到 2 秒轮询，都在 fragment 之外。
    # AppTest 只能证明"请求发出去了"（它不模拟 fragment 局部重跑），真效果靠人工验收。
    st.rerun()


def _rerun(job_name: str, state: process.RunState) -> None:
    """沿用上次 argv 重跑。状态文件是手工改得动的普通 JSON，所以先 parse_argv
    反解、再由 _start 重新 build_argv，让它整个过一遍白名单闸门。"""
    try:
        params = jobs.parse_argv(job_name, state.argv)
    except ValueError as e:
        st.error(f"无法重跑: {e}")
        return
    _start(job_name, params)


def _result_table(path_str: str) -> None:
    """扫描/信号的产物 CSV。symbol 必须按字符串读，否则 000333 变成 333。"""
    try:
        df = pd.read_csv(_resolve(path_str), dtype={"symbol": str})
    except (ValueError, OSError) as e:
        st.warning(f"产物 {path_str} 读取失败（{type(e).__name__}），可能已被删除或仍在写。")
        return
    if len(df):
        st.dataframe(df, use_container_width=True)
    else:
        st.write("当次无新信号")


def _result_metrics(dir_str: str) -> None:
    """回测产物：每个策略一个报告目录，各出一组指标卡。"""
    try:
        metrics = json.loads((_resolve(dir_str) / "metrics.json").read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        st.warning(f"产物 {dir_str} 读取失败（{type(e).__name__}），可能已被删除或仍在写。")
        return
    st.caption(dir_str)
    cols = st.columns(4)
    for i, (k, label) in enumerate(METRIC_LABELS.items()):
        cols[i % 4].metric(label, _fmt_metric(k, metrics.get(k)))


_RESULT_RENDERERS = {"scan_csv": _result_table, "signal_csv": _result_table,
                     "backtest_run": _result_metrics}


def _render_card(job_name: str) -> str | None:
    """一张任务卡片。返回当前"谁在跑"（None=全空闲），外层据此决定要不要继续轮询。

    判断与格式化全在 quant.runner.view（可单测），这里只把字符串塞进 st.*。
    """
    job = jobs.JOBS[job_name]
    try:
        state = process.read_state(job_name, RUNS_DIR)
        busy = process.any_running(RUNS_DIR)
    except RuntimeError as e:
        # 状态文件损坏必须响亮失败：静默当"空闲"会放开互斥，两个 baostock 会话互踢。
        st.subheader(job.label)
        st.error(str(e))
        return None
    st.subheader(f"{job.label} {view.status_badge(state)}")
    params = _param_widgets(job)
    disabled, notice = view.start_button_state(busy, job_name)
    running = state is not None and state.status == process.RUNNING
    left, mid, right = st.columns(3)
    if left.button("▶ 开始", key=f"start_{job_name}", disabled=disabled):
        _start(job_name, params)
    if mid.button("⏹ 停止", key=f"stop_{job_name}", disabled=not running):
        process.stop(state, RUNS_DIR)     # SIGTERM 进程组 → 10s → SIGKILL
        st.rerun()                        # 同 _start：解禁另外两个"开始"并摘掉轮询
    if right.button("↻ 重跑", key=f"rerun_{job_name}", disabled=disabled or state is None):
        _rerun(job_name, state)
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
    caption = view.progress_caption(prog, view.elapsed_seconds(state), status=state.status)
    ratio = view.progress_ratio(prog)
    if ratio is not None:
        # 已终止也照画：进度条这时是"停在哪儿"的存档（文案已由 view 换成终态词、不带 ETA），
        # 用户据此判断要不要从这儿接着补跑。
        st.progress(ratio, text=caption)
    elif running:
        st.status(caption, state="running")   # 不确定态：转圈 + 阶段 + 已用时长
    else:
        st.caption(caption)
    st.code(progress.tail(log, LOG_TAIL_LINES) or "（暂无输出）", language="text")
    with st.expander("完整日志"):
        st.code(log or "（暂无输出）", language="text")
    if state.status == process.SUCCESS:
        for out in prog.outputs:
            _RESULT_RENDERERS[job.result_kind](out)
    st.divider()
    return busy


@st.fragment(run_every=AUTO_REFRESH_S)
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
    st.caption("任务在独立进程里运行：关掉浏览器、甚至停掉本面板都不会中断它。"
               "同时只允许一个任务（baostock 单会话，并发会互踢下线）。")
    try:
        busy = process.any_running(RUNS_DIR)
    except RuntimeError as e:
        st.error(str(e))
        busy = None
    card = _card_for(busy)
    for name in jobs.JOBS:
        card(name, busy or "")


st.set_page_config(page_title="quant_demo v0.1.1", layout="wide")
st.sidebar.title("quant_demo")
page = st.sidebar.radio("页面", ["回测报告", "个股K线", "今日信号", "任务控制台"])
st.sidebar.caption("本面板纯只读；回测与信号请用命令行运行。策略仅用于学习，不构成投资建议。")
{"回测报告": page_backtest, "个股K线": page_kline, "今日信号": page_signals,
 "任务控制台": page_console}[page]()
