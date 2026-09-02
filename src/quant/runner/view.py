"""状态/进度 → 面板文案（v0.2.0 设计 §4.1）。纯函数：不碰文件、不碰进程、不碰 streamlit。

面板本身写不了单元测试（顶层就是 UI 代码），所以凡是"要判断、要格式化"的都搬到这里：
徽标带不带退出码、ETA 该不该显示、百分比怎么算，错了全是用户直接看到的假信息。
面板只负责把这些字符串塞进 st.* 里。
"""
from __future__ import annotations

from datetime import datetime

from quant.runner.jobs import JOBS
from quant.runner.process import FAILED, RUNNING, STOPPED, SUCCESS, RunState
from quant.runner.progress import CRASHED, DONE, Progress

# 状态 pill 的四个类目（§2.3 第 5 条：运行中琥珀 / 成功绿 / 失败红 / 已停止灰）。
# 这里只给类目字符串，配色在 app/theme.py 的 .qd-pill-* 里——纯函数不该知道十六进制。
PILL_RUNNING, PILL_SUCCESS, PILL_FAILED, PILL_IDLE = (
    "running", "success", "failed", "idle")
IDLE_TEXT = "空闲"
# 状态文件损坏时页头用这句。绝不能退回"空闲"——互斥状态不可知恰恰是最该
# fail-safe 的时候，谎报空闲会让用户以为可以随手点开始。
UNKNOWN_TEXT = "任务状态未知"
# 已停止归灰而不是红：那是用户自己按的停止，不是故障。
_PILLS = {RUNNING: ("运行中", PILL_RUNNING), SUCCESS: ("成功", PILL_SUCCESS),
          FAILED: ("失败", PILL_FAILED), STOPPED: ("已停止", PILL_IDLE)}

# 任务已终止时用来顶掉日志相位的终态词。日志解析器只看得见日志，看不见进程死活：
# 被 SIGTERM 停掉的扫描，日志最后一行仍是 `[1800/3010]`，相位就一直卡在"扫描中"。
_END_PHASES = {SUCCESS: DONE, FAILED: "失败", STOPPED: "已停止"}
# 日志自己交代了终局的两种相位，比状态词更具体（"异常退出"带 traceback 尾行），保留不顶掉。
_LOG_TERMINAL = (DONE, CRASHED)


def status_pill(state: RunState | None) -> tuple[str, str]:
    """(pill 文案, CSS 类目)。取代 v0.2.0 的 emoji 徽标：有底色的 pill 更容易扫到。

    失败必须把退出码带出来——1=脚本自己 sys.exit、2=argparse 参数错、
    -9/-15=被信号打死，这是排查的第一手线索。僵尸清理拿不到退出码（进程不是面板的
    孩子），如实写"未知"，不能渲染出 "退出码 None"。

    未知状态（状态文件是普通 JSON，手工改成 "paused" 也可能）原样显示文案但**归灰**：
    用下标取值会 KeyError 崩页，猜成 running 则等于谎报"有任务在跑"。
    """
    if state is None:
        return IDLE_TEXT, PILL_IDLE
    text, kind = _PILLS.get(state.status, (state.status, PILL_IDLE))
    if state.status == FAILED:
        code = "未知" if state.exit_code is None else state.exit_code
        text += f"（退出码 {code}）"
    return text, kind


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


def _phase(p: Progress, status: str) -> str:
    """相位。任务一旦终止，日志里那个进行时（"扫描中"/"取数中"）就是假的：
    进程都没了，同屏徽标写着"⏹ 已停止"，两句话当面打架。此时以状态为准换成终态词，
    除非日志自己已经交代了终局（"完成"/"异常退出"——后者还带着 traceback 尾行）。"""
    if status == RUNNING or p.phase in _LOG_TERMINAL:
        return p.phase
    return _END_PHASES.get(status, p.phase)


