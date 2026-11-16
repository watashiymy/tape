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

from quant.runner import jobs, process, steps
from tests.conftest import (app_module, copy_app, element_path, goto_page, path_to_element,
                            sits_on_the_right, stub_navigation)

DASHBOARD = Path(__file__).resolve().parent.parent / "app" / "dashboard.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCAN_LOG = (FIXTURES / "market_scan_sample.log").read_text(encoding="utf-8")
BACKTEST_LOG = (FIXTURES / "backtest_sample.log").read_text(encoding="utf-8")
CONSOLE = "任务控制台"
#: 控制台上真的有卡片/按钮的任务名单——不是 jobs.JOBS 的全集：market_breadth
#: 的控件搬到了「市场概览」页（app/pages_console.py 的 _ELSEWHERE），控制台不出它。
#: 硬编码而不是导入 pages_console._PIPELINE + _TOOLS：那两个是 AppTest exec 出来的
#: 模块级元组，在 bare 模式之外不方便稳定取到；这里的三个名字与它们逐一对应。
CONSOLE_JOBS = ("market_scan", "daily_signal", "backtest")


def _htmls(at: AppTest) -> list[str]:
    """页面上我们自己包的 .qd-* HTML（st.html 在 AppTest 里没有 .value）。
    theme.inject() 那段 <style> 也走 st.html，必须滤掉——CSS 里出现每一个
    .qd-pill-* 类名，不滤的话"页面上有没有红 pill"这种断言永远为真。"""
    return [b for e in at.get("html")
            if not (b := e.proto.body).lstrip().startswith("<style>")]


def _zone_heads(at: AppTest) -> list[str]:
    """两个区标题（衬线 + 发丝线）：「每日流水线」与「研究工具」。"""
    return [h for h in _htmls(at) if 'class="qd-section"' in h]


def _card_titles(at: AppTest) -> list[str]:
    """三张任务卡片的标题行：标题 + 帮助圆点 + 状态 pill（2026-11-16 起是 .qd-task-title，
    不再借用带发丝线的区标题——卡片自己有边框）。"""
    return [h for h in _htmls(at) if 'class="qd-task-title"' in h]


def _card_metas(at: AppTest) -> list[str]:
    """各张卡片标题下那一行「最新结果」（读不出最新结果的卡片没有这一行）。
    2026-11-16 第二轮之前这个元素里还有一句常驻的「做什么 / 什么时候跑」，拿掉了。"""
    return [h for h in _htmls(at) if 'class="qd-task-status"' in h]


def _card_block(at: AppTest, name: str):
    """name 那张任务卡片（带边框的容器）：同时包住它的主按钮（标题行右端）与重跑按钮
    （「更多选项」里）的**最里层**那个块——标题行自己是一行分栏，只包得住前者。"""
    best = None
    for block in _descend(at.main):
        if getattr(block, "children", None) and {f"start_{name}", f"rerun_{name}"} <= _keys_under(block):
            best = block               # _descend 自外向里：最后一个符合的就是最里层的
    assert best is not None, f"找不到 {name} 的卡片"
    return best


def _card_html(at: AppTest, name: str) -> list[str]:
    """name 那张卡片里我们自己包的全部 HTML（标题行、最新结果那一行……），按页面上的先后。"""
    return [b for e in _descend(_card_block(at, name))
            if (b := getattr(getattr(e, "proto", None), "body", None)) and "qd-" in b]


def _card_meta(at: AppTest, name: str) -> str:
    """name 那张卡片的「最新结果」那一行；没有就是空串。"""
    return next((h for h in _card_html(at, name) if 'class="qd-task-status"' in h), "")


def _expanders(at: AppTest, label: str) -> list:
    return [e for e in at.expander if e.label == label]


def _descend(block):
    """元素树按**页面上的先后**展开（AppTest 只给顶层容器，卡片 / 列 / 折叠区要自己走下去）。"""
    yield block
    for child in getattr(block, "children", {}).values():
        yield from _descend(child)


def _keys_under(block) -> set[str]:
    return {k for e in _descend(block) if (k := getattr(e, "key", None))}


def _guide():
    """面板这一轮 import 进来的 app/guide.py（文案只认它，不在测试里再抄一遍按钮上的字）。"""
    return app_module("guide")


def _log_tails(at: AppTest) -> list[str]:
    """**摊在卡片上**的日志尾部：定高、自动滚到底的那个容器里的代码块。
    折叠区里的日志全文不算——只数 at.code 分不清两者（审查抓到过一条因此空转的测试）。"""
    return [el.value for block in _descend(at.main)
            if getattr(block, "type", "") == "flex_container" and block.proto.autoscroll
            for el in _descend(block) if getattr(el, "type", "") == "code"]


def _kinds(at: AppTest) -> list[str]:
    """页面上各元素的类型，按先后。用来断言"谁在谁上面"。"""
    return [getattr(e, "type", "") for e in _descend(at.main)]


def _console(tmp_path: Path) -> AppTest:
    """app/ 整目录复制进 tmp_path 再交给 AppTest：ROOT/OUTPUT/RUNS_DIR 全落在
    tmp_path，与仓库真实 output/runs/ 完全隔离（否则测试会读到、甚至停掉真任务）。"""
    dashboard = copy_app(tmp_path)
    return goto_page(AppTest.from_file(str(dashboard), default_timeout=30).run(),
                     CONSOLE)


