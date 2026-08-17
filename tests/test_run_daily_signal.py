# tests/test_run_daily_signal.py
import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "run_daily_signal", Path(__file__).resolve().parent.parent / "scripts" / "run_daily_signal.py")
run_daily_signal = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(run_daily_signal)


def test_empty_strategy_config_aborts():
    """空策略表必须退出，不能空跑。

    "今日无新信号"是多数日子的正常结果，与"一个策略都没跑"输出**逐字相同**：
    同样打印无信号、同样写出只有表头的 CSV、同样退出码 0。
    config.py 的 `raw.get("strategies") or {}` 让 settings.yaml 里
    strategies 段缺失/为空/键名拼错时静默得到 {}，于是信号系统天天空跑且零告警。
    """
    with pytest.raises(SystemExit) as e:
        run_daily_signal.require_strategies({})
    assert "strategies" in str(e.value)


def test_valid_strategy_config_builds_strategies():
    """反向断言：正常配置必须照常构造出策略，守卫不能误伤。"""
    got = run_daily_signal.require_strategies(
        {"ma_cross": {"fast": 20, "slow": 60}})
    assert [s.name for s in got] == ["ma_cross"]
