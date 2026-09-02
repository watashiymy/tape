"""面板的共享工具（v0.2.2 §4 拆分）：页头、指标网格、数据表、控制条与路径。

**为什么这些不留在 dashboard.py**：设计 §4 要求 dashboard.py 只做导航装配
（六页 × st.Page + 侧栏），页面函数拆到 app/pages_*.py。而页面函数要用页头、
表格渲染这些共享件——若共享件仍留在 dashboard.py，页面模块就得 `import dashboard`，
那会把主脚本**再执行一遍**（streamlit 下主脚本是 __main__，`import dashboard`
会得到另一个模块对象）：注入两次样式、装配两次导航。所以共享件下沉到本模块，
dashboard.py 与 pages_*.py 都只依赖它，单向、无环。

**路径为什么要 bind()**：ROOT 从 __file__ 推出来，本模块在生产里只有一份，
按 __file__ 推是对的。但测试会把整个 app/ 复制到 tmp_path 再跑（conftest.copy_app），
而 sys.modules 是进程级的：第二个测试 `import ui` 拿到的是**第一个测试**加载的模块
对象，它的 __file__ 指向上一个 tmp 目录。那样第二个测试会去读第一个测试的产物目录，
断言还照样"通过"——正是本项目最忌讳的静默失败。dashboard.py 每轮都调一次
`ui.bind(ROOT)`（主脚本每次 rerun 都重新 exec，ROOT 一定是当前那份），
把路径重新钉一遍。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

import guide
import theme
from quant.data import symbols
from quant.journal import store
from quant.report import fmt
from quant.strategy import strategy_label
from quant.runner import jobs, process, view
from quant.signal import scan_meta

# 生产里的默认值（app/ui.py 的上一级就是仓库根）。测试会用 bind() 覆盖。
ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "output"
RUNS_DIR = OUTPUT / "runs"
CONFIG_PATH = ROOT / "config" / "settings.yaml"
CACHE_DIR = ROOT / "data" / "cache"
SYMBOLS_PATH = ROOT / "data" / "symbols.parquet"
# 交易日志（v0.3.0）。刻意不在 data/ 下：那里混着 cache/ 与 symbols.parquet
# （都已 gitignore），而这份是**用户数据，要留住**（store.TRADES_PATH 的同一个约定）。
JOURNAL_PATH = ROOT / store.TRADES_PATH


def bind(root: Path) -> None:
    """把全部路径钉到 root（见模块 docstring 里"为什么要 bind"）。"""
    global ROOT, OUTPUT, RUNS_DIR, CONFIG_PATH, CACHE_DIR, SYMBOLS_PATH, JOURNAL_PATH
    ROOT = root
    OUTPUT = root / "output"
    RUNS_DIR = OUTPUT / "runs"
    CONFIG_PATH = root / "config" / "settings.yaml"
    CACHE_DIR = root / "data" / "cache"
    SYMBOLS_PATH = root / "data" / "symbols.parquet"
    JOURNAL_PATH = root / store.TRADES_PATH


#: 页对象登记表（v0.5.0）：{页 key: st.Page}。`st.page_link` 与 `st.switch_page`
#: 收的都是 **Page 对象**，而页面函数没有文件路径可给。按下标从导航分组里取
#: 「第几个」是行不通的——插一页就全体错位，而且错位之后链接照常渲染、
#: 点下去跳到另一页，没有任何报错。所以由 dashboard.py 建完 Page 就登记进来。
_PAGES: dict[str, object] = {}


def bind_pages(pages: dict) -> None:
    """dashboard.py 建完 st.Page 之后调一次。每轮 rerun 都重建，所以整体覆盖。"""
    _PAGES.clear()
    _PAGES.update(pages)


def page_ref(key: str):
    """按 key 取 Page 对象。未登记时**响亮失败**而不是返回 None：
    `st.page_link(None)` 会渲染出一个点了没反应的链接，谁也不会报上来。"""
    if key not in _PAGES:
        raise KeyError(
            f"页 {key!r} 还没登记（已登记：{sorted(_PAGES)}）。"
            f"dashboard.py 建完 st.Page 之后必须调 ui.bind_pages()。")
    return _PAGES[key]


AUTO_REFRESH_S = "2s"     # 运行中卡片的局部刷新间隔（空闲时不设，避免面板空转）
LOG_TAIL_LINES = 30
LOG_BOX_HEIGHT = 220      # 日志区固定高度滚动（§2.4）：几十行日志不许把版面顶飞
SCAN_TABLE_HEIGHT = 600   # 扫描结果表（v0.5.0）：一次全量能报上百条，默认约十行的窗口要翻十几屏
# 标的名称取自最近这么多份扫描 CSV。名称几乎不变，30 份够用，同时把每次渲染的
# 文件读取量兜住（一天一份，长期跑下来 output/scan/ 会攒到几百份）。
NAME_LOOKBACK_FILES = 30

# 每页顶部那句话（§2.4 通用页头）。只讲"这页给你看什么、什么时候看"，
# 数字必须与实测一致（baostock 约 17:30 后才有当日数据 —— README/spec §11）。
# 键就是页名（page_head 按页名取值），顺序与 dashboard.py 的 PAGES 一致；
# 少一页会让那一页的页头 KeyError 崩页（tests/test_dashboard_nav.py 钉住两边一致）。
# 说明手册那四页（v0.5.0）的键必须逐个**字面量**写出来，不许改写成
# `**{p.title: p.lead for p in guide.GUIDE_PAGES}`：tests/test_dashboard_guide.py
# 按 AST 读这个字典的键（`k.value`），推导式里的键在 AST 里是 None，那条测试会
# 以 AttributeError 炸掉——而它守的正是"每页都有页头文案"这件事。
PAGE_INTRO = {
    "使用说明": guide.PAGE_INTRO,
    "读懂回测": guide.guide_page("guide_metrics").lead,
    "自定义策略": guide.guide_page("guide_custom").lead,
    "边界与安全": guide.guide_page("guide_limits").lead,
    "任务控制台": "在本机启动三个任务并盯进度、日志与结果；"
             "同时只允许一个任务（baostock 单会话）。",
    # 「17:30」只留在空态文案里：那才是用户问"为什么是空的"的时刻，
    # 而空态每天都会出现（绝大多数日子没有新信号）。页头这句覆盖面更广但不可执行。
    "今日信号": "固定池的每日买卖信号，页尾是全市场扫描当日新 BUY。",
    "记账": "记你**真实成交**的每一笔；历史日志可筛选、可改删、"
          "可导出（CSV / Excel）。",
    "持仓与盈亏": "按整本日志算出的当前持仓、已实现盈亏与来源对比；"
             "不受「记账」页筛选的影响。",
    # 「改动写进哪个文件」交给页内那句**动态**说明（它知道当前到底是本地文件
    # 还是种子），这里只说这页是干什么的。
    "信号池": "增删「每日信号」跟踪的固定池；命令行运行同样生效。",
    "回测报告": "历史检验的结果：绩效指标、净值报告与逐笔成交，读自 output/ 里已完成的回测。",
    "个股K线": "单只标的的日线走势，叠加本次回测在它身上的买卖点。",
}

# run_backtest.py 的 {策略键}_{YYYYMMDD}_{HHMMSS}。定义在 fmt（run_label 也用它），
# 这里只转发：排序键与显示名对同一个形态各写一份正则，迟早有一份悄悄过期。
RUN_STAMP = fmt.RUN_STAMP

# 一次可展示的回测最少要有这三件；缺任何一件都是被 Ctrl-C 打断留下的半截目录。
# run_backtest.py 已把 metrics.json 挪到最后写作为完成标记，但老目录仍可能残缺。
_REQUIRED_FILES = ("metrics.json", "report.html", "trades.csv")

SCAN_COLOR_COLUMNS = ("pct_chg",)   # 扫描表里唯一有方向的列（红涨绿跌）

CONSOLE_HINT = ("进度、实时日志与完整结果见侧边栏「任务控制台」页；"
                "这里按默认参数运行（要指定 --limit / --date / 策略请去控制台）。")


def page_head(page: str) -> None:
    """通用页头（§2.4）：页名衬线大字 + 一句话说明 + 右侧全局任务状态 pill。

    这里**不**报 st.error：状态文件损坏时下面的控制条/卡片会报，页头再喊一遍只是噪声；
    但 pill 必须如实说"未知"，不能退回"空闲"（那是猜的，而且正是 fail-safe 要挡的误导）。
    """
    try:
        text, kind = view.busy_pill(process.any_running(RUNS_DIR))
    except RuntimeError:
        text, kind = view.UNKNOWN_TEXT, view.PILL_IDLE
    st.html(theme.page_head(page, PAGE_INTRO[page], theme.pill(text, kind)))


def metric_grid(metrics: dict, per_row: int = 4) -> None:
    """指标卡网格。默认 2 行 × 4 列（§2.4）。

    `per_row` 留着给窄容器用；v0.5.0 起控制台的回测卡片整幅宽渲染，不再传 2。

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


