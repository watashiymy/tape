"""进程生命周期（v0.2.0 设计 §3.1）：起任务、探活、停任务、全局互斥。

任务与面板**完全解耦**：`start_new_session=True` 让子进程自成会话/进程组，
关浏览器、甚至停掉 Streamlit 服务，跑着的扫描都不会被带走；重开面板靠
`output/runs/<job>.json` 重新认领。因此状态只能靠"落盘 + 轮询"维护，
面板每次交互都是新一轮脚本执行，内存里的任何东西都不可信。
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path

RUNNING, SUCCESS, FAILED, STOPPED = "running", "success", "failed", "stopped"

TERM_GRACE_S = 10.0     # SIGTERM 后等多久才升级 SIGKILL（设计 §5.2）
REAP_WAIT_S = 2.0       # SIGKILL 之后收尸的最长等待
# PID 复用防护的静默上限。真实扫描日志相邻进度行最大间隔约 60s（PROGRESS_EVERY=100），
# 回测写几十 MB HTML 时更是几分钟不出声，阈值取 60s 会把**健康运行中**的任务判成结束，
# 从而放开互斥、让第二个 baostock 会话把第一个踢下线。宁可晚认几分钟。
LOG_STALE_S = 600.0
# 僵尸清理时唯一可信的"跑完了"痕迹：两个信号脚本的收尾行是"已保存:"，
# run_backtest.py 的收尾行是"  报告目录:"（缩进两格，故按子串匹配）。
# 少了后者，一次被 kill -9 的**成功**回测会被判成 failed。
DONE_MARKERS = ("已保存:", "报告目录:")

# 本进程亲手起的子进程。留着引用有两个作用：
# 1) 拿得到真实退出码（Popen.poll）；2) 防止 Popen 被 GC 后由 subprocess 内部
#    的 _active 列表偷偷回收掉——那样退出码就永远拿不回来了。
_CHILDREN: dict[int, subprocess.Popen] = {}


@dataclass(frozen=True)
class RunState:
    script: str          # 任务名（= 状态文件名），如 market_scan
    run_id: str
    pid: int
    argv: list[str]
    log_path: str
    started_at: str
    status: str          # running | success | failed | stopped
    exit_code: int | None = None
    finished_at: str | None = None


def _state_path(job_name: str, runs_dir: str | Path) -> Path:
    return Path(runs_dir) / f"{job_name}.json"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _write_state(state: RunState, runs_dir: str | Path) -> RunState:
    """原子写（同 data/cache.py：mkstemp + os.replace）。

    面板每 2 秒读一次，非原子写一定会被读到半截 JSON。
    """
    d = Path(runs_dir)
    d.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=f"{state.script}.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(asdict(state), ensure_ascii=False))
    os.replace(tmp, _state_path(state.script, d))
    return state


def is_alive(pid: int) -> bool:
    """PID 是否存在。PermissionError 视为存活（进程在，只是不归本用户）。"""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _probe(pid: int) -> tuple[str, int | None]:
    """("running", None) | ("exited", 退出码) | ("unknown", None)。

    unknown = 这个 PID 不是本进程的孩子（面板重启过，或 PID 根本不是我们的），
    只能退化到 is_alive + 日志新鲜度判断。
    注意顺序：僵尸子进程的 os.kill(pid, 0) 照样成功，必须先 wait 再探活。
    """
    proc = _CHILDREN.get(pid)
    if proc is not None:
        rc = proc.poll()
        if rc is None:
            return "running", None
        _CHILDREN.pop(pid, None)
        return "exited", rc
    try:
        got, status = os.waitpid(pid, os.WNOHANG)
    except (ChildProcessError, OSError):
        return "unknown", None
    if got == 0:
        return "running", None
    return "exited", os.waitstatus_to_exitcode(status)


def _log_text(state: RunState) -> str:
    try:
        return Path(state.log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _log_is_stale(state: RunState) -> bool:
    """日志（或启动时刻）多久没动静。起步阶段还没输出时用 started_at 兜底。"""
    try:
        mtime = os.path.getmtime(state.log_path)
    except OSError:
        mtime = 0.0
    try:
        started = datetime.fromisoformat(state.started_at).timestamp()
    except ValueError:
        started = 0.0
    return time.time() - max(mtime, started) > LOG_STALE_S


def _finish(state: RunState, runs_dir: str | Path, exit_code: int | None,
            status: str | None = None) -> RunState:
    if status is None:
        status = SUCCESS if exit_code == 0 else FAILED
    return _write_state(
        replace(state, status=status, exit_code=exit_code, finished_at=_now()), runs_dir)


def _reap_zombie(state: RunState, runs_dir: str | Path) -> RunState:
    """状态说 running、进程却已不在：从日志尾部推断结果并落盘修正。

    不做这一步的话，一次异常退出（kill -9、关机、面板被 SIGKILL）会让
    "有任务在跑"永远为真，三个按钮从此全部禁用，只能手工删文件才能恢复。
    退出码拿不到了（进程不是我们的孩子），如实留 None。
    """
    log = _log_text(state)
    ok = any(marker in log for marker in DONE_MARKERS)
    return _finish(state, runs_dir, exit_code=None, status=SUCCESS if ok else FAILED)


def read_state(job_name: str, runs_dir: str | Path) -> RunState | None:
    """读状态，顺便做僵尸清理与 PID 复用防护。没跑过返回 None。"""
    p = _state_path(job_name, runs_dir)
    if not p.is_file():
        return None
    try:
        state = RunState(**json.loads(p.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, TypeError, OSError) as e:
        # 静默吞掉会让面板永远显示"空闲"，而真任务还在跑（互斥失效）。必须响亮失败。
        raise RuntimeError(f"运行状态文件损坏: {p}，删除该文件后重开面板即可") from e
    if state.status != RUNNING:
        return state
    kind, rc = _probe(state.pid)
    if kind == "exited":
        return _finish(state, runs_dir, exit_code=rc)
    if kind == "unknown":
        # 认不了亲的情况下才需要防 PID 复用：进程在、但日志已经很久没动
        # → 多半是别人的进程占了这个 PID，按结束处理。
        if not is_alive(state.pid) or _log_is_stale(state):
            return _reap_zombie(state, runs_dir)
    return state


def any_running(runs_dir: str | Path) -> str | None:
    """全局互斥检查：返回正在跑的任务名，没有则 None。

    baostock 单会话，两个任务并发会互踢下线且更慢——这是硬约束，不靠用户自觉。
    """
    d = Path(runs_dir)
    if not d.is_dir():
        return None
    for p in sorted(d.glob("*.json")):
        state = read_state(p.stem, d)
        if state is not None and state.status == RUNNING:
            return state.script
    return None


def start(job_name: str, argv: list[str], runs_dir: str | Path) -> RunState:
    """起任务：argv 列表 + shell=False（绝不拼 shell 字符串），独立会话，输出进日志文件。"""
    busy = any_running(runs_dir)
    if busy:
        raise RuntimeError(f"「{busy}」正在运行，同时只允许一个任务（baostock 单会话）")
    logs = Path(runs_dir) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    run_id = f"{job_name}_{now:%Y%m%d_%H%M%S}_{now.microsecond // 1000:03d}"
    log_path = logs / f"{run_id}.log"
    cmd = [str(a) for a in argv]
    with open(log_path, "wb") as log:
        proc = subprocess.Popen(
            cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            shell=False,            # 显式写死：这一行就是注入防线本身
            start_new_session=True,  # 自成会话/进程组：面板退出不带走任务，停止可整组发信号
            close_fds=True)
    _CHILDREN[proc.pid] = proc
    state = RunState(script=job_name, run_id=run_id, pid=proc.pid, argv=cmd,
                     log_path=str(log_path), started_at=now.isoformat(timespec="seconds"),
                     status=RUNNING, exit_code=None, finished_at=None)
    return _write_state(state, runs_dir)


def _signal_group(pid: int, sig: int) -> bool:
    """向**进程组**发信号，且只对"自建会话的组长"下手。

    两道拦截缺一不可：pgid != pid 说明这 PID 不是我们 start_new_session 起来的；
    pgid == 自己的组 说明一发信号就会把 Streamlit（连同整个终端作业）一起打死
    （这一条同时覆盖 pid 就是自己的情形）。
    """
    if pid <= 0:
        return False
    try:
        pgid = os.getpgid(pid)
        if pgid != pid or pgid == os.getpgid(0):
            return False
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    return True


def _wait_gone(pid: int, timeout: float) -> tuple[bool, int | None]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        kind, rc = _probe(pid)
        if kind == "exited":
            return True, rc
        if kind == "unknown" and not is_alive(pid):
            return True, None
        time.sleep(0.05)
    return False, None


def stop(state: RunState, runs_dir: str | Path,
         grace_s: float = TERM_GRACE_S) -> RunState:
    """停止：先给进程组 SIGTERM，宽限期内没退再 SIGKILL。

    直接 -9 会剥夺脚本走完当前标的、让缓存原子落盘的机会（缓存层虽已原子写，
    仍应优先温和退出）。已结束的任务原样返回，不重复发信号。
    """
    if state.status != RUNNING:
        return state
    if _signal_group(state.pid, signal.SIGTERM):
        gone, rc = _wait_gone(state.pid, grace_s)
        if not gone:
            _signal_group(state.pid, signal.SIGKILL)
            _, rc = _wait_gone(state.pid, REAP_WAIT_S)
    else:
        rc = None   # 信号没发出去（进程早没了，或 PID 不是我们的）——只做状态收尾
    return _finish(state, runs_dir, exit_code=rc, status=STOPPED)
