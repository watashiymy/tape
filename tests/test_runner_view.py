# tests/test_runner_view.py — v0.2.0 §4.1 面板文案纯函数（离线）
#
# 面板本身写不了单测（顶层执行 UI 代码），所以"显示什么字"这件事必须挤到纯函数里来测：
# 徽标要不要带退出码、ETA 该不该显示、百分比怎么算，错了都是用户直接看到的假信息。
from datetime import datetime
from pathlib import Path

import pytest

from quant.runner import view
from quant.runner.process import RunState
from quant.runner.progress import Progress, parse_backtest, parse_market_scan

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCAN_LOG = (FIXTURES / "market_scan_sample.log").read_text(encoding="utf-8")
BACKTEST_LOG = (FIXTURES / "backtest_sample.log").read_text(encoding="utf-8")


def _state(**kw) -> RunState:
    base = dict(script="market_scan", run_id="market_scan_20260825_100000_000",
                pid=4321, argv=["python", "-u", "scripts/run_market_scan.py"],
                log_path="/tmp/x.log", started_at="2026-08-25T10:00:00",
                status="running", exit_code=None, finished_at=None)
    return RunState(**(base | kw))


# ---------------------------------------------------------------- 状态徽标
def test_idle_badge_when_never_run():
    assert view.status_badge(None) == view.IDLE_BADGE
    assert "空闲" in view.IDLE_BADGE


@pytest.mark.parametrize("status, word", [
    ("running", "运行中"), ("success", "成功"), ("stopped", "已停止"),
])
def test_badge_words(status, word):
    assert word in view.status_badge(_state(status=status))


def test_failed_badge_carries_exit_code():
    """退出码是排查的第一手线索（1=脚本 sys.exit，2=argparse 参数错，-9=被 kill）。
    只显示"失败"等于把它藏起来。"""
    assert "退出码 3" in view.status_badge(_state(status="failed", exit_code=3))


def test_failed_badge_says_unknown_when_exit_code_lost():
    """僵尸清理（进程不是面板的孩子）拿不到退出码，如实写"未知"，不能显示"退出码 None"。"""
    badge = view.status_badge(_state(status="failed", exit_code=None))
    assert "未知" in badge and "None" not in badge


# ---------------------------------------------------------------- 时长格式化
@pytest.mark.parametrize("seconds, text", [
    (None, "—"),
    (0, "0秒"),
    (45.4, "45秒"),
    (60, "1分0秒"),        # 不能出现 "60秒"
    (758, "12分38秒"),
    (3905, "1小时5分"),
    (-3, "0秒"),           # 时钟回拨/跨时区，负数不能漏出去
])
def test_human_duration(seconds, text):
    assert view.human_duration(seconds) == text


@pytest.mark.parametrize("seconds, text", [
    (None, ""),            # 没有 ETA 就交白卷，绝不显示编出来的数字
    (0, ""),
    (-5, ""),
    (30, "~1分钟内"),
    (452.4, "~8分钟"),
    (3905, "~1小时5分钟"),
])
def test_human_eta(seconds, text):
    assert view.human_eta(seconds) == text


# ---------------------------------------------------------------- 进度比例
def test_progress_ratio_needs_both_current_and_total():
    assert view.progress_ratio(Progress()) is None
    assert view.progress_ratio(Progress(current=5)) is None
    assert view.progress_ratio(Progress(total=3010)) is None


def test_progress_ratio_value():
    assert view.progress_ratio(Progress(current=1800, total=3010)) == pytest.approx(0.598, abs=1e-3)


def test_progress_ratio_survives_zero_and_overflow():
    """total=0 会 ZeroDivisionError 崩页；current>total（日志错位）会让 st.progress 抛
    StreamlitAPIException（只收 0.0~1.0）。两种都必须在纯函数里挡掉。"""
    assert view.progress_ratio(Progress(current=5, total=0)) is None
    assert view.progress_ratio(Progress(current=9, total=3)) == 1.0