def data_table(df: pd.DataFrame, config: dict, empty_text: str, *,
               hint: str = "", color_columns: tuple[str, ...] = (),
               height: int | str = "auto") -> None:
    """信号/扫描/交易表的统一渲染口。

    `hint` 是表格上方那行灰字（§3.2 就地帮助），只解释关键列——逐列说明在
    「使用说明」页里。**表格为空时不出**：对着一张空表解释列只是噪声，
    而且会把"当日无新信号"这句真正要看的话往下挤。

    `height` 给行数多的表用。缺省 `"auto"` 就是 `st.dataframe` 自己的缺省
    （**不能写 None**——1.61 会抛 StreamlitInvalidHeightError，而这一路会把
    信号池、记账、回测等所有走 data_table 的页面一起打没：实测 50 项测试变红）：Streamlit 不给
    height 时表格只显示约十行，而一次全量扫描能报出上百条——十九屏内滚动条里挑票
    是这一页最花时间的动作。同 pages_console 的日志框（ui.LOG_BOX_HEIGHT）一个理由。

    `color_columns` 走 pandas Styler：`column_config` 里没有条件着色能力
    （NumberColumn 无 color 参数），红绿只能这么给；而 column_config 的格式串
    优先级高于 Styler，所以千分位、百分号不会被顶掉。
    必须写成 if/return 语句：裸三元会被 streamlit magic 整条包进 st.write。
    """
    if not len(df):
        st.write(empty_text)
        return
    if hint:
        st.caption(hint)
    data = fmt.direction_styler(df, color_columns) if color_columns else df
    st.dataframe(data, width="stretch", column_config=config, hide_index=True,
                 height=height)


