# tests/test_runner_process.py — v0.2.0 §3.1 进程生命周期（离线，只跑无害命令）
#
# 全程不碰真实脚本、不联网：起 python -c 的 sleep/echo/exit(3)。
import dataclasses
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


@pytest.fixture
def runs(tmp_path):
    """独立 runs 目录；退出前兜底清掉本用例起的任何残留进程。

    只对"自建会话的组长"下手（pgid == pid 且不是自己）：有的用例会故意把
    os.getpid() 写进伪造状态文件，照着 killpg 会把 pytest 连同整个终端打死。
    """
    d = tmp_path / "runs"
    yield d
    for f in sorted(d.glob("*.json")) if d.exists() else []:
        pid = json.loads(f.read_text(encoding="utf-8")).get("pid")
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


def test_zombie_backtest_infers_success_from_report_dir(runs):
    """回测的收尾行不是"已保存:"而是"  报告目录:"（真实日志见 fixtures/backtest_sample.log）。
    只认"已保存:"会把一次被 kill -9 的成功回测判成 failed。"""
    write_state(runs, "backtest", pid=dead_pid(),
                log_text=(FIXTURES / "backtest_sample.log").read_text(encoding="utf-8"))
    assert process.read_state("backtest", runs).status == "success"


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
