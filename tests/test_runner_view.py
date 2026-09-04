# tests/test_runner_view.py — v0.2.0 §4.1 面板文案纯函数（离线）
#
# 面板本身写不了单测（顶层执行 UI 代码），所以"显示什么字"这件事必须挤到纯函数里来测：
# 徽标要不要带退出码、ETA 该不该显示、百分比怎么算，错了都是用户直接看到的假信息。
from datetime import date, datetime
from pathlib import Path

import pytest

from quant.runner import view
from quant.runner.process import RUNNING, STOPPED, SUCCESS, RunState
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


# ---------------------------------------------------------------- 状态 pill（§2.3 第 5 条）
def test_idle_pill_when_never_run():
    """从未跑过 = 灰底"空闲"。pill 的两个返回值都要能单测：文案给用户看，
    第二个是 CSS 类目（qd-pill-*），面板只负责把它拼进 class 属性。"""
    text, kind = view.status_pill(None)
    assert text == "空闲"
    assert kind == view.PILL_IDLE


@pytest.mark.parametrize("status, word, kind", [
    ("running", "运行中", "running"),
    ("success", "成功", "success"),
    ("stopped", "已停止", "idle"),      # 已停止 = 灰，不是失败的红
    ("failed", "失败", "failed"),
])
def test_pill_words_and_colors(status, word, kind):
    """四色对应 §2.3 第 5 条：运行中琥珀 / 成功绿 / 失败红 / 已停止灰。"""
    text, got = view.status_pill(_state(status=status, exit_code=0))
    assert word in text
    assert got == kind


def test_failed_pill_carries_exit_code():
    """退出码是排查的第一手线索（1=脚本 sys.exit，2=argparse 参数错，-9=被 kill）。
    只显示"失败"等于把它藏起来。"""
    text, kind = view.status_pill(_state(status="failed", exit_code=3))
    assert "退出码 3" in text
    assert kind == view.PILL_FAILED


def test_failed_pill_says_unknown_when_exit_code_lost():
    """僵尸清理（进程不是面板的孩子）拿不到退出码，如实写"未知"，不能显示"退出码 None"。"""
    text, _ = view.status_pill(_state(status="failed", exit_code=None))
    assert "未知" in text and "None" not in text


def test_unknown_status_pill_does_not_crash():
    """状态文件是普通 JSON，手工改成任何字符串都可能（status: "paused"）。
    用下标取值会 KeyError 直接崩页——整个控制台连"删掉这个文件"的提示都给不出。
    未知状态归灰：绝不能瞎报成"运行中"（那会把互斥说成正在跑）。"""
    text, kind = view.status_pill(_state(status="paused"))
    assert "paused" in text
    assert kind == view.PILL_IDLE


def test_pill_kinds_are_the_four_css_classes():
    """类目字符串直接拼进 .qd-pill-* 类名，写错就是没有底色的裸文字。"""
    assert {view.PILL_RUNNING, view.PILL_SUCCESS, view.PILL_FAILED, view.PILL_IDLE} == \
        {"running", "success", "failed", "idle"}


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
def _joined(*args, **kw) -> str:
    """两行文案接回一行来断言原有口径。§2.4 把进度文案拆成"主状态"+"细节"两行，
    但拆的位置不能改变说了什么——下面这批用例逐字沿用 v0.2.0 的期望串。"""
    return "，".join(x for x in view.progress_lines(*args, **kw) if x)


def test_progress_lines_split_headline_from_detail():
    """§2.4：主状态（阶段 + 计数 + 百分比）一行，细节（已用/预计剩余/额外计数）另一行。
    挤成一行的问题是眼睛没有落点——"扫描中"和"失败 0 只"视觉权重一样。"""
    running = "\n".join(SCAN_LOG.splitlines()[:20])
    head, detail = view.progress_lines(parse_market_scan(running), elapsed_s=900,
                                       status="running")
    assert head == "扫描中，1800/3010（60%）"
    assert detail == "已用 15分0秒，预计剩余 ~8分钟，信号 58 条，失败 0 只"


def test_progress_lines_detail_is_empty_when_nothing_to_say():
    """细节为空串（不是 None、不是 "—"）：面板据此**不渲染**第二行，
    免得留一条空 caption 把版面撑开。"""
    assert view.progress_lines(Progress(phase="启动中"), status="running") == ("启动中", "")


def test_progress_lines_headline_never_hides_the_phase():
    """扫描跑到一半崩了：主状态那行必须以"异常退出"开头，不能被百分比顶掉。"""
    crashed = ("\n".join(SCAN_LOG.splitlines()[:20])
               + "\nTraceback (most recent call last):\nValueError: boom")
    head, _ = view.progress_lines(parse_market_scan(crashed), elapsed_s=900,
                                  status="running")
    assert head.startswith("异常退出")


