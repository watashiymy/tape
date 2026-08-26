# tests/test_runner_process.py — v0.2.0 §3.1 进程生命周期（离线，只跑无害命令）
#
# 全程不碰真实脚本、不联网：起 python -c 的 sleep/echo/exit(3)。
import dataclasses
import gc
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from quant.runner import process
from quant.runner.process import RunState

FIXTURES = Path(__file__).resolve().parent / "fixtures"
# 真实回测日志（默认配置 = ma_cross + donchian 两个策略，exit 0）与它"跑到一半"的截断：
# 截到第一个"报告目录:"处 —— ma_cross 已收尾、donchian 还没开始算。
BACKTEST_LOG = (FIXTURES / "backtest_sample.log").read_text(encoding="utf-8")
BACKTEST_HALF = BACKTEST_LOG.split("\n\n===== donchian")[0] + "\n"
PY = sys.executable
SLEEP = [PY, "-c", "import time; time.sleep(30)"]
ECHO = [PY, "-c", "print('hi')"]
FAIL3 = [PY, "-c", "import sys; sys.exit(3)"]

# 起一个子进程再自己 sleep：用于验证 SIGTERM 打的是**进程组**而不是单个 PID
SPAWNER = [PY, "-c", (
    "import subprocess, sys, time\n"
    "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "print('child', p.pid, flush=True)\n"
    "time.sleep(60)\n"
)]
# 装死：忽略 SIGTERM，用于验证 10 秒后升级 SIGKILL
IGNORES_TERM = [PY, "-c", (
    "import signal, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "print('ready', flush=True)\n"
    "time.sleep(60)\n"
)]
# 收到 SIGTERM 后先花 0.3 秒做收尾（模拟"走完当前标的 + 缓存原子落盘"）再自行 exit 0。
# 用于钉死"先 SIGTERM、并且真的等它收尾"这条语义：直接 SIGKILL 或宽限期为 0，
# 这行 cleanup-done 都写不出来，退出码也不会是 0。
CLEANS_UP_ON_TERM = [PY, "-c", (
    "import signal, sys, time\n"
    "def bye(*_):\n"
    "    time.sleep(0.3)\n"
    "    print('cleanup-done', flush=True)\n"
    "    sys.exit(0)\n"
    "signal.signal(signal.SIGTERM, bye)\n"
    "print('ready', flush=True)\n"
    "time.sleep(60)\n"
)]


@pytest.fixture
def runs(tmp_path):
    """独立 runs 目录；退出前兜底清掉本用例起的任何残留进程。

    只对"自建会话的组长"下手（pgid == pid 且不是自己）：有的用例会故意把
    os.getpid() 写进伪造状态文件，照着 killpg 会把 pytest 连同整个终端打死。
    """
    d = tmp_path / "runs"
    yield d
    for f in sorted(d.glob("*.json")) if d.exists() else []:
        try:                                    # 有的用例故意写半截 JSON
            pid = json.loads(f.read_text(encoding="utf-8")).get("pid")
        except json.JSONDecodeError:
            continue
        if not isinstance(pid, int) or pid == os.getpid():
            continue
        try:
            if os.getpgid(pid) == pid:
                os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def wait_until(fn, timeout=15.0, what="条件"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = fn()
        if got:
            return got
        time.sleep(0.05)
    raise AssertionError(f"等待{what}超时（{timeout}s）")


def wait_done(job, runs_dir, timeout=15.0):
    return wait_until(
        lambda: (lambda s: s if s and s.status != "running" else None)(
            process.read_state(job, runs_dir)),
        timeout, what=f"{job} 结束")


def wait_log(state, marker, timeout=10.0):
    return wait_until(lambda: marker in Path(state.log_path).read_text(encoding="utf-8"),
                      timeout, what=f"日志出现 {marker!r}")


def write_state(runs_dir: Path, job: str, **over) -> Path:
    """伪造状态文件（僵尸清理用）。同时钉死磁盘 schema。"""
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "logs").mkdir(exist_ok=True)
    log = runs_dir / "logs" / f"{job}_fake.log"
    log.write_text(over.pop("log_text", ""), encoding="utf-8")
    doc = {"script": job, "run_id": f"{job}_fake", "pid": 999999,
           "argv": ["/bin/echo", "x"], "log_path": str(log),
           "started_at": "2026-08-25T10:00:00", "status": "running",
           "exit_code": None, "finished_at": None}
    doc.update(over)
    p = runs_dir / f"{job}.json"
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return p


