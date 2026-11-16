"""面板的共享工具（v0.2.2 §4 拆分）：页头、指标网格、数据表、任务条与路径。

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
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

import guide
import theme
from quant.data import symbols
from quant.journal import store
from quant.report import fmt
from quant.strategy import strategy_label
from quant.runner import jobs, process, steps, view
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

# 每页页名后面那个帮助圆点里的话（§2.4 通用页头）。只讲"这页给你看什么、什么时候看"。
# **2026-11-16 第二轮之前它是页名旁常驻的一行灰色小字**。用户的话：这类解释性文字读起来
# 是有成本的，用「？」提示、光标移上去再弹出来就行——所以页头只剩页名与状态，这句话要看才出来。
# 键就是页名（page_head 按页名取值），顺序与 dashboard.py 的 PAGES 一致；
# 少一页会让那一页的页头 KeyError 崩页（tests/test_dashboard_nav.py 钉住两边一致）。
# 说明手册那三页（v0.5.0）的键必须逐个**字面量**写出来，不许改写成
# `**{p.title: p.lead for p in guide.GUIDE_PAGES}`：tests/test_dashboard_guide.py
# 按 AST 读这个字典的键（`k.value`），推导式里的键在 AST 里是 None，那条测试会
# 以 AttributeError 炸掉——而它守的正是"每页都有页头文案"这件事。
# 进的是帮助圆点的提示框：认 `**加粗**`、空行分段、行首 `- ` 的列表（theme._tip_markup）。
PAGE_INTRO = {
    # 硬约束：不能含「今天」/「今日」（test_home_copy_never_says_today）。
    "市场概览": guide.HOME_LEAD,
    "使用说明": guide.guide_page("guide").lead,
    "读懂回测": guide.guide_page("guide_metrics").lead,
    "边界与安全": guide.guide_page("guide_limits").lead,
    "任务控制台": "启动任务、查看进度与结果；同时只跑一个。",
    "信号": "信号池标的的最新买卖信号；页尾是全市场扫描发现的新买入信号。",
    "记账": "记你真实成交的每一笔；历史记录可筛选、可修改、可删除、可导出。",
    "持仓与盈亏": "按全部记录算出的当前持仓、已实现盈亏与来源对比。",
    "信号池": "增删「信号跟踪」每天盯着的那批标的。",
    "回测报告": "历史检验的结果：绩效指标、净值曲线与逐笔成交。",
    # 后两段原来是 K 线图下面常驻的三行灰字（怎么操作这张图、▲▼ 是什么）。
    "个股K线": ("单只标的的日线走势，叠加这次回测在它身上的买卖点。\n\n"
              "悬停查看开高低收与成交量、成交额；滚轮或触控板双指缩放，拖动平移，双击复位。\n\n"
              "▲▼ 标在 K 线外侧：▲ 在最低价下方是买入，▼ 在最高价上方是卖出。"
              "横轴按交易日紧排，不留周末与节假日的空档。"),
}


#: 监听地址算不算"只有本机"。侧栏的安全警告只在**不是**时出现（dashboard.py）。
#: 空串 / None 是 streamlit 的出厂默认——监听所有网卡，所以归到"不是"。
LOOPBACK_ADDRESSES = frozenset({"127.0.0.1", "localhost", "::1"})


def is_loopback(address) -> bool:
    return str(address or "").strip().lower() in LOOPBACK_ADDRESSES


# run_backtest.py 的 {策略键}_{YYYYMMDD}_{HHMMSS}。定义在 fmt（run_label 也用它），
# 这里只转发：排序键与显示名对同一个形态各写一份正则，迟早有一份悄悄过期。
RUN_STAMP = fmt.RUN_STAMP

# 一次可展示的回测最少要有这三件；缺任何一件都是被 Ctrl-C 打断留下的半截目录。
# run_backtest.py 已把 metrics.json 挪到最后写作为完成标记，但老目录仍可能残缺。
_REQUIRED_FILES = ("metrics.json", "report.html", "trades.csv")

SCAN_COLOR_COLUMNS = ("pct_chg",)   # 扫描表里唯一有方向的列（红涨绿跌）


def page_head(page: str, *, help_center: bool = True) -> None:
    """通用页头（§2.4）：页名衬线大字 + 帮助圆点（这页给你看什么）+ 右侧全局任务状态 pill。

    `help_center=True`（默认）时一行两栏：左栏是页头，右栏靠右一个「帮助中心」链接到「使用说明」
    （2026-09-30：手册不是系统的功能，不占侧边栏，从这里进）。用分栏不用水平容器：
    st.container(horizontal=True) 在 1.61.1 强制允许换行，1024 宽、侧边栏展开时会把链接挤到下一行
    （评审实测）；分栏不换行。用 st.page_link 不用页头 HTML 里的
    <a>：后者整页刷新、开新会话，记账页填到一半的内容就丢了。手册三页自己传 False（它们有页内导航）。
    两栏顶端对齐：页头自带下外边距，居中会把链接压到页名下方；顶端对齐与页名齐平。

    这里**不**报 st.error：状态文件损坏时下面的任务条/卡片会报，页头再喊一遍只是噪声；
    但 pill 必须如实说"未知"，不能退回"空闲"（那是猜的，而且正是 fail-safe 要挡的误导）。
    """
    try:
        text, kind = view.busy_pill(process.any_running(RUNS_DIR))
    except RuntimeError:
        text, kind = view.UNKNOWN_TEXT, view.PILL_IDLE
    head = theme.page_head(page, theme.pill(text, kind), help=PAGE_INTRO[page])
    if not help_center:
        st.html(head)
        return
    left, right = st.columns([4, 1], vertical_alignment="top", gap="small")
    left.html(head)
    with right.container(horizontal=True, horizontal_alignment="right"):
        st.page_link(page_ref("guide"), label=guide.HELP_CENTER_LABEL, icon=":material/help:")


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
               help: str = "", color_columns: tuple[str, ...] = (),
               height: int | str = "auto") -> None:
    """信号/扫描/交易表的统一渲染口。

    **表格上方不再有那行解释列的灰字**（2026-11-16 第二轮；原先的 `hint` 参数）：用户的话，
    解释性文字读起来是有成本的，用「？」提示就行。读表说明现在在帮助圆点里，挂哪儿分两种：
    - 表格上方有区块小标题的：调用方给 theme.section 传 help=guide.TABLE_HINTS[...]；
    - 没有小标题的（标签页里的表、折叠区里的表）：传给这里的 `help`，表格上方出一行很短的
      「读表说明 ?」。**表格为空时不出**——对着一句"还没有已平仓的交易"解释列只是噪声。

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
    if help:
        st.html(theme.help_line(guide.TABLE_HELP_LABEL, help))
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
# 三个落点集中在这里：任务条（本模块）与控制台卡片（app/pages_console.py）共用。

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


