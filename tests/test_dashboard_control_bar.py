# tests/test_dashboard_control_bar.py — v0.2.0 §4.2/§4.3 既有三页顶部的任务条（2026-11-16 改版，
# 原"内嵌控制条"）、侧边栏安全提示，以及 README「面板任务控制台」一节（离线，AppTest + 文本断言）
#
# 与控制台页的测试同一口径：**绝不真启动任务**。要"运行中"就伪造状态文件（pid 用
# 测试进程自己的，探活自然为真），要"点了开始"就 monkeypatch process.start 记调用。
import ast
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import streamlit as st
from streamlit.runtime.scriptrunner_utils.script_run_context import ScriptRunContext
from streamlit.testing.v1 import AppTest

from quant.runner import jobs, process, steps, view
from quant.signal import scan_meta
from tests.conftest import (app_module, copy_app, element_path, goto_page, path_to_element,
                            sits_on_the_right)

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "app" / "dashboard.py"
README = (ROOT / "README.md").read_text(encoding="utf-8")
SOURCE = DASHBOARD.read_text(encoding="utf-8")
# 真实扫描日志的前 20 行，截到 [1800/3010]（与控制台页的测试同一份夹具）
SCAN_LOG_HALFWAY = "\n".join((ROOT / "tests" / "fixtures" / "market_scan_sample.log")
                             .read_text(encoding="utf-8").splitlines()[:20])

# 哪一页的任务条上该出现哪些任务：K 线页与回测报告页读的都是回测产物，
# 信号页同屏展示固定池信号与全市场扫描两块产物，所以它有两条。
PAGE_JOBS = {
    "回测报告": ("backtest",),
    "个股K线": ("backtest",),
    "信号": ("daily_signal", "market_scan"),
}
READONLY_PAGES = list(PAGE_JOBS)
ALL_PAGES = [*READONLY_PAGES, "任务控制台"]


def _page(tmp_path: Path, page: str) -> AppTest:
    """app/ 整目录复制进 tmp_path 再交给 AppTest：ROOT/OUTPUT/RUNS_DIR 全落在
    tmp_path，与仓库真实 output/runs/ 完全隔离（否则测试会读到、甚至停掉真任务）。"""
    dashboard = copy_app(tmp_path)
    return goto_page(AppTest.from_file(str(dashboard), default_timeout=30).run(), page)


def _fake_run(root: Path, job: str, status: str, *, log: str = "", exit_code=None,
              argv: list[str] | None = None) -> None:
    """伪造一次运行的状态文件 + 日志。running 时 pid 必须是活着的进程，
    否则 read_state 的僵尸清理会立刻改判 failed——用测试进程自己的 pid 最安全
    （_signal_group 的两道拦截保证连误发信号都不可能）。"""
    runs = root / "output" / "runs"
    (runs / "logs").mkdir(parents=True, exist_ok=True)
    log_path = runs / "logs" / f"{job}_20260825_100000_000.log"
    log_path.write_text(log, encoding="utf-8")
    started = datetime.now() - timedelta(minutes=3)
    payload = {
        "script": job, "run_id": f"{job}_20260825_100000_000", "pid": os.getpid(),
        "argv": argv or [sys.executable, "-u", jobs.JOBS[job].script],
        "log_path": str(log_path), "started_at": started.isoformat(timespec="seconds"),
        "status": status, "exit_code": exit_code,
        "finished_at": None if status == "running"
        else datetime.now().isoformat(timespec="seconds"),
    }
    (runs / f"{job}.json").write_text(json.dumps(payload, ensure_ascii=False),
                                      encoding="utf-8")


def _bar_keys(at: AppTest) -> list[str]:
    return [b.key for b in at.button if b.key.startswith("bar_")]


def _walk(node):
    """AppTest 元素树递归展开（顶层只给容器，任务条里的控件要自己走下去）。"""
    for child in getattr(node, "children", {}).values():
        yield child
        yield from _walk(child)


def _main_links(at: AppTest) -> list[str]:
    """页面正文里 page_link 上的字。页头那个「帮助中心」不算——它每页都有。"""
    return [el.proto.label for el in at.main.get("page_link") if "帮助中心" not in el.proto.label]


def _guide():
    return app_module("guide")


def _signals(root: Path, day: str = "2026-08-24") -> None:
    sig = root / "output" / "signals"
    sig.mkdir(parents=True, exist_ok=True)
    (sig / f"{day}.csv").write_text(
        "symbol,date,strategy,action,close\n000333," + day + ",ma_cross,BUY,10.0\n",
        encoding="utf-8")


def _backtest_run(root: Path, name: str = "ma_cross_20260826_112606") -> None:
    run = root / "output" / name
    run.mkdir(parents=True, exist_ok=True)
    (run / "metrics.json").write_text('{"n_trades": 243}', encoding="utf-8")
    (run / "report.html").write_text("<html></html>", encoding="utf-8")
    (run / "trades.csv").write_text("symbol,action\n", encoding="utf-8")


def _htmls(at: AppTest) -> list[str]:
    """页面上我们自己包的 .qd-* HTML（st.html 在 AppTest 里没有 .value）；
    theme.inject() 那段 <style> 也走 st.html，滤掉免得断言蒙对。"""
    return [b for e in at.get("html")
            if not (b := e.proto.body).lstrip().startswith("<style>")]


# ------------------------------------------------------------------ §4.3 侧边栏
def _sidebar_text(at: AppTest) -> str:
    return " ".join(e.value for e in [*at.sidebar.caption, *at.sidebar.warning,
                                      *at.sidebar.markdown, *at.sidebar.info])