def progress_lines(p: Progress, elapsed_s: float | None = None, *,
                   status: str) -> tuple[str, str]:
    """进度文案，拆成两行（§2.4）：("主状态", "细节")。

    主状态 = 阶段 + 计数 + 百分比；细节 = 已用 + 预计剩余 + 额外计数（信号条数等）。
    挤成一行时"扫描中"和"失败 0 只"视觉权重一样，眼睛没有落点。
    细节没内容时返回**空串**，面板据此不渲染第二行（别留一条空 caption 撑版面）。

    阶段永远排第一且不被百分比顶掉——扫描跑到一半崩了的时候，"异常退出"比 "60%" 重要。
    `elapsed_s` 是**墙钟**耗时（由调用方按状态文件算），不能拿日志里的"耗时 673s"冒充：
    那一项不含 get_all_symbols 的 2-4 分钟固定开销，是给 ETA 外推用的每票速率口径。

    `status` 是必填的关键字参数（不给默认值：默认成 running 就等于默认说谎，
    而这正是漏掉它时会犯的错）。**ETA 只在 running 时给**：线性外推的前提是"还在按
    这个速率往下跑"，进程已经被停掉/崩掉之后，那句"预计剩余 ~8分钟"是纯粹编出来的，
    用户照着等就是白等（设计 §3.2"宁可不显示，不显示假数字"）。
    停在 1800/3010、已用多久、出了几条信号都是既成事实，照常留着。
    """
    head = [_phase(p, status)]
    ratio = progress_ratio(p)
    if ratio is not None:
        head.append(f"{p.current}/{p.total}（{ratio:.0%}）")
    detail: list[str] = []
    if elapsed_s is not None:
        detail.append(f"已用 {human_duration(elapsed_s)}")
    eta = human_eta(p.eta_s) if status == RUNNING else ""
    if eta:
        detail.append(f"预计剩余 {eta}")
    detail += [f"{k} {v}" for k, v in p.extras.items()]
    return "，".join(head), "，".join(detail)


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


def job_label(name: str) -> str:
    """任务的中文显示名。JOBS 里没有（runs/ 混进手工实验残留的 json）就原样回显，
    绝不 KeyError 崩页。"""
    return JOBS[name].label if name in JOBS else name


def busy_pill(busy: str | None) -> tuple[str, str]:
    """页头右侧的**全局**任务 pill（文案, 类目）。busy 取自 process.any_running()。

    全局而不是按页：用户在 K 线页也该看见"扫描还在跑"，否则他会去点另一个开始，
    然后对着一句"启动失败"发愁。
    """
    if busy is None:
        return IDLE_TEXT, PILL_IDLE
    return f"{job_label(busy)} 运行中", PILL_RUNNING


def start_button_state(busy: str | None, job_name: str) -> tuple[bool, str]:
    """(是否禁用"开始", 提示文案)。全局互斥在按钮层强制（设计 §5.1）。

    提示必须点名是谁在跑，而且用中文显示名：面对三个灰按钮却不知道该等什么，
    用户下一步就是去删状态文件。任务名不在 JOBS 里（手工实验残留的 json）也照常提示，
    不能 KeyError 崩页。
    """
    if busy is None:
        return False, ""
    return True, (f"「{job_label(busy)}」正在运行，"
                  f"同时只允许一个任务（baostock 单会话）")


def no_output_note(p: Progress, *, status: str) -> str:
    """终止且**没有产物**时那句补充说明。没什么要说的就返回空串（同 progress_lines
    的既有约定：空串 = 面板不渲染这一行）。

    要补的缺口很具体：扫描跑到 2500/3012 时细节行已经写着「信号 130 条」，这时
    按下 ⏹ 停止 —— 那 130 条**一条都没落盘**（结果整轮跑完才写 CSV），而屏幕上
    留下的唯一数字正是那个 130，指向一份根本不存在的产物。页尾「今日信号」显示的
    是上一次那份（标题里带日期，得自己发现不是今天的）。

    顺带纠正一处更早的误解：v0.2.0 设计里写着终止后仍画进度条是为了让用户
    "判断要不要接着补跑"——**不存在断点续跑**，重跑从第 1 只开始，而且热缓存
    也省不掉每票一次联网往返（README 实测：热 0.51s/票 vs 冷 0.9–2.5s，
    全量热缓存仍是 1048 秒）。所以这句话必须说清"重跑是从头开始"。
    """
    if status not in (STOPPED, FAILED) or p.outputs:
        return ""
    return ("本次**没有产物**：结果整轮跑完才落盘，上面那些计数没有写进任何文件。"
            "页尾/「今日信号」页看到的是上一次的结果（注意标题里的日期）。"
            "重跑是从第 1 只开始，不接着跑——缓存省的是流量不是时间。")
