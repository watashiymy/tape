# tests/test_dashboard_scan_scope.py — v0.2.4 面板的扫描范围徽标与选文件优先级（设计 §2.3/§3）
#
# 面板显示"当日无新信号"时，用户必须分得出这是全市场真没机会、还是一次 3 只票冒烟
# 测试的残渣。两件事要钉住：
#   1. **选文件的优先级**：同一天既有全量又有试跑 → 优先显示全量；只有试跑才显示试跑；
#   2. **徽标**：全量 N 只 / 试跑 N 只 / 范围未知（老产物，不报错也不猜）。
# 五种状态各来一遍，外加 AppTest 端到端"不崩页"。
import importlib.util

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from quant.signal import scan_meta
from tests.conftest import copy_app, goto_page, stub_navigation

SCAN_HEADER = "date,symbol,name,strategy,close,pct_chg,amount,amount_ratio_20d\n"
DAY = "2026-08-27"
# 两份产物的标的故意不同：面板挑错文件时，断言要能指着"表里是谁"当场说破。
FULL_ROWS = ("600000", "000020")
TRIAL_ROWS = ("600519",)


def _row(symbol: str) -> str:
    return f"{DAY},{symbol},某股票,ma_cross,11.74,-5.09,2.9e8,4.22\n"


def _write_scan(tmp_path, *, limit=None, symbols=FULL_ROWS, pool_total=3010,
                with_meta=True) -> None:
    """造一份扫描产物（CSV + 可选的伴生 meta），路径一律由被测的 scan_csv_path 算。

    `with_meta=False` 就是 v0.2.4 之前的老产物：有 CSV、没有范围记录。
    """
    from datetime import date, datetime

    scan_dir = tmp_path / "output" / "scan"
    scan_dir.mkdir(parents=True, exist_ok=True)
    day = date.fromisoformat(DAY)
    csv = scan_meta.scan_csv_path(day, limit, scan_dir)
    csv.write_text(SCAN_HEADER + "".join(_row(s) for s in symbols), encoding="utf-8")
    if with_meta:
        scan_meta.save_meta(scan_meta.ScanMeta(
            date=day, scanned=limit or pool_total, pool_total=pool_total, limit=limit,
            signals=len(symbols), skipped={"no_signal": 1}, failed=0, elapsed_s=12.0,
            started_at=datetime(2026, 8, 27, 18, 32, 11)), csv)


# ---------------------------------------------------------------- 五种状态

def _only_full(tmp_path):
    _write_scan(tmp_path)


def _only_trial(tmp_path):
    _write_scan(tmp_path, limit=3, symbols=TRIAL_ROWS)


def _both(tmp_path):
    _write_scan(tmp_path, limit=3, symbols=TRIAL_ROWS)
    _write_scan(tmp_path)


def _neither(tmp_path):
    pass


def _legacy(tmp_path):
    """v0.2.4 之前的老产物：有 CSV，没有 meta。"""
    _write_scan(tmp_path, with_meta=False)


STATES = [("只有全量", _only_full), ("只有试跑", _only_trial), ("两者都有", _both),
          ("都没有", _neither), ("有CSV无meta", _legacy)]


def _load_signals_page(tmp_path, monkeypatch):
    """bare 模式渲染「今日信号」页，返回 (喂给 st.dataframe 的表, 区块小标题的 HTML)。

    与 tests/test_dashboard_import.py 同一套桩：扫描表实际喂进去的是 Styler，
    取回底层 DataFrame。
    """
    dashboard = copy_app(tmp_path)
    frames: list[pd.DataFrame] = []
    sections: list[str] = []
    stub_navigation(monkeypatch, "今日信号")
    monkeypatch.setattr(st, "dataframe",
                        lambda df, *a, **k: frames.append(getattr(df, "data", df)))
    monkeypatch.setattr(st, "html", lambda body, *a, **k: sections.append(str(body)))
    spec = importlib.util.spec_from_file_location("dashboard_scan_scope", dashboard)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return frames, [h for h in sections if 'class="qd-section"' in h]


def _scan_section(sections: list[str]) -> str:
    picked = [s for s in sections if "全市场扫描" in s]
    assert picked, f"没找到全市场扫描区块的小标题: {sections}"
    return picked[-1]


# ---------------------------------------------------------------- 选文件的优先级

def test_only_a_full_scan_is_shown_as_a_full_scan(tmp_path, monkeypatch):
    _only_full(tmp_path)

    frames, sections = _load_signals_page(tmp_path, monkeypatch)

    assert frames[-1]["symbol"].tolist() == list(FULL_ROWS)
    assert "全量 3010 只" in _scan_section(sections)