@pytest.fixture
def exposed_address():
    """把面板的监听地址临时改成 0.0.0.0（streamlit 的全局配置，AppTest 的脚本线程
    读的就是它），用完恢复成本仓库 config.toml 里的 127.0.0.1。"""
    from streamlit import config as st_config
    before = st.get_option("server.address")
    st_config.set_option("server.address", "0.0.0.0")
    try:
        yield
    finally:
        st_config.set_option("server.address", before)


def test_sidebar_no_longer_claims_the_panel_is_read_only(tmp_path):
    """M2 之后面板已经能在本机起进程，侧边栏还写着"本面板纯只读；回测与信号请用命令行
    运行"——这是 HEAD 上一句关于**可执行能力**的假话（设计 §7 的 M3 第一件事）。
    2026-09-05 起侧栏只剩免责声明，那句关于"能不能执行"的话干脆不再出现。"""
    at = _page(tmp_path, "回测报告")
    text = " ".join(c.value for c in at.sidebar.caption)
    assert "只读" not in text, f"侧边栏仍自称只读: {text}"


def test_sidebar_has_no_security_warning_when_bound_to_loopback(tmp_path):
    """2026-09-05：本仓库的 .streamlit/config.toml 已把默认绑到 127.0.0.1（从项目
    根目录启动时生效，pytest 也在根目录跑）。这时天天亮一条"请绑回环地址"的警告
    等于没有警告——所以监听本机时侧栏**没有**安全警告。"""
    assert st.get_option("server.address") == "127.0.0.1", \
        "测试得在项目根目录跑，才吃得到 .streamlit/config.toml"
    at = _page(tmp_path, "回测报告")
    assert at.sidebar.warning == [], [w.value for w in at.sidebar.warning]


def test_sidebar_warns_when_the_panel_listens_on_all_interfaces(tmp_path, exposed_address):
    """§4.3 + §5.3：面板能执行本机命令，暴露到网络等同于远程命令执行漏洞。
    监听地址不是本机时，侧栏必须警告，并给出**可执行**的修法（那个开关）。"""
    at = _page(tmp_path, "回测报告")
    text = _sidebar_text(at)
    assert "--server.address 127.0.0.1" in text and "0.0.0.0" in text, \
        f"缺少暴露风险提示: {text}"
    assert "局域网" in text, text


def test_sidebar_warning_shows_on_every_page_when_exposed(tmp_path, exposed_address):
    """提示写在侧边栏而不是某一页里：每一页都得看得见（用户可能只待在 K 线页）。"""
    for page in ALL_PAGES:
        at = _page(tmp_path, page)
        assert "局域网" in _sidebar_text(at), f"{page} 页的侧边栏没有安全提示"


@pytest.mark.parametrize("address, loopback", [
    ("127.0.0.1", True), ("localhost", True), ("::1", True), ("LOCALHOST", True),
    ("0.0.0.0", False), ("192.168.1.8", False), ("", False), (None, False),
])
def test_is_loopback_treats_the_factory_default_as_exposed(address, loopback):
    """空串 / None 是 streamlit 的出厂默认——监听所有网卡，必须归到"不是本机"。"""
    from tests.conftest import app_module
    assert app_module("ui").is_loopback(address) is loopback


def test_page_title_is_not_stale():
    """page_title 停在 v0.1.1 也是同一句假话的一部分（设计 §7）。

    自 v0.2.2 起它不再是写死的字面量，而是 theme.PAGE_TITLE（与侧栏品牌
    TAPE 同一个来源，版本号从此不进标签页，也就没有"过期"这回事了）。
    标签页名的具体内容由 tests/test_dashboard_nav.py 对账。"""
    tree = ast.parse(SOURCE)
    titles = [ast.unparse(kw.value) for node in ast.walk(tree)
              if isinstance(node, ast.Call)
              and getattr(node.func, "attr", "") == "set_page_config"
              for kw in node.keywords if kw.arg == "page_title"]
    assert titles == ["theme.PAGE_TITLE"], \
        f"page_title 应取自 theme（别再写死版本号）: {titles}"


# ------------------------------------------------------------------ §4.2 任务条
#
# 2026-11-16 改版：一个带边框的容器里一个任务一行——任务名、状态、一句「最新结果」，右端一个
# 与控制台同名的按钮。「停止」与进度只在运行中出现；去控制台的链接只在有任务在跑、或上次
# 没跑成时出现；说明挂在按钮的悬停提示上。原先那两行常驻的灰字、常驻的链接与分隔线都没了。

@pytest.mark.parametrize("page", READONLY_PAGES)
def test_each_readonly_page_has_a_status_bar_for_its_own_jobs(page, tmp_path):
    """每页顶部一条任务条，只给这一页看得见结果的任务（回测页不该出现扫描按钮，
    否则用户在回测页误点一下就是 0.5-2 小时的全市场扫描）。
    没有任务在跑时每个任务只有一个「开始」——「停止」不常驻（九成九的时候它点不了）。"""
    at = _page(tmp_path, page)
    assert not at.exception, at.exception
    assert sorted(_bar_keys(at)) == sorted(f"bar_start_{job}" for job in PAGE_JOBS[page]), \
        _bar_keys(at)


@pytest.mark.parametrize("page", READONLY_PAGES)
def test_control_bar_renders_even_with_no_results_yet(page, tmp_path):
    """空 output/ 是最需要"开始"的时候：三页在无产物时都会 st.info 后**早退**，
    任务条排在早退之后就永远看不见——用户只能回去敲命令行。"""
    at = _page(tmp_path, page)
    assert not at.exception, at.exception
    assert at.info, "无产物时原有的提示不该消失"
    for job in PAGE_JOBS[page]:
        assert at.button(f"bar_start_{job}").disabled is False, job