def symbol_names() -> dict[str, str]:
    """symbol → 名称。两个离线来源，合并规则见下。查不到的键**不出现**在字典里
    （由渲染层显示 fmt.MISSING，绝不在这里编一个"未知"——那看着像个名字）。

    **来源一：全市场清单 data/symbols.parquet（底稿）**。它含每只票的 name、覆盖
    整个扫描池（实测约 3000 只），由 run_market_scan.py 每 7 天内刷新一份。
    这是 v0.2.3 加的，也是名称列真正被填满的原因。

    **来源二：扫描 CSV（补充 + 覆盖）**。它只记录**出了信号**的标的（实测 5 份文件
    累计 313 只），所以单独用它时缺名是常态——这正是过去信号池表格名称列大半空缺的
    原因。但它不能丢：清单是**筛过**的（主板、非 ST、上市满 400 天），后来变成 ST
    的票会从清单里消失，而它可能还在用户的信号池里、还在老扫描文件里。

    **冲突取谁：CSV 覆盖清单。** CSV 记的是"扫描那天实际看到的名字"，而清单文件按
    is_fresh 的窗口最多可以滞后 7 天。正常情况下两者恒等（扫描写 CSV 用的就是当轮
    那份清单），这条规则只在清单明显更旧时才起作用；但它必须是**确定**的一条。
    多份 CSV 之间仍是"新的先到者胜"（改过名的取最近一份）。

    坏文件一律降级、不许把页面打没：半截 CSV 跳过；清单文件坏了退回只用 CSV，并把
    路径与自愈办法用 st.warning 如实说出来（命令行那边是**响亮抛错**——那里必须响亮，
    静默会退化成每轮白拉 2-4 分钟；这里只是显示名字，页面打不开的代价更大）。
    """
    names: dict[str, str] = {}
    scan_dir = OUTPUT / "scan"
    files = sorted(scan_dir.glob("*.csv"), reverse=True) if scan_dir.exists() else []
    for path in files[:NAME_LOOKBACK_FILES]:   # 新的排前面，先到者胜（改过名的取最近）
        try:
            df = pd.read_csv(path, dtype={"symbol": str})
        except (ValueError, OSError):
            continue
        if "symbol" not in df.columns or "name" not in df.columns:
            continue
        for sym, name in zip(df["symbol"], df["name"]):
            names.setdefault(str(sym), name)
    for sym, name in _listing_names().items():
        names.setdefault(sym, name)            # CSV 已有的不覆盖（见上面"冲突取谁"）
    return names