def step_status(job_name: str) -> steps.StepStatus | None:
    """某个任务「最新结果」那句话：最近一份结果是哪天的、什么范围。返回 None 表示没有这一句。

    控制台卡片标题下那一行与各页顶部的任务条共用这一处（2026-11-16 从 pages_console 搬来）：
    同一份结果在两页上各说一套，是最容易悄悄对不上的地方。
    一律**现算**、不许由页面在外面算好传进来：卡片与任务条都是 fragment，运行中每 2 秒
    只重跑自己，传进来的值会停在开跑那一刻——扫描跑完了还写着「最近一次是上周」。

    `steps.scan_status` 会读扫描的范围记录，那份文件损坏时是响亮抛 RuntimeError
    （数据层的设计，不改）——调用方必须接住，否则异常会让它后面的整页都不再渲染。
    """
    if job_name == "market_scan":
        return steps.scan_status(OUTPUT / "scan", date.today())
    if job_name == "daily_signal":
        return steps.signal_status(OUTPUT / "signals", date.today())
    if job_name == "backtest":
        # 回测不在每日那条链上、没有"就绪"可言，但这一句三个任务都有：最近一次是哪个策略、
        # 什么时候跑的，一共攒了几次。按磁盘上的结果说，不按"面板起没起过它"说。
        return steps.backtest_status([fmt.run_label(p.name) for p in list_runs()])
    return None