@pytest.mark.parametrize("page", READONLY_PAGES)
def test_the_empty_state_names_the_button_that_is_really_on_the_bar(page, tmp_path):
    """空态那句话叫人点的按钮，得是这一页任务条上真有的那个字——
    页面上写着「开始回测」、提示里却叫人找「开始」，新手就找不到。"""
    at = _page(tmp_path, page)
    hints = " ".join(i.value for i in at.info)
    for job in PAGE_JOBS[page]:
        label = at.button(f"bar_start_{job}").label
        assert f"「{label}」" in hints, f"空态没点名「{label}」: {hints}"


@pytest.mark.parametrize("page", READONLY_PAGES)
def test_control_bar_shows_the_current_status(page, tmp_path):
    """"当前状态"必须是真状态：失败要带退出码（排查第一手线索）。"""
    job = PAGE_JOBS[page][0]
    _fake_run(tmp_path, job, "failed", exit_code=3, log="Traceback\n")
    at = _page(tmp_path, page)
    assert not at.exception, at.exception
    # 状态徽标自 v0.2.1 起是有底色的 pill（st.html），不再是 markdown 里的 emoji
    text = " ".join(_htmls(at))
    assert jobs.JOBS[job].label in text and "退出码 3" in text, text
    assert "qd-pill-failed" in text, text


def test_the_bar_says_which_day_the_results_on_this_page_are_from(tmp_path):
    """真机上看到的毛病：「信号」页顶上写着「成功」，最新信号却停在两个月前。
    「成功」说的是上一次**运行**，人要知道的是这页的**数据**是哪天的——
    所以每行都带一句「最新结果」，而且与控制台卡片上那句是同一句话（steps.*_status）。"""
    _signals(tmp_path, "2026-08-24")
    _fake_run(tmp_path, "daily_signal", "success", exit_code=0, log="已保存: x.csv\n")
    at = _page(tmp_path, "信号")
    assert not at.exception, at.exception
    strips = [h for h in _htmls(at) if 'class="qd-strip"' in h]
    assert len(strips) == 2, strips
    signal = next(h for h in strips if "信号跟踪" in h)
    said = steps.signal_status(tmp_path / "output" / "signals", None).text
    assert "2026-08-24" in said and said in signal, f"任务条没说最新信号是哪天的: {signal}"
    assert "不是今天的" in signal, signal
    scan = next(h for h in strips if "全市场扫描" in h)
    assert steps.UNKNOWN in scan, f"没扫过就该说没扫过: {scan}"


def test_the_backtest_bar_counts_the_results_on_disk(tmp_path):
    """回测那一行按**磁盘上的结果**说话（同控制台卡片）：命令行跑出来的结果也算。"""
    _backtest_run(tmp_path)
    at = _page(tmp_path, "回测报告")
    strip = next(h for h in _htmls(at) if 'class="qd-strip"' in h)
    assert "共 1 次" in strip and "双均线交叉" in strip, strip


def test_the_bar_sits_in_one_bordered_container_without_a_divider(tmp_path):
    """任务条是一个带边框的原生容器（与控制台的任务卡片一家），按钮在框里；
    框自己把它与下面的数据隔开了，原先那道分隔线不再画。"""
    at = _page(tmp_path, "信号")
    assert not at.exception, at.exception
    cards = [n for n in _walk(at._tree)
             if getattr(n, "type", "") == "flex_container" and n.proto.flex_container.border]
    assert len(cards) == 1, f"任务条应恰好一个带框容器，实际 {len(cards)} 个"
    inside = {getattr(e, "key", None) for e in _walk(cards[0])}
    assert {"bar_start_daily_signal", "bar_start_market_scan"} <= inside, inside
    assert not at.main.get("divider"), "任务条下面不该再有分隔线"


def test_an_idle_bar_carries_no_standing_hint_and_no_standing_link(tmp_path):
    """没有任务在跑、上一次也好好跑完了：条上不挂提示、不挂链接。
    改之前「这里按默认参数运行……」与「去『任务控制台』看进度与日志」天天亮在三页顶上。
    按默认参数跑、想改去哪——挂在按钮自己的悬停提示上，要点的时候才看得到。"""
    _fake_run(tmp_path, "daily_signal", "success", exit_code=0, log="已保存: x.csv\n")
    at = _page(tmp_path, "信号")
    assert not at.exception, at.exception
    assert _main_links(at) == [], _main_links(at)
    # 只认页面正文里的提示：侧边栏也提"任务控制台"，用 at.caption 会被它蒙过去
    assert not any("任务控制台" in c.value for c in at.main.caption), \
        [c.value for c in at.main.caption]
    for job in PAGE_JOBS["信号"]:
        tip = at.button(f"bar_start_{job}").proto.help
        assert tip == _guide().BAR_START_HELP[job] and "任务控制台" in tip, tip


def test_the_scan_button_warns_it_is_the_full_scan_before_you_click(tmp_path):
    """「信号」页上它与十几秒的信号跟踪并排、长得一样，而一点就是全量扫描。
    悬停提示得把「全量」、时长和「想先试跑去哪」说在前面。"""
    guide = _guide()
    tip = guide.BAR_START_HELP["market_scan"]
    assert "全量" in tip and guide.FACTS["scan_minutes"] in tip and guide.FACTS["scan_cold"] in tip
    assert f"「{guide.CONSOLE_TRIAL_LABEL}」" in tip, tip


def test_control_bar_points_to_the_console_while_something_runs(tmp_path):
    """任务条不放日志与参数（那是控制台页的事）——有任务在跑时必须明确指路。"""
    _fake_run(tmp_path, "daily_signal", "running", log="login success!\n")
    at = _page(tmp_path, "信号")
    assert not at.exception, at.exception
    assert any("任务控制台" in label for label in _main_links(at)), _main_links(at)


