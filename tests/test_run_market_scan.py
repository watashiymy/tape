# tests/test_run_market_scan.py — v0.1.1 §3.4 入口脚本的可离线部分：空策略守卫、单票重试
import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "run_market_scan", Path(__file__).resolve().parent.parent / "scripts" / "run_market_scan.py")
run_market_scan = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(run_market_scan)


def test_empty_strategy_config_aborts_before_network():
    """空策略表必须在联网前退出：3200 只白抓一遍后输出一张空表是最贵的静默失败。"""
    with pytest.raises(SystemExit) as e:
        run_market_scan.require_strategies({})
    assert "strategies" in str(e.value)


def test_valid_strategy_config_builds_strategies():
    got = run_market_scan.require_strategies({"ma_cross": {"fast": 20, "slow": 60}})
    assert [s.name for s in got] == ["ma_cross"]


class FlakyService:
    """第 fail_times 次调用前都抛错的假 DataService。"""

    def __init__(self, fail_times):
        self.fail_times, self.calls = fail_times, 0

    def get_bars(self, symbol, start, end=None):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ConnectionError(f"boom #{self.calls}")
        return f"bars:{symbol}", []


def test_fetch_with_retry_recovers_from_single_failure():
    svc = FlakyService(fail_times=1)
    assert run_market_scan.fetch_with_retry(svc, "600000", None, None) == ("bars:600000", [])
    assert svc.calls == 2


def test_fetch_with_retry_gives_up_after_second_failure():
    """重试**一次**：第二次仍失败必须抛给调用方计数，不能无限重试卡死全场扫描。"""
    svc = FlakyService(fail_times=2)
    with pytest.raises(ConnectionError):
        run_market_scan.fetch_with_retry(svc, "600000", None, None)
    assert svc.calls == 2


# ---------------------------------------------------------------------------
# 预估耗时口径（评审阻塞 1 的回归守卫）
#
# 曾犯过的方法论错误：把 get_all_symbols（每次扫描固定一次、约 11500 行分页拉取、
# 实测 2-4 分钟）的固定开销摊进每票速率，得出"3.3-3.9 秒/只、全量约 3 小时"。
# 2026-08-24 实测拆解：固定开销 133s；热缓存 30 只中位 0.51s/只、均值 0.88s/只；
# 冷缓存抽样 0.9-2.5s/只。合理口径 = 固定 2-4 分钟 + 每票 0.5-2 秒 ≈ 全量 0.5-2 小时。
# ---------------------------------------------------------------------------

_README = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")


_DOCS = [(run_market_scan.__doc__, "docstring"), (_README, "README")]


@pytest.mark.parametrize("doc,label", _DOCS, ids=[l for _, l in _DOCS])
def test_timing_estimate_not_amortized(doc, label):
    """耗时文案不得复现"固定开销摊进每票速率"的错误结论。"""
    # "3.3-3.9 秒/只"两种连字符写法都要挡；不裸查 "3.3"（会误伤将来的 §3.3 引用）
    for wrong in ("约 3 小时", "3.3-3.9", "3.3–3.9"):
        assert wrong not in doc, f"{label} 仍含错误耗时结论 {wrong!r}"


@pytest.mark.parametrize("doc,label", _DOCS, ids=[l for _, l in _DOCS])
def test_timing_estimate_separates_fixed_overhead(doc, label):
    """耗时文案必须把固定开销与每票速率分开表述（防止错误口径回潮）。"""
    assert "固定开销" in doc, f"{label} 未区分固定开销与每票速率"


def test_readme_cache_window_claim_matches_scan_config():
    """扫描票缓存的是 400 自然日窗口（scan.history_days），不是 10 年——
    "省的是不重拉 10 年历史"只对回测 universe 票成立，README 曾写错。"""
    assert "省的是不重拉 10 年历史" not in _README
