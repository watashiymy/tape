import ast
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from tests.conftest import copy_app

# 必须从 __file__ 推导仓库根：Path("app/dashboard.py") 依赖 cwd，
# 从任何非仓库根目录跑 pytest（IDE、CI 的绝对路径调用）整个文件全挂。
DASHBOARD = Path(__file__).resolve().parent.parent / "app" / "dashboard.py"


def test_dashboard_syntax_ok():
    """streamlit 脚本无法直接 import 测试（顶层执行 UI 代码），至少保证语法正确。"""
    src = DASHBOARD.read_text(encoding="utf-8")
    ast.parse(src)


def test_no_statement_is_wrapped_by_streamlit_magic():
    """streamlit 的 magic 会把函数体里**裸的表达式语句**包进
    __streamlitmagic__.transparent_write()（= st.write）。它只豁免 ast.Call /
    docstring / yield / await，裸三元 ast.IfExp 不在名单里：
    `st.dataframe(...) if len(df) else st.write("...")` 会被整条包起来，
    于是 st.dataframe() 返回的 DeltaGenerator 被 st.write 走对象内省分支，
    把约 90 行 Streamlit API 方法表糊在信号表下面；走 else 分支那天则渲染出一个 `None`。
    只在 `streamlit run` 下发作，普通 import 察觉不到，所以这里直接跑它的 AST 改写。
    """
    from streamlit.runtime.scriptrunner import magic

    tree = magic.add_magic(DASHBOARD.read_text(encoding="utf-8"), str(DASHBOARD))
    wrapped = [
        f"L{n.lineno}: {ast.unparse(n)}"
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "transparent_write"
    ]
    assert wrapped == [], (
        "以下语句会被 streamlit magic 悄悄包进 st.write（请改写成 if/else 语句）:\n"
        + "\n".join(wrapped)
    )


def _load_dashboard(tmp_path, monkeypatch, page):
    """把 app/ 复制到临时根目录再加载 dashboard.py，使 ROOT/OUTPUT 落在 tmp_path，
    与仓库真实 output/ 完全隔离（quant 是 editable 安装，import 不受 sys.path 影响）。
    返回 (模块, 该页渲染时喂给 st.dataframe 的 DataFrame 列表)。"""
    dashboard = copy_app(tmp_path)
    frames: list[pd.DataFrame] = []
    # 顶层代码在 exec_module 时就会渲染选中页，所以桩必须先装好
    monkeypatch.setattr(st.sidebar, "radio", lambda *a, **k: page)
    # 扫描表喂给 st.dataframe 的是 pandas Styler（column_config 没有条件着色能力，
    # 红绿只能走 Styler）。这里取回底层 DataFrame——本文件关心的是"数据对不对"。
    monkeypatch.setattr(st, "dataframe",
                        lambda df, *a, **k: frames.append(getattr(df, "data", df)))
    spec = importlib.util.spec_from_file_location(
        f"dashboard_under_test_{page}", dashboard)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, frames


def test_backtest_page_keeps_leading_zero_symbols(tmp_path, monkeypatch):
    """trades.csv / skipped.csv 里 symbol 是字符串 000333，pd.read_csv 不带 dtype
    会把整列推断成 int64 吃掉前导零，表里显示成不存在的股票代码 333。"""
    run = tmp_path / "output" / "ma_cross_20260817_121152"
    run.mkdir(parents=True)
    (run / "metrics.json").write_text('{"total_return": 0.1}', encoding="utf-8")
    (run / "report.html").write_text("<html></html>", encoding="utf-8")
    (run / "trades.csv").write_text(
        "symbol,action,date,price,shares,commission\n"
        "000333,buy,2016-04-05,10.0,100,5.0\n", encoding="utf-8")
    (run / "skipped.csv").write_text(
        "symbol,date,reason\n000001,2016-04-05,涨停\n", encoding="utf-8")

    _, frames = _load_dashboard(tmp_path, monkeypatch, "回测报告")

    assert [f["symbol"].tolist() for f in frames] == [["000333"], ["000001"]]