def test_control_bar_offers_the_log_after_a_failed_run(tmp_path):
    """上次没跑成：为什么，写在控制台那张卡片摊开的日志里——这时才给那条链接。"""
    _fake_run(tmp_path, "backtest", "failed", exit_code=1, log="Traceback\n")
    at = _page(tmp_path, "回测报告")
    assert not at.exception, at.exception
    assert any("上次的日志" in label for label in _main_links(at)), _main_links(at)


def test_control_bar_start_passes_only_whitelisted_default_argv(tmp_path, monkeypatch):
    """任务条没有参数控件，点开始 = 默认参数。argv 仍必须由 build_argv 现造
    （列表 + shell=False），不能在这里另起一套拼命令的路子。"""
    started: list[tuple] = []
    monkeypatch.setattr(process, "start", lambda *a, **k: started.append(a))
    at = _page(tmp_path, "信号")
    at.button("bar_start_daily_signal").click().run()
    assert len(started) == 1, started
    job_name, argv, runs_dir = started[0]
    assert job_name == "daily_signal"
    assert argv == [sys.executable, "-u", jobs.JOBS["daily_signal"].script]
    assert Path(runs_dir) == tmp_path / "output" / "runs"


def test_the_scan_button_on_the_bar_starts_the_full_scan_and_nothing_else(tmp_path, monkeypatch):
    """「开始扫描」= 全量：命令行里不许悄悄带上只数或日期。按钮的悬停提示写的就是全量，
    点下去跑的得是同一件事（上一条只点了信号跟踪，这一个按钮各验各的）。"""
    started: list[tuple] = []
    monkeypatch.setattr(process, "start", lambda *a, **k: started.append(a))
    at = _page(tmp_path, "信号")
    at.button("bar_start_market_scan").click().run()
    assert [(a[0], a[1]) for a in started] == [
        ("market_scan", [sys.executable, "-u", jobs.JOBS["market_scan"].script])], started


def test_control_bar_start_requests_a_full_rerun(tmp_path, monkeypatch):
    """点开始后要整页重跑，否则同屏另一行的"开始"不会被互斥禁用、
    状态徽标也还是"空闲"——用户看到的就是"点了没反应"，于是再点一次。"""
    import streamlit as st

    reruns: list[int] = []
    monkeypatch.setattr(process, "start", lambda *a, **k: None)
    at = _page(tmp_path, "信号")
    monkeypatch.setattr(st, "rerun", lambda *a, **k: reruns.append(1))
    at.button("bar_start_daily_signal").click().run()
    assert reruns, "点了开始却没请求整页重跑"


def test_control_bar_start_failure_is_shown_not_raised(tmp_path, monkeypatch):
    """抢跑（另一个标签页刚点了开始）时 start() 抛 RuntimeError：
    漏接就是整页 traceback，而不是一句"启动失败"。"""
    def boom(*a, **k):
        raise RuntimeError("「全市场扫描」正在运行，同时只允许一个任务（baostock 单会话）")

    monkeypatch.setattr(process, "start", boom)
    at = _page(tmp_path, "回测报告")
    at.button("bar_start_backtest").click().run()
    assert not at.exception, f"启动失败不该崩页: {at.exception}"
    errors = [e for e in at.error if "启动失败" in e.value]
    assert len(errors) == 1, [e.value for e in at.error]
    # 那句话要占整行：按钮是在右边那一窄栏里画的，起任务必须在那一栏**外面**做——
    # 否则报错挤在一百多像素宽的栏里折成一长条（独立审查指出这一条一直没人钉）。
    assert not [n for n in path_to_element(at, errors[0]) if getattr(n, "type", "") == "column"], \
        "启动失败的提示落在了按钮那一栏里"


def test_control_bar_start_disabled_by_the_global_mutex(tmp_path):
    """baostock 单会话：扫描在跑时，回测页的"开始"也必须禁用并点名是谁在跑。
    互斥是全局的，不是每页各管一摊。"""
    _fake_run(tmp_path, "market_scan", "running", log="[1800/3010] 信号 15 条\n")
    at = _page(tmp_path, "回测报告")
    assert not at.exception, at.exception
    assert at.button("bar_start_backtest").disabled is True, "跨页互斥失效"
    assert any("全市场扫描" in c.value and "正在运行" in c.value for c in at.main.caption), \
        [c.value for c in at.main.caption]
    assert "bar_stop_backtest" not in _bar_keys(at), "没在跑的任务不该有停止按钮"


def test_the_mutex_notice_is_said_once_for_two_rows(tmp_path):
    """互斥是全局的：「信号」页的两行拿到的是同一句提示，去重后只说一次。"""
    _fake_run(tmp_path, "backtest", "running", log="")
    at = _page(tmp_path, "信号")
    assert not at.exception, at.exception
    said = [c.value for c in at.main.caption if "正在运行" in c.value]
    assert len(said) == 1 and "回测" in said[0], said
    assert all(at.button(f"bar_start_{job}").disabled for job in PAGE_JOBS["信号"])


def test_control_bar_stop_only_exists_for_the_running_job(tmp_path):
    """「停止」只在运行中出现，而且只给正在跑的那一个；它在进度那一行，
    不占「开始」的位置——任务恰好跑完的那一刻，光标底下的按钮不许从停止换成开始。"""
    _fake_run(tmp_path, "daily_signal", "running", log="login success!\n")
    at = _page(tmp_path, "信号")
    assert not at.exception, at.exception
    assert "bar_stop_daily_signal" in _bar_keys(at)
    assert "bar_stop_market_scan" not in _bar_keys(at), "没在跑的任务不该能停"
    assert at.button("bar_stop_daily_signal").disabled is False
    # 正在跑的那个任务，自己的「开始」还在原位、只是点不了
    assert at.button("bar_start_daily_signal").disabled is True


