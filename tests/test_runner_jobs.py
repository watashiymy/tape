# tests/test_runner_jobs.py — v0.2.0 §3.3 任务定义与参数白名单（安全关键，离线）
#
# 面板能在本机起进程，参数校验就是唯一的闸门：这里的反向用例全部是"必须被拒"。
import sys
from datetime import date
from pathlib import Path

import pytest

from quant.runner import progress
from quant.runner.jobs import JOBS, Job, build_argv
from quant.strategy import REGISTRY

ROOT = Path(__file__).resolve().parent.parent


def test_three_jobs_only():
    assert set(JOBS) == {"market_scan", "daily_signal", "backtest"}
    assert all(isinstance(j, Job) and j.label for j in JOBS.values())


def test_scripts_exist_and_parsers_bound():
    assert JOBS["market_scan"].parser is progress.parse_market_scan
    assert JOBS["daily_signal"].parser is progress.parse_daily_signal
    assert JOBS["backtest"].parser is progress.parse_backtest
    for job in JOBS.values():
        assert Path(job.script).is_file(), job.script


def test_argv_is_a_list_of_str_with_venv_python():
    argv = build_argv("market_scan", {})
    assert argv[0] == sys.executable
    assert Path(argv[2]) == ROOT / "scripts" / "run_market_scan.py"
    assert all(isinstance(a, str) for a in argv)


def test_python_runs_unbuffered():
    """必须带 -u：stdout 重定向到文件是块缓冲，实测真实扫描起跑 12 秒后日志仍为空
    （卡在 get_all_symbols，2-4 分钟），面板的"实时输出"会一直空白。"""
    for name in JOBS:
        assert build_argv(name)[1] == "-u"


def test_no_params_means_bare_command():
    assert build_argv("daily_signal", {}) == build_argv("daily_signal")
    assert len(build_argv("daily_signal")) == 3


def test_unknown_job_rejected():
    with pytest.raises(ValueError):
        build_argv("rm", {})


# ------------------------------------------------------------------ --config
def test_config_never_exposed():
    """--config 不进 UI：任意路径读取（如 /etc/passwd）必须无从下手。"""
    for name in JOBS:
        assert "--config" not in build_argv(name, {})
        assert all(p.flag != "--config" for p in JOBS[name].params)
    for bad in ["/etc/passwd", "config/settings.yaml"]:
        with pytest.raises(ValueError):
            build_argv("market_scan", {"config": bad})


def test_unknown_param_rejected():
    with pytest.raises(ValueError):
        build_argv("market_scan", {"strategy": "ma_cross"})   # 扫描没有这个参数
    with pytest.raises(ValueError):
        build_argv("daily_signal", {"limit": 10})             # 每日信号无参数


# ------------------------------------------------------------------ --limit
def test_limit_ok():
    assert build_argv("market_scan", {"limit": 30})[-2:] == ["--limit", "30"]
    assert build_argv("market_scan", {"limit": 10000})[-1] == "10000"


def test_limit_omitted_when_none():
    assert "--limit" not in build_argv("market_scan", {"limit": None})


@pytest.mark.parametrize("bad", [0, -1, 10001, 99999, 3.5, True, False,
                                 "30", "30; rm -rf /", "", "1e3", [30]])
def test_limit_rejected(bad):
    """字符串一律拒（哪怕全是数字）：唯一进 argv 的整数来源必须是真 int。
    bool 也要拒——isinstance(True, int) 为 True，放行会得到 --limit 1。"""
    with pytest.raises(ValueError):
        build_argv("market_scan", {"limit": bad})


# ------------------------------------------------------------------ --date
def test_date_ok_as_str_and_as_date_object():
    assert build_argv("market_scan", {"date": "2026-08-24"})[-2:] == ["--date", "2026-08-24"]
    assert build_argv("market_scan", {"date": date(2026, 8, 24)})[-1] == "2026-08-24"


def test_date_omitted_when_none():
    assert "--date" not in build_argv("market_scan", {"date": None})


@pytest.mark.parametrize("bad", [
    "2026-01-01; rm -rf /",          # 注入尝试
    "2026-01-01 && cat /etc/passwd",
    "$(date)",
    "--config=/etc/passwd",          # 想塞第二个开关
    "2026-13-45",                    # 正则过得去，fromisoformat 过不去
    "2026-02-30",
    "20260824",                      # 格式不对
    "2026-8-24",
    " 2026-08-24",
    "2026-08-24\n--config=/etc/passwd",
    20260824,
])
def test_date_rejected(bad):
    with pytest.raises(ValueError):
        build_argv("market_scan", {"date": bad})


# ------------------------------------------------------------------ --strategy
def test_strategy_must_come_from_registry():
    for name in REGISTRY:
        assert build_argv("backtest", {"strategy": name})[-2:] == ["--strategy", name]
    spec = next(p for p in JOBS["backtest"].params if p.flag == "--strategy")
    assert set(spec.choices) == set(REGISTRY)


@pytest.mark.parametrize("bad", ["ma_corss", "ma_cross; rm -rf /", "--config=/etc/passwd",
                                 "", "*", 1, ["ma_cross"]])
def test_strategy_rejected(bad):
    with pytest.raises(ValueError):
        build_argv("backtest", {"strategy": bad})


def test_strategy_omitted_when_none_means_all():
    assert "--strategy" not in build_argv("backtest", {"strategy": None})


# ------------------------------------------------------------------ --refresh
def test_refresh_flag_takes_no_value():
    argv = build_argv("backtest", {"refresh": True})
    assert argv[-1] == "--refresh"
    assert "--refresh" not in build_argv("backtest", {"refresh": False})


@pytest.mark.parametrize("bad", ["true", "1", 1, "; rm -rf /"])
def test_refresh_rejected(bad):
    with pytest.raises(ValueError):
        build_argv("backtest", {"refresh": bad})


# ------------------------------------------------------------------ 组合 / 兜底
def test_combined_params():
    argv = build_argv("market_scan", {"limit": 30, "date": "2026-08-24"})
    assert argv[3:] == ["--limit", "30", "--date", "2026-08-24"]


def test_rejected_input_never_reaches_argv():
    """反证：被拒的输入不得以任何形式出现在 argv 里（不是靠转义，是靠拒绝）。"""
    for payload, params in [("rm -rf", {"date": "2026-01-01; rm -rf /"}),
                            ("passwd", {"config": "/etc/passwd"})]:
        with pytest.raises(ValueError) as e:
            build_argv("market_scan", params)
        assert payload not in " ".join(build_argv("market_scan", {}))
        assert str(e.value)
