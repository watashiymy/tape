# 每日流水线三步的「就绪状态」文案（v0.5.0 设计 §6）。
#
# 这几句话里全是数字（扫了多少只、池子里几只、最新信号是哪天），而本项目对
# "页面上的数字"只有一条纪律：**宁可说不知道，也不许猜**。所以每个函数的
# "什么都没有"分支都单独测——那正是最容易被写成 0 / 空串 / 今天日期的地方。
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant.runner import steps          # noqa: E402

DAY = date(2026, 9, 1)


def _scan(dirpath: Path, day: str, *, scanned=3010, pool=3010, failed=0, signals=86,
          limit=None) -> None:
    """一份扫描产物（CSV + meta），形状与 run_market_scan.py 落盘的一致。"""
    dirpath.mkdir(parents=True, exist_ok=True)
    stem = day if limit is None else f"{day}_limit{limit}"
    (dirpath / f"{stem}.csv").write_text("date,symbol\n", encoding="utf-8")
    (dirpath / f"{stem}.meta.json").write_text(json.dumps({
        "date": day, "scanned": scanned, "pool_total": pool, "limit": limit,
        "is_full": scanned == pool, "signals": signals, "skipped": {},
        "failed": failed, "elapsed_s": 900.0, "started_at": f"{day}T18:30:00",
    }), encoding="utf-8")


# ================================================================ ① 全市场扫描

def test_scan_status_says_it_never_ran_instead_of_zero(tmp_path):
    """一次都没跑过时不许显示「0 条信号」——那是"扫过了，没机会"的意思。"""
    got = steps.scan_status(tmp_path / "scan", DAY)
    assert steps.UNKNOWN in got.text and got.ready is False
    assert "0" not in got.text


def test_scan_status_quotes_the_same_scope_wording_as_the_badge(tmp_path):
    """与徽标同一套说法：同一次扫描在两处不许各说一套。"""
    d = tmp_path / "scan"
    _scan(d, "2026-09-01")
    got = steps.scan_status(d, DAY)
    assert "2026-09-01" in got.text and "全量 3010 只" in got.text and "86" in got.text
    assert got.ready is True


def test_scan_status_carries_the_failure_count_through(tmp_path):
    """徽标 v0.5.0 起会报取数失败数，这一行也得跟着报——
    否则控制台说"全量 3010 只"、信号页说"全量 3010 只（800 只取数失败）"。"""
    d = tmp_path / "scan"
    _scan(d, "2026-09-01", failed=800, signals=0)
    assert "800" in steps.scan_status(d, DAY).text


def test_scan_status_flags_a_stale_result(tmp_path):
    """最近一次不是今天时必须说出来：面板上最容易犯的错就是把旧扫描当今天的。"""
    d = tmp_path / "scan"
    _scan(d, "2026-08-25")
    assert "不是今天的" in steps.scan_status(d, DAY).text


def test_scan_status_survives_a_product_with_no_meta(tmp_path):
    """v0.2.4 之前的老产物没有 meta：显示「范围未知」，不猜也不崩。"""
    d = tmp_path / "scan"
    d.mkdir(parents=True)
    (d / "2026-08-20.csv").write_text("date,symbol\n", encoding="utf-8")
    got = steps.scan_status(d, DAY)
    assert "范围未知" in got.text and got.ready is True


# ================================================================ ② 信号池

def test_pool_status_separates_broken_config_from_empty_pool():
    """「读不出来」与「是空的」是两个不同的出路：一个去修文件，一个去加票。
    合成一句话就是把人支到另一个方向。"""
    broken = steps.pool_status(None)
    empty = steps.pool_status(())
    assert "读不到" in broken.text and broken.ready is False
    assert "空的" in empty.text and empty.ready is False
    assert broken.text != empty.text


def test_pool_status_counts_and_names_the_source():
    got = steps.pool_status(("600519", "000333"), "config/universe.local.yaml")
    assert "2 只" in got.text and "universe.local.yaml" in got.text
    assert got.ready is True


# ================================================================ ③ 每日信号

def test_signal_status_says_it_never_ran(tmp_path):
    got = steps.signal_status(tmp_path / "signals", DAY)
    assert steps.UNKNOWN in got.text and got.ready is False


def test_signal_status_never_claims_there_are_no_signals_today(tmp_path):
    """**刻意只说日期**：「没有产物」与「今天确实没有新信号」是两件事，
    而后者是绝大多数日子的正常结果。混成一句"今日无信号"正是那类误导。"""
    d = tmp_path / "signals"
    d.mkdir(parents=True)
    (d / "2026-09-01.csv").write_text("date,symbol\n", encoding="utf-8")
    got = steps.signal_status(d, DAY)
    assert "2026-09-01" in got.text and got.ready is True
    assert "无信号" not in got.text and "没有信号" not in got.text


def test_signal_status_flags_a_stale_result(tmp_path):
    d = tmp_path / "signals"
    d.mkdir(parents=True)
    (d / "2026-08-28.csv").write_text("date,symbol\n", encoding="utf-8")
    assert "不是今天的" in steps.signal_status(d, DAY).text


@pytest.mark.parametrize("fn", [steps.scan_status, steps.signal_status])
def test_a_missing_directory_is_not_an_error(fn, tmp_path):
    """面板首跑时 output/ 下什么都没有。缺目录不许抛——那会让整页打不开。"""
    assert fn(tmp_path / "does-not-exist", DAY).ready is False