def dead_pid() -> int:
    """一个确定已经结束且已被回收的 PID。"""
    p = subprocess.Popen([PY, "-c", "pass"])
    p.wait()
    return p.pid


# ------------------------------------------------------------------ 生命周期
def test_start_writes_running_state_and_log(runs):
    st = process.start("echo_job", ECHO, runs)
    assert isinstance(st, RunState)
    assert st.status == "running" and st.pid > 0
    assert st.script == "echo_job" and st.argv == ECHO
    assert st.exit_code is None and st.finished_at is None
    assert (runs / "echo_job.json").exists()
    assert Path(st.log_path).exists() and st.run_id in st.log_path


def test_success_exit_zero(runs):
    st = process.start("echo_job", ECHO, runs)
    done = wait_done("echo_job", runs)
    assert done.status == "success" and done.exit_code == 0
    assert done.finished_at
    assert "hi" in Path(st.log_path).read_text(encoding="utf-8")


def test_exit_code_survives_popen_being_garbage_collected(runs):
    """必须留住 Popen 引用（_CHILDREN）。

    不留的话，start() 一返回 Popen 就被 GC，`Popen.__del__` 把它挂进 subprocess._active，
    下一次任何 Popen 创建都会触发内部收尸抢先 wait 掉它——退出码从此拿不回来，
    面板只能退化成"按日志标记猜结果"（本轮 B2 修的正是这种猜法）。
    """
    st = process.start("loud_fail_job", [PY, "-c",
                                         "import sys; print('bye', flush=True); sys.exit(3)"], runs)
    wait_log(st, "bye")
    time.sleep(0.3)                 # 让子进程确实走完 sys.exit（print 之后只差微秒）
    gc.collect()
    subprocess._cleanup()           # 模拟内部收尸（真实触发点是下一次 Popen 创建）
    done = wait_done("loud_fail_job", runs)
    assert (done.status, done.exit_code) == ("failed", 3)


def test_exit_code_3_is_failed(runs):
    """非零退出码必须被捕获成 failed，且原样带出退出码（面板要显示 ❌ 退出码 3）。"""
    process.start("fail_job", FAIL3, runs)
    done = wait_done("fail_job", runs)
    assert done.status == "failed" and done.exit_code == 3


def test_stderr_goes_to_the_same_log(runs):
    process.start("err_job", [PY, "-c", "import sys; print('boom', file=sys.stderr)"], runs)
    done = wait_done("err_job", runs)
    assert "boom" in Path(done.log_path).read_text(encoding="utf-8")


def test_finished_state_is_persisted_not_recomputed(runs):
    """结束态必须落盘：重开面板（新进程再读）看到的还是 failed/3。"""
    process.start("fail_job", FAIL3, runs)
    wait_done("fail_job", runs)
    doc = json.loads((runs / "fail_job.json").read_text(encoding="utf-8"))
    assert doc["status"] == "failed" and doc["exit_code"] == 3


def test_read_state_missing_returns_none(runs):
    assert process.read_state("never_ran", runs) is None


def test_no_tmp_files_left_behind(runs):
    """原子写：mkstemp + os.replace，不得留下 .tmp 残渣。"""
    process.start("echo_job", ECHO, runs)
    wait_done("echo_job", runs)
    assert list(runs.glob("*.tmp")) == []


def test_state_file_is_replaced_atomically_not_overwritten_in_place(runs):
    """状态必须是"整个文件换掉"而不是"就地覆写"。

    面板每 2 秒读一次状态文件，就地覆写一定会被读到半截 JSON —— 直接撞上
    read_state 的"状态文件损坏"RuntimeError（面板整页崩）。
    只查 .tmp 残渣是查不出来的：非原子写天然没有残渣。这里改查 inode：
    os.replace 换的是目录项，每写一次 inode 必变；open(path,"w") 覆写则 inode 不变。
    """
    process.start("echo_job", ECHO, runs)
    p = runs / "echo_job.json"
    first_inode = p.stat().st_ino
    wait_done("echo_job", runs)                     # 结束态又写了一次
    assert p.stat().st_ino != first_inode, "同一个 inode 被就地改写 = 非原子写"
    assert json.loads(p.read_text(encoding="utf-8"))["status"] == "success"