def test_progress_lines_of_stopped_run_keeps_eta_out_of_both_lines():
    """B1 回归：ETA 不许"搬到第二行"偷偷复活。两行都不能有。"""
    stopped = "\n".join(SCAN_LOG.splitlines()[:20])
    head, detail = view.progress_lines(parse_market_scan(stopped), elapsed_s=900,
                                       status="stopped")
    assert "预计剩余" not in head and "预计剩余" not in detail
    assert "扫描中" not in head and "扫描中" not in detail
    assert head.startswith("已停止")


def test_caption_of_running_scan_uses_real_log():
    """真实扫描日志（截到第 20 行 = 跑到 1800/3010）：百分比、ETA 都对着手算结果。
    ETA = 673s/1800 × 1210 只 ≈ 452s ≈ 8 分钟；"已用"取墙钟（含 get_all_symbols
    那 2-4 分钟固定开销），不能拿日志里的"耗时 673s"冒充。"""
    running = "\n".join(SCAN_LOG.splitlines()[:20])
    caption = _joined(parse_market_scan(running), elapsed_s=900, status="running")
    assert caption == "扫描中，1800/3010（60%），已用 15分0秒，预计剩余 ~8分钟，信号 58 条，失败 0 只"


def test_caption_without_progress_falls_back_to_phase_and_elapsed():
    """信号跟踪没有可解析的进度行：只能给阶段 + 已用时长，不许出现百分比或 ETA。"""
    caption = _joined(Progress(phase="取数中"), elapsed_s=12, status="running")
    assert caption == "取数中，已用 12秒"


def test_caption_keeps_phase_when_progress_known():
    """扫描到一半崩了：百分比还在，但"异常退出"绝不能被百分比顶掉。"""
    crashed = "\n".join(SCAN_LOG.splitlines()[:20]) + "\nTraceback (most recent call last):\nValueError: boom"
    caption = _joined(parse_market_scan(crashed), elapsed_s=900, status="running")
    assert caption.startswith("异常退出，1800/3010（60%）")


def test_caption_of_finished_backtest_lists_every_strategy():
    """默认配置两个策略，两个报告目录都要出现（只留第一个 = donchian 的产物人间蒸发）。"""
    caption = _joined(parse_backtest(BACKTEST_LOG), elapsed_s=243, status="success")
    assert caption.startswith("完成，已用 4分3秒")
    assert "output/ma_cross_20260826_112606" in caption
    assert "output/donchian_20260826_112606" in caption


def test_caption_never_shows_elapsed_when_unknown():
    assert _joined(Progress(phase="启动中"), status="running") == "启动中"


# ------------------------------------------------ 终止态的文案（B1：进程都没了还在报 ETA）
def test_caption_of_stopped_run_drops_eta_and_running_phase():
    """用户按了停止、进程已死，日志最后一行仍是 [1800/3010]。此时：
    1) 绝不能再说"扫描中"——同屏徽标写着"⏹ 已停止"，两句话互相打架；
    2) 更不能拿死掉的速率外推"预计剩余 ~8分钟"，照着等就是白等（设计 §3.2
       "宁可不显示，不显示假数字"）。
    停在哪儿（1800/3010、已用时长、信号条数）都是既成事实，必须留着。"""
    stopped = "\n".join(SCAN_LOG.splitlines()[:20])
    caption = _joined(parse_market_scan(stopped), elapsed_s=900, status="stopped")
    assert caption == "已停止，1800/3010（60%），已用 15分0秒，信号 58 条，失败 0 只"


def test_caption_of_failed_run_without_traceback_says_failed():
    """崩在 argparse（退出码 2，日志里只有 usage 没有 traceback）：解析器只能看出"扫描中"，
    状态却是 failed。文案得跟徽标一致，且照样不许有 ETA。"""
    failed = "\n".join(SCAN_LOG.splitlines()[:20])
    caption = _joined(parse_market_scan(failed), elapsed_s=900, status="failed")
    assert caption.startswith("失败，1800/3010（60%）")
    assert "预计剩余" not in caption and "扫描中" not in caption


def test_caption_keeps_crashed_phase_from_log_but_still_no_eta():
    """日志里有 traceback：解析出的"异常退出"比状态词"失败"更具体，保留它；ETA 仍然不给。"""
    crashed = ("\n".join(SCAN_LOG.splitlines()[:20])
               + "\nTraceback (most recent call last):\nValueError: boom")
    caption = _joined(parse_market_scan(crashed), elapsed_s=900, status="failed")
    assert caption.startswith("异常退出，1800/3010（60%）")
    assert "预计剩余" not in caption