@pytest.mark.parametrize("finished", ["success", "failed", "stopped"])
def test_a_job_that_has_run_before_gets_no_stop_button_once_it_is_over(finished, tmp_path):
    """「停止」与进度只属于**正在跑**的那一个。跑过、但已经结束的任务（成功 / 失败 / 被停掉）
    一样没有——上面那条里没在跑的任务连状态文件都没有，区分不了"没跑过"和"跑完了"。"""
    _fake_run(tmp_path, "daily_signal", finished, exit_code=0 if finished == "success" else 1,
              log="login success!\n")
    at = _page(tmp_path, "信号")
    assert not at.exception, at.exception
    assert not [k for k in _bar_keys(at) if k.startswith("bar_stop_")], _bar_keys(at)
    assert not at.main.get("progress") and not at.status, "结束了的任务不该还摊着进度"

    _fake_run(tmp_path, "market_scan", "running", log=SCAN_LOG_HALFWAY)
    at = at.run()
    assert [k for k in _bar_keys(at) if k.startswith("bar_stop_")] == ["bar_stop_market_scan"]
    assert len(at.main.get("progress")) == 1, "只有正在跑的那一个有进度"


def test_a_running_job_shows_its_progress_on_the_bar(tmp_path):
    """点了「开始」之后这一页得自己会动：进度就摊在任务条上（与控制台卡片同一套文案）。
    数得出进度时是进度条，数不出来时转圈；已用多久、预计剩余这些会变的数写在下面一行。
    「关掉页面也不会中断」不再常驻在那一行后面（2026-11-16 第二轮）——它是「停止」按钮的悬停说明。"""
    _fake_run(tmp_path, "market_scan", "running", log=SCAN_LOG_HALFWAY)
    at = _page(tmp_path, "信号")
    assert not at.exception, at.exception
    bars = at.main.get("progress")
    assert len(bars) == 1 and "1800/3010" in bars[0].proto.text, [b.proto.text for b in bars]
    guide = _guide()
    captions = [c.value for c in at.main.caption]
    assert any("已用" in c and "信号 58 条" in c for c in captions), captions
    assert not any("关掉浏览器" in c for c in captions), captions
    tip = at.button("bar_stop_market_scan").proto.help
    assert tip == guide.stop_help("market_scan") and tip.startswith(guide.CONSOLE_BACKGROUND_NOTE)
    assert "丢掉这趟已扫出的信号" in tip, "停掉一趟扫描会丢什么，得在停止按钮自己身上说"
    # 谁在跑就摊在这条上（运行中的状态 + 进度），同一条上的另一行为什么点不了不言自明——
    # 不再另说一句「……正在运行」。别的页起的任务才需要点名（见互斥那两条）。
    assert not any("正在运行" in c.value for c in at.main.caption), \
        [c.value for c in at.main.caption]


@pytest.mark.parametrize("running", ["daily_signal", "market_scan"])
def test_no_start_button_can_slide_under_the_stop_button(tmp_path, running):
    """任务恰好跑完的那一刻，光标底下的按钮不许从「停止」换成某个「开始」。
    信号跟踪十几秒就跑完，而它下面那一行的按钮是半小时到两小时的全市场扫描。所以：

    - 进度与「停止」那一行排在**所有任务行之后**——它出现、消失，哪个「开始」都不挪位置；
      它消失时往上补位的只有任务条**下面**的内容，那里没有开始按钮；
    - 「停止」自己占一行，不与任何「开始」同一行。

    2026-11-16 第二轮起「停止」在那一行的右端，与页面上别的操作按钮一样靠右（用户要求
    按钮位置统一）。第一轮里它在最左，是怕与上面几行的「开始」上下对齐；真正防住
    "按钮在光标下换掉"的是上面第一条，那条没变。"""
    _fake_run(tmp_path, running, "running", log=SCAN_LOG_HALFWAY)
    at = _page(tmp_path, "信号")
    assert not at.exception, at.exception
    keys = _bar_keys(at)
    assert keys == ["bar_start_daily_signal", "bar_start_market_scan", f"bar_stop_{running}"], \
        f"「停止」那一行必须排在所有任务行之后: {keys}"
    # 进度那一行是一个靠右的横向容器（进度条、停止）；任务行是一行分栏，按钮在最后一栏
    stop_row = element_path(at, f"bar_stop_{running}")[-1]
    kinds = [getattr(e, "type", "") for e in stop_row.children.values()]
    # 数得出进度时是进度条，数不出来（信号跟踪）时是转圈的那一块
    assert kinds in (["progress", "button"], ["status", "button"]), kinds
    for job in PAGE_JOBS["信号"]:
        assert not [n for n in element_path(at, f"bar_start_{job}") if n is stop_row], \
            "「停止」不许与「开始」同一行"
    # 按钮一律靠右
    for key in (f"bar_stop_{running}", *(f"bar_start_{job}" for job in PAGE_JOBS["信号"])):
        assert sits_on_the_right(at, key), f"{key} 不在右边"



def test_an_indeterminate_job_spins_instead_of_drawing_a_fake_bar(tmp_path):
    _fake_run(tmp_path, "daily_signal", "running", log="login success!\n")
    at = _page(tmp_path, "信号")
    assert not at.exception, at.exception
    assert not at.main.get("progress"), "数不出进度就不许画进度条"
    assert [s.state for s in at.status] == ["running"], [s.state for s in at.status]