def test_only_a_trial_run_is_labelled_as_a_trial(tmp_path, monkeypatch):
    """那天只试跑过：老实显示试跑结果，并明确标注是试跑。
    这三个字就是本次要买的东西——"当日无新信号"下面挂着"试跑 3 只"，
    用户一眼知道该重跑全量，而不是以为全市场真没机会。"""
    _only_trial(tmp_path)

    frames, sections = _load_signals_page(tmp_path, monkeypatch)

    assert frames[-1]["symbol"].tolist() == list(TRIAL_ROWS)
    assert "试跑 3 只" in _scan_section(sections)


def test_a_full_scan_wins_when_the_same_day_also_has_a_trial(tmp_path, monkeypatch):
    """核心优先级：同一天两份产物都在，面板显示**全量**（用户关心的是真结果）。

    朴素的 `sorted(files, reverse=True)[0]` 恰好选错：ASCII 里 '_' > '.'，
    "2026-08-27_limit3.csv" 排在 "2026-08-27.csv" 前面。
    """
    _both(tmp_path)

    frames, sections = _load_signals_page(tmp_path, monkeypatch)

    assert frames[-1]["symbol"].tolist() == list(FULL_ROWS), \
        "同一天既有全量又有试跑，面板显示的却是试跑那份"
    assert "全量 3010 只" in _scan_section(sections)
    assert "试跑" not in _scan_section(sections)


def test_an_old_product_without_meta_says_unknown_instead_of_guessing(tmp_path,
                                                                     monkeypatch):
    """老产物（有 CSV 无 meta）：不报错、不猜、不编，显示"范围未知"，
    并在 tooltip 里说清为什么未知。诚实优于编造。"""
    _legacy(tmp_path)

    frames, sections = _load_signals_page(tmp_path, monkeypatch)

    section = _scan_section(sections)
    assert frames[-1]["symbol"].tolist() == list(FULL_ROWS), "老产物照常显示表格"
    assert "范围未知" in section
    assert "全量" in section and "试跑" in section, f"tooltip 没解释未知在哪儿: {section}"


def test_the_section_title_still_carries_the_scan_date(tmp_path, monkeypatch):
    """徽标是加上去的，日期不许被挤掉：停牌日/忘跑的日子别让人把旧扫描当今天的。"""
    _only_trial(tmp_path)

    _, sections = _load_signals_page(tmp_path, monkeypatch)

    assert DAY in _scan_section(sections)


def test_the_trial_suffix_is_not_mistaken_for_the_scan_date(tmp_path, monkeypatch):
    """标题里的日期取自 meta / 文件名的**日期部分**，不能连 `_limit3` 一起显示。"""
    _only_trial(tmp_path)

    _, sections = _load_signals_page(tmp_path, monkeypatch)

    assert "_limit3" not in _scan_section(sections)


def test_a_corrupt_meta_is_reported_as_broken_not_as_an_old_product(tmp_path):
    """坏 meta 与"没有 meta"必须分得开：都显示"范围未知"没问题，但话要说对——
    含糊成"这是老产物"，那份坏文件就永远不会被人发现。

    面板这一侧是**降级**（警告 + 照常显示信号表）：命令行那边响亮抛错是对的，
    这里把整页信号藏起来的代价更大（与 ui.symbol_names 对坏清单的处置同一口径）。
    """
    _only_full(tmp_path)
    scan_meta.meta_path(tmp_path / "output" / "scan" / f"{DAY}.csv").write_text(
        "{不是 json", encoding="utf-8")

    at = goto_page(AppTest.from_file(str(copy_app(tmp_path)), default_timeout=15).run(),
                   "今日信号")

    assert not at.exception, f"坏 meta 不该崩页: {at.exception}"
    assert any("损坏" in w.value for w in at.warning), \
        f"坏 meta 应有 st.warning 说明是文件坏了: {[w.value for w in at.warning]}"
    assert at.dataframe, "坏 meta 不该把信号表一起藏掉"


# ---------------------------------------------------------------- 端到端不崩页

@pytest.mark.parametrize("label,setup", STATES, ids=[n for n, _ in STATES])
def test_the_signals_page_renders_in_every_scope_state(tmp_path, label, setup):
    """五态 × AppTest：任何一种都不得抛异常，也不得报错（老产物不是错误）。"""
    setup(tmp_path)
    at = goto_page(AppTest.from_file(str(copy_app(tmp_path)), default_timeout=15).run(),
                   "今日信号")

    assert not at.exception, f"扫描范围状态「{label}」把页面打崩了: {at.exception}"
    assert not at.error, f"扫描范围状态「{label}」不该报错: {[e.value for e in at.error]}"
