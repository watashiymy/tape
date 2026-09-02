# tests/test_dashboard_console.py — v0.2.0 §4.1 任务控制台页（离线，AppTest）
#
# 这里**绝不真启动扫描**：需要"运行中"就伪造状态文件（pid 用测试进程自己的，
# 探活自然为真），需要"点了开始"就 monkeypatch process.start 记调用。
# 真实启停由 §6 的人工验收覆盖。
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from quant.runner import jobs, process
from tests.conftest import app_module, copy_app, goto_page, stub_navigation

DASHBOARD = Path(__file__).resolve().parent.parent / "app" / "dashboard.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCAN_LOG = (FIXTURES / "market_scan_sample.log").read_text(encoding="utf-8")
BACKTEST_LOG = (FIXTURES / "backtest_sample.log").read_text(encoding="utf-8")
CONSOLE = "任务控制台"


def _htmls(at: AppTest) -> list[str]:
    """页面上我们自己包的 .qd-* HTML（st.html 在 AppTest 里没有 .value）。
    theme.inject() 那段 <style> 也走 st.html，必须滤掉——CSS 里出现每一个
    .qd-pill-* 类名，不滤的话"页面上有没有红 pill"这种断言永远为真。"""
    return [b for e in at.get("html")
            if not (b := e.proto.body).lstrip().startswith("<style>")]


def _sections(at: AppTest) -> list[str]:
    """三张卡片的标题（衬线小标题 + 状态 pill），取代 v0.2.0 的 st.subheader。"""
    return [h for h in _htmls(at) if 'class="qd-section"' in h]


def _console(tmp_path: Path) -> AppTest:
    """app/ 整目录复制进 tmp_path 再交给 AppTest：ROOT/OUTPUT/RUNS_DIR 全落在
    tmp_path，与仓库真实 output/runs/ 完全隔离（否则测试会读到、甚至停掉真任务）。"""
    dashboard = copy_app(tmp_path)
    return goto_page(AppTest.from_file(str(dashboard), default_timeout=30).run(),
                     CONSOLE)


