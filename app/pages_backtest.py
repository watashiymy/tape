"""回测产物的两页：「回测报告」与「个股K线」。

两页放同一个模块是因为它们读的是**同一批产物**（output/{策略}_{时间戳}/ 里的
metrics.json / report.html / trades.csv / kline_*.html），共用 ui.list_runs()
与同一句空态文案；拆成两个文件只会把"选哪次回测"的逻辑抄两遍。
"""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

import guide
import theme
import ui
from quant.backtest.portfolio import Trade
from quant.data.cache import BarCache
from quant.data.pipeline import prepare_bars
from quant.report import charts, fmt


def page_backtest() -> None:
    ui.page_head("回测报告")
    ui.control_bar("backtest")   # §4.2 控制条；下面的只读逻辑一行未动
    runs = ui.list_runs()
    if not runs:
        st.info(guide.EMPTY_STATES["backtest"])
        return
    # 下拉框里 58 次跑只有「策略显示名 + 时间」可比，同一分钟能挤着四个——而认不出的
    # 恰好是最要紧的那几次（README 那张叠加层归因表的四组对照就是同一天连着跑的，
    # 区别只在 config_snapshot 里）。所以先铺一张总览表，再选。
    with st.expander(f"全部 {len(runs)} 次回测一览", expanded=False):
        ui.data_table(ui.runs_overview(runs), {}, "无",
                      hint="叠加层两列为 — 的是较早的回测（那时还没有这项设置），"
                           "不是「当时关着」。「标的数」不同就不是同一个池子，收益不可直接比。",
                      height=ui.SCAN_TABLE_HEIGHT)
    # 显示「策略显示名 时间」，底层值仍是产物目录（目录名存键，是数据不是界面）
    run = st.selectbox("选择回测", runs, format_func=lambda p: fmt.run_label(p.name))
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
    except (ValueError, OSError):
        # 错误信息可以指名目录：使用者只能手工删它，目录名就是修法。但先说怎么办。
        st.error(f"这次回测（{fmt.run_label(run.name)}）的结果不完整，多半是中途被打断。"
                 f"重跑一次回测即可；若它一直出现，删掉结果目录 `{run.name}`。")
        return
    ui.metric_grid(metrics)
    # src 直接给 Path：st.iframe 会自己读这个 HTML 文件并内嵌（report.html 约 5 MB，
    # 自己 read_text 白读一遍）。st.iframe **没有** scrolling 参数（签名只有
    # src/width/height/tab_index），iframe 自带滚动条，不需要它。
    # 不能换成 st.html：那个不套 iframe 且默认忽略 JavaScript，plotly 报告会是空白页。
    st.iframe(run / "report.html", height=650)
    st.html(theme.section("交易明细"))
    ui.data_table(trades, fmt.trades_column_config(), "本次回测没有任何成交",
                  hint=guide.TABLE_HINTS["trades"])
    if skipped is not None:
        st.html(theme.section("被跳过的订单（涨跌停/资金不足等）"))
        # 这张表只有 date/symbol/reason，列配置得各用各的（成交表那套会漏掉 reason）
        ui.data_table(skipped, fmt.skipped_column_config(), "无被跳过的订单",
                      hint=guide.TABLE_HINTS["skipped"])


def page_kline() -> None:
    ui.page_head("个股K线")
    ui.control_bar("backtest")   # K 线也读回测产物（标的清单 + trades.csv）
    runs = ui.list_runs()
    if not runs:
        st.info(guide.EMPTY_STATES["backtest"])   # 与回测报告页同一句：读的是同一批产物
        return
    run = st.selectbox("选择回测", runs, format_func=lambda p: fmt.run_label(p.name))
    try:
        trades_df = pd.read_csv(run / "trades.csv", dtype={"symbol": str})
    except (ValueError, OSError):
        st.error(f"这次回测（{fmt.run_label(run.name)}）的成交记录读不出来，多半是中途被打断。"
                 f"重跑一次回测即可；若它一直出现，删掉结果目录 `{run.name}`。")
        return
    symbols = ui.run_symbols(run)
    if not symbols:
        st.warning(f"这次回测（{fmt.run_label(run.name)}）里读不出标的清单，重跑一次回测即可。")
        return
    # 名称让人看得出这是哪家公司（§2.4）。查不到就只显示代码——扫描 CSV 是唯一的
    # 离线名称来源，而它只记录出信号的标的，所以缺名是常态。
    names = ui.symbol_names()
    pick, period, span_col = st.columns([3, 2, 2], vertical_alignment="bottom")
    with pick:
        sym = st.selectbox("选择标的", symbols,
                           format_func=lambda s: fmt.symbol_label(s, names.get(s)))
    with period:
        # 周期 / 范围都用内部键存 session_state，显示名走 charts 里那两张表
        # （同策略键 / 显示名的分工）。
        freq = st.radio("周期", list(charts.KLINE_FREQS), horizontal=True, key="kline_freq",
                        format_func=lambda k: charts.KLINE_FREQS[k][0])
    with span_col:
        # 默认 120 根：一次画全部（十年两千多根）每根不到一个像素，正是"太细太密"。
        span = st.radio("显示最近", list(charts.KLINE_SPANS), horizontal=True, key="kline_span",
                        format_func=lambda k: charts.KLINE_SPANS[k][0])
    rows = trades_df[trades_df["symbol"].astype(str).str.zfill(6) == sym]
    _symbol_summary(rows)
    raw = BarCache(ui.CACHE_DIR).load(sym)
    if raw is None:
        st.error(f"本地还没有 {sym} 的行情数据；跑一次回测或信号跟踪就有了。")
        return
    df, _ = prepare_bars(raw)
    sym_trades = [
        Trade(r.symbol, r.action, pd.Timestamp(r.date), r.price, r.shares, r.commission)
        for r in rows.itertuples()
    ]
    # config 必须传：滚轮/双指缩放是前端行为，图对象自己开不了（见 charts.KLINE_CONFIG）
    st.plotly_chart(charts.kline_chart(df, sym_trades, sym, freq=freq, span=span),
                    width="stretch", config=charts.KLINE_CONFIG)
    st.caption("悬停看开高低收与量额；滚轮或触控板双指缩放，拖动平移，双击复位。"
               "▲▼ 标在 K 线外侧：▲ 在最低价下方是买入，▼ 在最高价上方是卖出。"
               "横轴按交易日紧排，不留周末与节假日的空档。")


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