def _listing_names() -> dict[str, str]:
    """全市场清单里的 symbol → 名称；没有清单文件就返回空表（降级到只用扫描 CSV）。"""
    try:
        loaded = symbols.load_symbols(SYMBOLS_PATH)
    except RuntimeError as e:
        st.warning(f"{e} 名称暂时只能取自扫描结果，多数标的会显示 —。")
        return {}
    if loaded is None:
        return {}
    listing, _as_of = loaded
    return {str(s): n for s, n in zip(listing["symbol"], listing["name"])}


# ---------------------------------------------------------------- 扫描产物（v0.2.4）

def latest_scan() -> Path | None:
    """面板该显示哪份扫描产物：最新一天的，**同一天优先全量**（试跑只是抽样）。
    挑选规则连同"为什么不能用 sorted()[0]"都在 quant.signal.scan_meta 里（可单测）。"""
    return scan_meta.latest_scan(OUTPUT / "scan")


def scan_scope_pill(csv_path: Path) -> str:
    """扫描范围徽标的 HTML：`全量 3010 只` / `试跑 30 只` / `范围未知`。

    这个徽标要回答的是一个真实发生过的困惑：面板上写着"当日无新信号"时，
    到底是全市场真没机会，还是一次 3 只票冒烟测试的残渣。

    三处降级都不许把页面打没，但话都要说全：
    - 没有 meta（v0.2.4 之前的老产物）→「范围未知」+ tooltip 说清为什么未知，
      **不猜也不编**：显示成"全量"或"试跑"都可能是错的，而这正是用户拿来做决定的依据；
    - meta 损坏 → 徽标同样退回「范围未知」，但 tooltip 与 st.warning 说的是**真原因**
      （文件坏了，不是老产物）——两者混为一谈，那份坏文件就永远不会被发现；
    - 一律中性灰（PILL_IDLE）：这是一条事实注记，不该跟"运行中/失败"抢眼。
    """
    try:
        text, tip = scan_meta.scope_badge(scan_meta.load_meta(csv_path))
    except RuntimeError as e:
        st.warning(str(e))
        text, tip = scan_meta.UNKNOWN_SCOPE, str(e)
    return theme.pill(text, view.PILL_IDLE, tip)


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


def run_symbols(run: Path) -> list[str]:
    """某次回测跑了哪些标的。

    **先看 `config_snapshot.json` 的 universe**——那是这趟回测真正的标的清单，
    由 run_backtest.py 原样留档。v0.5.0 起单标的 K 线图不再落盘
    （每份 `kline_*.html` 内嵌一整份 plotly.js，十只票就是 48 MB，而这一页的图
    是**现场重画**的、从来不读那些文件），清单只能从这里取。

    读不到就退回按 `kline_*.html` 的文件名列——**老产物全靠这条**（v0.5.0 之前
    落的那 58 个目录都有那些文件）。两条路都空就返回空列表，由页面显示空态：
    编一个清单出来会让人对着一只根本没回测过的票看图。
    """
    try:
        snapshot = json.loads((run / "config_snapshot.json").read_text(encoding="utf-8"))
        universe = [str(s) for s in snapshot.get("universe") or []]
        if universe:
            return sorted(universe)
    except (ValueError, OSError):
        pass                    # 快照缺失/损坏都退回文件名，不打断这一页
    return sorted({p.stem.replace("kline_", "") for p in run.glob("kline_*.html")})


def resolve(path_str: str) -> Path:
    """脚本打进日志的产物路径是相对仓库根的（"output/scan/2026-08-24.csv"）。
    面板的 cwd 未必是仓库根，一律按 ROOT 兜底，否则完成后的结果区永远是"读取失败"。"""
    p = Path(path_str)
    return p if p.is_absolute() else ROOT / p


# ---------------------------------------------------------------- 任务启停（v0.2.0 §4.1）
# 三个落点集中在这里：控制条（本模块）与控制台卡片（app/pages_console.py）共用。

def start_job(job_name: str, params: dict) -> None:
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


