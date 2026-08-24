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
