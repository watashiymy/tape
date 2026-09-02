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
from quant.report import fmt
from quant.report.charts import kline_chart


def page_backtest() -> None:
    ui.page_head("回测报告")
    ui.control_bar("backtest")   # §4.2 控制条；下面的只读逻辑一行未动
    runs = ui.list_runs()
    if not runs:
        st.info(guide.EMPTY_STATES["backtest"])
        return
    # 显示「策略显示名 时间戳」，底层值仍是产物目录（目录名存键，是数据不是界面）
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
    except (ValueError, OSError) as e:
        st.error(f"回测目录 {run.name} 数据残缺（{type(e).__name__}），"
                 f"多半是回测中途被打断；请删除该目录后刷新页面。")
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
        st.error(f"回测目录 {run.name} 的 trades.csv 读取失败，"
                 f"多半是回测中途被打断；请删除该目录后刷新页面。")
        return
    symbols = ui.run_symbols(run)
    if not symbols:
        st.warning(f"回测目录 {run.name} 里读不出标的清单（config_snapshot.json 缺失或损坏，"
                   f"且没有 kline_*.html 可以兜底）。重跑一次回测即可。")
        return
    # 名称让人看得出这是哪家公司（§2.4）。查不到就只显示代码——扫描 CSV 是唯一的
    # 离线名称来源，而它只记录出信号的标的，所以缺名是常态。
    names = ui.symbol_names()
    sym = st.selectbox("选择标的", symbols,
                       format_func=lambda s: fmt.symbol_label(s, names.get(s)))
    rows = trades_df[trades_df["symbol"].astype(str).str.zfill(6) == sym]
    _symbol_summary(rows)
    raw = BarCache(ui.CACHE_DIR).load(sym)
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
