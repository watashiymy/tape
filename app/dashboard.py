"""Streamlit 本地面板：streamlit run app/dashboard.py
四页面：回测报告 / 个股K线 / 今日信号 / 任务控制台。
前三页展示 output/ 与 data/cache/ 里的产物，顶部各有一条精简控制条（开始/停止，§4.2）；
控制台页可在**本机**起三个入口脚本并看进度、日志与结果
（进程管理与解析全在 src/quant/runner/，本文件只负责渲染）。
面板能执行本机命令，只许 localhost，切勿 --server.address 0.0.0.0 暴露到局域网。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
# app/ 也得显式进 sys.path：`streamlit run` 会把脚本目录放进去，但用 importlib 直接
# 加载本文件（测试就是这么干的）不会，那时 `import theme` 直接 ModuleNotFoundError。
sys.path.insert(0, str(Path(__file__).resolve().parent))

import theme  # noqa: E402  视觉基座，与本文件同目录（app/theme.py）
from quant.backtest.portfolio import Trade  # noqa: E402
from quant.data.cache import BarCache       # noqa: E402
from quant.data.pipeline import prepare_bars  # noqa: E402
from quant.report import fmt                  # noqa: E402  格式化/配色/列配置（纯函数）
from quant.report.charts import kline_chart   # noqa: E402
from quant.runner import jobs, process, progress, view  # noqa: E402

OUTPUT = ROOT / "output"
RUNS_DIR = OUTPUT / "runs"

AUTO_REFRESH_S = "2s"     # 运行中卡片的局部刷新间隔（空闲时不设，避免面板空转）
LOG_TAIL_LINES = 30
LOG_BOX_HEIGHT = 220      # 日志区固定高度滚动（§2.4）：几十行日志不许把版面顶飞
# 标的名称取自最近这么多份扫描 CSV。名称几乎不变，30 份够用，同时把每次渲染的
# 文件读取量兜住（一天一份，长期跑下来 output/scan/ 会攒到几百份）。
NAME_LOOKBACK_FILES = 30

# 每页顶部那句话（§2.4 通用页头）。只讲"这页给你看什么、什么时候看"，
# 数字必须与实测一致（baostock 约 17:30 后才有当日数据 —— README/spec §11）。
PAGE_INTRO = {
    "回测报告": "历史检验的结果：绩效指标、净值报告与逐笔成交，读自 output/ 里已完成的回测。",
    "个股K线": "单只标的的日线走势，叠加本次回测在它身上的买卖点。",
    "今日信号": "固定池的每日买卖信号，页尾是全市场扫描当日新 BUY；"
            "收盘后 17:30 之后跑才有当日数据。",
    "任务控制台": "在本机启动三个任务并盯进度、日志与结果；"
             "同时只允许一个任务（baostock 单会话）。",
}

RUN_STAMP = re.compile(r"_(\d{8}_\d{6})$")   # run_backtest.py 的 {策略}_{YYYYMMDD}_{HHMMSS}

# 一次可展示的回测最少要有这三件；缺任何一件都是被 Ctrl-C 打断留下的半截目录。
# run_backtest.py 已把 metrics.json 挪到最后写作为完成标记，但老目录仍可能残缺。
_REQUIRED_FILES = ("metrics.json", "report.html", "trades.csv")


def _page_head(page: str) -> None:
    """通用页头（§2.4）：页名衬线大字 + 一句话说明 + 右侧全局任务状态 pill。

    这里**不**报 st.error：状态文件损坏时下面的控制条/卡片会报，页头再喊一遍只是噪声；
    但 pill 必须如实说"未知"，不能退回"空闲"（那是猜的，而且正是 fail-safe 要挡的误导）。
    """
    try:
        text, kind = view.busy_pill(process.any_running(RUNS_DIR))
    except RuntimeError:
        text, kind = view.UNKNOWN_TEXT, view.PILL_IDLE
    st.html(theme.page_head(page, PAGE_INTRO[page], theme.pill(text, kind)))


def _metric_grid(metrics: dict, per_row: int = 4) -> None:
    """指标卡网格。默认 2 行 × 4 列（§2.4）；控制台卡片只有三分之一宽，传 per_row=2。

    老写法 `st.columns(4)` 配 `cols[i % 4]` 是**一行 4 列、每列纵向摞两张**：
    右半边版面空着，两排数字还错位。这里按行现开列。
    """
    items = list(fmt.METRIC_LABELS.items())
    for start in range(0, len(items), per_row):
        row = items[start:start + per_row]
        for col, (key, label) in zip(st.columns(per_row), row):
            value = metrics.get(key)
            col.html(theme.metric(label, fmt.fmt_metric(key, value),
                                  fmt.metric_color(key, value)))


def _data_table(df: pd.DataFrame, config: dict, empty_text: str,
                color_columns: tuple[str, ...] = ()) -> None:
    """信号/扫描/交易表的统一渲染口。

    `color_columns` 走 pandas Styler：`column_config` 里没有条件着色能力
    （NumberColumn 无 color 参数），红绿只能这么给；而 column_config 的格式串
    优先级高于 Styler，所以千分位、百分号不会被顶掉。
    必须写成 if/return 语句：裸三元会被 streamlit magic 整条包进 st.write。
    """
    if not len(df):
        st.write(empty_text)
        return
    data = fmt.direction_styler(df, color_columns) if color_columns else df
    st.dataframe(data, width="stretch", column_config=config, hide_index=True)


def symbol_names() -> dict[str, str]:
    """symbol → 名称。唯一的离线来源是扫描 CSV，而扫描只记录**出信号**的标的，
    所以多数标的（含 universe 里那十只蓝筹）查不到名字——缺名是常态，不是异常。

    坏掉的扫描文件一律跳过：K 线页不该因为一份半截 CSV 就打不开。
    """
    scan_dir = OUTPUT / "scan"
    files = sorted(scan_dir.glob("*.csv"), reverse=True) if scan_dir.exists() else []
    names: dict[str, str] = {}
    for path in files[:NAME_LOOKBACK_FILES]:   # 新的排前面，先到者胜（改过名的取最近）
        try:
            df = pd.read_csv(path, dtype={"symbol": str})
        except (ValueError, OSError):
            continue
        if "symbol" not in df.columns or "name" not in df.columns:
            continue
        for sym, name in zip(df["symbol"], df["name"]):
            names.setdefault(str(sym), name)
    return names


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
    _page_head("回测报告")
    _control_bar("backtest")   # §4.2 控制条；下面的只读逻辑一行未动
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
    _metric_grid(metrics)
    # src 直接给 Path：st.iframe 会自己读这个 HTML 文件并内嵌（report.html 约 5 MB，
    # 自己 read_text 白读一遍）。st.iframe **没有** scrolling 参数（签名只有
    # src/width/height/tab_index），iframe 自带滚动条，不需要它。
    # 不能换成 st.html：那个不套 iframe 且默认忽略 JavaScript，plotly 报告会是空白页。
    st.iframe(run / "report.html", height=650)
    st.html(theme.section("交易明细"))
    st.caption("金额与股数已加千分位、数字列右对齐；盈亏只在**平仓**那一笔上有值，"
               "买入行与仍持仓的标的是空值。")
    _data_table(trades, fmt.trades_column_config(), "本次回测没有任何成交")
    if skipped is not None:
        st.html(theme.section("被跳过的订单（涨跌停/资金不足等）"))
        # 这张表只有 date/symbol/reason，列配置得各用各的（成交表那套会漏掉 reason）
        _data_table(skipped, fmt.skipped_column_config(), "无被跳过的订单")


def page_kline() -> None:
    _page_head("个股K线")
    _control_bar("backtest")   # K 线图也是回测产物（kline_*.html + trades.csv）
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
    # 名称让人看得出这是哪家公司（§2.4）。查不到就只显示代码——扫描 CSV 是唯一的
    # 离线名称来源，而它只记录出信号的标的，所以缺名是常态。
    names = symbol_names()
    sym = st.selectbox("选择标的", symbols,
                       format_func=lambda s: fmt.symbol_label(s, names.get(s)))
    rows = trades_df[trades_df["symbol"].astype(str).str.zfill(6) == sym]
    _symbol_summary(rows)
    raw = BarCache(ROOT / "data" / "cache").load(sym)
    if raw is None:
        st.error(f"缓存中无 {sym} 行情")
        return
    df, _ = prepare_bars(raw)
    sym_trades = [
        Trade(r.symbol, r.action, pd.Timestamp(r.date), r.price, r.shares, r.commission)
        for r in rows.itertuples()
    ]
    st.plotly_chart(kline_chart(df, sym_trades, sym), width="stretch")


def _symbol_summary(rows: pd.DataFrame) -> None:
    """图上方那行小结（§2.4）：本次回测在该标的上成交几笔、盈亏多少。

    盈亏只算已平仓的那些；一笔都没平（仍持仓）时显示 —，不能写 0
    ——那等于宣布"这只不赚不亏"，是编出来的数字。
    """
    s = fmt.symbol_trade_summary(rows)
    cards = (("成交笔数", str(s["n_trades"]), None),
             ("买入笔数", str(s["n_buy"]), None),
             ("卖出笔数", str(s["n_sell"]), None),
             ("已平仓盈亏", fmt.fmt_amount(s["pnl"]), fmt.signed_color(s["pnl"])))
    for col, (label, value, color) in zip(st.columns(len(cards)), cards):
        col.html(theme.metric(label, value, color))


SCAN_COLOR_COLUMNS = ("pct_chg",)   # 扫描表里唯一有方向的列（红涨绿跌）


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
    st.html(theme.section(f"全市场扫描（{latest.stem}）"))
    # 扫描被 Ctrl-C 打断可能留下零字节/半截 CSV：EmptyDataError / ParserError
    # 都是 ValueError 子类，与回测页同一套容错口径，崩页不如明说
    try:
        df = pd.read_csv(latest, dtype={"symbol": str})
    except (ValueError, OSError) as e:
        st.error(f"扫描文件 {latest.name} 读取失败（{type(e).__name__}），"
                 f"多半是扫描中途被打断；请删除该文件后重新运行扫描。")
        return
    st.caption("放量倍数（当日成交额 / 前 20 日均额）是判断信号质量最该看的一列——"
               "没有量的突破多半是假突破；涨跌幅的参考价值反而低。")
    _data_table(df, fmt.scan_column_config(), "当日无新信号", SCAN_COLOR_COLUMNS)


def page_signals() -> None:
    _page_head("今日信号")
    # 本页同屏展示两类产物：固定池每日信号 + 页尾的全市场扫描区块，故控制条有两条
    _control_bar("daily_signal", "market_scan")
    sig_dir = OUTPUT / "signals"
    files = sorted(sig_dir.glob("*.csv"), reverse=True) if sig_dir.exists() else []
    # 无信号记录不能 return 早退：全市场扫描区块在页尾，早退会把它一并吞掉
    if not files:
        st.info("暂无信号记录。收盘后运行: python scripts/run_daily_signal.py")
    else:
        latest = files[0]
        st.html(theme.section(f"最新信号（{latest.stem}）"))
        df = pd.read_csv(latest, dtype={"symbol": str})
        # 表格渲染统一走 _data_table：空表时那句提示必须是 if/else **语句**，
        # streamlit 的 magic 会把裸三元（ast.IfExp）整条包进 st.write()，
        # 于是 st.dataframe 的返回值被当对象内省，把整份 API 手册糊在信号表下面。
        _data_table(df, fmt.signal_column_config(), "当日无新信号")
        if len(files) > 1:
            st.html(theme.section("历史信号"))
            hist = pd.concat([pd.read_csv(f, dtype={"symbol": str}) for f in files[1:]],
                             ignore_index=True)
            _data_table(hist, fmt.signal_column_config(), "无")
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


def _stop(state: process.RunState) -> None:
    """停止唯一的落点。信号一律由 process.stop 发（SIGTERM 进程组 → 10s → SIGKILL），
    面板自己绝不碰 os.kill。"""
    process.stop(state, RUNS_DIR)
    st.rerun()   # 同 _start：解禁另外两个"开始"并摘掉轮询


def _rerun(job_name: str, state: process.RunState) -> None:
    """沿用上次 argv 重跑。状态文件是手工改得动的普通 JSON，所以先 parse_argv
    反解、再由 _start 重新 build_argv，让它整个过一遍白名单闸门。"""
    try:
        params = jobs.parse_argv(job_name, state.argv)
    except ValueError as e:
        st.error(f"无法重跑: {e}")
        return
    _start(job_name, params)


CONSOLE_HINT = ("进度、实时日志与完整结果见侧边栏「任务控制台」页；"
                "这里按默认参数运行（要指定 --limit / --date / 策略请去控制台）。")


def _control_bar(*job_names: str) -> None:
    """既有三页顶部的精简控制条（设计 §4.2）：当前状态 + 开始/停止 + 去控制台看详情。

    只放这三样：进度条、实时日志、结果全在控制台页，这条越薄越好。
    必须排在各页只读逻辑的**最前面**：三页在无产物时都会 st.info 之后早退，
    控制条排在早退后面，恰好在"最需要点开始"的那一刻看不见。
    每页只给这一页看得见产物的任务（回测页不出扫描按钮：误点一下就是 0.5-2 小时）。
    """
    try:
        busy = process.any_running(RUNS_DIR)
        states = {name: process.read_state(name, RUNS_DIR) for name in job_names}
    except RuntimeError as e:
        # 与控制台页同口径：互斥状态不可知就一个"开始"都不给（fail-safe），
        # 否则真有扫描在跑也照样能点，两个 baostock 会话互踢下线。
        # 只读内容照常渲染——一个坏了的 runs/*.json 不该把回测报告一起藏起来。
        st.error(str(e))
        st.divider()
        return
    notices: list[str] = []
    for name in job_names:
        job = jobs.JOBS[name]
        state = states[name]
        running = state is not None and state.status == process.RUNNING
        disabled, notice = view.start_button_state(busy, name)
        head, start_col, stop_col = st.columns([6, 1, 1], vertical_alignment="center")
        head.html(theme.section(job.label, theme.pill(*view.status_pill(state))))
        if start_col.button("▶ 开始", key=f"bar_start_{name}", disabled=disabled):
            _start(name, {})              # 精简条无参数控件：一律默认参数
        if stop_col.button("⏹ 停止", key=f"bar_stop_{name}", disabled=not running):
            _stop(state)
        if notice:
            notices.append(notice)        # 点名是谁在跑，否则灰按钮无从解释
    # 互斥是全局的，两条控制条拿到的是同一句提示——去重后只说一次，别刷屏
    for text in dict.fromkeys(notices):
        st.caption(text)
    st.caption(CONSOLE_HINT)
    st.divider()


def _result_table(path_str: str, config: dict, color_columns: tuple[str, ...]) -> None:
    """扫描/信号的产物 CSV。symbol 必须按字符串读，否则 000333 变成 333。"""
    try:
        df = pd.read_csv(_resolve(path_str), dtype={"symbol": str})
    except (ValueError, OSError) as e:
        st.warning(f"产物 {path_str} 读取失败（{type(e).__name__}），可能已被删除或仍在写。")
        return
    _data_table(df, config, "当次无新信号", color_columns)


def _result_scan(path_str: str) -> None:
    _result_table(path_str, fmt.scan_column_config(), SCAN_COLOR_COLUMNS)


def _result_signal(path_str: str) -> None:
    """每日信号 CSV 的列与扫描**不是**同一套（没有 name/成交额/放量倍数），
    列配置也得各用各的，否则 action / close 连中文标签都没有。"""
    _result_table(path_str, fmt.signal_column_config(), ())


def _result_metrics(dir_str: str) -> None:
    """回测产物：每个策略一个报告目录，各出一组指标卡。
    卡片只有三分之一宽，指标网格用 2 列（4 列会挤成一团）。"""
    try:
        metrics = json.loads((_resolve(dir_str) / "metrics.json").read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        st.warning(f"产物 {dir_str} 读取失败（{type(e).__name__}），可能已被删除或仍在写。")
        return
    st.caption(dir_str)
    _metric_grid(metrics, per_row=2)


_RESULT_RENDERERS = {"scan_csv": _result_scan, "signal_csv": _result_signal,
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
        st.html(theme.section(job.label))
        st.error(str(e))
        return None
    st.html(theme.section(job.label, theme.pill(*view.status_pill(state))))
    params = _param_widgets(job)
    disabled, notice = view.start_button_state(busy, job_name)
    running = state is not None and state.status == process.RUNNING
    left, mid, right = st.columns(3)
    if left.button("▶ 开始", key=f"start_{job_name}", disabled=disabled):
        _start(job_name, params)
    if mid.button("⏹ 停止", key=f"stop_{job_name}", disabled=not running):
        _stop(state)                      # SIGTERM 进程组 → 10s → SIGKILL，然后整页重跑
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
    st.code(progress.tail(log, LOG_TAIL_LINES) or "（暂无输出）", language="text",
            height=LOG_BOX_HEIGHT)
    with st.expander("完整日志"):
        st.code(log or "（暂无输出）", language="text", height=LOG_BOX_HEIGHT)
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
    _page_head("任务控制台")
    st.caption("任务在独立进程里运行：关掉浏览器、甚至停掉本面板都不会中断它。"
               "同时只允许一个任务（baostock 单会话，并发会互踢下线）。")
    try:
        busy = process.any_running(RUNS_DIR)
    except RuntimeError as e:
        st.error(str(e))
        busy = None
    card = _card_for(busy)
    # 三张卡片等宽并排（§2.4）：纵向堆叠时要滚很久才看得见第三张。
    # fragment 写进列里是允许的（实测过）——列就是它的父容器，局部重跑照旧只动这一列。
    for col, name in zip(st.columns(len(jobs.JOBS)), jobs.JOBS):
        with col:
            card(name, busy or "")


st.set_page_config(page_title="quant_demo v0.2.0", layout="wide")
# 字体与语义化 CSS（app/theme.py）。必须每轮都注入：Streamlit 每次 rerun 重画整棵
# 元素树，上一轮的 <style> 不留下来。底色/主色不在这里——那些走 .streamlit/config.toml。
theme.inject()
st.sidebar.title("quant_demo")
page = st.sidebar.radio("页面", ["回测报告", "个股K线", "今日信号", "任务控制台"])
# 面板自 v0.2.0 起能在本机起进程，"纯只读"从此是假话（设计 §4.3）。
st.sidebar.caption("本面板可在**本机**启动三个任务（全市场扫描 / 每日信号 / 回测），"
                   "同时只允许一个任务；进度、日志与结果见「任务控制台」页。"
                   "命令行入口全部保留，两种方式等价。")
# 必须是侧边栏里看得见的一句，不能只写在模块 docstring 里：面板能执行本机命令，
# 暴露到网络就等同于把远程命令执行接口挂到局域网上（设计 §5.3）。
st.sidebar.warning("安全提示：本面板可在本机执行脚本，仅限 localhost 使用，"
                   "请勿通过 `--server.address 0.0.0.0` 暴露到局域网。")
st.sidebar.caption("策略仅用于学习，不构成投资建议。")
{"回测报告": page_backtest, "个股K线": page_kline, "今日信号": page_signals,
 "任务控制台": page_console}[page]()