def test_corrupt_state_file_fails_loudly(runs):
    """状态文件损坏必须响亮失败，绝不能静默当成"没跑过"。

    静默返回 None 会让面板显示"空闲"、按钮放开，而真任务还在跑——
    互斥失效，第二个 baostock 会话把第一个踢下线（本项目的静默失败清单同款）。
    """
    runs.mkdir(parents=True, exist_ok=True)
    (runs / "market_scan.json").write_text(          # 半截 JSON（非原子写的典型产物）
        '{"script": "market_scan", "run_id": "x", "pid": 123', encoding="utf-8")
    with pytest.raises(RuntimeError) as e:
        process.read_state("market_scan", runs)
    assert "market_scan.json" in str(e.value)
    with pytest.raises(RuntimeError):                # 互斥检查也不许把损坏读成"没人在跑"
        process.any_running(runs)
    (runs / "backtest.json").write_text('{"script": "backtest"}', encoding="utf-8")
    with pytest.raises(RuntimeError):                # JSON 合法但字段缺失，同样要炸
        process.read_state("backtest", runs)


def test_log_path_is_absolute_so_it_survives_a_cwd_change(tmp_path, monkeypatch):
    """状态里的 log_path 必须是绝对路径。

    存成相对路径的话，换个 cwd（面板从别处启动、或以后加了定时任务）就读不到日志，
    而 _log_text 把 OSError 吞成 ""：僵尸清理直接判 failed、实时输出全空白，全程不报错。
    """
    monkeypatch.chdir(tmp_path)
    st = process.start("echo_job", ECHO, Path("runs"))      # 故意给相对 runs_dir
    assert Path(st.log_path).is_absolute(), st.log_path
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.chdir(tmp_path / "elsewhere")
    done = wait_done("echo_job", tmp_path / "runs")
    assert Path(done.log_path).is_absolute()
    assert "hi" in Path(done.log_path).read_text(encoding="utf-8")
    assert done.status == "success"


# ------------------------------------------------------------------ 探活 / 停止
def test_is_alive_true_for_self_false_for_dead():
    assert process.is_alive(os.getpid()) is True
    assert process.is_alive(dead_pid()) is False


def test_stop_terminates_process(runs):
    st = process.start("sleep_job", SLEEP, runs)
    t0 = time.monotonic()
    stopped = process.stop(st, runs)
    assert stopped.status == "stopped"
    assert time.monotonic() - t0 < 10, "SIGTERM 就该秒退，不该等到 SIGKILL 宽限期"
    wait_until(lambda: not process.is_alive(st.pid), 5, what="进程消失")
    assert process.read_state("sleep_job", runs).status == "stopped"


def test_stop_signals_the_whole_process_group(runs):
    """SIGTERM 必须打进程组：脚本自己起的子进程也要跟着退，否则残留 baostock 会话。"""
    st = process.start("group_job", SPAWNER, runs)
    wait_log(st, "child ")
    child = int(Path(st.log_path).read_text(encoding="utf-8").split("child ")[1].split()[0])
    assert process.is_alive(child)
    process.stop(st, runs)
    wait_until(lambda: not process.is_alive(child), 5, what="子进程消失")


def test_stop_sends_sigterm_first_and_waits_out_the_grace_period(runs):
    """铁律 5 / 设计 §5.2：先给进程组 SIGTERM，并**等**它走完收尾，10 秒后才升级 SIGKILL。

    直接 -9（或把 TERM_GRACE_S 归零）会剥夺脚本走完当前标的、让缓存原子落盘的机会。
    这里不传 grace_s，钉的就是默认宽限期本身：子进程装了 SIGTERM handler，
    收到信号后 sleep 0.3s 再打 cleanup-done 并 exit 0。
    - 换成 SIGKILL：handler 根本不会被调用，日志里没有 cleanup-done，退出码 -9；
    - 宽限期归零：SIGTERM 刚发出就补 SIGKILL，同样收不到 cleanup-done。
    """
    st = process.start("graceful_job", CLEANS_UP_ON_TERM, runs)
    wait_log(st, "ready")
    stopped = process.stop(st, runs)
    assert stopped.status == "stopped"
    assert stopped.exit_code == 0, "自行退出的退出码必须是 0（被 SIGKILL 收掉会是 -9）"
    assert "cleanup-done" in Path(st.log_path).read_text(encoding="utf-8"), \
        "SIGTERM 的收尾输出必须落盘——没有它说明进程是被强杀的"


def test_stop_escalates_to_sigkill(runs):
    """装死（忽略 SIGTERM）的进程，宽限期后必须被 SIGKILL 收掉。"""
    st = process.start("stubborn_job", IGNORES_TERM, runs)
    wait_log(st, "ready")
    stopped = process.stop(st, runs, grace_s=0.5)
    assert stopped.status == "stopped"
    wait_until(lambda: not process.is_alive(st.pid), 5, what="进程被 SIGKILL")


