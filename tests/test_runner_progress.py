# tests/test_runner_progress.py — v0.2.0 §3.2 日志 → 进度（纯函数，离线）
#
# 全部用例只吃**真实日志**：
#   market_scan_sample.log      2026-08-24 全量扫描实跑截取（含表头/进度行/结果表/汇总/已保存）
#   backtest_sample.log         2026-08-26 run_backtest.py --strategy ma_cross 实跑全文
#   daily_signal_stale_sample.log  2026-08-26 上午 run_daily_signal.py 实跑（数据未更新，退出码 1）
# 唯一没有实跑素材的是 run_daily_signal 的成功尾部（当日 17:30 前 baostock 无数据），
# 该两行按脚本源码里的 f-string 逐字构造，构造依据见 DAILY_TAIL 处注释。
from pathlib import Path

import pytest

from quant.runner import progress as progress_module
from quant.runner.progress import (
    Progress,
    parse_backtest,
    parse_daily_signal,
    parse_market_scan,
    tail,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCAN_LOG = (FIXTURES / "market_scan_sample.log").read_text(encoding="utf-8")
BACKTEST_LOG = (FIXTURES / "backtest_sample.log").read_text(encoding="utf-8")
DAILY_STALE_LOG = (FIXTURES / "daily_signal_stale_sample.log").read_text(encoding="utf-8")

# 真实日志的前 N 行 = 一次"跑到一半"的日志（进程仍在写，尚无汇总/已保存）
SCAN_HEADER_ONLY = "\n".join(SCAN_LOG.splitlines()[:2]) + "\n"          # login + 基准日
SCAN_MID = "\n".join(SCAN_LOG.splitlines()[:20]) + "\n"                 # 末行 [1800/3010]

TRACEBACK_LOG = """login success!
基准日 2026-08-24，扫描池 3010 只，策略: ['ma_cross', 'donchian']，流动性门槛 20日均额 ≥ 50,000,000 元
[100/3010] 信号 1 条，失败 0 只，耗时 38s
Traceback (most recent call last):
  File "scripts/run_market_scan.py", line 120, in <module>
    main()
  File "scripts/run_market_scan.py", line 95, in main
    universe = provider.get_all_symbols(expected)
ValueError: 2026-08-24 的证券清单为空
"""


# ---------------------------------------------------------------- market_scan
def test_empty_log_is_indeterminate():
    """空日志（进程刚起、还没冲刷第一行）必须是不确定态，不能瞎编 0/0。"""
    p = parse_market_scan("")
    assert (p.current, p.total, p.elapsed_s, p.eta_s) == (None, None, None, None)
    assert p.phase and p.extras == {}


def test_market_scan_header_gives_total_but_no_eta():
    """只有表头时 total 已知、current 未知 —— ETA 必须是 None（宁可不显示，不给假数字）。"""
    p = parse_market_scan(SCAN_HEADER_ONLY)
    assert p.total == 3010
    assert p.current is None
    assert p.eta_s is None
    assert p.elapsed_s is None


def test_market_scan_progress_line_fields():
    p = parse_market_scan(SCAN_MID)
    assert (p.current, p.total) == (1800, 3010)
    assert p.elapsed_s == 673.0
    assert p.extras["信号"] == "58 条"
    assert p.extras["失败"] == "0 只"


def test_market_scan_eta_hand_computed():
    """ETA = elapsed/current×(total−current) = 673/1800×1210 = 452.4055…（手算对照）。"""
    p = parse_market_scan(SCAN_MID)
    assert p.eta_s == pytest.approx(673 / 1800 * (3010 - 1800))
    assert p.eta_s == pytest.approx(452.4055555, abs=1e-4)


def test_market_scan_takes_last_progress_line_not_first():
    """进度必须取最后一条，取到第一条会让进度条永远停在 100/3010。"""
    p = parse_market_scan(SCAN_MID)
    assert p.current == 1800 and p.elapsed_s == 673.0


def test_market_scan_truncated_half_line_ignored():
    """日志尾部半行（进程正在写、只落了一半）不得被解析成进度。"""
    p = parse_market_scan(SCAN_MID + "[1900/3010] 信号 6")
    assert p.current == 1800          # 仍是上一条完整行
    assert p.elapsed_s == 673.0


def test_market_scan_truncated_header_gives_no_total():
    p = parse_market_scan("login success!\n基准日 2026-08-24，扫描池 30")
    assert p.total is None and p.current is None


def test_market_scan_done_phase_and_output_path():
    """完整日志（含"已保存:"）→ 完成态，并给出 CSV 路径供面板渲染结果。"""
    p = parse_market_scan(SCAN_LOG)
    assert p.phase == "完成"
    assert p.extras["输出"] == "output/scan/2026-08-24.csv"


def test_market_scan_running_phase_is_not_done():
    assert parse_market_scan(SCAN_MID).phase != "完成"


def test_traceback_beats_saved_marker():
    """崩溃优先于"已保存:"，且不看两者先后：重定向到文件时 stdout 块缓冲、
    stderr 不缓冲，崩溃前打的"已保存:"完全可能落在 traceback 之后。"""
    p = parse_market_scan(SCAN_LOG + TRACEBACK_LOG)     # 已保存 在前，traceback 在后
    assert "异常" in p.phase
    p2 = parse_market_scan(TRACEBACK_LOG + "已保存: output/scan/2026-08-24.csv\n")
    assert "异常" in p2.phase


def test_eta_policy_needs_current_and_total():
    """ETA 的判定规则直接钉死：缺 current / 缺 total / current=0 / total<current
    一律不给数字。（走 parse_* 到不了这些分支——扫描的进度行同时给出三者——
    但规则本身是"宁可不显示也不给假数字"的底线，必须有测试守着。）"""
    assert progress_module._eta(None, 3010, 673.0) is None
    assert progress_module._eta(1800, None, 673.0) is None
    assert progress_module._eta(1800, 3010, None) is None
    assert progress_module._eta(0, 3010, 673.0) is None
    assert progress_module._eta(3010, 1800, 673.0) is None
    assert progress_module._eta(1800, 3010, 673.0) == pytest.approx(452.4055555, abs=1e-4)


def test_market_scan_traceback_is_reported():
    """异常日志必须被识别为异常态并带出最后一行异常摘要，否则面板只显示"扫描中"卡死。"""
    p = parse_market_scan(TRACEBACK_LOG)
    assert "异常" in p.phase
    assert "ValueError" in p.extras["错误"]
    assert p.current == 100           # 已解析到的进度不丢


# ---------------------------------------------------------------- daily_signal
def test_daily_signal_stale_real_log():
    """真实日志：17:30 前数据未更新，脚本 exit 1。面板必须显示这条人话原因。"""
    p = parse_daily_signal(DAILY_STALE_LOG)
    assert "未更新" in p.phase
    assert p.current is None and p.total is None and p.eta_s is None


def test_daily_signal_empty_and_startup():
    p = parse_daily_signal("login success!\n")
    assert p.current is None and p.total is None
    assert p.phase


# 下面两行按 scripts/run_daily_signal.py 的字面 f-string 构造（当日 17:30 前无法实跑成功路径）：
#   print(f"\n===== {expected} 信号 =====（扫描 {len(bars)} 只 × {len(strategies)} 个策略）")
#   print(f"已保存: {out}")   # out = SIGNAL_DIR / f"{expected}.csv"
DAILY_TAIL = """login success!
[warn] 600519: 停牌 2 日
logout success!

===== 2026-08-25 信号 =====（扫描 10 只 × 2 个策略）
今日无新信号
已保存: output/signals/2026-08-25.csv
"""


def test_daily_signal_done_phase_and_output_path():
    p = parse_daily_signal(DAILY_TAIL)
    assert p.phase == "完成"
    assert p.extras["输出"] == "output/signals/2026-08-25.csv"


def test_daily_signal_scan_header_phase():
    without_saved = DAILY_TAIL.rsplit("已保存", 1)[0]
    p = parse_daily_signal(without_saved)
    assert p.phase != "完成" and p.phase


def test_daily_signal_warn_lines_do_not_break_parsing():
    p = parse_daily_signal("login success!\n[warn] 600519: 停牌 2 日\n")
    assert p.current is None and p.phase


# ---------------------------------------------------------------- backtest
def test_backtest_counts_data_lines_as_current():
    """真实回测日志有 10 行 [data]，即已取数 10 只。"""
    p = parse_backtest(BACKTEST_LOG)
    assert p.current == 10


def test_backtest_total_comes_from_caller():
    """total 只能由调用方给（日志里没有 universe 长度），不给就是不确定态。"""
    assert parse_backtest(BACKTEST_LOG).total is None
    assert parse_backtest(BACKTEST_LOG, total=10).total == 10


def test_backtest_has_no_eta():
    """回测日志没有耗时字段 → 不许编 ETA。"""
    p = parse_backtest(BACKTEST_LOG, total=10)
    assert p.eta_s is None and p.elapsed_s is None


def test_backtest_done_phase_and_report_dir():
    p = parse_backtest(BACKTEST_LOG)
    assert p.phase == "完成"
    assert p.extras["报告目录"] == "output/ma_cross_20260826_104235"


def test_backtest_fetching_phase_midway():
    """取数阶段（尚未出现 ===== 策略 =====）。"""
    mid = "\n".join(BACKTEST_LOG.splitlines()[:5]) + "\n"
    p = parse_backtest(mid, total=10)
    assert p.current == 4 and p.phase != "完成"


def test_backtest_running_phase_after_strategy_header():
    upto = BACKTEST_LOG.split("        total_return")[0]
    p = parse_backtest(upto)
    assert "ma_cross" in p.phase


def test_backtest_truncated_data_line_not_counted():
    p = parse_backtest("\n".join(BACKTEST_LOG.splitlines()[:5]) + "\n[data] 0003")
    assert p.current == 4


def test_backtest_empty_log():
    p = parse_backtest("")
    assert p.current is None and p.phase


# ---------------------------------------------------------------- tail
def test_tail_empty():
    assert tail("") == ""


def test_tail_returns_last_n_lines_in_order():
    got = tail(SCAN_LOG, 3).splitlines()
    assert got == SCAN_LOG.splitlines()[-3:]
    assert got[-1].startswith("已保存: ")


def test_tail_shorter_than_n_returns_all():
    assert tail("a\nb\n", 30).splitlines() == ["a", "b"]


def test_tail_keeps_partial_last_line():
    """半行也要显示——面板的"实时输出"就是要看正在写的这一行。"""
    assert tail("a\nb\n[190", 2).splitlines() == ["b", "[190"]


def test_progress_is_frozen():
    p = parse_market_scan("")
    with pytest.raises(Exception):
        p.current = 5  # type: ignore[misc]
    assert isinstance(p, Progress)