def test_signal_page_keeps_leading_zero_symbols(tmp_path, monkeypatch):
    """今日信号页（最新 + 历史）同样不能吃掉前导零。"""
    sig = tmp_path / "output" / "signals"
    sig.mkdir(parents=True)
    header = "symbol,date,strategy,signal,close\n"
    (sig / "2026-08-14.csv").write_text(
        header + "000333,2026-08-14,ma_cross,buy,10.0\n", encoding="utf-8")
    (sig / "2026-08-13.csv").write_text(
        header + "000001,2026-08-13,ma_cross,sell,9.0\n", encoding="utf-8")

    _, frames = _load_dashboard(tmp_path, monkeypatch, "今日信号")

    assert [f["symbol"].tolist() for f in frames] == [["000333"], ["000001"]]


def test_metric_formatting_is_not_duplicated_in_the_dashboard(tmp_path, monkeypatch):
    """指标卡的标签与格式化自 v0.2.1 起在 quant.report.fmt（§4"UI 层只做组装"）。
    面板里再留一份私有副本，改一处漏一处就会出现两种长相的指标卡。
    边界（int/None/NaN/未知键）在 tests/test_report_fmt.py 里钉。"""
    from quant.report import fmt

    mod, _ = _load_dashboard(tmp_path, monkeypatch, "今日信号")  # 空 output，不读文件
    assert not hasattr(mod, "_fmt_metric"), "格式化应已下沉到 fmt，面板不该再留一份"
    assert not hasattr(mod, "METRIC_LABELS"), "标签表同理"
    assert fmt.fmt_metric("n_trades", 243) == "243"
    assert fmt.fmt_metric("total_return", 1.1642) == "116.42%"


def _make_apptest(tmp_path) -> AppTest:
    """app/ 整目录复制进 tmp_path 再交给 AppTest，使 OUTPUT 指向 tmp_path/output。"""
    dashboard = copy_app(tmp_path)
    return AppTest.from_file(str(dashboard), default_timeout=15)


