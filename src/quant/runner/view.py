"""状态/进度 → 面板文案（v0.2.0 设计 §4.1）。纯函数：不碰文件、不碰进程、不碰 streamlit。

面板本身写不了单元测试（顶层就是 UI 代码），所以凡是"要判断、要格式化"的都搬到这里：
徽标带不带退出码、ETA 该不该显示、百分比怎么算，错了全是用户直接看到的假信息。
面板只负责把这些字符串塞进 st.* 里。
"""
from __future__ import annotations

from datetime import datetime

from quant.runner.jobs import JOBS
from quant.runner.process import FAILED, RUNNING, STOPPED, SUCCESS, RunState
from quant.runner.progress import Progress

IDLE_BADGE = "⚪ 空闲"
_BADGES = {RUNNING: "🔵 运行中", SUCCESS: "✅ 成功", FAILED: "❌ 失败", STOPPED: "⏹ 已停止"}


def status_badge(state: RunState | None) -> str:
    """状态徽标。失败必须把退出码带出来——1=脚本自己 sys.exit、2=argparse 参数错、
    -9/-15=被信号打死，这是排查的第一手线索。僵尸清理拿不到退出码（进程不是面板的孩子），
    如实写"未知"，不能渲染出 "退出码 None"。"""
    if state is None:
        return IDLE_BADGE
    badge = _BADGES.get(state.status, f"❔ {state.status}")
    if state.status == FAILED:
        code = "未知" if state.exit_code is None else state.exit_code
        badge += f"（退出码 {code}）"
    return badge


def human_duration(seconds: float | None) -> str:
    """已用时长。先四舍五入到整秒再拆分：先拆后舍会渲染出 "60秒" 这种东西。
    负数（时钟回拨/状态文件手改）钳到 0，不能把 "-3秒" 甩给用户。"""
    if seconds is None:
        return "—"
    total = max(0, round(seconds))
    if total < 60:
        return f"{total}秒"
    minutes, sec = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}分{sec}秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}小时{minutes}分"


def human_eta(seconds: float | None) -> str:
    """剩余时长，粗到分钟即可（线性外推本来就只是参考，秒级精度是假精确）。
    没有 ETA（不确定态）或已归零时返回空串——宁可不显示，不显示编出来的数字。"""
    if not seconds or seconds < 0:
        return ""
    if round(seconds) < 60:
        return "~1分钟内"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"~{minutes}分钟"
    hours, minutes = divmod(minutes, 60)
    return f"~{hours}小时{minutes}分钟"


def progress_ratio(p: Progress) -> float | None:
    """0.0~1.0，缺 current/total 就是不确定态（None）。

    两个都得挡：total=0 会 ZeroDivisionError 直接崩页；current>total（日志错位）
    会让 st.progress 抛 StreamlitAPIException（它只收 0~1）。
    """
    if p.current is None or not p.total or p.total < 0:
        return None
    return min(1.0, max(0.0, p.current / p.total))


def progress_caption(p: Progress, elapsed_s: float | None = None) -> str:
    """一行进度文案：阶段、百分比、已用、预计剩余、计数。

    阶段永远排第一且不被百分比顶掉——扫描跑到一半崩了的时候，"异常退出"比 "60%" 重要。
    `elapsed_s` 是**墙钟**耗时（由调用方按状态文件算），不能拿日志里的"耗时 673s"冒充：
    那一项不含 get_all_symbols 的 2-4 分钟固定开销，是给 ETA 外推用的每票速率口径。
    """
    parts = [p.phase]
    ratio = progress_ratio(p)
    if ratio is not None:
        parts.append(f"{p.current}/{p.total}（{ratio:.0%}）")
    if elapsed_s is not None:
        parts.append(f"已用 {human_duration(elapsed_s)}")
    eta = human_eta(p.eta_s)
    if eta:
        parts.append(f"预计剩余 {eta}")
    parts += [f"{k} {v}" for k, v in p.extras.items()]
    return "，".join(parts)


def elapsed_seconds(state: RunState, now: datetime | None = None) -> float | None:
    """墙钟耗时。已结束的任务冻结在 finished_at——否则隔天再开面板，
    上周那次回测会显示"已用 3天"。时间戳解析不了就交白卷（None → 显示 —）。"""
    try:
        start = datetime.fromisoformat(state.started_at)
    except (TypeError, ValueError):
        return None
    end = None
    if state.status != RUNNING and state.finished_at:
        try:
            end = datetime.fromisoformat(state.finished_at)
        except (TypeError, ValueError):
            end = None
    return max(0.0, ((end or now or datetime.now()) - start).total_seconds())


def start_button_state(busy: str | None, job_name: str) -> tuple[bool, str]:
    """(是否禁用"开始", 提示文案)。全局互斥在按钮层强制（设计 §5.1）。

    提示必须点名是谁在跑，而且用中文显示名：面对三个灰按钮却不知道该等什么，
    用户下一步就是去删状态文件。任务名不在 JOBS 里（手工实验残留的 json）也照常提示，
    不能 KeyError 崩页。
    """
    if busy is None:
        return False, ""
    label = JOBS[busy].label if busy in JOBS else busy
    return True, f"「{label}」正在运行，同时只允许一个任务（baostock 单会话）"