def _auto_rerun_spy(monkeypatch) -> list[float]:
    """记下每个 fragment 发给前端的 run_every（AppTest 不会真的按它定时重跑，
    只能在发出去的 ForwardMsg 里看有没有挂定时——与首页那组测试同一个手法）。"""
    seen: list[float] = []
    real = ScriptRunContext.enqueue

    def spy(self, msg):
        if msg.WhichOneof("type") == "auto_rerun":
            seen.append(msg.auto_rerun.interval)
        return real(self, msg)
    monkeypatch.setattr(ScriptRunContext, "enqueue", spy)
    return seen


@pytest.mark.parametrize("page", READONLY_PAGES)
def test_the_bar_refreshes_itself_only_while_a_job_runs(page, tmp_path, monkeypatch):
    """点了「开始」之后这一页得自己会动：有任务在跑时任务条每 2 秒自己重画一次；
    全空闲时**不**定时——否则这三页会一直每 2 秒空转（与控制台卡片同一条规矩）。"""
    seen = _auto_rerun_spy(monkeypatch)
    at = _page(tmp_path, page)      # 先落到首页再切过来：首页自己有一个行情 fragment，不算这一页的
    seen.clear()
    at = at.run()
    assert not at.exception, at.exception
    assert seen == [], f"没有任务在跑，{page} 页却挂了定时重画: {seen}"

    _fake_run(tmp_path, "market_scan", "running", log=SCAN_LOG_HALFWAY)
    seen.clear()
    at = at.run()
    assert not at.exception, at.exception
    assert seen == [2.0], f"有任务在跑，{page} 页的任务条应每 2 秒重画一次: {seen}"


def test_the_bar_requests_a_full_rerun_when_the_running_job_finishes(tmp_path, monkeypatch):
    """任务在两次轮询之间跑完了：任务条必须请求**整页**重跑——下面的表才会换成新结果，
    页头那颗全局状态才会回到「空闲」，定时重画也才摘得掉。
    AppTest 不模拟 fragment 局部重跑，只能断言"重跑请求确实发出去了"这一机制本身
    （手法同控制台页那条：整页快照阶段说还在跑，任务条里再问时已经结束）。"""
    import streamlit as st

    reruns: list[int] = []
    _fake_run(tmp_path, "daily_signal", "success", exit_code=0, log="已保存: x.csv\n")
    at = _page(tmp_path, "信号")
    real_any_running = process.any_running
    real_read_state = process.read_state
    entered_bar = {"yes": False}

    def read_state_probe(*a, **kw):
        # 任务条渲染的第一件事就是 read_state（页头只问 any_running）：拿它当分界
        entered_bar["yes"] = True
        return real_read_state(*a, **kw)

    def any_running_that_just_finished(runs_dir):
        return real_any_running(runs_dir) if entered_bar["yes"] else "daily_signal"

    monkeypatch.setattr(process, "read_state", read_state_probe)
    monkeypatch.setattr(process, "any_running", any_running_that_just_finished)
    monkeypatch.setattr(st, "rerun", lambda *a, **k: reruns.append(1))
    at.run()
    assert reruns, "任务已经结束，任务条却没请求整页重跑——下面的表还是旧的，轮询也会一直空转"


def test_an_idle_bar_does_not_ask_for_reruns(tmp_path, monkeypatch):
    """反过来：什么都没变时不许请求重跑，否则整页原地打转。"""
    import streamlit as st

    reruns: list[int] = []
    _fake_run(tmp_path, "daily_signal", "success", exit_code=0, log="已保存: x.csv\n")
    at = _page(tmp_path, "信号")
    monkeypatch.setattr(st, "rerun", lambda *a, **k: reruns.append(1))
    at.run()
    assert reruns == []
    _fake_run(tmp_path, "market_scan", "running", log=SCAN_LOG_HALFWAY)
    at.run()
    assert reruns == [], "任务还在跑（状态没变），不该请求整页重跑"


def test_control_bar_stop_goes_through_runner_stop(tmp_path, monkeypatch):
    """停止必须走 process.stop（SIGTERM 进程组 → 10s → SIGKILL），
    不能在面板里另发信号；停完同样要整页重跑好解禁其余"开始"。"""
    import streamlit as st

    stopped: list = []
    reruns: list[int] = []
    monkeypatch.setattr(process, "stop", lambda state, runs_dir: stopped.append(state))
    _fake_run(tmp_path, "market_scan", "running", log="[1800/3010] 信号 15 条\n")
    at = _page(tmp_path, "信号")
    monkeypatch.setattr(st, "rerun", lambda *a, **k: reruns.append(1))
    at.button("bar_stop_market_scan").click().run()
    assert len(stopped) == 1 and stopped[0].script == "market_scan"
    assert reruns, "点了停止却没请求整页重跑"


def test_corrupt_state_file_offers_no_start_but_keeps_the_readonly_page(tmp_path):
    """状态文件损坏 = 互斥状态**不可知**：一个"开始"都不给（fail-safe，与控制台页同口径）。
    但既有只读内容不受影响——一个坏了的 runs/*.json 不该把回测报告一起藏起来。"""
    runs = tmp_path / "output" / "runs"
    runs.mkdir(parents=True)
    (runs / "backtest.json").write_text('{"script": "backtest", "pid":', encoding="utf-8")
    _backtest_run(tmp_path)
    at = _page(tmp_path, "回测报告")
    assert not at.exception, at.exception
    assert _bar_keys(at) == [], f"互斥状态未知却给了按钮: {_bar_keys(at)}"
    assert any("损坏" in e.value for e in at.error), [e.value for e in at.error]
    blob = " ".join(_htmls(at))
    assert 'class="qd-metric-value">243<' in blob, "只读内容被任务条的故障连坐了"
    # 页头那颗 pill 此时必须如实说"未知"：报"空闲"就是猜的，而这正是 fail-safe 要挡的
    assert view.UNKNOWN_TEXT in blob, blob