def running_row(state: process.RunState, prog, *, stop_key: str, trailing=None) -> str:
    """正在跑的任务那一行：进度（数得出来就画进度条，否则转圈 + 阶段）+ 右端的停止按钮。
    返回细节那句话（已用多久、预计剩余、信号条数），由调用方决定要不要写出来。

    控制台卡片、各页顶部的任务条与首页的收盘数据区共用（2026-11-16）：各画一份，迟早只改一边。
    **停止按钮在这一行、不在开始按钮的位置上**：任务恰好跑完的那一刻，光标底下的按钮
    不许从「停止」换成「开始」——点下去就是又一趟十几分钟的扫描。
    **停止在进度条右边**（2026-11-16 第二轮，用户要求操作按钮统一靠右）。第一轮里任务条上的
    停止在最左，是怕它与上面几行的「开始」上下对齐；真正防住"按钮在光标下换掉"的是另一条——
    这一行排在所有任务行**之后**，它来了又走，上面哪个「开始」都不挪位置（那条没变，有测试钉着）。

    一个靠右的横向容器：进度占剩下的地方，按钮是它自己的宽度（与上面几行开始按钮的右缘对齐；
    不用按比例的分栏——窄屏时比例栏会把按钮挤成两行）。
    `trailing` 是一个可选的回调，在停止按钮**右边**接着画东西。首页用它把那个点不了的开始按钮
    留在这一行的最右端（见 pages_home._job_area：那一区在页面最底下，上下错开防不住）。
    """
    head, detail = view.progress_lines(prog, view.elapsed_seconds(state), status=state.status)
    ratio = view.progress_ratio(prog)
    with st.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center"):
        if ratio is not None:              # 必须是 if 语句：裸三元会被 streamlit magic 包进 st.write
            st.progress(ratio, text=head)
        else:
            st.status(head, state="running")     # 不确定态：转圈 + 阶段
        # "关掉浏览器也不会中断"挂在停止按钮自己的悬停说明上：想走开的人以为得先点它，其实不用。
        # 后面跟着"停掉这个任务会丢什么"（扫描：这趟已扫出的信号全丢）——要点它的那一刻才用得上。
        clicked = st.button(guide.CONSOLE_STOP, key=stop_key, icon=":material/stop:",
                            help=guide.stop_help(state.script))
        if trailing is not None:
            trailing()
    if clicked:
        stop_job(state)                    # SIGTERM 进程组 → 10s → SIGKILL，然后整页重跑
    return detail


#: 「眼下谁在跑」读不出来时（状态文件损坏），任务条 / 控制台卡片交回这个值，不交回 None。
#: None 的意思是"全空闲"，两者不能混：混了，外层就会把"不知道"当成"跑完了"。
BUSY_UNKNOWN = "\x00unknown"


def busy_changed(busy: str | None, was_busy: str) -> bool:
    """定时重画的那一轮里「谁在跑」变了没有——变了才值得整页重跑（摘掉轮询、解禁按钮、换新结果）。

    **读不出来不算变了**（2026-11-16 审查抓到的死循环）：`process.any_running` 按文件名顺序读，
    读到第一个在跑的就返回，后面那个坏掉的状态文件它根本没碰。于是整页那一轮以为"有任务在跑"、
    挂上定时重画；任务条 / 卡片自己去读坏文件时报错——若把这也算成"变了"而请求整页重跑，
    下一轮还是一样：报错 → 重跑 → 报错，页面原地打转。不知道就是不知道：报错留在页面上，
    等人把文件修好；这期间定时重画照旧，修好之后它自己会发现。
    """
    return busy != BUSY_UNKNOWN and (busy or "") != was_busy


