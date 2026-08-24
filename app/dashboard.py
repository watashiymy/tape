"""Streamlit 本地面板：streamlit run app/dashboard.py
三页面：回测报告 / 个股K线 / 今日信号。只读 output/ 与 data/cache/，不触发任何计算。"""
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

OUTPUT = ROOT / "output"

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


def page_signals() -> None:
    sig_dir = OUTPUT / "signals"
    files = sorted(sig_dir.glob("*.csv"), reverse=True) if sig_dir.exists() else []
    if not files:
        st.info("暂无信号记录。收盘后运行: python scripts/run_daily_signal.py")
        return
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


st.set_page_config(page_title="quant_demo v0.1", layout="wide")
st.sidebar.title("quant_demo")
page = st.sidebar.radio("页面", ["回测报告", "个股K线", "今日信号"])
st.sidebar.caption("本面板纯只读；回测与信号请用命令行运行。策略仅用于学习，不构成投资建议。")
{"回测报告": page_backtest, "个股K线": page_kline, "今日信号": page_signals}[page]()