@pytest.mark.parametrize("page", ["信号", "任务控制台"])
@pytest.mark.parametrize("running", ["backtest", "daily_signal"])
def test_a_corrupt_state_file_next_to_a_running_job_does_not_spin_the_page(
        page, running, tmp_path, monkeypatch):
    """审查抓到的死循环：一个任务在跑，另一个任务的状态文件又恰好坏了。

    `process.any_running` 按文件名顺序读，读到第一个在跑的就返回，根本没碰后面那个坏文件——
    于是页面以为"有任务在跑"，挂上定时重画；任务条 / 卡片自己去读那个坏文件时报错、
    交回一个"不知道谁在跑"，而外层把它当成"跑完了"请求整页重跑：报错 → 重跑 → 报错……
    现在"不知道"就是不知道：报错、不给按钮（互斥状态不可知），**不**请求重跑，页面其余照常。"""
    _fake_run(tmp_path, running, "running", log="login success!\n")
    (tmp_path / "output" / "runs" / "market_scan.json").write_text(
        '{"script": "market_scan", "pid":', encoding="utf-8")
    at = AppTest.from_file(str(copy_app(tmp_path)), default_timeout=30).run()
    reruns: list[int] = []
    monkeypatch.setattr(st, "rerun", lambda *a, **k: reruns.append(1))
    at = goto_page(at, page)
    assert not at.exception, at.exception
    assert reruns == [], f"{page} 页在原地打转：一轮渲染里请求了 {len(reruns)} 次整页重跑"
    assert any("损坏" in e.value for e in at.error), [e.value for e in at.error]
    if page == "信号":
        assert _bar_keys(at) == [], f"互斥状态不可知却给了按钮: {_bar_keys(at)}"
        assert at.info, "任务条读不出状态，下面的只读内容照常渲染"


@pytest.mark.parametrize("page", ["回测报告", "个股K线"])
def test_the_bar_fails_safe_when_the_mutex_becomes_unreadable_mid_poll(page, tmp_path, monkeypatch):
    """轮询到一半才读不出来的那一种（复审指出这条分支没有测试）：整页那一轮以为回测在跑，
    轮到任务条自己再问"谁在跑"时任务刚好结束——没有谁在跑了，`any_running` 就得把所有状态
    文件读一遍，这才撞上那个坏文件。这时互斥状态不可知：报错、**一个开始按钮都不给**、
    也不请求整页重跑。把这里写成"读不出来就当没人在跑"，按钮就亮着等人点。"""
    import streamlit as st

    _fake_run(tmp_path, "backtest", "success", exit_code=0, log="已保存: x\n")
    _backtest_run(tmp_path)
    (tmp_path / "output" / "runs" / "market_scan.json").write_text(
        '{"script": "market_scan", "pid":', encoding="utf-8")
    at = AppTest.from_file(str(copy_app(tmp_path)), default_timeout=30).run()
    real_any_running, real_read_state = process.any_running, process.read_state
    entered_bar = {"yes": False}

    def read_state_probe(*a, **kw):
        entered_bar["yes"] = True          # 任务条渲染的第一件事（页头只问 any_running）
        return real_read_state(*a, **kw)

    def any_running_that_just_finished(runs_dir):
        return real_any_running(runs_dir) if entered_bar["yes"] else "backtest"

    reruns: list[int] = []
    monkeypatch.setattr(process, "read_state", read_state_probe)
    monkeypatch.setattr(process, "any_running", any_running_that_just_finished)
    monkeypatch.setattr(st, "rerun", lambda *a, **k: reruns.append(1))
    at = goto_page(at, page)
    assert not at.exception, at.exception
    assert any("损坏" in e.value for e in at.error), [e.value for e in at.error]
    assert _bar_keys(at) == [], f"互斥状态不可知却给了按钮: {_bar_keys(at)}"
    assert reruns == [], "读不出谁在跑不等于跑完了，不该请求整页重跑"


def test_a_corrupt_scan_record_does_not_take_the_bar_down(tmp_path):
    """「最新结果」那句要读扫描的范围记录，而那份文件损坏时数据层是响亮抛错。
    任务条必须接住：如实警告、那半句不写（不许补一句"还没有结果"），按钮照常给——
    重扫一遍正是修好它的办法。"""
    scan = tmp_path / "output" / "scan"
    scan.mkdir(parents=True)
    csv = scan / "2026-08-24.csv"
    csv.write_text("date,symbol,name,strategy,close,pct_chg,amount,amount_ratio_20d\n"
                   "2026-08-24,000020,深华发A,ma_cross,11.74,-5.09,2.9e8,4.22\n", encoding="utf-8")
    scan_meta.meta_path(csv).write_text("{不是 json", encoding="utf-8")
    at = _page(tmp_path, "信号")
    assert not at.exception, f"坏的范围记录不该崩页: {at.exception}"
    assert any("损坏" in w.value for w in at.warning), [w.value for w in at.warning]
    strip = next(h for h in _htmls(at) if 'class="qd-strip"' in h and "全市场扫描" in h)
    assert "qd-strip-status" not in strip and steps.UNKNOWN not in strip, strip
    assert at.button("bar_start_market_scan").disabled is False


@pytest.mark.parametrize("page", ALL_PAGES)
def test_every_page_renders_while_a_job_is_running(page, tmp_path):
    """四页 × 有任务在跑：任何一页都不得抛异常（M3 验收）。"""
    _fake_run(tmp_path, "market_scan", "running", log="[1800/3010] 信号 15 条\n")
    at = _page(tmp_path, page)
    assert not at.exception, f"页面 {page} 抛异常: {at.exception}"