def test_stop_on_finished_state_is_noop(runs):
    process.start("fail_job", FAIL3, runs)
    done = wait_done("fail_job", runs)
    again = process.stop(done, runs)
    assert (again.status, again.exit_code) == ("failed", 3)


def test_signal_group_refuses_a_pid_that_is_not_a_group_leader(runs):
    """PID 复用防护的另一半：状态里的 PID 若被复用给**别人进程组里的一个成员**，
    照着 killpg 会连带打死那一整组无辜进程（比如某个终端作业）。

    只有 `pgid != pid` 这道能拦住它——`pgid == 自己的组` 那道拦的是另一种情形
    （PID 复用成本进程/同组进程，一发信号连 Streamlit 自己一起打死）。
    这里用 SPAWNER 造出一个真实的"组员"：它是 st.pid 那组里的孩子，pgid 是组长的 pid。
    """
    st = process.start("group_job", SPAWNER, runs)
    wait_log(st, "child ")
    child = int(Path(st.log_path).read_text(encoding="utf-8").split("child ")[1].split()[0])
    assert os.getpgid(child) == st.pid != child, "前提：child 是组员而不是组长"
    assert process._signal_group(child, signal.SIGTERM) is False
    time.sleep(0.3)
    assert process.is_alive(child) and process.is_alive(st.pid), "整组一个都不该被碰"
    process.stop(st, runs)


def test_is_alive_treats_permission_error_as_alive(monkeypatch):
    """进程在、只是不归本用户（EPERM）→ 必须算存活（设计 §3.1）。
    判成"不在"会放开互斥，第二个 baostock 会话把第一个踢下线。"""
    def denied(pid, sig):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(os, "kill", denied)
    assert process.is_alive(4242) is True


def test_probe_reports_running_when_waitpid_says_not_exited_yet(monkeypatch):
    """waitpid(WNOHANG) 返回 (0, 0) = "这个孩子还没退"。少了这道判断，
    会拿 status=0 当退出码算成 exit 0 → 面板给正在跑的任务打 ✅ 成功。"""
    monkeypatch.setattr(process, "_CHILDREN", {})
    monkeypatch.setattr(os, "waitpid", lambda pid, flags: (0, 0))
    assert process._probe(4242) == ("running", None)


def test_stop_does_not_kill_our_own_group(runs):
    """安全兜底：状态里的 PID 不是自建会话的组长（如被复用成本进程），
    绝不能 killpg —— 那会连 Streamlit 自己一起打死。"""
    st = process.start("sleep_job", SLEEP, runs)
    faked = dataclasses.replace(st, pid=os.getpid())
    assert process.stop(faked, runs).status == "stopped"   # 不得抛，也不得杀 pytest 自己
    assert process.is_alive(os.getpid())
    process.stop(st, runs)


# ------------------------------------------------------------------ 全局互斥
def test_any_running_reports_the_running_job(runs):
    assert process.any_running(runs) is None
    st = process.start("sleep_job", SLEEP, runs)
    assert process.any_running(runs) == "sleep_job"
    process.stop(st, runs)
    assert process.any_running(runs) is None


def test_any_running_ignores_finished_jobs(runs):
    process.start("echo_job", ECHO, runs)
    wait_done("echo_job", runs)
    assert process.any_running(runs) is None


def test_start_refuses_while_another_job_runs(runs):
    """全局互斥：baostock 单会话，两个任务并发会互踢下线。"""
    st = process.start("sleep_job", SLEEP, runs)
    with pytest.raises(RuntimeError) as e:
        process.start("echo_job", ECHO, runs)
    assert "sleep_job" in str(e.value)
    assert process.read_state("echo_job", runs) is None
    process.stop(st, runs)
    process.start("echo_job", ECHO, runs)          # 前一个停了就该放行
    assert wait_done("echo_job", runs).status == "success"


# ------------------------------------------------------------------ 僵尸清理
def test_zombie_running_state_with_dead_pid_becomes_success(runs):
    """状态说 running 但进程早没了（kill -9 / 关机）：日志有"已保存:"→ success。
    不做这一步的话，一次异常退出会让按钮永久禁用。"""
    write_state(runs, "market_scan", pid=dead_pid(),
                log_text="[100/3010] 信号 1 条，失败 0 只，耗时 38s\n"
                         "已保存: output/scan/2026-08-24.csv\n")
    st = process.read_state("market_scan", runs)
    assert st.status == "success" and st.finished_at


def test_zombie_running_state_without_saved_marker_becomes_failed(runs):
    write_state(runs, "market_scan", pid=dead_pid(),
                log_text="[100/3010] 信号 1 条，失败 0 只，耗时 38s\n")
    assert process.read_state("market_scan", runs).status == "failed"


