# tests/test_dashboard_control_bar.py — v0.2.0 §4.2/§4.3 既有三页的内嵌控制条、
# 侧边栏安全提示，以及 README「面板任务控制台」一节（离线，AppTest + 文本断言）
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
from streamlit.testing.v1 import AppTest

from quant.runner import jobs, process, view
from tests.conftest import copy_app

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "app" / "dashboard.py"
README = (ROOT / "README.md").read_text(encoding="utf-8")
SOURCE = DASHBOARD.read_text(encoding="utf-8")

# 哪一页该出现哪些任务的控制条：K 线页与回测报告页读的都是回测产物，
# 今日信号页同屏展示固定池信号与全市场扫描两块产物，所以它有两条。
PAGE_JOBS = {
    "回测报告": ("backtest",),
    "个股K线": ("backtest",),
    "今日信号": ("daily_signal", "market_scan"),
}
READONLY_PAGES = list(PAGE_JOBS)
ALL_PAGES = [*READONLY_PAGES, "任务控制台"]


def _page(tmp_path: Path, page: str) -> AppTest:
    """app/ 整目录复制进 tmp_path 再交给 AppTest：ROOT/OUTPUT/RUNS_DIR 全落在
    tmp_path，与仓库真实 output/runs/ 完全隔离（否则测试会读到、甚至停掉真任务）。"""
    dashboard = copy_app(tmp_path)
    at = AppTest.from_file(str(dashboard), default_timeout=30).run()
    at.sidebar.radio[0].set_value(page).run()
    return at


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


def _htmls(at: AppTest) -> list[str]:
    """页面上我们自己包的 .qd-* HTML（st.html 在 AppTest 里没有 .value）；
    theme.inject() 那段 <style> 也走 st.html，滤掉免得断言蒙对。"""
    return [b for e in at.get("html")
            if not (b := e.proto.body).lstrip().startswith("<style>")]


# ------------------------------------------------------------------ §4.3 侧边栏
def test_sidebar_no_longer_claims_the_panel_is_read_only(tmp_path):
    """M2 之后面板已经能在本机起进程，侧边栏还写着"本面板纯只读；回测与信号请用命令行
    运行"——这是 HEAD 上一句关于**可执行能力**的假话（设计 §7 的 M3 第一件事）。"""
    at = _page(tmp_path, "回测报告")
    text = " ".join(c.value for c in at.sidebar.caption)
    assert "只读" not in text, f"侧边栏仍自称只读: {text}"
    assert "任务控制台" in text, f"侧边栏应说明可执行任务并指向控制台: {text}"


def test_sidebar_warns_against_exposing_the_panel_to_the_lan(tmp_path):
    """§4.3 + §5.3：面板能执行本机命令，暴露到网络等同于远程命令执行漏洞。
    这句提示原先只在模块 docstring 里（用户看不见），必须出现在侧边栏。"""
    at = _page(tmp_path, "回测报告")
    text = " ".join(e.value for e in [*at.sidebar.caption, *at.sidebar.warning,
                                      *at.sidebar.markdown, *at.sidebar.info])
    assert "--server.address" in text and "0.0.0.0" in text, f"缺少暴露风险提示: {text}"
    assert "局域网" in text, text


def test_sidebar_warning_shows_on_every_page(tmp_path):
    """提示写在侧边栏而不是某一页里：四页都得看得见（用户可能只待在 K 线页）。"""
    for page in ALL_PAGES:
        at = _page(tmp_path, page)
        text = " ".join(e.value for e in [*at.sidebar.caption, *at.sidebar.warning,
                                          *at.sidebar.markdown, *at.sidebar.info])
        assert "0.0.0.0" in text, f"{page} 页的侧边栏没有安全提示"


def test_page_title_is_not_stale():
    """page_title 停在 v0.1.1 也是同一句假话的一部分（设计 §7）。"""
    tree = ast.parse(SOURCE)
    titles = [kw.value.value for node in ast.walk(tree)
              if isinstance(node, ast.Call)
              and getattr(node.func, "attr", "") == "set_page_config"
              for kw in node.keywords if kw.arg == "page_title"]
    assert titles, "找不到 st.set_page_config(page_title=...)"
    assert "v0.1.1" not in titles[0], f"page_title 仍停在旧版本: {titles[0]}"


# ------------------------------------------------------------------ §4.2 控制条
@pytest.mark.parametrize("page", READONLY_PAGES)
def test_each_readonly_page_has_a_control_bar_for_its_own_jobs(page, tmp_path):
    """每页顶部一条精简控制条，只给这一页看得见产物的任务（回测页不该出现扫描按钮，
    否则用户在回测页误点一下就是 0.5-2 小时的全市场扫描）。"""
    at = _page(tmp_path, page)
    assert not at.exception, at.exception
    expected = [f"bar_{action}_{job}" for job in PAGE_JOBS[page]
                for action in ("start", "stop")]
    assert sorted(_bar_keys(at)) == sorted(expected), _bar_keys(at)