def _fake_run(root: Path, job: str, status: str, *, log: str = "", exit_code=None,
              argv: list[str] | None = None, minutes_ago: float = 3.0,
              pid: int | None = None) -> Path:
    """伪造一次运行的状态文件 + 日志。

    status=running 时 pid 必须是**活着**的进程，否则 read_state 的僵尸清理会
    立刻把它改判成 failed —— 用测试进程自己的 pid 最省事且绝对安全
    （_signal_group 的两道拦截保证连误发信号都不可能）。
    """
    runs = root / "output" / "runs"
    (runs / "logs").mkdir(parents=True, exist_ok=True)
    log_path = runs / "logs" / f"{job}_20260825_100000_000.log"
    log_path.write_text(log, encoding="utf-8")
    started = datetime.now() - timedelta(minutes=minutes_ago)
    payload = {
        "script": job, "run_id": f"{job}_20260825_100000_000",
        "pid": pid if pid is not None else os.getpid(),
        "argv": argv or [sys.executable, "-u", jobs.JOBS[job].script],
        "log_path": str(log_path),
        "started_at": started.isoformat(timespec="seconds"),
        "status": status, "exit_code": exit_code,
        "finished_at": None if status == "running" else datetime.now().isoformat(timespec="seconds"),
    }
    (runs / f"{job}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return log_path


# ---------------------------------------------------------------- 页面骨架
def test_console_is_a_sidebar_page_and_not_the_default(tmp_path):
    """控制台是侧栏的一页，但**不是**默认落地页——默认页自 v0.2.1 M3 起是
    「使用说明」（设计 §3.1）。位次自 v0.2.2 起从末位提到第二位（设计 §2.2），
    页表本身由 tests/test_dashboard_nav.py 逐项对账，这里只守"没顶掉落地页"。"""
    dashboard = copy_app(tmp_path)
    at = AppTest.from_file(str(dashboard), default_timeout=30).run()
    assert not at.exception
    heads = [e.proto.body for e in at.get("html") if 'class="qd-head"' in e.proto.body]
    assert heads and 'class="qd-title">使用说明<' in heads[0], heads
    at = goto_page(at, CONSOLE)
    assert not at.exception
    heads = [e.proto.body for e in at.get("html") if 'class="qd-head"' in e.proto.body]
    assert heads and f'class="qd-title">{CONSOLE}<' in heads[0], heads


def test_three_cards_render(tmp_path):
    at = _console(tmp_path)
    assert not at.exception, at.exception
    text = " ".join(_sections(at))
    for job in jobs.JOBS.values():
        assert job.label in text, f"缺少「{job.label}」卡片，实际: {text}"
    for name in jobs.JOBS:
        assert at.button(f"start_{name}"), name


def test_empty_state_when_nothing_ever_ran(tmp_path):
    """三张卡片都空闲：开始可用、停止与重跑禁用，且明说"尚未运行过"。"""
    at = _console(tmp_path)
    assert not at.exception
    for name in jobs.JOBS:
        assert at.button(f"start_{name}").disabled is False, name
        assert at.button(f"stop_{name}").disabled is True, name
        assert at.button(f"rerun_{name}").disabled is True, name
    assert sum("尚未运行过" in c.value for c in at.caption) == len(jobs.JOBS)
    # v0.5.0 两区版（设计 §6）：区标题「每日流水线」「研究工具」+ 人工步骤卡片
    # 「加进信号池」+ 三张任务卡片各一个。带状态 pill 的必须**恰好**是任务卡片：
    # 人工那一步没有进程，给它挂状态 pill 就是无中生有。
    with_pill = [h for h in _sections(at) if "qd-pill-" in h]
    assert len(with_pill) == len(jobs.JOBS), with_pill
    for head in with_pill:
        assert "空闲" in head and "qd-pill-idle" in head, head
    plain = [h for h in _sections(at) if "qd-pill-" not in h]
    assert [h for h in plain if "每日流水线" in h], plain
    assert [h for h in plain if "研究工具" in h], plain
    assert [h for h in plain if "加进信号池" in h and "人工步骤" in h], plain


def test_console_page_starts_no_process(tmp_path, monkeypatch):
    """光是打开页面绝不能起进程——真发生过的事故形态是"一刷新就又跑一次全市场扫描"。"""
    calls = []
    monkeypatch.setattr(process, "start", lambda *a, **k: calls.append(a))
    _console(tmp_path)
    assert calls == []


# ---------------------------------------------------------------- 全局互斥
def test_start_buttons_all_disabled_while_a_job_runs(tmp_path):
    """baostock 单会话：一个任务在跑时，三个"开始"全部禁用，并点名是谁在跑。"""
    _fake_run(tmp_path, "market_scan", "running", log=SCAN_LOG.split("已保存")[0])
    at = _console(tmp_path)
    assert not at.exception, at.exception
    for name in jobs.JOBS:
        assert at.button(f"start_{name}").disabled is True, f"{name} 的开始按钮没被互斥禁用"
    notices = [c.value for c in at.caption]
    assert any("全市场扫描" in n and "正在运行" in n for n in notices), notices


def test_stop_only_enabled_for_the_running_job(tmp_path):
    _fake_run(tmp_path, "market_scan", "running", log=SCAN_LOG.split("已保存")[0])
    at = _console(tmp_path)
    assert at.button("stop_market_scan").disabled is False
    assert at.button("stop_backtest").disabled is True
    assert at.button("rerun_market_scan").disabled is True, "运行中不得重跑（互斥）"


# ---------------------------------------------------------------- 状态呈现
def test_failed_state_shows_exit_code(tmp_path):
    _fake_run(tmp_path, "backtest", "failed", exit_code=3,
              log="login success!\nTraceback (most recent call last):\nValueError: boom\n")
    at = _console(tmp_path)
    assert not at.exception, at.exception
    assert any("退出码 3" in h and "qd-pill-failed" in h for h in _sections(at)), \
        _sections(at)


def test_stopped_state_shows_badge_and_reenables_start(tmp_path):
    _fake_run(tmp_path, "market_scan", "stopped", exit_code=-15,
              log=SCAN_LOG.split("已保存")[0])
    at = _console(tmp_path)
    assert any("已停止" in h and "qd-pill-idle" in h for h in _sections(at)), _sections(at)
    assert at.button("start_market_scan").disabled is False, "停掉的任务不该继续占着互斥"


def test_running_scan_shows_progress_bar_with_real_percent(tmp_path):
    """真实日志截到 [1800/3010]：进度条 60%，文案带 ETA。"""
    _fake_run(tmp_path, "market_scan", "running",
              log="\n".join(SCAN_LOG.splitlines()[:20]))
    at = _console(tmp_path)
    bars = at.get("progress")
    assert len(bars) == 1, f"运行中且有 current/total 时应有且仅有一个进度条，实际 {len(bars)}"
    # 1800/3010 = 59.8%：st.progress 内部取整是**截断**（59），文案 {:.0%} 是四舍五入（60%）
    assert bars[0].proto.value == 59
    assert "1800/3010" in bars[0].proto.text
    # v0.2.1 §2.4：文案拆两行——进度条只放主状态，ETA 在下面那行细节里
    assert "预计剩余" not in bars[0].proto.text, bars[0].proto.text
    assert any("预计剩余" in c.value for c in at.main.caption), \
        [c.value for c in at.main.caption]


def test_stopped_midway_scan_shows_where_it_stopped_without_faking_eta(tmp_path):
    """B1：用户点了停止、进程已经没了，日志最后一行还停在 [1800/3010]。

    卡片必须闭嘴不许再外推：进程都不在了，"预计剩余 ~8分钟"是纯粹编出来的，
    照着等就是白等；"扫描中"还会跟同屏徽标"⏹ 已停止"当面打架。
    但"停在 1800/3010、已用多久、出了几条信号"是既成事实，要留着——
    这是用户判断"要不要从这儿接着补跑"的唯一依据。"""
    _fake_run(tmp_path, "market_scan", "stopped", exit_code=-15,
              log="\n".join(SCAN_LOG.splitlines()[:20]))
    at = _console(tmp_path)
    assert not at.exception, at.exception
    bars = at.get("progress")
    assert len(bars) == 1, f"停在 60% 也该看得见停在哪儿，实际进度条 {len(bars)} 个"
    # 文案自 v0.2.1 起分两行，所以要连细节行一起看：把 ETA 挪到第二行
    # 等于让这个缺陷原地复活
    text = " ".join([bars[0].proto.text, *(c.value for c in at.main.caption)])
    assert "1800/3010" in text and "已用" in text, text
    assert "预计剩余" not in text, f"任务已停止却还在报 ETA: {text}"
    assert "扫描中" not in text, f"任务已停止却还在说「扫描中」: {text}"
    assert "已停止" in bars[0].proto.text, bars[0].proto.text
    assert at.get("status") == [], "已终止的任务不该再转圈"


def test_crashed_scan_does_not_extrapolate_eta_either(tmp_path):
    """崩溃路径同样中招：退出码 1 + 半截进度 + traceback，照样不许有 ETA。"""
    _fake_run(tmp_path, "market_scan", "failed", exit_code=1,
              log="\n".join(SCAN_LOG.splitlines()[:20])
                  + "\nTraceback (most recent call last):\nValueError: boom\n")
    at = _console(tmp_path)
    assert not at.exception, at.exception
    bar_text = at.get("progress")[0].proto.text
    assert bar_text.startswith("异常退出，1800/3010"), bar_text
    everything = " ".join([bar_text, *(c.value for c in at.main.caption)])
    assert "预计剩余" not in everything, f"任务已崩溃却还在报 ETA: {everything}"


def test_running_job_without_parsable_progress_shows_spinner(tmp_path):
    """每日信号没有进度行：转圈 + 阶段 + 已用时长，绝不显示假百分比。"""
    _fake_run(tmp_path, "daily_signal", "running", log="login success!\n")
    at = _console(tmp_path)
    assert at.get("progress") == [], "无 current/total 时不许画进度条"
    assert len(at.status) == 1 and at.status[0].state == "running"
    assert at.status[0].label == "取数中", at.status[0].label
    assert any("已用" in c.value for c in at.main.caption), \
        [c.value for c in at.main.caption]


def test_log_tail_is_30_lines_and_expander_has_everything(tmp_path):
    full = "\n".join(f"line {i}" for i in range(1, 41))
    _fake_run(tmp_path, "daily_signal", "running", log=full)
    at = _console(tmp_path)
    codes = [c.value for c in at.code]
    assert "\n".join(f"line {i}" for i in range(11, 41)) in codes, "尾部应是最后 30 行"
    assert full in codes, "expander 里应有完整日志"
    assert any("完整日志" in e.label for e in at.expander)


def test_corrupt_state_file_shows_error_not_crash(tmp_path):
    """状态文件写了一半（面板被 kill 在 os.replace 之前几乎不可能，但手工改坏很可能）：
    read_state 会 RuntimeError，页面必须接住并说清怎么恢复。"""
    runs = tmp_path / "output" / "runs"
    runs.mkdir(parents=True)
    (runs / "backtest.json").write_text('{"script": "backtest", "pid":', encoding="utf-8")
    at = _console(tmp_path)
    assert not at.exception, f"损坏状态文件不该崩页: {at.exception}"
    assert any("损坏" in e.value for e in at.error), [e.value for e in at.error]


# ---------------------------------------------------------------- 完成后结果
def test_finished_scan_shows_result_table(tmp_path):
    """扫描成功后直接在卡片里出当次信号表；symbol 前导零不得被吃掉。"""
    scan = tmp_path / "output" / "scan"
    scan.mkdir(parents=True)
    (scan / "2026-08-24.csv").write_text(
        "date,symbol,name,strategy,close,pct_chg,amount,amount_ratio_20d\n"
        "2026-08-24,000020,深华发A,ma_cross,11.74,-5.09,2.9e8,4.22\n", encoding="utf-8")
    _fake_run(tmp_path, "market_scan", "success", exit_code=0, log=SCAN_LOG)
    at = _console(tmp_path)
    assert not at.exception, at.exception
    assert len(at.dataframe) == 1, "成功后应展示当次扫描 CSV"
    assert at.dataframe[0].value["symbol"].tolist() == ["000020"]


def _finished_scan(tmp_path) -> Path:
    """一次成功的扫描 + 它的产物 CSV（日志里的 `已保存:` 指着它）。"""
    scan = tmp_path / "output" / "scan"
    scan.mkdir(parents=True)
    csv = scan / "2026-08-24.csv"
    csv.write_text(
        "date,symbol,name,strategy,close,pct_chg,amount,amount_ratio_20d\n"
        "2026-08-24,000020,深华发A,ma_cross,11.74,-5.09,2.9e8,4.22\n", encoding="utf-8")
    _fake_run(tmp_path, "market_scan", "success", exit_code=0, log=SCAN_LOG)
    return csv


def test_the_scan_result_card_says_whether_it_was_a_trial_run(tmp_path):
    """`--limit` 的控件就在这一页上：一次 3 只票的试跑与一次全量扫描，
    在卡片里长得一模一样（尤其两者都"无新信号"时）。徽标必须说破（v0.2.4 §2.3）。"""
    from datetime import datetime as _dt

    from quant.signal import scan_meta

    csv = _finished_scan(tmp_path)
    scan_meta.save_meta(scan_meta.ScanMeta(
        date=date(2026, 8, 24), scanned=3, pool_total=3010, limit=3, signals=1,
        skipped={"no_signal": 2}, failed=0, elapsed_s=8.0,
        started_at=_dt(2026, 8, 24, 18, 0, 0)), csv)

    at = _console(tmp_path)

    assert not at.exception, at.exception
    blob = "\n".join(_htmls(at))
    assert "试跑 3 只" in blob, blob


def test_an_old_scan_product_shows_unknown_scope_in_the_console(tmp_path):
    """有 CSV 无 meta（v0.2.4 之前的产物）：说"范围未知"，不报错也不猜。"""
    _finished_scan(tmp_path)

    at = _console(tmp_path)

    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    assert "范围未知" in "\n".join(_htmls(at))


def test_finished_backtest_shows_metrics_of_every_strategy(tmp_path):
    """默认两个策略两个报告目录：两张指标卡都要出（只认第一条 = donchian 白跑）。"""
    for name, n_trades in (("ma_cross_20260826_112606", 243), ("donchian_20260826_112606", 436)):
        run = tmp_path / "output" / name
        run.mkdir(parents=True)
        (run / "metrics.json").write_text(json.dumps({"n_trades": n_trades}), encoding="utf-8")
    _fake_run(tmp_path, "backtest", "success", exit_code=0, log=BACKTEST_LOG)
    at = _console(tmp_path)
    assert not at.exception, at.exception
    blob = "\n".join(_htmls(at))
    for n in ("243", "436"):
        assert f'class="qd-metric-value">{n}<' in blob, blob
    assert at.metric == [], "指标卡已改成 .qd-metric，不该再有 st.metric"


def test_missing_result_file_warns_instead_of_crashing(tmp_path):
    """日志说存了 CSV，文件却被手工删了：给个提示，不能 FileNotFoundError 崩页。"""
    _fake_run(tmp_path, "market_scan", "success", exit_code=0, log=SCAN_LOG)
    at = _console(tmp_path)
    assert not at.exception, at.exception
    assert at.warning, "产物读不到时应有 st.warning"


# ---------------------------------------------------------------- 参数控件的闸门
def test_limit_widget_carries_the_whitelist_bounds(tmp_path):
    """控件本身就是第一道白名单（设计 §4.1）：--limit 只能是 1..LIMIT_MAX。

    build_argv 还会复核一遍，所以去掉上下界不会立刻酿成安全事故；但那样用户能在
    控件里敲出 0 或 99999，点了开始只收到一句"启动失败"——闸门前移就是为了不让他敲得出来。
    """
    at = _console(tmp_path)
    widget = at.number_input("market_scan_limit")
    assert widget.min == 1, f"下界应为 1，实际 {widget.min}"
    assert widget.max == jobs.LIMIT_MAX, f"上界应为 {jobs.LIMIT_MAX}，实际 {widget.max}"


# ---------------------------------------------------------------- 按钮动作
def test_start_failure_from_runner_is_shown_not_raised(tmp_path, monkeypatch):
    """抢跑时 process.start 抛的是 RuntimeError（互斥），不是 ValueError。

    按钮禁用只是 UI 层：状态在两次渲染之间变化（另一台标签页刚点了开始）时，
    点下去照样能走到 start()。漏接 RuntimeError = 整页 traceback，
    而不是那句"启动失败:「全市场扫描」正在运行"。
    """
    def boom(*a, **k):
        raise RuntimeError("「全市场扫描」正在运行，同时只允许一个任务（baostock 单会话）")

    monkeypatch.setattr(process, "start", boom)
    at = _console(tmp_path)
    at.button("start_backtest").click().run()
    assert not at.exception, f"启动失败不该崩页: {at.exception}"
    assert any("启动失败" in e.value and "正在运行" in e.value for e in at.error), \
        [e.value for e in at.error]


def test_start_button_builds_whitelisted_argv(tmp_path, monkeypatch):
    """点开始 = build_argv 的结果原样交给 process.start；参数走控件，不拼字符串。"""
    started: list[tuple] = []
    monkeypatch.setattr(process, "start", lambda *a, **k: started.append(a))
    at = _console(tmp_path)
    at.number_input("market_scan_limit").set_value(30).run()
    at.button("start_market_scan").click().run()
    assert len(started) == 1, started
    job_name, argv, runs_dir = started[0]
    assert job_name == "market_scan"
    assert argv == [sys.executable, "-u", jobs.JOBS["market_scan"].script, "--limit", "30"]
    assert Path(runs_dir) == tmp_path / "output" / "runs"


def test_date_picker_value_reaches_argv(tmp_path, monkeypatch):
    """日期控件的值必须真的进 argv。丢掉它不会报任何错——扫描照跑，只是跑的是
    **最近交易日**而不是用户挑的那天，结果表看上去完全正常，是纯静默的错答案。"""
    started: list[tuple] = []
    monkeypatch.setattr(process, "start", lambda *a, **k: started.append(a))
    at = _console(tmp_path)
    at.date_input("market_scan_date").set_value(date(2026, 8, 24)).run()
    at.button("start_market_scan").click().run()
    assert [a[1] for a in started] == [
        [sys.executable, "-u", jobs.JOBS["market_scan"].script, "--date", "2026-08-24"]]


def test_strategy_dropdown_and_refresh_flag_reach_argv(tmp_path, monkeypatch):
    """下拉选定策略 + 勾上 --refresh，两者都要落进 argv，且顺序稳定（按 schema）。
    漏掉 --refresh 同样是静默的：用户以为刷了缓存，实际拿旧行情回测。"""
    started: list[tuple] = []
    monkeypatch.setattr(process, "start", lambda *a, **k: started.append(a))
    at = _console(tmp_path)
    at.selectbox("backtest_strategy").set_value("ma_cross").run()
    at.checkbox("backtest_refresh").set_value(True).run()
    at.button("start_backtest").click().run()
    assert [a[1] for a in started] == [
        [sys.executable, "-u", jobs.JOBS["backtest"].script,
         "--strategy", "ma_cross", "--refresh"]]


def test_strategy_dropdown_displays_chinese_labels_but_the_value_is_the_key(tmp_path,
                                                                            monkeypatch):
    """v0.4.0 M1（设计 §1.3 的 AppTest 项）：下拉**显示**中文显示名，
    **底层值**仍是内部键——argv、REGISTRY、产物目录名认的都是键。"""
    from quant.strategy import REGISTRY, strategy_label

    started: list[tuple] = []
    monkeypatch.setattr(process, "start", lambda *a, **k: started.append(a))
    at = _console(tmp_path)
    box = at.selectbox("backtest_strategy")
    assert box.options == ["全部"] + [strategy_label(k) for k in REGISTRY], \
        "选项的显示文案应是中文显示名（「全部」除外）"
    assert "双均线交叉" in box.options and "ma_cross" not in box.options
    box.set_value("ma_cross").run()
    assert at.selectbox("backtest_strategy").value == "ma_cross", "底层值必须仍是键"
    at.button("start_backtest").click().run()
    assert [a[1] for a in started] == [
        [sys.executable, "-u", jobs.JOBS["backtest"].script,
         "--strategy", "ma_cross"]], "argv 里传的必须是键，不是中文"


def test_finished_scan_result_table_displays_the_strategy_label(tmp_path):
    """控制台卡片里的结果表也要换显示名（磁盘上的 CSV 照旧存键）。"""
    csv = _finished_scan(tmp_path)
    at = _console(tmp_path)
    assert not at.exception, at.exception
    shown = at.dataframe[0].value
    shown = getattr(shown, "data", shown)          # 扫描表可能包着 Styler
    assert shown["strategy"].tolist() == ["双均线交叉"]
    assert "ma_cross" in csv.read_text(encoding="utf-8"), "导出物是数据，保持键"


def test_strategy_all_option_omits_the_flag(tmp_path, monkeypatch):
    """留空（「全部」）= 不传 --strategy，跑全部策略。把「全部」当策略名传下去
    会被 build_argv 拒（不在 REGISTRY），用户点了开始只看到一句报错。"""
    started: list[tuple] = []
    monkeypatch.setattr(process, "start", lambda *a, **k: started.append(a))
    at = _console(tmp_path)
    assert at.selectbox("backtest_strategy").value == "全部", "默认应为「全部」"
    at.button("start_backtest").click().run()
    assert [a[1] for a in started] == [
        [sys.executable, "-u", jobs.JOBS["backtest"].script]], "「全部」不该拼进 argv"
    assert not at.error, [e.value for e in at.error]


def test_corrupt_state_file_offers_no_start_button(tmp_path):
    """状态文件损坏 = 互斥状态**不可知**，此时必须一个「开始」都不给（fail-safe）。

    这条独立于"不崩页"：把损坏分支改成"照常渲染卡片、只是多一条报错"看起来更友好，
    但那样 any_running() 的结果没人要了，真有扫描在跑也照样能点开始，
    两个 baostock 会话互踢下线——正是全局互斥要挡的事故。
    """
    runs = tmp_path / "output" / "runs"
    runs.mkdir(parents=True)
    (runs / "backtest.json").write_text('{"script": "backtest", "pid":', encoding="utf-8")
    at = _console(tmp_path)
    assert not at.exception, at.exception
    assert list(at.button) == [], f"互斥状态未知却给了按钮: {[b.label for b in at.button]}"


def test_rerun_button_reuses_last_argv(tmp_path, monkeypatch):
    started: list[tuple] = []
    monkeypatch.setattr(process, "start", lambda *a, **k: started.append(a))
    argv = [sys.executable, "-u", jobs.JOBS["market_scan"].script, "--limit", "30"]
    _fake_run(tmp_path, "market_scan", "success", exit_code=0, log=SCAN_LOG, argv=argv)
    at = _console(tmp_path)
    at.button("rerun_market_scan").click().run()
    assert [a[1] for a in started] == [argv]


def test_rerun_refuses_tampered_argv(tmp_path, monkeypatch):
    """状态文件是普通 JSON，手工改得动。重跑必须让 argv 重新过一遍白名单闸门，
    不能把文件里写的东西直接喂给 Popen。"""
    started: list[tuple] = []
    monkeypatch.setattr(process, "start", lambda *a, **k: started.append(a))
    _fake_run(tmp_path, "market_scan", "success", exit_code=0, log=SCAN_LOG,
              argv=[sys.executable, "-u", "-c", "print('pwned')"])
    at = _console(tmp_path)
    at.button("rerun_market_scan").click().run()
    assert started == [], f"被篡改的 argv 竟然被执行了: {started}"
    assert at.error, "应提示无法重跑"


def test_stop_button_calls_stop_not_kill(tmp_path, monkeypatch):
    stopped: list = []
    monkeypatch.setattr(process, "stop", lambda state, runs_dir: stopped.append(state))
    _fake_run(tmp_path, "market_scan", "running", log=SCAN_LOG.split("已保存")[0])
    at = _console(tmp_path)
    at.button("stop_market_scan").click().run()
    assert len(stopped) == 1 and stopped[0].script == "market_scan"


# ---------------------------------------------------------------- 自动刷新档位
def _run_every(fragment_fn):
    """取 st.fragment 闭包里的 run_every（面板唯一能从外部验证刷新档位的地方）。"""
    cells = dict(zip(fragment_fn.__code__.co_freevars,
                     (c.cell_contents for c in fragment_fn.__closure__ or ())))
    assert "run_every" in cells, f"streamlit 内部结构变了，闭包变量: {list(cells)}"
    return cells["run_every"]


def test_idle_cards_do_not_poll(tmp_path, monkeypatch):
    """无任务运行时不设 run_every：否则面板一直空转（每 2 秒重跑三张卡片）。"""
    import importlib.util

    dashboard = copy_app(tmp_path)
    stub_navigation(monkeypatch, CONSOLE)
    spec = importlib.util.spec_from_file_location("dashboard_console_probe",
                                                  dashboard)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # 卡片自 v0.2.2 M3 起在 app/pages_console.py，刷新档位常量在共享件 app/ui.py。
    console, ui = app_module("pages_console"), app_module("ui")

    assert _run_every(console._card_idle) is None
    assert _run_every(console._card_live) == ui.AUTO_REFRESH_S
    assert console._card_for(None) is console._card_idle
    assert console._card_for("market_scan") is console._card_live


def test_card_requests_full_rerun_when_the_running_job_finishes(tmp_path, monkeypatch):
    """任务在两次轮询之间跑完了：卡片必须请求**整页**重跑。

    不请求的话页面还挂着 busy 那一轮选出来的 `_card_live`，三张卡片就永远每 2 秒
    重跑一次（设计 §4.1 要求"无任务运行时不设 run_every，避免空转"），另外两个"开始"
    按钮也一直禁用着，直到用户自己按 F5。
    AppTest 不模拟 fragment 局部重跑，只能断言"重跑请求确实发出去了"这一机制本身。
    """
    import streamlit as st

    reruns: list[int] = []
    monkeypatch.setattr(st, "rerun", lambda *a, **k: reruns.append(1))
    _fake_run(tmp_path, "market_scan", "success", exit_code=0, log=SCAN_LOG)
    at = _console(tmp_path)
    real_any_running = process.any_running
    real_read_state = process.read_state
    entered_card = {"yes": False}

    def read_state_probe(*a, **kw):
        # 卡片渲染的第一件事就是 read_state：拿它当"整页快照阶段已结束"的分界，
        # 比数 any_running 的调用次数稳——页头也会问一次（v0.2.1 新增），
        # 数次数的写法会因此错位一格，测的就不是"跑完了没请求重跑"这件事了。
        entered_card["yes"] = True
        return real_read_state(*a, **kw)

    def any_running_that_just_finished(runs_dir):
        # 卡片之前 = page_console 拿的整页快照（那会儿还在跑）；
        # 卡片之内 = 2 秒后 fragment 再问（已结束）
        if entered_card["yes"]:
            return real_any_running(runs_dir)
        return "market_scan"

    monkeypatch.setattr(process, "read_state", read_state_probe)
    monkeypatch.setattr(process, "any_running", any_running_that_just_finished)
    reruns.clear()
    at.run()
    assert reruns, "任务已经结束，卡片却没请求整页重跑——轮询会一直空转下去"


def test_start_click_requests_full_rerun(tmp_path, monkeypatch):
    """点开始后必须整页重跑：另外两张卡片的"开始"要被互斥禁用、本页要切到 2 秒轮询，
    这些都在 fragment 之外，只重跑这张卡片是做不到的（用户看到的就是"点了没反应"）。
    AppTest 的点击本来就整页重跑，渲染结果看不出差别，只能断言这次请求发出去了。"""
    import streamlit as st

    reruns: list[int] = []
    monkeypatch.setattr(process, "start", lambda *a, **k: None)
    at = _console(tmp_path)
    monkeypatch.setattr(st, "rerun", lambda *a, **k: reruns.append(1))
    at.button("start_daily_signal").click().run()
    assert reruns, "点了开始却没请求整页重跑"


def test_stop_click_requests_full_rerun(tmp_path, monkeypatch):
    """停止同理：停完要立刻解禁另外两个"开始"并摘掉轮询，必须整页重跑。"""
    import streamlit as st

    reruns: list[int] = []
    monkeypatch.setattr(process, "stop", lambda state, runs_dir: None)
    _fake_run(tmp_path, "market_scan", "running", log=SCAN_LOG.split("已保存")[0])
    at = _console(tmp_path)
    monkeypatch.setattr(st, "rerun", lambda *a, **k: reruns.append(1))
    at.button("stop_market_scan").click().run()
    assert reruns, "点了停止却没请求整页重跑"


@pytest.mark.parametrize("page", ["回测报告", "个股K线", "今日信号", CONSOLE])
def test_all_pages_still_render(tmp_path, page):
    """加了控制台页之后，四页任何一页都不得抛异常。"""
    dashboard = copy_app(tmp_path)
    at = goto_page(AppTest.from_file(str(dashboard), default_timeout=30).run(), page)
    assert not at.exception, f"页面 {page} 抛异常: {at.exception}"
