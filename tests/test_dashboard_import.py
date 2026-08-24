import ast
import importlib.util
import json
import shutil
from pathlib import Path

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

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
    """把 dashboard.py 复制到临时根目录再加载，使 ROOT/OUTPUT 落在 tmp_path，
    与仓库真实 output/ 完全隔离（quant 是 editable 安装，import 不受 sys.path 影响）。
    返回 (模块, 该页渲染时喂给 st.dataframe 的 DataFrame 列表)。"""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    shutil.copy(DASHBOARD, app_dir / "dashboard.py")
    frames: list[pd.DataFrame] = []
    # 顶层代码在 exec_module 时就会渲染选中页，所以桩必须先装好
    monkeypatch.setattr(st.sidebar, "radio", lambda *a, **k: page)
    monkeypatch.setattr(st, "dataframe", lambda df, *a, **k: frames.append(df))
    spec = importlib.util.spec_from_file_location(
        f"dashboard_under_test_{page}", app_dir / "dashboard.py")
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


def test_fmt_metric_int_has_no_decimals(tmp_path, monkeypatch):
    """指标卡把所有数值一律 f"{v:.2f}"，n_trades（int）显示成 '243.00'。
    int 必须原样 str()；None 显示 —；比率类走百分号。"""
    mod, _ = _load_dashboard(tmp_path, monkeypatch, "今日信号")  # 空 output，不读文件
    assert mod._fmt_metric("n_trades", 243) == "243"
    assert mod._fmt_metric("total_return", 1.1642) == "116.42%"
    assert mod._fmt_metric("win_rate", 0.41975) == "41.98%"
    assert mod._fmt_metric("sharpe", 0.9621) == "0.96"
    assert mod._fmt_metric("profit_factor", None) == "—"


def _make_apptest(tmp_path) -> AppTest:
    """dashboard.py 复制进 tmp_path/app 再交给 AppTest，使 OUTPUT 指向 tmp_path/output。"""
    app_dir = tmp_path / "app"
    app_dir.mkdir(exist_ok=True)
    shutil.copy(DASHBOARD, app_dir / "dashboard.py")
    return AppTest.from_file(str(app_dir / "dashboard.py"), default_timeout=15)


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
    values = {m.label: m.value for m in at.metric}
    assert values["交易次数"] == "243"


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