@pytest.mark.parametrize("page", READONLY_PAGES)
def test_control_bar_renders_even_with_no_results_yet(page, tmp_path):
    """空 output/ 是最需要"开始"的时候：三页在无产物时都会 st.info 后**早退**，
    控制条排在早退之后就永远看不见——用户只能回去敲命令行。"""
    at = _page(tmp_path, page)
    assert not at.exception, at.exception
    assert at.info, "无产物时原有的提示不该消失"
    for job in PAGE_JOBS[page]:
        assert at.button(f"bar_start_{job}").disabled is False, job


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


def test_control_bar_points_to_the_console_for_details(tmp_path):
    """精简条不放进度条/日志/结果（那是控制台页的事），所以必须明确指路。"""
    at = _page(tmp_path, "今日信号")
    # 只认页面正文里的提示：侧边栏也提"任务控制台"，用 at.caption 会被它蒙过去
    captions = [c.value for c in at.main.caption]
    assert any("任务控制台" in c for c in captions), captions


def test_control_bar_start_passes_only_whitelisted_default_argv(tmp_path, monkeypatch):
    """精简条没有参数控件，点开始 = 默认参数。argv 仍必须由 build_argv 现造
    （列表 + shell=False），不能在这里另起一套拼命令的路子。"""
    started: list[tuple] = []
    monkeypatch.setattr(process, "start", lambda *a, **k: started.append(a))
    at = _page(tmp_path, "今日信号")
    at.button("bar_start_daily_signal").click().run()
    assert len(started) == 1, started
    job_name, argv, runs_dir = started[0]
    assert job_name == "daily_signal"
    assert argv == [sys.executable, "-u", jobs.JOBS["daily_signal"].script]
    assert Path(runs_dir) == tmp_path / "output" / "runs"


def test_control_bar_start_requests_a_full_rerun(tmp_path, monkeypatch):
    """点开始后要整页重跑，否则同屏另一条控制条的"开始"不会被互斥禁用、
    状态徽标也还是"空闲"——用户看到的就是"点了没反应"，于是再点一次。"""
    import streamlit as st

    reruns: list[int] = []
    monkeypatch.setattr(process, "start", lambda *a, **k: None)
    at = _page(tmp_path, "今日信号")
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
    assert any("启动失败" in e.value for e in at.error), [e.value for e in at.error]


def test_control_bar_start_disabled_by_the_global_mutex(tmp_path):
    """baostock 单会话：扫描在跑时，回测页的"开始"也必须禁用并点名是谁在跑。
    互斥是全局的，不是每页各管一摊。"""
    _fake_run(tmp_path, "market_scan", "running", log="[1800/3010] 信号 15 条\n")
    at = _page(tmp_path, "回测报告")
    assert not at.exception, at.exception
    assert at.button("bar_start_backtest").disabled is True, "跨页互斥失效"
    assert any("全市场扫描" in c.value and "正在运行" in c.value for c in at.main.caption), \
        [c.value for c in at.main.caption]


def test_control_bar_stop_only_enabled_for_the_running_job(tmp_path):
    _fake_run(tmp_path, "daily_signal", "running", log="login success!\n")
    at = _page(tmp_path, "今日信号")
    assert at.button("bar_stop_daily_signal").disabled is False
    assert at.button("bar_stop_market_scan").disabled is True, "没在跑的任务不该能停"


def test_control_bar_stop_goes_through_runner_stop(tmp_path, monkeypatch):
    """停止必须走 process.stop（SIGTERM 进程组 → 10s → SIGKILL），
    不能在面板里另发信号；停完同样要整页重跑好解禁其余"开始"。"""
    import streamlit as st

    stopped: list = []
    reruns: list[int] = []
    monkeypatch.setattr(process, "stop", lambda state, runs_dir: stopped.append(state))
    _fake_run(tmp_path, "market_scan", "running", log="[1800/3010] 信号 15 条\n")
    at = _page(tmp_path, "今日信号")
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
    run = tmp_path / "output" / "ma_cross_20260826_112606"
    run.mkdir(parents=True)
    (run / "metrics.json").write_text('{"n_trades": 243}', encoding="utf-8")
    (run / "report.html").write_text("<html></html>", encoding="utf-8")
    (run / "trades.csv").write_text("symbol,action\n", encoding="utf-8")
    at = _page(tmp_path, "回测报告")
    assert not at.exception, at.exception
    assert _bar_keys(at) == [], f"互斥状态未知却给了按钮: {_bar_keys(at)}"
    assert any("损坏" in e.value for e in at.error), [e.value for e in at.error]
    blob = " ".join(_htmls(at))
    assert 'class="qd-metric-value">243<' in blob, "只读内容被控制条的故障连坐了"
    # 页头那颗 pill 此时必须如实说"未知"：报"空闲"就是猜的，而这正是 fail-safe 要挡的
    assert view.UNKNOWN_TEXT in blob, blob


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
    at = _page(tmp_path, "今日信号")
    assert not at.exception, at.exception
    assert [df.value["symbol"].tolist() for df in at.dataframe] == [["000333"], ["000020"]]


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
    for label in ("全市场扫描", "每日信号", "回测"):
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