def _complete_run(out: Path, name: str, metrics: dict) -> Path:
    """造一个完整的回测目录（与半截目录形成对照）。"""
    run = out / name
    run.mkdir(parents=True)
    (run / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    (run / "report.html").write_text("<html></html>", encoding="utf-8")
    (run / "trades.csv").write_text(
        "symbol,action,date,price,shares,commission,stamp,pnl,holding_days\n",
        encoding="utf-8")
    return run


def test_metric_card_shows_n_trades_as_integer(tmp_path):
    """真渲染（AppTest）：交易次数卡必须是 '243'，不是 '243.00'。"""
    _complete_run(tmp_path / "output", "ma_cross_20260817_121152", {"n_trades": 243})
    at = _make_apptest(tmp_path).run()
    assert not at.exception
    blob = "\n".join(e.proto.body for e in at.get("html"))
    assert '>交易次数</div><div class="qd-metric-value">243<' in blob, blob


@pytest.mark.parametrize("files", [
    ["metrics.json"],                    # Ctrl-C 在写 report.html 之前：只有 metrics.json
    ["metrics.json", "report.html"],     # 缺 trades.csv
], ids=["只有metrics", "缺trades"])
def test_half_written_run_dir_is_excluded_not_crashing(tmp_path, files):
    """run_backtest 被 Ctrl-C 打断留下半截目录，面板默认选中最新目录直接
    FileNotFoundError 崩页。半截目录必须被 list_runs 排除。"""
    run = tmp_path / "output" / "ma_cross_20260824_151600"
    run.mkdir(parents=True)
    for f in files:
        (run / f).write_text('{"n_trades": 1}' if f.endswith("json") else "<html></html>",
                             encoding="utf-8")
    at = _make_apptest(tmp_path).run()
    assert not at.exception, f"半截目录（{files}）不该崩页: {at.exception}"
    assert at.info, "半截目录被排除后应显示'暂无回测结果'提示"


def test_truncated_metrics_json_shows_error_not_crash(tmp_path):
    """metrics.json 本身写了一半（无效 JSON）但其余文件齐全：
    页面不能抛 JSONDecodeError，要用 st.error 提示删除残缺目录。"""
    run = tmp_path / "output" / "ma_cross_20260824_151600"
    run.mkdir(parents=True)
    (run / "metrics.json").write_text('{"total_return": 0.48', encoding="utf-8")  # 截断
    (run / "report.html").write_text("<html></html>", encoding="utf-8")
    (run / "trades.csv").write_text("symbol,action\n", encoding="utf-8")
    at = _make_apptest(tmp_path).run()
    assert not at.exception, f"残缺 metrics.json 不该崩页: {at.exception}"
    assert any("删除" in e.value for e in at.error), "应出现提示删除残缺目录的 st.error"


def test_half_written_run_does_not_shadow_complete_run(tmp_path):
    """最新目录半截、更早目录完整：完整的那次必须仍然可看（默认被选中）。"""
    out = tmp_path / "output"
    _complete_run(out, "ma_cross_20260817_121152", {"n_trades": 7})
    broken = out / "ma_cross_20260824_151600"
    broken.mkdir(parents=True)
    (broken / "metrics.json").write_text('{"n_trades": 1}', encoding="utf-8")
    at = _make_apptest(tmp_path).run()
    assert not at.exception
    assert at.selectbox[0].value.name == "ma_cross_20260817_121152"


# ---------- v0.1.1 M3：今日信号页"全市场扫描"区块 ----------

SCAN_HEADER = "date,symbol,name,strategy,close,pct_chg,amount,amount_ratio_20d\n"


def _goto_signals(tmp_path) -> AppTest:
    """AppTest 渲染并切到"今日信号"页（radio 默认选中第一页"回测报告"）。"""
    at = _make_apptest(tmp_path).run()
    at.sidebar.radio[0].set_value("今日信号").run()
    return at


def test_scan_block_prompts_command_when_no_csv(tmp_path):
    """无 output/scan/ 或其中无 CSV 时，区块应 st.info 提示运行扫描命令。"""
    at = _goto_signals(tmp_path)
    assert not at.exception
    assert any("run_market_scan" in i.value for i in at.info), \
        f"无扫描 CSV 时应提示运行 run_market_scan.py，实际 info: {[i.value for i in at.info]}"


def test_scan_block_shows_latest_csv_even_without_daily_signals(tmp_path, monkeypatch):
    """有多份扫描 CSV 只展示最新一份；symbol 前导零不得被吃掉；
    且 output/signals/ 不存在时区块也必须渲染（不能被信号页的早退挡住）。"""
    scan = tmp_path / "output" / "scan"
    scan.mkdir(parents=True)
    (scan / "2026-08-20.csv").write_text(
        SCAN_HEADER + "2026-08-20,600000,浦发银行,ma_cross,10.0,1.0,5e8,2.0\n",
        encoding="utf-8")
    (scan / "2026-08-21.csv").write_text(
        SCAN_HEADER + "2026-08-21,000020,深华发A,ma_cross,11.74,-5.09,2.9e8,4.22\n",
        encoding="utf-8")

    _, frames = _load_dashboard(tmp_path, monkeypatch, "今日信号")

    assert [f["symbol"].tolist() for f in frames] == [["000020"]], \
        "应只展示最新一份扫描 CSV（2026-08-21），且保留前导零"


def test_scan_block_renders_after_daily_signals(tmp_path, monkeypatch):
    """信号清单与扫描结果同时存在：两块都要渲染，扫描区块排在信号之后。"""
    sig = tmp_path / "output" / "signals"
    sig.mkdir(parents=True)
    (sig / "2026-08-21.csv").write_text(
        "symbol,date,strategy,signal,close\n000333,2026-08-21,ma_cross,buy,10.0\n",
        encoding="utf-8")
    scan = tmp_path / "output" / "scan"
    scan.mkdir(parents=True)
    (scan / "2026-08-21.csv").write_text(
        SCAN_HEADER + "2026-08-21,000020,深华发A,ma_cross,11.74,-5.09,2.9e8,4.22\n",
        encoding="utf-8")

    _, frames = _load_dashboard(tmp_path, monkeypatch, "今日信号")

    assert [f["symbol"].tolist() for f in frames] == [["000333"], ["000020"]]


def test_scan_block_shows_scan_date(tmp_path):
    """区块标题必须带扫描日期（文件名 stem），别让人误把旧扫描当今天的。"""
    scan = tmp_path / "output" / "scan"
    scan.mkdir(parents=True)
    (scan / "2026-08-21.csv").write_text(
        SCAN_HEADER + "2026-08-21,000020,深华发A,ma_cross,11.74,-5.09,2.9e8,4.22\n",
        encoding="utf-8")
    at = _goto_signals(tmp_path)
    assert not at.exception
    sections = [e.proto.body for e in at.get("html")
                if 'class="qd-section"' in e.proto.body]
    assert any("2026-08-21" in s for s in sections), \
        f"扫描区块标题应含扫描日期，实际小标题: {sections}"


def test_scan_block_empty_csv_says_no_signal(tmp_path):
    """只有表头的 CSV（当日无新信号是常态）：显示文字说明，不崩页不留空表。"""
    scan = tmp_path / "output" / "scan"
    scan.mkdir(parents=True)
    (scan / "2026-08-21.csv").write_text(SCAN_HEADER, encoding="utf-8")
    at = _goto_signals(tmp_path)
    assert not at.exception
    assert any("无新信号" in m.value for m in at.markdown), \
        f"空扫描结果应显示'无新信号'，实际 markdown: {[m.value for m in at.markdown]}"


def test_scan_block_corrupt_csv_shows_error_not_crash(tmp_path):
    """扫描被 Ctrl-C 打断可能留下零字节 CSV（pandas EmptyDataError）：
    页面不能抛异常，要 st.error 提示删除后重跑。"""
    scan = tmp_path / "output" / "scan"
    scan.mkdir(parents=True)
    (scan / "2026-08-24.csv").write_bytes(b"")
    at = _goto_signals(tmp_path)
    assert not at.exception, f"损坏扫描 CSV 不该崩页: {at.exception}"
    assert at.error, "应出现提示删除损坏扫描文件的 st.error"


@pytest.mark.parametrize("with_scan", [True, False], ids=["有scanCSV", "无scanCSV"])
@pytest.mark.parametrize("page", ["回测报告", "个股K线", "今日信号"])
def test_three_pages_render_without_exception(tmp_path, page, with_scan):
    """三页面 × 有/无扫描 CSV：任何组合都不得抛异常（M3 验收矩阵）。"""
    if with_scan:
        scan = tmp_path / "output" / "scan"
        scan.mkdir(parents=True)
        (scan / "2026-08-21.csv").write_text(
            SCAN_HEADER + "2026-08-21,000020,深华发A,ma_cross,11.74,-5.09,2.9e8,4.22\n",
            encoding="utf-8")
    at = _make_apptest(tmp_path).run()
    at.sidebar.radio[0].set_value(page).run()
    assert not at.exception, f"页面 {page}（with_scan={with_scan}）抛异常: {at.exception}"


def test_list_runs_puts_newest_first_regardless_of_strategy_name(tmp_path, monkeypatch):
    """目录名是 {策略}_{YYYYMMDD}_{HHMMSS}；按整条路径字符串排序会让策略名压过时间戳
    （"ma_cross_" > "donchian_"），面板默认选中的就不是最新那次回测。"""
    out = tmp_path / "output"
    for name in ("ma_cross_20260817_121152", "donchian_20260817_123313",
                 "ma_cross_20200101_000000"):
        _complete_run(out, name, {})   # 必须造完整目录：半截目录会被 list_runs 排除

    mod, _ = _load_dashboard(tmp_path, monkeypatch, "今日信号")  # 信号页不读 run 目录

    assert [p.name for p in mod.list_runs()] == [
        "donchian_20260817_123313",      # 最新
        "ma_cross_20260817_121152",
        "ma_cross_20200101_000000",      # 最旧
    ]