def test_caption_of_success_whose_log_is_gone_still_says_done():
    """日志被手工删了/只剩半截（read_log 读不到就是空串），status 却是 success：
    以状态为准说"完成"，不能因为日志里最后一行是进度行就继续喊"扫描中"。"""
    partial = "\n".join(SCAN_LOG.splitlines()[:20])
    caption = _joined(parse_market_scan(partial), elapsed_s=1500, status="success")
    assert caption.startswith("完成，1800/3010（60%）")
    assert "预计剩余" not in caption


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


# ---------------------------------------------------------------- 页头的全局任务 pill
def test_busy_pill_is_idle_when_nothing_runs():
    assert view.busy_pill(None) == (view.IDLE_TEXT, view.PILL_IDLE)


def test_busy_pill_names_the_running_job_in_chinese():
    """页头右侧那颗 pill 是**全局**状态：用户在 K 线页也该看见"扫描还在跑"，
    否则他会去点另一个开始，然后对着"启动失败"发愁。用中文显示名，不是内部 key。"""
    text, kind = view.busy_pill("market_scan")
    assert "全市场扫描" in text and "运行中" in text
    assert "market_scan" not in text
    assert kind == view.PILL_RUNNING


def test_busy_pill_of_an_unknown_job_name_does_not_crash():
    """runs/ 里混进别的 json（手工实验残留）：照样出 pill，不许 KeyError 崩页。"""
    text, kind = view.busy_pill("ghost")
    assert "ghost" in text and kind == view.PILL_RUNNING


def test_unknown_text_never_claims_idle():
    """状态文件损坏时页头用这句：绝不能说"空闲"——那是猜的，
    而互斥状态不可知恰恰是最该 fail-safe 的时候。"""
    assert "未知" in view.UNKNOWN_TEXT
    assert "空闲" not in view.UNKNOWN_TEXT


# ================================================================ 终止且无产物（v0.5.0）

def test_no_output_note_speaks_up_when_a_stopped_run_left_nothing():
    """停在半路时细节行会写着「信号 N 条」，而那 N 条一条都没落盘。
    不补这句，屏幕上唯一的数字就指向一份不存在的文件。"""
    p = Progress(phase="已停止", current=2500, total=3012,
                          extras={"信号": "130 条"}, outputs=())
    note = view.no_output_note(p, status=STOPPED)
    assert "没有产物" in note
    assert "上一次" in note, "没说清页面上看到的是哪一次的结果"
    assert "第 1 只" in note, "没说清重跑是从头开始（不存在断点续跑）"


def test_no_output_note_stays_quiet_when_there_is_something_to_show():
    """跑成功、或者失败但已经落了盘：这句话就是噪声，返回空串（同 progress_lines
    的既有约定：空串 = 面板不渲染这一行）。"""
    done = Progress(phase="完成", current=3012, total=3012, extras={},
                             outputs=("output/scan/2026-09-01.csv",))
    assert view.no_output_note(done, status=SUCCESS) == ""
    assert view.no_output_note(done, status=STOPPED) == "", \
        "有产物就不该说「没有产物」"
    running = Progress(phase="扫描中", current=100, total=3012,
                                extras={}, outputs=())
    assert view.no_output_note(running, status=RUNNING) == "", \
        "还在跑的时候说这句话等于劝人别等了"


# ================================================================ 重跑说清它要跑什么（v0.5.0）

def test_rerun_hint_names_the_parameters_it_will_reuse():
    """三个按钮并排，前两个的参数就在上面的控件里看得见，而「↻ 重跑」的参数
    **只存在磁盘那份 JSON 里**。真实后果：本机那份 argv 带着 --date，
    今天点一下就是花十几分钟重扫昨天，而屏幕上没有任何东西提示过。"""
    from quant.runner import jobs
    hint = view.rerun_hint("market_scan", jobs.build_argv("market_scan", {"limit": 300}))
    assert "300" in hint and "沿用" in hint, hint

    day = view.rerun_hint("market_scan",
                          jobs.build_argv("market_scan", {"date": date(2026, 9, 1)}))
    assert "2026-09-01" in day, day


def test_rerun_hint_says_so_when_there_is_nothing_to_reuse():
    """全默认时别写成空话（v0.5.0 起三个任务都有参数，信号跟踪多了 --date）。"""
    from quant.runner import jobs
    assert "默认" in view.rerun_hint("market_scan", jobs.build_argv("market_scan", {}))
    assert "默认" in view.rerun_hint("daily_signal", jobs.build_argv("daily_signal", {}))


def test_rerun_hint_does_not_promise_a_run_that_will_be_refused():
    """argv 被手改坏时不许说"沿用上次参数"——真正的拦截在 ui.rerun_job 的
    build_argv(parse_argv(...)) 往返闸门，这里只负责别把话说满。"""
    hint = view.rerun_hint("market_scan", ["随便", "改", "过"])
    assert "改坏" in hint and "沿用" not in hint, hint