def _fake_run(root: Path, job: str, status: str, *, log: str = "", exit_code=None,
              argv: list[str] | None = None, minutes_ago: float = 3.0,
              pid: int | None = None, finished_days_ago: float = 0.0) -> Path:
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
        "finished_at": None if status == "running" else (
            datetime.now() - timedelta(days=finished_days_ago)).isoformat(timespec="seconds"),
    }
    (runs / f"{job}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return log_path


# ---------------------------------------------------------------- 页面骨架
def test_home_is_the_default_landing_page(tmp_path):
    """市场概览是默认落地页（2026-09-23 用户定；09-05～09-23 是「任务控制台」，
    v0.2.1～v0.5.0 是「使用说明」）。页表本身由 tests/test_dashboard_nav.py
    逐项对账，这里守"不切页时是它、切去控制台也还是控制台"。"""
    dashboard = copy_app(tmp_path)
    at = AppTest.from_file(str(dashboard), default_timeout=30).run()
    assert not at.exception
    heads = [e.proto.body for e in at.get("html") if 'class="qd-head"' in e.proto.body]
    assert heads and 'class="qd-title">市场概览<' in heads[0], heads
    at = goto_page(at, CONSOLE)
    assert not at.exception
    heads = [e.proto.body for e in at.get("html") if 'class="qd-head"' in e.proto.body]
    assert heads and f'class="qd-title">{CONSOLE}<' in heads[0], heads


def _guide_links(at: AppTest) -> list[str]:
    """页面上指向「使用说明」的 page_link 标签（AppTest 把 page_link 当 UnknownElement，
    标签在 proto 里）。"""
    return [el.proto.label for el in at.get("page_link") if "使用说明" in el.proto.label]


def test_console_points_first_time_users_at_the_guide(tmp_path):
    """落地页不再是手册了，第一次打开面板的人得有条明路：任何任务的状态文件一个都
    没有时，落地页给一条去「使用说明」的链接（2026-09-05 挂在控制台，09-23 落地页换成
    「市场概览」后跟着搬过去，测试名是那时留下的）。这里不切页，测的就是落地页。
    平时的入口是各页页头右端的「帮助中心」——标签里没有「使用说明」，不在这里的数里。"""
    at = AppTest.from_file(str(copy_app(tmp_path)), default_timeout=30).run()
    assert not at.exception, at.exception
    assert _guide_links(at) == ["第一次用？先看「使用说明」"], _guide_links(at)


def test_the_first_run_hint_disappears_once_anything_has_run(tmp_path):
    """跑过一次之后它就消失——常驻的引导等于噪声（同侧栏安全警告一个道理）。"""
    _fake_run(tmp_path, "backtest", "success", log="全部完成: 2 个策略\n", exit_code=0)
    at = AppTest.from_file(str(copy_app(tmp_path)), default_timeout=30).run()
    assert not at.exception, at.exception
    assert _guide_links(at) == [], _guide_links(at)


def test_the_first_run_hint_stays_quiet_when_the_state_file_is_corrupt(tmp_path):
    """状态文件坏了：卡片会报错，这条引导不猜"是不是第一次"，直接不显示。"""
    runs = tmp_path / "output" / "runs"
    runs.mkdir(parents=True)
    (runs / "backtest.json").write_text('{"script": "backtest", "pid":', encoding="utf-8")
    at = AppTest.from_file(str(copy_app(tmp_path)), default_timeout=30).run()
    assert not at.exception, at.exception
    assert _guide_links(at) == []


def test_three_cards_render(tmp_path):
    at = _console(tmp_path)
    assert not at.exception, at.exception
    text = " ".join(_card_titles(at))
    for name in CONSOLE_JOBS:
        label = jobs.JOBS[name].label
        assert f'class="qd-task-title">{label}<' in text, f"缺少「{label}」卡片，实际: {text}"
    for name in CONSOLE_JOBS:
        assert at.button(f"start_{name}"), name


def test_exactly_one_job_lives_elsewhere(tmp_path):
    """对账 tests/test_dashboard_guide.py::CONSOLE_CARDS（排除掉 market_breadth 的那组卡片）：
    市场概览拿走了几个任务的卡片，这里数的是同一个数字（当前只有 market_breadth）。"""
    _console(tmp_path)
    assert len(app_module("pages_console")._ELSEWHERE) == 1


def test_empty_state_when_nothing_ever_ran(tmp_path):
    """三张卡片都空闲：主按钮可用、**没有**停止按钮、重跑禁用，且每张都明说还没有结果。

    停止按钮 2026-11-16 起只在运行中出现（在进度条旁）：三个永远灰着的「停止」是噪声。"""
    at = _console(tmp_path)
    assert not at.exception
    keys = [b.key for b in at.button]
    for name in CONSOLE_JOBS:
        assert at.button(f"start_{name}").disabled is False, name
        assert f"stop_{name}" not in keys, f"{name} 空闲着却有停止按钮"
        assert at.button(f"rerun_{name}").disabled is True, name
    metas = _card_metas(at)
    assert len(metas) == len(CONSOLE_JOBS), metas
    assert all(steps.UNKNOWN in m for m in metas), metas
    # 两区：区标题「每日流水线」「研究工具」+ 三张任务卡片。状态 pill **恰好**在任务卡片上：
    # 区标题与人工那一步都没有进程，给它们挂状态 pill 就是无中生有。
    titles = _card_titles(at)
    assert len(titles) == len(CONSOLE_JOBS), titles
    for head in titles:
        assert "空闲" in head and "qd-pill-idle" in head, head
    plain = _zone_heads(at)
    assert not [h for h in plain if "qd-pill-" in h], plain
    assert [h for h in plain if "每日流水线" in h], plain
    assert [h for h in plain if "研究工具" in h], plain
    assert len(plain) == 2, plain     # 只有两个区标题：人工那一步不再是卡片（2026-09-14）
    # 人工那一步是一行：灰标签「人工步骤」+「加进信号池：正在跟踪 N 只…」+ 两个跳转链接
    rows = [e.proto.body for e in at.get("html") if 'class="qd-manual-row"' in e.proto.body]
    assert len(rows) == 1 and "人工步骤" in rows[0] and "加进信号池" in rows[0], rows
    links = [el.proto.label for el in at.get("page_link")]
    assert "去看扫描结果" in links and "去增删信号池" in links, links


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
    for name in CONSOLE_JOBS:
        assert at.button(f"start_{name}").disabled is True, f"{name} 的开始按钮没被互斥禁用"
    notices = [c.value for c in at.caption]
    assert any("全市场扫描" in n and "正在运行" in n for n in notices), notices


def test_stop_button_exists_only_on_the_running_card(tmp_path):
    """停止按钮只属于正在跑的那张卡片，而且**不占主按钮的位置**（它在进度条旁）：
    任务恰好跑完的那一刻，光标底下的按钮不许从「停止」换成「开始」。"""
    _fake_run(tmp_path, "market_scan", "running", log=SCAN_LOG.split("已保存")[0])
    at = _console(tmp_path)
    keys = [b.key for b in at.button]
    assert at.button("stop_market_scan").disabled is False
    assert "stop_backtest" not in keys and "stop_daily_signal" not in keys, keys
    assert at.button("start_market_scan").disabled is True, "主按钮还在原位，只是禁用"
    assert at.button("rerun_market_scan").disabled is True, "运行中不得重跑（互斥）"


def test_the_running_card_does_not_repeat_the_mutex_notice_about_itself(tmp_path):
    """「全市场扫描」正在运行这句话，是说给**另外两张**卡片的灰按钮听的。
    正在跑的那张自己就摊着进度条，再说一遍只是重复。"""
    _fake_run(tmp_path, "market_scan", "running", log=SCAN_LOG.split("已保存")[0])
    at = _console(tmp_path)
    notices = [c.value for c in at.caption if "正在运行" in c.value]
    assert len(notices) == len(CONSOLE_JOBS) - 1, notices


# ---------------------------------------------------------------- 状态呈现
def test_failed_state_shows_exit_code(tmp_path):
    _fake_run(tmp_path, "backtest", "failed", exit_code=3,
              log="login success!\nTraceback (most recent call last):\nValueError: boom\n")
    at = _console(tmp_path)
    assert not at.exception, at.exception
    assert any("退出码 3" in h and "qd-pill-failed" in h for h in _card_titles(at)), \
        _card_titles(at)


def test_stopped_state_shows_badge_and_reenables_start(tmp_path):
    _fake_run(tmp_path, "market_scan", "stopped", exit_code=-15,
              log=SCAN_LOG.split("已保存")[0])
    at = _console(tmp_path)
    assert any("已停止" in h and "qd-pill-idle" in h for h in _card_titles(at)), _card_titles(at)
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


def test_the_stop_button_on_a_card_sits_to_the_right_of_the_progress(tmp_path):
    """卡片上「停止」在进度条**右边**（2026-11-16 改版定的，照旧）。进度那一行与各页顶部的任务条、
    首页的收盘数据区共用一个画法（ui.running_row）：一个靠右的横向容器，进度在前、停止在后。"""
    _fake_run(tmp_path, "market_scan", "running",
              log="\n".join(SCAN_LOG.splitlines()[:20]))
    at = _console(tmp_path)
    row = element_path(at, "stop_market_scan")[-1]
    kinds = [getattr(e, "type", "") for e in row.children.values()]
    assert kinds == ["progress", "button"], f"进度那一行应是「进度条、停止」: {kinds}"
    assert sits_on_the_right(at, "stop_market_scan")
    # 与标题行的主按钮不同行：任务跑完那一刻，光标底下的按钮不许从停止换成开始
    assert not [n for n in element_path(at, "start_market_scan") if n is row]


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
    """信号跟踪没有进度行：转圈 + 阶段 + 已用时长，绝不显示假百分比。"""
    _fake_run(tmp_path, "daily_signal", "running", log="login success!\n")
    at = _console(tmp_path)
    assert at.get("progress") == [], "无 current/total 时不许画进度条"
    assert len(at.status) == 1 and at.status[0].state == "running"
    assert at.status[0].label == "获取数据中", at.status[0].label
    assert any("已用" in c.value for c in at.main.caption), \
        [c.value for c in at.main.caption]


def test_log_tail_is_30_lines_and_expander_has_everything(tmp_path):
    full = "\n".join(f"line {i}" for i in range(1, 41))
    _fake_run(tmp_path, "daily_signal", "running", log=full)
    at = _console(tmp_path)
    codes = [c.value for c in at.code]
    assert _log_tails(at) == ["\n".join(f"line {i}" for i in range(11, 41))], \
        "摊在卡片上的应是最后 30 行"
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


@pytest.mark.parametrize("page", ["回测报告", "个股K线", "信号", CONSOLE])
def test_all_pages_still_render(tmp_path, page):
    """加了控制台页之后，四页任何一页都不得抛异常。"""
    dashboard = copy_app(tmp_path)
    at = goto_page(AppTest.from_file(str(dashboard), default_timeout=30).run(), page)
    assert not at.exception, f"页面 {page} 抛异常: {at.exception}"


def test_a_corrupt_scan_meta_does_not_take_the_whole_page_down(tmp_path):
    """**v0.5.0 回归**：一份损坏的 output/scan/*.meta.json 曾让整个控制台页阵亡。

    机制：M5 新加的就绪状态行调 steps.scan_status → scan_meta.load_meta，而后者对
    损坏文件是**响亮抛错**（数据层的设计，不改）。那次调用落在卡片的 try 之外，
    异常从 fragment 冒到 exec_code 会让整页**提前中止**——第一张卡片之后的一切都不
    渲染，包括正在跑的那趟全量扫描的 ⏹ 停止 按钮。实测按钮数从 9 掉到 0。

    修法与 app/ui.scan_scope_pill 同口径：如实把错误说出来，但不把页面打没。
    「信号」页在同一份坏文件下一直是这么做的，两页从此一致。
    """
    scan = tmp_path / "output" / "scan"
    scan.mkdir(parents=True)
    (scan / "2026-09-01.csv").write_text("date,symbol\n", encoding="utf-8")
    (scan / "2026-09-01.meta.json").write_text("{ broken json", encoding="utf-8")

    at = _console(tmp_path)

    assert not at.exception, at.exception
    for name in CONSOLE_JOBS:                    # 三个任务的按钮一个都不能少
        assert at.button(f"start_{name}"), f"{name} 的开始按钮不见了"
        assert at.button(f"rerun_{name}"), f"{name} 的重跑按钮不见了"
    warnings = [w.value for w in at.main.warning]
    assert any("损坏" in w for w in warnings), f"没有如实说文件坏了：{warnings}"


def test_the_rerun_hint_is_plain_sight_text_naming_the_parameters(tmp_path):
    """「沿用上次参数重跑」的参数只存在磁盘那份 JSON 里——本机那份 argv 就带着 --date，
    点一下白扫昨天十几分钟。v0.5.0 把"要重跑什么"挂在按钮的悬停提示上；2026-11-16 起它
    直接写在按钮上方（不悬停也看得见），同一句话就不再往悬停里放第二遍。

    这里钉的是接线（漏了不会报错，只是那行字不出现），文案本身由
    tests/test_runner_view.py 的 rerun_hint 用例覆盖。
    """
    _fake_run(tmp_path, "market_scan", "success",
              argv=[sys.executable, "-u", jobs.JOBS["market_scan"].script,
                    "--date", "2026-09-01"],
              log=SCAN_LOG)
    at = _console(tmp_path)
    assert not at.exception, at.exception

    captions = [c.value for c in at.main.caption]
    assert any("沿用上次参数" in c and "2026-09-01" in c for c in captions), \
        f"页面上没写清要重跑哪一天：{captions}"
    # 没跑过的那两个任务：也得有句人话，不许只剩一个灰按钮
    assert sum("没有可沿用的参数" in c for c in captions) == len(CONSOLE_JOBS) - 1, captions
    assert not at.button("rerun_market_scan").help, "同一句话已经写在按钮上方，悬停里不放第二遍"


# ================================================================ 2026-11-16 改版
#
# 改版要回答的是一个问题：**第一次打开这一页的人，知道先点哪儿吗。** 改之前三个按钮等权重、
# 两个参数框摆在按钮前面像必填、几个月前那次的日志和 100% 进度条一直占着版面。
# 下面钉的是新版式的几条骨架：一张卡片一个主按钮、参数与日志收进折叠区、
# 版面自上而下就是流程、第一次来的人有一条明路。

# ---------------------------------------------------------------- 主按钮与试跑
def test_each_card_has_exactly_one_primary_button_with_its_own_label(tmp_path):
    """每张卡片一个主按钮（琥珀色、带图标），字是这个任务自己的——「开始扫描 / 开始跟踪 /
    开始回测」。三个一模一样的「开始」得先读标题才知道点的是什么。"""
    at = _console(tmp_path)
    assert not at.exception, at.exception
    guide = _guide()
    for name in CONSOLE_JOBS:
        btn = at.button(f"start_{name}")
        assert btn.label == guide.CONSOLE_START[name], btn.label
        assert btn.proto.type == "primary", f"{name} 的开始不是主按钮"
        assert btn.proto.icon == ":material/play_arrow:", btn.proto.icon
    primary = [b.key for b in at.button if b.proto.type == "primary"]
    assert sorted(primary) == sorted(f"start_{n}" for n in CONSOLE_JOBS), \
        f"主按钮只该是三个开始: {primary}"


def test_only_the_scan_card_offers_a_trial_run(tmp_path):
    """试跑是全市场扫描独有的：只有它一跑就是十几分钟，值得先小范围确认环境。"""
    at = _console(tmp_path)
    trials = [b for b in at.button if b.key.startswith("trial_")]
    assert [b.key for b in trials] == ["trial_market_scan"]
    assert trials[0].label == _guide().CONSOLE_TRIAL_LABEL
    assert trials[0].proto.type == "secondary", "试跑是次按钮：主按钮只有一个"
    assert trials[0].help == _guide().CONSOLE_TRIAL_HELP


def test_trial_button_starts_a_scan_limited_to_the_trial_size(tmp_path, monkeypatch):
    """说明页一直建议"第一次先扫前 300 只"；过去要自己把 300 敲进输入框，现在点一下。"""
    started: list[tuple] = []
    monkeypatch.setattr(process, "start", lambda *a, **k: started.append(a))
    at = _console(tmp_path)
    at.button("trial_market_scan").click().run()
    assert [a[:2] for a in started] == [(
        "market_scan",
        [sys.executable, "-u", jobs.JOBS["market_scan"].script,
         "--limit", str(jobs.TRIAL_LIMIT)])], started
    assert not at.error, [e.value for e in at.error]


def test_trial_button_keeps_the_chosen_date_but_not_a_typed_limit(tmp_path, monkeypatch):
    """按钮上写着"试跑 300 只"，扫的就得是 300 只——输入框里另填的只数不许悄悄顶掉它；
    但基准交易日照样带上（补扫历史某天时也想先试跑）。"""
    started: list[tuple] = []
    monkeypatch.setattr(process, "start", lambda *a, **k: started.append(a))
    at = _console(tmp_path)
    at.number_input("market_scan_limit").set_value(30).run()
    at.date_input("market_scan_date").set_value(date(2026, 8, 24)).run()
    at.button("trial_market_scan").click().run()
    assert [a[1] for a in started] == [
        [sys.executable, "-u", jobs.JOBS["market_scan"].script,
         "--limit", str(jobs.TRIAL_LIMIT), "--date", "2026-08-24"]]


def test_trial_button_obeys_the_global_mutex(tmp_path):
    _fake_run(tmp_path, "backtest", "running", log="login success!\n")
    at = _console(tmp_path)
    assert at.button("trial_market_scan").disabled is True, "别的任务在跑，试跑也不许点"


# ---------------------------------------------------------------- 参数收进折叠区
def test_params_and_rerun_live_in_a_collapsed_options_expander(tmp_path):
    """参数框摆在按钮前面，看着像必填——而它们九成的时候都该留空。
    收进「更多选项」（默认收起）；重跑也在里面：它沿用的是**上一次**的参数，不是主路径。"""
    at = _console(tmp_path)
    options = _expanders(at, _guide().CONSOLE_OPTIONS)
    assert len(options) == len(CONSOLE_JOBS), [e.label for e in at.expander]
    assert all(e.proto.expanded is False for e in options), "更多选项默认该是收起的"
    inside = set().union(*(_keys_under(e) for e in options))
    widgets = {f"{job.name}_{p.name}" for name in CONSOLE_JOBS
               for job in [jobs.JOBS[name]] for p in job.params}
    assert widgets <= inside, f"有参数控件露在折叠区外面: {sorted(widgets - inside)}"
    assert {f"rerun_{n}" for n in CONSOLE_JOBS} <= inside, sorted(inside)
    assert not {f"start_{n}" for n in CONSOLE_JOBS} & inside, "主按钮不该被藏进折叠区"


# ---------------------------------------------------------------- 首次引导
def _callouts(at: AppTest) -> list[str]:
    return [h for h in _htmls(at) if 'class="qd-callout"' in h]


def _page_links(at: AppTest) -> list[str]:
    return [el.proto.label for el in at.get("page_link")]


def test_first_time_visitors_are_pointed_at_the_trial_button(tmp_path):
    """从没跑过任何任务：页顶一条引导，点名先点哪个按钮，旁边一条去「使用说明」的链接。"""
    at = _console(tmp_path)
    assert not at.exception, at.exception
    guide = _guide()
    callouts = _callouts(at)
    assert len(callouts) == 1, callouts
    assert guide.CONSOLE_TRIAL_LABEL in callouts[0], callouts[0]
    assert guide.CONSOLE_FIRST_RUN_LINK in _page_links(at), _page_links(at)


def test_the_first_time_callout_is_gone_once_anything_has_run(tmp_path):
    """跑过一次之后它就消失——常驻的引导等于噪声（同首页那条链接、同侧栏的安全警告）。"""
    _fake_run(tmp_path, "backtest", "success", log="全部完成: 2 个策略\n", exit_code=0)
    at = _console(tmp_path)
    assert not at.exception, at.exception
    assert _callouts(at) == []
    assert _guide().CONSOLE_FIRST_RUN_LINK not in _page_links(at)


def test_the_first_time_callout_stays_quiet_when_the_state_file_is_corrupt(tmp_path):
    """状态文件坏了：卡片会报错，这条引导不猜"是不是第一次"，直接不显示。"""
    runs = tmp_path / "output" / "runs"
    runs.mkdir(parents=True)
    (runs / "backtest.json").write_text('{"script": "backtest", "pid":', encoding="utf-8")
    at = _console(tmp_path)
    assert not at.exception, at.exception
    assert _callouts(at) == []


def test_the_rerun_button_sits_right_of_the_line_that_says_what_it_reruns(tmp_path):
    """「更多选项」里：要沿用的是哪些参数写在左边（那是会变的事实，必须在明面上），
    「沿用上次参数重跑」在同一行的右端——操作按钮一律靠右。"""
    _finished_scan(tmp_path)
    at = _console(tmp_path)
    assert not at.exception, at.exception
    row = element_path(at, "rerun_market_scan")[-1]
    said, button = row.children.values()
    assert getattr(said, "type", "") == "caption" and said.value.startswith("沿用上次参数"), said
    assert button.key == "rerun_market_scan"
    assert sits_on_the_right(at, "rerun_market_scan")
    # 一个横向容器，不是按比例的分栏：按钮在比例栏里，1024 宽时那一栏只有一百二十几像素，
    # 「沿用上次参数重跑」会折成两行（独立审查在真实浏览器里量的）。
    assert not _in_a_ratio_column(at, "rerun_market_scan")


def test_the_manual_step_line_only_states_the_fact(tmp_path):
    """「人工步骤」那一行只说现在盯着几只；"为什么这一步得你自己来"在「去增删信号池」的
    悬停说明里，不再跟在这一行后面。"""
    at = _console(tmp_path)
    rows = [h for h in _htmls(at) if 'class="qd-manual-row"' in h]
    assert len(rows) == 1 and "加进信号池" in rows[0], rows
    assert "值不值得" not in rows[0] and "自己判断" not in rows[0], rows[0]
    link = next(e for e in at.get("page_link") if e.proto.label == "去增删信号池")
    assert link.proto.help == _guide().PIPELINE_MANUAL_HELP


def _in_a_ratio_column(at: AppTest, key: str) -> bool:
    """key 那个控件是不是**直接**待在一行分栏的某一栏里（中间可以隔着一层横向小容器）。
    按比例分出来的窄栏会把长一点的按钮挤成两行；该用"文字占剩下的地方 + 按钮自己的宽度"。"""
    path = element_path(at, key)
    return any(getattr(n, "type", "") == "column" for n in path[-2:])


@pytest.mark.parametrize("name", CONSOLE_JOBS)
def test_the_start_button_says_what_it_will_run_on_hover(tmp_path, name):
    """主按钮点下去会跑什么、要多久、什么时候跑：在它自己的悬停说明里。卡片上常驻的那一句
    拿掉之后，初稿里这个按钮悬停时一个字都没有——而它起的是一趟十几分钟到两小时的任务
    （独立审查指出的；任务条上同名的按钮一直有）。"""
    at = _console(tmp_path)
    guide = _guide()
    tip = at.button(f"start_{name}").proto.help
    assert tip == guide.CONSOLE_START_HELP[name] and len(tip) > 20, tip
    facts = {"market_scan": ("scan_minutes", "scan_cold", "data_ready"),
             "daily_signal": ("data_ready",), "backtest": ("backtest_seconds",)}[name]
    for key in facts:
        assert guide.FACTS[key] in tip, f"{name} 主按钮的悬停说明里缺 {key}: {tip}"


def test_the_limit_box_says_what_blank_means_inside_the_box(tmp_path):
    """「更多选项」里的输入框，标题只说它是什么（不带括号说明）；"留空 = 全量"在空着的框里。"""
    at = _console(tmp_path)
    box = at.number_input(key="market_scan_limit")
    assert box.label == "只扫前 N 只", box.label
    assert box.proto.placeholder == "留空 = 全量"
    labels = [e.label for kind in ("number_input", "date_input", "selectbox", "checkbox")
              for e in at.get(kind)]
    assert labels and not [x for x in labels if "（" in x or "(" in x], labels


@pytest.mark.parametrize("name", CONSOLE_JOBS)
def test_a_failed_start_is_reported_across_the_card_not_inside_the_button_column(tmp_path, name,
                                                                                 monkeypatch):
    """起任务失败的那句话要占整行、落在按钮下方——不许挤在标题行右边那一窄栏里。
    按钮是在一栏里画的，起任务必须在那一栏外面做（独立审查指出这一条一直没人钉）。"""
    def refuse(*args, **kwargs):
        raise RuntimeError("另一个任务抢先了")
    monkeypatch.setattr(process, "start", refuse)
    at = _console(tmp_path)
    at = at.button(f"start_{name}").click().run()
    errors = [e for e in at.error if "启动失败" in e.value]
    assert len(errors) == 1, [e.value for e in at.error]
    path_to_error = path_to_element(at, errors[0])
    assert not [n for n in path_to_error if getattr(n, "type", "") == "column"], \
        "启动失败的提示落在了某一栏里"


def test_a_failed_rerun_is_reported_below_its_row_not_inside_it(tmp_path, monkeypatch):
    """「沿用上次参数重跑」起不来时，那句话同样要占整行：重跑是在按钮那一行**外面**起的
    （独立审查点名的三处之一，另两处是主按钮与任务条上的开始按钮）。"""
    _finished_scan(tmp_path)

    def refuse(*args, **kwargs):
        raise RuntimeError("另一个任务抢先了")
    monkeypatch.setattr(process, "start", refuse)
    at = _console(tmp_path)
    at = at.button("rerun_market_scan").click().run()
    assert not at.exception, at.exception
    errors = [e for e in at.error if "启动失败" in e.value]
    assert len(errors) == 1, [e.value for e in at.error]
    row = element_path(at, "rerun_market_scan")[-1]
    assert not [n for n in path_to_element(at, errors[0]) if n is row], "报错落在了按钮那一行里"


# ---------------------------------------------------------------- 标题下那一行
@pytest.mark.parametrize("name", CONSOLE_JOBS)
def test_a_card_explains_itself_in_the_help_dot_not_on_its_face(tmp_path, name):
    """做什么、什么时候跑、多久：在标题旁的帮助圆点里，**不常驻在卡片上**（2026-11-16 第二轮，
    用户：这类解释性文字读起来是有成本的，用「？」提示、光标移上去再弹出来就行）。
    卡片明面上我们自己包的只有两样：标题行（标题 + 圆点 + 状态）与「最新结果」那一行。"""
    at = _console(tmp_path)
    assert not at.exception, at.exception
    title, *rest = _card_html(at, name)
    assert 'class="qd-task-title"' in title and 'class="qd-help-tip"' in title, title
    tip = title.split('class="qd-help-tip">', 1)[1]
    assert "做什么" in tip and "多久" in tip and "什么时候跑" in tip, tip
    assert len(rest) == 1 and 'class="qd-task-status"' in rest[0], rest
    assert "qd-task-lead" not in "".join(rest), "卡片上又有一句常驻的说明了"


def test_the_latest_product_line_is_labelled_and_dated(tmp_path):
    """「最新结果」那一行说的是**产物**（哪天的、什么范围），与上次运行成没成是两件事。"""
    _finished_scan(tmp_path)
    at = _console(tmp_path)
    scan_meta_line = _card_meta(at, "market_scan")
    assert _guide().CONSOLE_STATUS_KEY in scan_meta_line
    assert "最近一次 2026-08-24" in scan_meta_line, scan_meta_line


# ---------------------------------------------------------------- 按状态摊开 / 收起
def test_a_successful_run_folds_into_one_summary_line(tmp_path):
    """成功结束之后：不画一条 100% 的进度条、不摊着日志。留一行「上次运行」，
    日志整个收进「运行日志」。几个月前那次的日志一直占着半屏，正是改版要拿掉的东西。"""
    _finished_scan(tmp_path)
    at = _console(tmp_path)
    assert not at.exception, at.exception
    guide = _guide()
    assert at.get("progress") == [], "成功了还画进度条：一条 100% 的琥珀色横杠什么都没说"
    captions = [c.value for c in at.main.caption]
    assert any(c.startswith("上次运行：完成") and "已用" in c for c in captions), captions
    logs = _expanders(at, guide.CONSOLE_LOG)
    assert len(logs) == 1 and logs[0].proto.expanded is False, [e.label for e in at.expander]
    assert _expanders(at, guide.CONSOLE_FULL_LOG) == [], "没有摊着的日志尾部，就没有「完整日志」"
    assert _log_tails(at) == [], "成功了卡片上还摊着日志尾部"
    # st.code 会吃掉首尾的换行，所以两边都 strip 了再比
    assert [c.value.strip() for c in at.code] == [SCAN_LOG.strip()], \
        "日志只该出现一次（全文，在折叠区里）"


def test_a_result_from_today_is_open_and_still_a_real_table(tmp_path):
    """今天跑完的结果默认摊开——人就是来看它的。"""
    _finished_scan(tmp_path)                       # _fake_run 的结束时刻就是现在
    at = _console(tmp_path)
    result = _expanders(at, _guide().CONSOLE_RESULT)
    assert len(result) == 1 and result[0].proto.expanded is True
    assert len(at.dataframe) == 1


def test_the_scan_result_explains_itself_in_the_dot_after_its_title(tmp_path):
    """卡片里那张扫描结果表的读表说明：在「本次扫描结果」小标题后面的帮助圆点里（圆点在范围徽标
    之前），不再是表格上方常驻的一行灰字。变异自查抓到这一处没人钉——信号页那张同样的表有测试，
    控制台卡片里这张没有。"""
    _finished_scan(tmp_path)
    at = _console(tmp_path)
    assert not at.exception, at.exception
    hint = _guide().TABLE_HINTS["scan"]
    heads = [h for h in _htmls(at) if 'class="qd-section">本次扫描结果' in h]
    assert len(heads) == 1, heads
    assert app_module("theme").help_dot(hint) in heads[0]
    assert heads[0].index("qd-help-dot") < heads[0].index("qd-section-pill")
    assert hint not in [c.value for c in at.main.caption]


def test_an_older_result_starts_collapsed_but_is_not_thrown_away(tmp_path):
    """三天前跑完的：结果还在，只是收起——一打开页面就摊着一张旧表，会被当成今天的。"""
    scan = tmp_path / "output" / "scan"
    scan.mkdir(parents=True)
    (scan / "2026-08-24.csv").write_text(
        "date,symbol,name,strategy,close,pct_chg,amount,amount_ratio_20d\n"
        "2026-08-24,000020,深华发A,ma_cross,11.74,-5.09,2.9e8,4.22\n", encoding="utf-8")
    _fake_run(tmp_path, "market_scan", "success", exit_code=0, log=SCAN_LOG,
              finished_days_ago=3)
    at = _console(tmp_path)
    assert not at.exception, at.exception
    result = _expanders(at, _guide().CONSOLE_RESULT)
    assert len(result) == 1 and result[0].proto.expanded is False
    assert len(at.dataframe) == 1, "收起不等于不渲染"


def test_a_failed_run_keeps_its_log_tail_in_plain_sight(tmp_path):
    """没跑成的时候日志尾部必须摊着：那几行 traceback 就是"为什么"。"""
    _fake_run(tmp_path, "backtest", "failed", exit_code=3,
              log="login success!\nTraceback (most recent call last):\nValueError: boom\n")
    at = _console(tmp_path)
    assert not at.exception, at.exception
    guide = _guide()
    tails = _log_tails(at)
    assert len(tails) == 1 and "ValueError: boom" in tails[0], \
        f"失败了日志尾部却没摊在卡片上（只在折叠区里不算）: {tails}"
    assert len(_expanders(at, guide.CONSOLE_FULL_LOG)) == 1
    assert _expanders(at, guide.CONSOLE_LOG) == [] and _expanders(at, guide.CONSOLE_RESULT) == []


def test_closing_the_browser_is_safe_is_said_on_the_stop_button(tmp_path):
    """「关掉浏览器也不会中断」只在动作现场说，而且不常驻：它是「停止」按钮的悬停说明
    （想走开的人以为得先点它，其实不用）。进度条下面那一行只留会变的数。"""
    note = _guide().CONSOLE_BACKGROUND_NOTE
    _fake_run(tmp_path, "market_scan", "running", log="\n".join(SCAN_LOG.splitlines()[:20]))
    at = _console(tmp_path)
    assert not at.exception, at.exception
    captions = [c.value for c in at.main.caption]
    assert not [c for c in captions if "关掉浏览器" in c], captions
    assert any("已用" in c for c in captions), captions
    tip = at.button("stop_market_scan").proto.help
    assert tip.startswith(note), tip
    # 停掉一趟扫描会丢什么，要点停止的那一刻才用得上——跟在同一句悬停说明后面
    assert "丢掉这趟已扫出的信号" in tip and tip == _guide().stop_help("market_scan")


# ---------------------------------------------------------------- 版面就是流程
def _document_order(at: AppTest) -> list[str]:
    """页面上的先后：带 key 的控件记 key，我们自己包的 HTML 记正文。"""
    out = []
    for el in _descend(at.main):
        if key := getattr(el, "key", None):
            out.append(key)
        elif body := getattr(getattr(el, "proto", None), "body", None):
            out.append(body)
    return out


def _position(order: list[str], needle: str) -> int:
    hits = [i for i, item in enumerate(order) if needle in item]
    assert hits, f"页面上找不到 {needle!r}"
    return hits[0]


def test_the_page_reads_top_to_bottom_in_pipeline_order(tmp_path):
    """整幅宽的卡片自上而下：全市场扫描 → 人工那一步 → 信号跟踪，再是「研究工具」区的回测。
    顺序由**位置**表达——刻意不加 ①②③ 编号（说明页与 README 各有一套，第三套会互相矛盾）。"""
    at = _console(tmp_path)
    assert not at.exception, at.exception
    order = _document_order(at)
    marks = ["每日流水线", "start_market_scan", 'class="qd-manual-row"', "start_daily_signal",
             "研究工具", "start_backtest"]
    positions = [_position(order, m) for m in marks]
    assert positions == sorted(positions), dict(zip(marks, positions))


def test_two_down_arrows_bracket_the_manual_step_and_the_flow_strip_is_gone(tmp_path):
    """人工那一步夹在两个向下的箭头之间；页顶那条横着的流程图不再出现——
    版面本身已经是流程图，同一屏画两遍就是重复。"""
    at = _console(tmp_path)
    htmls = _htmls(at)
    assert not [h for h in htmls if 'class="qd-flow"' in h], "流程条还在页顶"
    assert len([h for h in htmls if h == '<div class="qd-rail"></div>']) == 2
    order = _document_order(at)
    rails = [i for i, item in enumerate(order) if item == '<div class="qd-rail"></div>']
    manual = _position(order, 'class="qd-manual-row"')
    assert rails[0] < manual < rails[1], (rails, manual)


# ================================================================ 审查后补的（2026-11-16）
#
# 独立审查指出三类问题，下面每条测试对应一处：
#   ① 收进折叠区的参数在卡片上看不见，主按钮却照样按它跑；
#   ② 点击等卡片渲染完才处理——中间任何一处抛错这次点击就落空，失败提示还落在卡片最底下；
#   ③ 五种"改坏了测试也不红"的空洞（别的卡片的重跑、没有进度条时的停止、结束后的停止、
#      失败时摊着的日志尾部、"没有保存任何结果"那句的接线）。

# ---------------------------------------------------------------- ① 看不见的参数
def test_filled_in_params_are_named_on_the_card_face(tmp_path):
    """「更多选项」默认收起。上午试跑时填的只数与日期，下午点「开始扫描」时卡片上一个字都
    看不见，而扫描照样按它跑——以为是全量，其实只扫了 30 只、还是上周那天。
    所以填了什么要写在卡片上（折叠区外面）；全留空时这一行不出现。"""
    at = _console(tmp_path)
    assert not at.exception, at.exception
    filled = _guide().CONSOLE_PARAMS_SET.split("{")[0]
    assert not [c.value for c in at.main.caption if filled in c.value], "没填参数却有这一行"

    at.number_input("market_scan_limit").set_value(30).run()
    at.date_input("market_scan_date").set_value(date(2026, 8, 24)).run()

    lines = [c.value for c in at.main.caption if filled in c.value]
    assert len(lines) == 1, f"只有扫描那张卡片填了参数: {lines}"
    assert "只扫前 N 只 = 30" in lines[0] and "基准交易日 = 2026-08-24" in lines[0], lines[0]
    hidden = {el.value for e in _expanders(at, _guide().CONSOLE_OPTIONS) for el in _descend(e)
              if getattr(el, "type", "") == "caption"}
    assert lines[0] not in hidden, "这一行写在了折叠区里面，收起来照样看不见"


def test_a_filled_in_strategy_is_named_by_its_display_name(tmp_path):
    """写在明面上的字按面板文案的规矩来：策略是显示名、不是内部键。"""
    at = _console(tmp_path)
    at.selectbox("backtest_strategy").set_value("ma_cross").run()
    filled = _guide().CONSOLE_PARAMS_SET.split("{")[0]
    lines = [c.value for c in at.main.caption if filled in c.value]
    assert len(lines) == 1 and "双均线交叉" in lines[0] and "ma_cross" not in lines[0], lines


# ---------------------------------------------------------------- ② 点击与失败提示
def _boom(*a, **k):
    raise RuntimeError("「全市场扫描」正在运行，同时只允许一个任务")


def test_a_failed_start_is_reported_under_the_buttons_not_below_the_results(tmp_path, monkeypatch):
    """启动失败的那句话要出现在按钮附近。结果表摊着的时候它要是落在卡片最底下，
    人看到的就是"点了没反应"。"""
    _finished_scan(tmp_path)                       # 今天跑完 → 结果表默认摊着
    monkeypatch.setattr(process, "start", _boom)
    at = _console(tmp_path)
    at.button("start_market_scan").click().run()
    assert any("启动失败" in e.value for e in at.error), [e.value for e in at.error]
    kinds = _kinds(at)
    assert kinds.index("error") < kinds.index("dataframe"), "失败提示排在结果表后面"
    assert kinds.index("error") < kinds.index("expander"), "失败提示排在折叠区后面"


def test_a_click_is_acted_on_before_the_rest_of_the_card_renders(tmp_path, monkeypatch):
    """点击必须在"参数一到手"时就处理。改版初稿是记下点击、等整张卡片渲染完再起任务——
    中间任何一处抛错（读日志、解析、渲染结果），这次点击就无声无息地丢了，
    而这张卡片再也没法从页面上重新起任务。"""
    started: list[tuple] = []
    monkeypatch.setattr(process, "start", lambda *a, **k: started.append(a))
    _fake_run(tmp_path, "backtest", "success", exit_code=0, log=BACKTEST_LOG)
    at = _console(tmp_path)

    def unreadable(state):
        raise RuntimeError("日志读不出来")

    monkeypatch.setattr(process, "read_log", unreadable)   # 卡片下半截从此渲染不出来
    at.button("start_backtest").click().run()
    assert [a[0] for a in started] == ["backtest"], "卡片下半截出错，把这次点击也吞掉了"


def test_a_result_file_of_the_wrong_shape_warns_instead_of_crashing(tmp_path):
    """结果文件被改成了别的形状（不是指标字典）：提示一句，不许把卡片连同后面的整页打没。"""
    for name in ("ma_cross_20260826_112606", "donchian_20260826_112606"):
        run = tmp_path / "output" / name
        run.mkdir(parents=True)
        (run / "metrics.json").write_text("[1, 2, 3]", encoding="utf-8")
    _fake_run(tmp_path, "backtest", "success", exit_code=0, log=BACKTEST_LOG)
    at = _console(tmp_path)
    assert not at.exception, at.exception
    assert at.warning, "结果读不懂时应有 st.warning"
    assert at.button("start_backtest"), "卡片被打没了"


def test_a_state_file_with_a_broken_argv_does_not_take_the_page_down(tmp_path):
    """状态文件是手工改得动的 JSON。argv 被改成 null：重跑会被拒（那句话照样写出来），
    但卡片与它后面的两张都得照常渲染。"""
    _fake_run(tmp_path, "market_scan", "success", exit_code=0, log=SCAN_LOG)
    path = tmp_path / "output" / "runs" / "market_scan.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["argv"] = None
    path.write_text(json.dumps(payload), encoding="utf-8")
    at = _console(tmp_path)
    assert not at.exception, at.exception
    assert any("改坏" in c.value for c in at.main.caption), [c.value for c in at.main.caption]
    for name in CONSOLE_JOBS:
        assert at.button(f"start_{name}"), f"{name} 的卡片没渲染出来"


# ---------------------------------------------------------------- ③ 测试空洞
def test_rerun_is_disabled_on_every_card_while_any_job_runs(tmp_path):
    """互斥对「重跑」同样成立，而且是对**每一张**卡片：回测跑过、平时可以重跑，
    但扫描正在跑的时候它也得是灰的。（runner 层还有一道闸，这里钉的是按钮本身。）"""
    _fake_run(tmp_path, "backtest", "success", exit_code=0, log=BACKTEST_LOG)
    at = _console(tmp_path)
    assert at.button("rerun_backtest").disabled is False, "前提：空闲时回测可以重跑"
    _fake_run(tmp_path, "market_scan", "running", log=SCAN_LOG.split("已保存")[0])
    at = _console(tmp_path)
    assert at.button("rerun_backtest").disabled is True, "别的任务在跑，回测的重跑却还能点"


@pytest.mark.parametrize("job", ["daily_signal", "backtest"])
def test_a_running_job_without_a_progress_bar_still_has_its_stop_button(tmp_path, job):
    """信号跟踪与回测没有可数的总量，运行中是转圈而不是进度条——停止按钮不能跟着进度条一起没了。"""
    _fake_run(tmp_path, job, "running", log="login success!\n")
    at = _console(tmp_path)
    assert not at.exception, at.exception
    assert at.get("progress") == [], "前提：这两个任务没有进度条"
    assert at.button(f"stop_{job}").disabled is False


@pytest.mark.parametrize("status,code", [("failed", 1), ("stopped", -15), ("success", 0)])
def test_no_stop_button_once_the_run_is_over(tmp_path, status, code):
    """任务已经结束了，就没有什么可停的：停止按钮只属于正在跑的那一刻。"""
    _fake_run(tmp_path, "market_scan", status, exit_code=code,
              log="\n".join(SCAN_LOG.splitlines()[:20]))
    at = _console(tmp_path)
    assert not at.exception, at.exception
    assert "stop_market_scan" not in [b.key for b in at.button]


def test_a_stopped_scan_says_that_nothing_was_saved(tmp_path):
    """停在半路时细节行还写着「信号 58 条」，而那 58 条一条都没保存。那句说明得真的出现在卡片上
    ——文案本身由 tests/test_runner_view.py 覆盖，这里钉的是接线。"""
    _fake_run(tmp_path, "market_scan", "stopped", exit_code=-15,
              log="\n".join(SCAN_LOG.splitlines()[:20]))
    at = _console(tmp_path)
    notes = [c.value for c in at.main.caption if "没有保存任何结果" in c.value]
    assert len(notes) == 1 and "第 1 只" in notes[0], notes


def test_a_failed_backtest_points_at_the_report_page_not_at_scanning(tmp_path):
    """那句说明按任务各说各的：回测没跑成，不该叫人去「信号」页、更不该说"从第 1 只开始"。"""
    _fake_run(tmp_path, "backtest", "failed", exit_code=3,
              log="login success!\nTraceback (most recent call last):\nValueError: boom\n")
    at = _console(tmp_path)
    notes = [c.value for c in at.main.caption if "没有保存任何结果" in c.value]
    assert len(notes) == 1, notes
    assert "回测报告" in notes[0] and "第 1 只" not in notes[0] and "「信号」页" not in notes[0], notes[0]


# ---------------------------------------------------------------- 状态与引导说得准
def test_the_first_time_callout_survives_a_run_of_the_home_page_job(tmp_path):
    """首页那个收盘数据任务跑过，不等于控制台上的任务跑过：新手在首页点过「开始准备」之后
    到了这一页，照样需要那条"先点试跑"的引导。"""
    _fake_run(tmp_path, "market_breadth", "success", exit_code=0, log="")
    at = _console(tmp_path)
    assert not at.exception, at.exception
    assert len(_callouts(at)) == 1, "只跑过首页的任务，控制台的首次引导就不见了"


def _backtest_meta(at: AppTest) -> str:
    return _card_meta(at, "backtest")


def test_the_backtest_card_reports_results_on_disk_even_if_the_panel_never_ran_it(tmp_path):
    """「最新结果」按**结果**说话，不按"面板起没起过它"说话：命令行跑出来的回测也算数。"""
    run = tmp_path / "output" / "ma_cross_20260826_112606"
    run.mkdir(parents=True)
    for name in ("metrics.json", "report.html", "trades.csv"):
        (run / name).write_text("{}", encoding="utf-8")
    at = _console(tmp_path)
    assert not at.exception, at.exception
    meta = _backtest_meta(at)
    assert "最近一次" in meta and "双均线交叉" in meta and "共 1 次" in meta, meta
    assert steps.UNKNOWN not in meta, f"磁盘上明明有回测结果: {meta}"


def test_the_backtest_card_keeps_its_latest_result_line_after_the_panel_ran_it(tmp_path):
    """面板跑过回测之后那一行不该消失（改版初稿里它只在"没跑过"时出现）。"""
    for name in ("ma_cross_20260826_112606", "donchian_20260826_112606"):
        run = tmp_path / "output" / name
        run.mkdir(parents=True)
        for f in ("metrics.json", "report.html", "trades.csv"):
            (run / f).write_text("{}", encoding="utf-8")
    _fake_run(tmp_path, "backtest", "success", exit_code=0, log=BACKTEST_LOG)
    at = _console(tmp_path)
    assert "共 2 次" in _backtest_meta(at), _backtest_meta(at)


def test_a_corrupt_scan_meta_is_not_reported_as_never_scanned(tmp_path):
    """扫描的 meta 坏了：如实警告，但别顺手再写一句"还没有结果"——结果文件明明在那儿。"""
    scan = tmp_path / "output" / "scan"
    scan.mkdir(parents=True)
    (scan / "2026-09-01.csv").write_text("date,symbol\n", encoding="utf-8")
    (scan / "2026-09-01.meta.json").write_text("{ broken json", encoding="utf-8")
    at = _console(tmp_path)
    assert not at.exception, at.exception
    # 读不出最新结果时那一行**整个不出**（不留一个后面什么都没有的「最新结果」标签）
    assert _card_meta(at, "market_scan") == "", _card_meta(at, "market_scan")
    assert steps.UNKNOWN not in "".join(_card_html(at, "market_scan"))
    assert any("损坏" in w.value for w in at.main.warning)
    # 另外两张卡片不受连累：它们的「最新结果」照常
    assert _card_meta(at, "daily_signal") and _card_meta(at, "backtest")