# ---------------------------------------------------------------- 进度文案
def test_caption_of_running_scan_uses_real_log():
    """真实扫描日志（截到第 20 行 = 跑到 1800/3010）：百分比、ETA 都对着手算结果。
    ETA = 673s/1800 × 1210 只 ≈ 452s ≈ 8 分钟；"已用"取墙钟（含 get_all_symbols
    那 2-4 分钟固定开销），不能拿日志里的"耗时 673s"冒充。"""
    running = "\n".join(SCAN_LOG.splitlines()[:20])
    caption = view.progress_caption(parse_market_scan(running), elapsed_s=900)
    assert caption == "扫描中，1800/3010（60%），已用 15分0秒，预计剩余 ~8分钟，信号 58 条，失败 0 只"


def test_caption_without_progress_falls_back_to_phase_and_elapsed():
    """每日信号没有可解析的进度行：只能给阶段 + 已用时长，不许出现百分比或 ETA。"""
    caption = view.progress_caption(Progress(phase="取数中"), elapsed_s=12)
    assert caption == "取数中，已用 12秒"


def test_caption_keeps_phase_when_progress_known():
    """扫描到一半崩了：百分比还在，但"异常退出"绝不能被百分比顶掉。"""
    crashed = "\n".join(SCAN_LOG.splitlines()[:20]) + "\nTraceback (most recent call last):\nValueError: boom"
    caption = view.progress_caption(parse_market_scan(crashed), elapsed_s=900)
    assert caption.startswith("异常退出，1800/3010（60%）")


def test_caption_of_finished_backtest_lists_every_strategy():
    """默认配置两个策略，两个报告目录都要出现（只留第一个 = donchian 的产物人间蒸发）。"""
    caption = view.progress_caption(parse_backtest(BACKTEST_LOG), elapsed_s=243)
    assert caption.startswith("完成，已用 4分3秒")
    assert "output/ma_cross_20260826_112606" in caption
    assert "output/donchian_20260826_112606" in caption


def test_caption_never_shows_elapsed_when_unknown():
    assert view.progress_caption(Progress(phase="启动中")) == "启动中"


# ---------------------------------------------------------------- 墙钟耗时
def test_elapsed_of_running_job_counts_to_now():
    state = _state(started_at="2026-08-25T10:00:00")
    now = datetime.fromisoformat("2026-08-25T10:12:38")
    assert view.elapsed_seconds(state, now=now) == 758.0


def test_elapsed_of_finished_job_freezes_at_finished_at():
    """已结束的任务再过一天打开面板，"已用"必须还是当时那 758 秒，不能跟着现在涨。"""
    state = _state(status="success", exit_code=0, finished_at="2026-08-25T10:12:38")
    now = datetime.fromisoformat("2026-08-26T09:00:00")
    assert view.elapsed_seconds(state, now=now) == 758.0


def test_elapsed_is_none_when_timestamp_unparsable():
    assert view.elapsed_seconds(_state(started_at="")) is None


def test_elapsed_never_negative():
    state = _state(started_at="2026-08-25T10:00:00")
    assert view.elapsed_seconds(state, now=datetime.fromisoformat("2026-08-25T09:00:00")) == 0.0


# ---------------------------------------------------------------- 全局互斥
def test_start_enabled_when_nothing_runs():
    assert view.start_button_state(None, "backtest") == (False, "")


def test_start_disabled_and_tells_which_job_runs():
    """互斥提示必须点名是谁在跑（用中文显示名，不是内部 job key），
    否则用户面对三个灰按钮完全不知道该等什么。"""
    disabled, notice = view.start_button_state("market_scan", "backtest")
    assert disabled is True
    assert "全市场扫描" in notice and "market_scan" not in notice


def test_start_disabled_for_the_running_job_itself():
    disabled, notice = view.start_button_state("market_scan", "market_scan")
    assert disabled is True and notice


def test_unknown_running_job_name_does_not_crash():
    """状态目录里混进别的 json（手工实验残留）：提示照出，绝不 KeyError 崩页。"""
    disabled, notice = view.start_button_state("ghost", "backtest")
    assert disabled is True and "ghost" in notice