def stop_job(state: process.RunState) -> None:
    """停止唯一的落点。信号一律由 process.stop 发（SIGTERM 进程组 → 10s → SIGKILL），
    面板自己绝不碰 os.kill。"""
    process.stop(state, RUNS_DIR)
    st.rerun()   # 同 start_job：解禁另外两个"开始"并摘掉轮询


def rerun_job(job_name: str, state: process.RunState) -> None:
    """沿用上次 argv 重跑。状态文件是手工改得动的普通 JSON，所以先 parse_argv
    反解、再由 start_job 重新 build_argv，让它整个过一遍白名单闸门。"""
    try:
        params = jobs.parse_argv(job_name, state.argv)
    except ValueError as e:
        st.error(f"无法重跑: {e}")
        return
    start_job(job_name, params)


def control_bar(*job_names: str) -> None:
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
            start_job(name, {})           # 精简条无参数控件：一律默认参数
        if stop_col.button("⏹ 停止", key=f"bar_stop_{name}", disabled=not running):
            stop_job(state)
        if notice:
            notices.append(notice)        # 点名是谁在跑，否则灰按钮无从解释
    # 互斥是全局的，两条控制条拿到的是同一句提示——去重后只说一次，别刷屏
    for text in dict.fromkeys(notices):
        st.caption(text)
    st.caption(CONSOLE_HINT)
    # 灰字换成真链接（v0.5.0）：点完 ▶ 之后这一页 15 分钟不会自己动一下，
    # 而"该去哪看进度"过去只是一句话。一步到位。
    st.page_link(page_ref("console"), label="去「任务控制台」看实时进度与日志",
                 icon=":material/play_circle:")
    st.divider()


#: 「回测报告」页那张总览表的列。**「标的数」不能省**：output/ 里有两次同一天、
#: 开关完全一样、总收益 113% vs 8.6% 的跑，看着像 bug——真实差别是"7 只信号池"
#: 与"10 只基准池"。没有这一列，那两行在表里长得一模一样。
_OVERVIEW_COLUMNS = ("策略", "跑的时间", "标的数", "趋势过滤", "ATR止损",
                     "总收益", "最大回撤", "夏普", "交易次数")


def runs_overview(runs: list[Path]) -> pd.DataFrame:
    """把 output/ 里每次回测折成一行，供「回测报告」页在下拉框上方铺开。

    为什么需要它：本机已经攒了 58 次回测，下拉框里的标签只有「策略显示名 + 时间戳」
    ——同一分钟里能挤着四个，认不出哪次是哪次。而**认不出的恰好是最要紧的那几次**：
    README 那张叠加层归因表的四组对照（无叠加层 / 只开趋势过滤 / 只开止损 / 双开）
    就是同一天连着跑的，唯一的区别在 config_snapshot 里，下拉框上一个字都看不到。

    缺键一律显示 `—`，**不猜**：v0.5.0 之前的产物（本机 43/58）没有 overlays 段，
    那两列就该是 `—`，填 False 等于替它编一个"当时关着"的事实。
    """
    rows = []
    for run in runs:
        row = dict.fromkeys(_OVERVIEW_COLUMNS, fmt.MISSING)
        m = RUN_STAMP.search(run.name)
        row["策略"] = strategy_label(run.name[:m.start()]) if m else run.name
        row["跑的时间"] = fmt.run_stamp_label(m.group(1)) if m else fmt.MISSING
        try:
            metrics = json.loads((run / "metrics.json").read_text(encoding="utf-8"))
        except (ValueError, OSError):
            metrics = {}
        for col, key in (("总收益", "total_return"), ("最大回撤", "max_drawdown"),
                         ("夏普", "sharpe"), ("交易次数", "n_trades")):
            if key in metrics:
                row[col] = fmt.fmt_metric(key, metrics[key])
        try:
            snap = json.loads((run / "config_snapshot.json").read_text(encoding="utf-8"))
        except (ValueError, OSError):
            snap = {}
        if snap.get("universe"):
            row["标的数"] = len(snap["universe"])
        overlays = snap.get("overlays") or {}
        for col, key in (("趋势过滤", "trend_filter"), ("ATR止损", "atr_stop")):
            cfg = overlays.get(key)
            if isinstance(cfg, dict) and "enabled" in cfg:
                row[col] = "开" if cfg["enabled"] else "关"
        rows.append(row)
    return pd.DataFrame(rows, columns=list(_OVERVIEW_COLUMNS))