def control_bar(*job_names: str) -> None:
    """「信号」「回测报告」「个股K线」三页顶部的任务条：这一页的数据是哪天的、要不要更新。

    **2026-11-16 改版**。改之前每个任务一行「名字 + 状态 + 开始 + 停止」，下面常驻两行灰字、
    一条去控制台的链接和一道分隔线。真机上看到的毛病：
    - 状态只说上一次运行「成功」，不说**结果是哪天的**——实测「信号」页写着成功，
      最新信号却停在两个月前；
    - 两个任务的按钮都叫「开始」，第二个一点就是半小时到两小时的全市场扫描；
    - 「停止」常驻却九成九的时候点不了，提示与链接没有任务在跑时也天天亮着；
    - 点了「开始」之后这一页不会自己动，得去控制台才知道跑完没有。

    现在一个带边框的容器里一个任务一行：任务名、状态、一句「最新结果」（与控制台卡片
    同一句话，step_status），右端一个与控制台同名的按钮（开始跟踪 / 开始扫描 / 开始回测），
    说明挂在按钮的悬停提示上。**运行中才多出一行**进度与「停止」；去控制台的链接只在
    有任务在跑、或上次没跑成时出现（view.bar_follow_up）。有任务在跑时每 2 秒只重画这一条，
    跑完整页重跑一次——下面的表自己换成新结果。

    **进度那一行排在所有任务行之后**：它出现、消失，哪个「开始」都不挪位置。
    信号跟踪十几秒就跑完，它下面那一行的按钮却是半小时到两小时的全市场扫描——任务恰好
    跑完的那一刻，光标底下的按钮不许从「停止」换成某个「开始」（与控制台卡片同一条规矩，
    有测试钉着）。「停止」在进度条右边，与页面上别的操作按钮一样靠右（见 running_row）。

    仍是薄的：实时日志、参数、结果表都在控制台页。
    必须排在各页只读逻辑的**最前面**：三页在没有结果时都会 st.info 之后早退，
    排在早退后面，恰好在"最需要点开始"的那一刻看不见。
    每页只给这一页看得见结果的任务（回测页不出扫描按钮：误点一下就是 0.5–2 小时）。
    """
    try:
        busy = process.any_running(RUNS_DIR)
    except RuntimeError as e:
        # 与控制台页同口径：互斥状态不可知就一个"开始"都不给（fail-safe），
        # 否则真有扫描在跑也照样能点，两个 baostock 会话互踢下线。
        # 只读内容照常渲染——一个坏了的 runs/*.json 不该把回测报告一起藏起来。
        st.error(str(e))
        return
    (_bar_live if busy else _bar_idle)(job_names, busy or "")


def _bar(job_names: tuple[str, ...]) -> str | None:
    """把任务条画一遍，返回眼下谁在跑（None = 全空闲，BUSY_UNKNOWN = 读不出来）。"""
    try:
        states = {name: process.read_state(name, RUNS_DIR) for name in job_names}
        busy = process.any_running(RUNS_DIR)
    except RuntimeError as e:
        # 同 control_bar：不可知就不给按钮。control_bar 那道关没拦住是可能的——
        # any_running 读到第一个在跑的就返回，不保证读过这一条上每个任务的状态文件。
        st.error(str(e))
        return BUSY_UNKNOWN
    with st.container(border=True):
        for name in job_names:
            _bar_row(name, states[name], busy)
        # —— 以下几行都是"适用时才出现"的，一律排在任务行之后：它们来了又走，任务行不挪位置。
        for name in job_names:
            state = states[name]
            if state is not None and state.status == process.RUNNING:
                _bar_running(name, state)
        if busy is not None and busy not in job_names:
            # 别的页起的任务占着互斥：点名是谁在跑，否则灰按钮无从解释。只说一次——
            # 互斥是全局的，这一条上的每一行拿到的都是同一句。跑的若是这一条自己的任务，
            # 上面已经摊着它的进度，不必再说一遍。
            st.caption(view.start_button_state(busy, job_names[0])[1])
        follow = view.bar_follow_up(busy, [s.status if s is not None else None
                                           for s in states.values()])
        if follow is not None:
            target, label = follow
            st.page_link(page_ref(target), label=label, icon=":material/play_circle:")
    return busy