def test_readonly_signal_content_survives_the_control_bar(tmp_path):
    """§4.2"既有的只读展示逻辑完全不动"：信号表与扫描区块照旧，前导零照旧不被吃掉。"""
    sig = tmp_path / "output" / "signals"
    sig.mkdir(parents=True)
    (sig / "2026-08-24.csv").write_text(
        "symbol,date,strategy,signal,close\n000333,2026-08-24,ma_cross,buy,10.0\n",
        encoding="utf-8")
    scan = tmp_path / "output" / "scan"
    scan.mkdir(parents=True)
    (scan / "2026-08-24.csv").write_text(
        "date,symbol,name,strategy,close,pct_chg,amount,amount_ratio_20d\n"
        "2026-08-24,000020,深华发A,ma_cross,11.74,-5.09,2.9e8,4.22\n", encoding="utf-8")
    _fake_run(tmp_path, "daily_signal", "success", exit_code=0, log="已保存: x.csv\n")
    at = _page(tmp_path, "信号")
    assert not at.exception, at.exception
    assert [df.value["symbol"].tolist() for df in at.dataframe] == [["000333"], ["000020"]]


def test_bar_buttons_carry_the_console_labels_and_material_icons(tmp_path):
    """按钮上的字与控制台同名（开始跟踪 / 开始扫描 / 开始回测）——两个都叫「开始」时，
    分不清哪个是十几秒、哪个是两小时。图标走 icon=，不把 ▶ ⏹ 拼在文字前面；
    是普通按钮而不是琥珀色的主按钮：这几页是来看结果的，主按钮留给控制台。"""
    _fake_run(tmp_path, "market_scan", "running", log="[1800/3010] 信号 15 条\n")
    at = _page(tmp_path, "信号")
    assert not at.exception, at.exception
    guide = _guide()
    for job in PAGE_JOBS["信号"]:
        b = at.button(f"bar_start_{job}")
        assert (b.label, b.proto.icon) == (guide.CONSOLE_START[job], ":material/play_arrow:"), \
            (b.label, b.proto.icon)
        assert b.proto.type == "secondary", b.proto.type
    stop = at.button("bar_stop_market_scan")
    assert (stop.label, stop.proto.icon) == (guide.CONSOLE_STOP, ":material/stop:")


# ------------------------------------------------------------------ README 一节
def _readme_section(title: str) -> str:
    """取 README 中某个标题（## 或 ###）到下一个同级/更高级标题之间的正文。"""
    lines = README.splitlines()
    starts = [i for i, ln in enumerate(lines) if ln.startswith("#") and title in ln]
    assert starts, f"README 缺少「{title}」一节"
    i = starts[0]
    level = len(lines[i]) - len(lines[i].lstrip("#"))
    for j in range(i + 1, len(lines)):
        head = lines[j]
        if head.startswith("#") and (len(head) - len(head.lstrip("#"))) <= level:
            return "\n".join(lines[i + 1:j])
    return "\n".join(lines[i + 1:])


CONSOLE_SECTION = "面板任务控制台"


def test_readme_has_a_console_section_covering_all_three_jobs():
    section = _readme_section(CONSOLE_SECTION)
    for label in ("全市场扫描", "信号跟踪", "回测"):
        assert label in section, f"README 控制台一节没写「{label}」"


def test_readme_explains_stop_is_a_gentle_sigterm():
    """停止语义写错会让用户以为点了停止就是 kill -9，从而不敢用；
    SIGTERM 是"让脚本走完当前标的、缓存原子落盘"的温和退出（设计 §5.2）。"""
    section = _readme_section(CONSOLE_SECTION)
    assert "SIGTERM" in section, section
    assert "SIGKILL" in section, "10 秒未退才升级 SIGKILL 这一段也要写明"


def test_readme_explains_the_detached_process_semantics():
    """任务是独立会话里的分离进程：关掉浏览器、甚至停掉 Streamlit 都不会中断它。
    不写清楚，用户会以为关页面就等于停任务，于是一次全量扫描在后台跑到天亮。"""
    section = _readme_section(CONSOLE_SECTION)
    assert "浏览器" in section and "Streamlit" in section, section


def test_readme_explains_the_global_mutex_and_progress():
    section = _readme_section(CONSOLE_SECTION)
    assert "baostock" in section and ("互斥" in section or "只允许一个" in section), section
    assert "进度" in section, "进度条/不确定态的含义要写"


def test_readme_says_the_cli_entries_are_kept_and_equivalent():
    """v0.1 的三个命令行入口全部保留、两种方式等价——用户的 nohup/cron 习惯不受影响。"""
    section = _readme_section(CONSOLE_SECTION)
    assert "命令行" in section and "等价" in section, section


def test_readme_security_warning_is_a_paragraph_of_its_own():
    """安全提示必须单独成段：塞进某个 40 行长的要点列表里等于没写。"""
    paras = [p.strip() for p in README.split("\n\n")]
    hits = [p for p in paras if "--server.address" in p and "0.0.0.0" in p]
    assert hits, "README 没有那句 --server.address 0.0.0.0 的安全提示"
    standalone = [p for p in hits if len(p) < 400 and p.count("\n") <= 3]
    assert standalone, f"安全提示没有单独成段: {hits}"


def test_readme_no_longer_calls_the_dashboard_read_only():
    """README 里"面板是纯只读的：它只读 output/ 与 data/cache/，不触发任何计算"
    在 M2 之后就是假话。"""
    assert "纯只读" not in README, "README 仍称面板纯只读"
    assert "不触发任何计算" not in README
