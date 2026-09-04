"""「信号」页：固定池的每日买卖信号 + 页尾的全市场扫描区块。

两张表的每一行都带按钮，各自补上闭环里的一环：

- 扫描表的 ＋（v0.2.2 M3 §3.4 B）：看到信号当场加进信号池——"发现 → 跟踪"；
- 两张表的「＋ 记一笔」（v0.3.0 §5.1）：真的下单之后当场留痕——"决策 → 留痕"。
  它预填代码/名称/日期/方向/来源并跳到「记账」页（v0.3.1 拆页后的录入子页）；
  **不预填成交价**，因为信号那天的收盘价不是用户的成交价。
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import guide
import journal_ui
import pool
import theme
import ui
from quant.report import fmt
from quant.signal import scan_meta
from quant.strategy import strategy_label


def _record_column(df: pd.DataFrame, names: dict[str, str], click_key: str) -> dict:
    """「＋ 记一笔」那一列。预填按**这一轮**的行序绑定（回调跑在下一轮之前）。"""
    return {journal_ui.RECORD_COLUMN: journal_ui.record_column(
        journal_ui.prefills(df, names), click_key)}


def _scan_columns(df: pd.DataFrame, symbols: tuple[str, ...],
                  names: dict[str, str]) -> dict:
    """扫描表列配置 + 末列的两个按钮（加进信号池 / 记一笔）。

    `allowed` 与 `symbols` 都取自**这张表自己**：扫描 CSV 里的每一只都是
    get_all_symbols 筛过的（主板、非 ST、上市满 400 天），所以这条加入路径
    不必联网就满足"必须在扫描池内"那条规则（校验仍在 quant.universe 里做）。
    """
    return {
        **fmt.scan_column_config(),
        pool.ADD_COLUMN: st.column_config.ButtonColumn(
            pool.ADD_COLUMN, on_click=pool.on_add,
            args=(symbols, ui.CONFIG_PATH, frozenset(symbols)),
            key=pool.ADD_CLICK_KEY,
            help="把这一行的标的加进信号池（写入本地 config/universe.local.yaml），"
                 "此后「信号跟踪」会替你盯它的卖出信号。已在池中的行不可点。"),
        **_record_column(df, names, journal_ui.SCAN_CLICK_KEY),
    }


SCAN_STRATEGY_KEY = "scan_filter_strategies"


def _filter_by_strategy(df: pd.DataFrame) -> pd.DataFrame:
    """按策略筛这张扫描表。**必须在别的什么都还没做之前调一次**，返回值同时喂给
    表格、`＋ 加入` 的 symbols 与「＋ 记一笔」的预填。

    为什么这条约束是硬的：那两个按钮都按**行号/行序**绑定
    （`pool._clicked_symbol` 拿 session_state 里的 row 去索引传进去的 symbols；
    `journal_ui.prefills` 同样按这一轮的行序）。给表格喂筛后的、给按钮喂筛前的，
    点 ＋ 就会加错票、记一笔会预填错代码——而且不会有任何报错。

    候选只列**这份 CSV 里真出现过的策略**（同 journal_ui.filter_options 的口径），
    不给注册表全集：全集里没出信号的那些选了也是空表。
    """
    if "strategy" not in df.columns or df.empty:
        return df
    options = journal_ui.filter_options(df, "strategy")
    if len(options) < 2:                 # 只有一个策略时这个控件是纯噪声
        return df
    picked = st.multiselect("按策略筛", options, default=[], key=SCAN_STRATEGY_KEY,
                            format_func=strategy_label,
                            help="留空 = 全部。同一只票被两个策略同时报出来只说明"
                                 "两套规则今天都满足，**不代表信号更强**。")
    if not picked:
        return df
    return df[df["strategy"].isin(picked)].reset_index(drop=True)


def _scan_table(df: pd.DataFrame, names: dict[str, str]) -> None:
    """扫描表：能读到信号池就带 ＋ 列，读不到就只留「记一笔」。

    配置读不到时**照常显示表格**：这页的价值是那些信号，不该因为一份改坏的
    配置整页打不开；但要明说 ＋ 为什么不见了，否则用户只会以为功能没了。
    「记一笔」不依赖配置（它只写 journal/trades.csv），所以那一列照留。
    """
    try:
        in_pool = pool.current(ui.CONFIG_PATH)
    except pool.CONFIG_ERRORS as e:
        st.caption(f"读不到信号池配置（{type(e).__name__}: {e}），本表暂不提供「＋ 加入」；"
                   "详情见「信号池」页。「＋ 记一笔」不依赖配置，照常可用。")
        # 策略列只在**显示层**换中文显示名（map_strategy_labels 返回新表）：
        # 预填与按钮回调（_record_column）拿的仍是原 df——journal 的 source 存键。
        ui.data_table(fmt.map_strategy_labels(journal_ui.record_table(df)),
                      {**fmt.scan_column_config(),
                       **_record_column(df, names, journal_ui.SCAN_CLICK_KEY)},
                      "当日无新信号", hint=guide.TABLE_HINTS["scan"],
                      color_columns=ui.SCAN_COLOR_COLUMNS,
                      height=ui.SCAN_TABLE_HEIGHT)
        return
    symbols = tuple(str(s) for s in df["symbol"])
    ui.data_table(fmt.map_strategy_labels(
                      journal_ui.record_table(pool.scan_table(df, in_pool))),
                  _scan_columns(df, symbols, names),
                  "当日无新信号", hint=guide.TABLE_HINTS["scan"],
                  color_columns=ui.SCAN_COLOR_COLUMNS,
                  height=ui.SCAN_TABLE_HEIGHT)


def scan_section(names: dict[str, str]) -> None:
    """全市场扫描区块（v0.1.1）：读 output/scan/ 最新一份产物。

    v0.2.4 起同一天可能有两份产物（全量 `<date>.csv` 与试跑 `<date>_limit{N}.csv`），
    选哪份由 ui.latest_scan 决定（全量优先），标题旁的徽标说清这份是什么范围。
    """
    latest = ui.latest_scan()
    if latest is None:
        st.info(guide.EMPTY_STATES["scan"])
        return
    # 标题必须带扫描日期：停牌日/忘跑的日子，别让人把旧扫描当今天的。
    # 日期取 scan_day 而不是整个 stem——试跑产物的 stem 带着 `_limit3` 那截给机器看的后缀。
    # 扫描被 Ctrl-C 打断可能留下零字节/半截 CSV：EmptyDataError / ParserError
    # 都是 ValueError 子类，与回测页同一套容错口径，崩页不如明说。
    # 读盘排在标题之前：标题要带条数，而条数只有读完才知道。
    try:
        df = pd.read_csv(latest, dtype={"symbol": str})
    except (ValueError, OSError) as e:
        st.html(theme.section(f"全市场扫描（{scan_meta.scan_day(latest)}）",
                              ui.scan_scope_pill(latest)))
        st.error(f"扫描文件 {latest.name} 读取失败（{type(e).__name__}），"
                 f"多半是扫描中途被打断；请删除该文件后重新运行扫描。")
        return
    # **筛选必须排在这里**（见 _filter_by_strategy 的 docstring）：筛后的这一份
    # 同时喂给表格、＋ 加入的 symbols 与记一笔的预填，三处按行序绑定。
    shown = _filter_by_strategy(df)
    # 标题带条数，用词与控制台那行就绪状态对齐（steps.scan_status 也说"报了 N 条"）。
    # 数的是**屏幕上这张表**的行数，不是磁盘 meta 里的 signals——筛过之后那两个数
    # 就不是一回事了，而用户看的是屏幕。筛过时两个数都给，才看得出自己筛掉了多少。
    count = (f"报了 {len(df)} 条" if len(shown) == len(df)
             else f"筛出 {len(shown)} / {len(df)} 条")
    st.html(theme.section(f"全市场扫描（{scan_meta.scan_day(latest)}）· {count}",
                          ui.scan_scope_pill(latest)))
    _scan_table(shown, names)


def page_signals() -> None:
    ui.page_head("信号")
    pool.show_flash()   # 扫描表里点了 ＋ 之后那句 toast / 错误（回调跑在重跑之前）
    # 本页同屏展示两类产物：固定池信号跟踪 + 页尾的全市场扫描区块，故控制条有两条
    ui.control_bar("daily_signal", "market_scan")
    # 名称查一次两张表共用：信号跟踪 CSV 没有 name 列，而「记一笔」要把名称一起预填
    # （查不到就留空，绝不编一个）。扫描表自己带 name，这份查询对它是冗余的兜底。
    names = ui.symbol_names()
    sig_dir = ui.OUTPUT / "signals"
    # 只认**文件名就是交易日**的产物，且按日期排序。朴素的 sorted(reverse=True) 会让
    # 任何非日期文件名压过真日期文件（'z' > '2'，一个手工备份 zzz-latest.csv 就够），
    # 而下面标题里直接印的就是 latest.stem——那会把文件名当日期显示出来。
    # 判据与 quant.signal.scan_meta.is_day 共用一份，避免第三次只修一边
    # （steps.py 已经改过，这一页当时漏了）。
    files = sorted((p for p in sig_dir.glob("*.csv") if scan_meta.is_day(p)),
                   key=lambda p: p.stem, reverse=True) if sig_dir.is_dir() else []
    # 无信号记录不能 return 早退：全市场扫描区块在页尾，早退会把它一并吞掉
    if not files:
        st.info(guide.EMPTY_STATES["signal"])
    else:
        _latest_signals(files, names)
    scan_section(names)


def _latest_signals(files: list, names: dict[str, str]) -> None:
    """最新一份信号清单 + 其余那些的历史表。

    单独成函数是为了让"读坏文件"那条路能**早退**而不吞掉页尾的扫描区块——
    scan_section 由 page_signals 在本函数之后调，不受这里的 return 影响。
    """
    latest = files[0]
    st.html(theme.section(f"最新信号（{latest.stem}）"))
    # 与扫描 CSV 同一套容错：半截/零字节文件不许把整页打没
    try:
        df = pd.read_csv(latest, dtype={"symbol": str})
    except (ValueError, OSError) as e:
        st.error(f"信号文件 {latest.name} 读取失败（{type(e).__name__}），"
                 f"多半是任务中途被打断；删掉该文件后重跑「信号跟踪」即可。")
        return
    # 表格渲染统一走 ui.data_table：空表时那句提示必须是 if/else **语句**，
    # streamlit 的 magic 会把裸三元（ast.IfExp）整条包进 st.write()，
    # 于是 st.dataframe 的返回值被当对象内省，把整份 API 手册糊在信号表下面。
    # 策略列显示中文显示名；预填（_record_column 里的 prefills）仍读原 df 的键
    ui.data_table(fmt.map_strategy_labels(journal_ui.record_table(df)),
                  {**fmt.signal_column_config(),
                   **_record_column(df, names, journal_ui.SIGNAL_CLICK_KEY)},
                  "当日无新信号", hint=guide.TABLE_HINTS["signal"])
    if len(files) == 1:
        return
    st.html(theme.section("历史信号"))
    # 逐个文件读、坏的那个单独说并跳过：整块 concat 里有一个零字节文件就是整页
    # traceback，而报错只有一句 "No columns to parse"，不告诉你是哪个文件。
    frames = []
    for f in files[1:]:
        try:
            frames.append(pd.read_csv(f, dtype={"symbol": str}))
        except (ValueError, OSError) as e:
            st.warning(f"跳过 {f.name}（{type(e).__name__}），其余照常显示。")
    if not frames:
        st.write("无")
        return
    hist = pd.concat(frames, ignore_index=True)
    # 历史表不再重复那行灰字：同一页里连着出现两遍等于噪声。
    # 也刻意**不**带「记一笔」：补记几个月前的老交易走录入表单更合适，
    # 而这张表可能有几百行，多一列按钮只会让"今天该做什么"更难看清。
    ui.data_table(fmt.map_strategy_labels(hist), fmt.signal_column_config(), "无")