def test_zombie_backtest_infers_success_from_all_finished_marker(runs):
    """回测的收尾行不是"已保存:"而是循环外那一行"全部完成: N 个策略"
    （真实日志见 fixtures/backtest_sample.log）。只认"已保存:"会把一次被 kill -9 的
    成功回测判成 failed。"""
    write_state(runs, "backtest", pid=dead_pid(), log_text=BACKTEST_LOG)
    assert process.read_state("backtest", runs).status == "success"


def test_zombie_backtest_half_done_is_not_success(runs):
    """跑到一半被 kill -9 的回测**不是** success。

    真实日志截到第一个"报告目录:"：ma_cross 已收尾，donchian 还没开始算。
    按子串匹配"报告目录:"，整轮只跑完 1/2 也会打 ✅ 成功，用户据此以为回测跑完了。
    （注意这段日志的最后一行就是"报告目录:"——donchian 那一整段计算完全静默，
    所以"要求标记出现在日志尾部"同样区分不出来，只能靠循环外的整轮完成标记。）
    """
    write_state(runs, "backtest", pid=dead_pid(), log_text=BACKTEST_HALF)
    assert process.read_state("backtest", runs).status == "failed"


def test_zombie_with_traceback_is_failed_even_with_done_marker(runs):
    """崩溃优先于收尾标记，且不比先后：stdout 重定向到文件是块缓冲、stderr 不缓冲，
    崩溃前 print 的"已保存:"完全可能被冲刷到 traceback 之后（与 progress.py 同一口径）。"""
    write_state(runs, "market_scan", pid=dead_pid(),
                log_text="已保存: output/scan/2026-08-24.csv\n"
                         "Traceback (most recent call last):\n"
                         '  File "scripts/run_market_scan.py", line 148, in main\n'
                         "ValueError: 2026-08-24 的证券清单为空\n")
    assert process.read_state("market_scan", runs).status == "failed"


def test_zombie_cleanup_is_persisted(runs):
    p = write_state(runs, "market_scan", pid=dead_pid(),
                    log_text="已保存: output/scan/2026-08-24.csv\n")
    process.read_state("market_scan", runs)
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert doc["status"] == "success" and doc["finished_at"]


def test_zombie_cleanup_unblocks_any_running(runs):
    write_state(runs, "market_scan", pid=dead_pid(), log_text="")
    assert process.any_running(runs) is None


def test_zombie_cleanup_survives_missing_log(runs):
    """日志文件被手工删了也不能炸——只能判 failed。"""
    p = write_state(runs, "market_scan", pid=dead_pid(), log_text="")
    doc = json.loads(p.read_text(encoding="utf-8"))
    Path(doc["log_path"]).unlink()
    assert process.read_state("market_scan", runs).status == "failed"


def test_pid_reuse_guard_marks_stale_log_as_finished(runs):
    """PID 存在但日志很久没动 → 多半是 PID 被复用给了别的进程，不能当作我们的任务还在跑。"""
    p = write_state(runs, "market_scan", pid=os.getpid(),
                    log_text="已保存: output/scan/2026-08-24.csv\n",
                    started_at="2026-08-25T10:00:00")
    log = Path(json.loads(p.read_text(encoding="utf-8"))["log_path"])
    old = time.time() - 3600
    os.utime(log, (old, old))
    assert process.read_state("market_scan", runs).status == "success"


def test_pid_alive_with_fresh_log_stays_running(runs):
    """反向：进程在、日志新鲜 → 必须仍算 running（误判成结束会破坏全局互斥）。"""
    write_state(runs, "market_scan", pid=os.getpid(),
                log_text="[100/3010] 信号 1 条，失败 0 只，耗时 38s\n")
    st = process.read_state("market_scan", runs)
    assert st.status == "running"
    assert process.any_running(runs) == "market_scan"


def test_long_silent_gap_still_counts_as_running(runs):
    """真实扫描日志相邻进度行最大间隔约 60s（PROGRESS_EVERY=100），
    回测写 HTML 更是几分钟不出声。宽限期必须明显大于这个量级，
    否则健康的任务会被误判成结束、互斥被打破。"""
    p = write_state(runs, "market_scan", pid=os.getpid(), log_text="扫描中\n")
    log = Path(json.loads(p.read_text(encoding="utf-8"))["log_path"])
    quiet = time.time() - 120           # 静默 2 分钟
    os.utime(log, (quiet, quiet))
    assert process.read_state("market_scan", runs).status == "running"