def _bar_row(name: str, state: process.RunState | None, busy: str | None) -> None:
    """任务条里一个任务的那一行：任务名 + 状态 + 一句「最新结果」，右端一个「开始」。"""
    disabled, _notice = view.start_button_state(busy, name)
    status_text = ""
    try:
        if (status := step_status(name)) is not None:
            status_text = status.text
    except RuntimeError as e:
        st.warning(str(e))                 # 读不出来就不写那半句——不许顺手补一句"还没有结果"
    # 3:1 而不是 4:1：1024 宽、侧边栏展开时右栏只有一百出头像素，四个字的按钮会折成两行
    # （真机看到的「开始跟 / 踪」）。左边那句话是行内文字，窄了自己折行。
    text, action = st.columns([3, 1], vertical_alignment="center")
    text.html(theme.job_strip(jobs.JOBS[name].label, theme.pill(*view.status_pill(state)),
                              status_text))
    with action.container(horizontal=True, horizontal_alignment="right"):
        # 普通按钮而不是主按钮（琥珀色）：这三页是来**看**结果的，起任务是顺手的事；
        # 主按钮留给控制台。
        start = st.button(guide.CONSOLE_START[name], key=f"bar_start_{name}",
                          icon=":material/play_arrow:", disabled=disabled,
                          help=guide.BAR_START_HELP[name])
    if start:
        # 在按钮那一栏**外面**起：启动失败的那句话要占整行，不能挤在右边那一窄栏里。
        start_job(name, {})                # 任务条没有参数控件：一律默认参数


def _bar_running(name: str, state: process.RunState) -> None:
    """正在跑的那个任务的进度与「停止」（与控制台卡片同一套文案，ui.running_row）。"""
    detail = running_row(state, jobs.JOBS[name].parser(process.read_log(state)),
                         stop_key=f"bar_stop_{name}")
    if detail:
        st.caption(detail)                 # 已用多久、预计剩余、信号条数：会变的数，留在明面上


@st.fragment(run_every=AUTO_REFRESH_S)
def _bar_live(job_names: tuple[str, ...], was_busy: str) -> None:
    """有任务在跑：每 2 秒只重画这一条（整页重跑会把下面的表与滚动位置一起抖掉）。"""
    if busy_changed(_bar(job_names), was_busy):
        st.rerun()   # 跑完了/被停了 → 整页重跑：摘掉轮询、解禁按钮，下面的表换成新结果


@st.fragment
def _bar_idle(job_names: tuple[str, ...], was_busy: str) -> None:
    """全空闲：**不设** run_every，否则这三页会每 2 秒无谓地重画一次。
    仍包成 fragment：点「开始」起任务失败时只重画这一条，报错就落在按钮旁边。"""
    _bar(job_names)


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
            # 存成字符串而不是 int：这一列还会有 —（老回测没有快照），int 与 str 混在一列里
            # Arrow 转不过去，Streamlit 会自动降级并在控制台打一整段 traceback（真机出现过）。
            # 整张总览表全是字符串，这一列也一样。
            row["标的数"] = str(len(snap["universe"]))
        overlays = snap.get("overlays") or {}
        for col, key in (("趋势过滤", "trend_filter"), ("ATR止损", "atr_stop")):
            cfg = overlays.get(key)
            if isinstance(cfg, dict) and "enabled" in cfg:
                row[col] = "开" if cfg["enabled"] else "关"
        rows.append(row)
    return pd.DataFrame(rows, columns=list(_OVERVIEW_COLUMNS))
