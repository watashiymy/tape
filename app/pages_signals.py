"""「今日信号」页：固定池的每日买卖信号 + 页尾的全市场扫描区块。

两张表的每一行都带按钮，各自补上闭环里的一环：

- 扫描表的 ＋（v0.2.2 M3 §3.4 B）：看到信号当场加进信号池——"发现 → 跟踪"；
- 两张表的「＋ 记一笔」（v0.3.0 §5.1）：真的下单之后当场留痕——"决策 → 留痕"。
  它预填代码/名称/日期/方向/来源并跳到「交易日志」页；**不预填成交价**，
  因为信号那天的收盘价不是用户的成交价。
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
            help="把这一行的标的加进信号池（写入 config/settings.yaml），"
                 "此后「每日信号」会替你盯它的卖出信号。已在池中的行不可点。"),
        **_record_column(df, names, journal_ui.SCAN_CLICK_KEY),
    }


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
        ui.data_table(journal_ui.record_table(df),
                      {**fmt.scan_column_config(),
                       **_record_column(df, names, journal_ui.SCAN_CLICK_KEY)},
                      "当日无新信号", hint=guide.TABLE_HINTS["scan"],
                      color_columns=ui.SCAN_COLOR_COLUMNS)
        return
    symbols = tuple(str(s) for s in df["symbol"])
    ui.data_table(journal_ui.record_table(pool.scan_table(df, in_pool)),
                  _scan_columns(df, symbols, names),
                  "当日无新信号", hint=guide.TABLE_HINTS["scan"],
                  color_columns=ui.SCAN_COLOR_COLUMNS)


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
    st.html(theme.section(f"全市场扫描（{scan_meta.scan_day(latest)}）",
                          ui.scan_scope_pill(latest)))
    # 扫描被 Ctrl-C 打断可能留下零字节/半截 CSV：EmptyDataError / ParserError
    # 都是 ValueError 子类，与回测页同一套容错口径，崩页不如明说
    try:
        df = pd.read_csv(latest, dtype={"symbol": str})
    except (ValueError, OSError) as e:
        st.error(f"扫描文件 {latest.name} 读取失败（{type(e).__name__}），"
                 f"多半是扫描中途被打断；请删除该文件后重新运行扫描。")
        return
    _scan_table(df, names)


def page_signals() -> None:
    ui.page_head("今日信号")
    pool.show_flash()   # 扫描表里点了 ＋ 之后那句 toast / 错误（回调跑在重跑之前）
    # 本页同屏展示两类产物：固定池每日信号 + 页尾的全市场扫描区块，故控制条有两条
    ui.control_bar("daily_signal", "market_scan")
    # 名称查一次两张表共用：每日信号 CSV 没有 name 列，而「记一笔」要把名称一起预填
    # （查不到就留空，绝不编一个）。扫描表自己带 name，这份查询对它是冗余的兜底。
    names = ui.symbol_names()
    sig_dir = ui.OUTPUT / "signals"
    files = sorted(sig_dir.glob("*.csv"), reverse=True) if sig_dir.exists() else []
    # 无信号记录不能 return 早退：全市场扫描区块在页尾，早退会把它一并吞掉
    if not files:
        st.info(guide.EMPTY_STATES["signal"])
    else:
        latest = files[0]
        st.html(theme.section(f"最新信号（{latest.stem}）"))
        df = pd.read_csv(latest, dtype={"symbol": str})
        # 表格渲染统一走 ui.data_table：空表时那句提示必须是 if/else **语句**，
        # streamlit 的 magic 会把裸三元（ast.IfExp）整条包进 st.write()，
        # 于是 st.dataframe 的返回值被当对象内省，把整份 API 手册糊在信号表下面。
        ui.data_table(journal_ui.record_table(df),
                      {**fmt.signal_column_config(),
                       **_record_column(df, names, journal_ui.SIGNAL_CLICK_KEY)},
                      "当日无新信号", hint=guide.TABLE_HINTS["signal"])
        if len(files) > 1:
            st.html(theme.section("历史信号"))
            hist = pd.concat([pd.read_csv(f, dtype={"symbol": str}) for f in files[1:]],
                             ignore_index=True)
            # 历史表不再重复那行灰字：同一页里连着出现两遍等于噪声
            # 历史表刻意**不**带「记一笔」：补记一笔几个月前的老交易走录入表单更合适，
            # 而这张表可能有几百行，多一列按钮只会让"今天该做什么"更难看清。
            ui.data_table(hist, fmt.signal_column_config(), "无")
    scan_section(names)
