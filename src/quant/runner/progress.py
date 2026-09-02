"""日志 → 进度（v0.2.0 设计 §3.2）。纯函数：只吃日志文本，不碰文件、不碰进程、不联网。

正则一律对着三个脚本的**实际 print 语句**写（见 scripts/run_*.py），并用真实日志片段
（tests/fixtures/*.log）做回归。半行、空日志、traceback 都必须能安全落到"不确定态"，
不确定就交白卷——面板宁可转圈显示已用时长，也不能显示编出来的进度和 ETA。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

STARTING = "启动中"
DONE = "完成"
CRASHED = "异常退出"

TRACEBACK = "Traceback (most recent call last):"    # 崩溃痕迹，process.py 的僵尸清理共用

# run_market_scan.py
_SCAN_HEADER = re.compile(r"^基准日 \S+，扫描池 (\d+) 只", re.M)
_SCAN_PROGRESS = re.compile(
    r"^\[(\d+)/(\d+)\] 信号 (\d+) 条，失败 (\d+) 只，耗时 (\d+)s$", re.M)
# 拉全市场清单那 2–4 分钟（约每 7 天一次）。之前这段时间主状态一直写「启动中」，
# 没有任何东西告诉用户在等什么——而它恰好是整趟里最长的一段静默。
# 三条入口（首次 / 过期 / --refresh-symbols）都以「拉取全市场清单…」收尾，一条正则够。
_FETCH_LISTING = re.compile(r"拉取全市场清单…$", re.M)
_GOT_LISTING = re.compile(r"^已拉取全市场清单", re.M)
# run_daily_signal.py
_STALE = re.compile(r"^全部标的数据均未更新到 (\S+?)，", re.M)
_SIGNAL_HEADER = re.compile(r"^===== \S+ 信号 =====", re.M)
# run_backtest.py
_DATA_LINE = re.compile(r"^\[data\] \S+: \d+ 根K线", re.M)
_STRATEGY_HEADER = re.compile(r"^===== (\S+) =====$", re.M)
# 每个策略一条，**打在 per-strategy 循环内部**：默认配置两个策略就有两行，
# 第一行落盘时下一个策略还没开始算。故只能 findall 全取，且它**不是**完成标记。
_REPORT_DIR = re.compile(r"^\s*报告目录: (\S+)$", re.M)
# 循环外只打一次的整轮完成标记（run_backtest.py 收尾行）。
_ALL_DONE = re.compile(r"^全部完成: \d+ 个策略$", re.M)
# 共用：两个信号脚本的收尾行
_SAVED = re.compile(r"^已保存: (\S+)$", re.M)


@dataclass(frozen=True)
class Progress:
    """current/total 缺一不可才谈得上百分比与 ETA；缺了就是不确定态（None）。

    extras 是给人看的（面板直接铺开显示）；outputs 是给面板**读产物**用的机器可读清单：
    扫描/信号 = CSV 路径，回测 = 每个策略一个报告目录（默认配置就是两个，不能只留一个）。
    """
    current: int | None = None
    total: int | None = None
    elapsed_s: float | None = None
    eta_s: float | None = None
    phase: str = STARTING
    extras: dict[str, str] = field(default_factory=dict)
    outputs: tuple[str, ...] = ()


def _eta(current: int | None, total: int | None, elapsed: float | None) -> float | None:
    """线性外推。三者缺一、或 current 为 0（除零）、或 total 反常小于 current 时一律不给。"""
    if not current or total is None or elapsed is None or total < current:
        return None
    return elapsed / current * (total - current)


def _last_nonempty(text: str) -> str:
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()
    return ""


def _crash(text: str) -> dict | None:
    """崩溃判定。traceback 优先于收尾行，且不比较两者的先后位置。

    三个脚本的 traceback 只会从顶层逃逸（单票失败是被 catch 计数的），出现即崩溃；
    而重定向到文件时 stdout 是块缓冲、stderr 不缓冲，崩溃前 print 的"已保存:"完全
    可能被冲刷到 traceback **之后**——按位置判先后会把崩溃读成完成。
    """
    if TRACEBACK in text:
        return {"错误": _last_nonempty(text)}
    return None


def _saved_terminal(text: str, phase: str,
                    extras: dict[str, str]) -> tuple[str, dict[str, str], tuple[str, ...]]:
    """两个信号脚本共用的收尾：`已保存: <csv>` → 完成 + 输出路径，崩溃压倒完成态。

    收尾态一律**并入**已有 extras（`|=`）而不是顶掉：顶掉的话完成时
    "信号 58 条 / 失败 0 只"这类进度计数会凭空消失。
    """
    outputs: tuple[str, ...] = ()
    saved = _SAVED.search(text)
    if saved:
        phase, outputs = DONE, (saved.group(1),)
        extras |= {"输出": saved.group(1)}
    crash = _crash(text)
    if crash:
        phase = CRASHED         # 崩溃优先，但已解析到的进度与产物都留着
        extras |= crash
    return phase, extras, outputs


def parse_market_scan(log_text: str) -> Progress:
    """全市场扫描：表头给 total，`[i/total] … 耗时 Ns` 给 current/elapsed/extras。"""
    total = current = elapsed = None
    extras: dict[str, str] = {}
    header = _SCAN_HEADER.search(log_text)
    if header:
        total = int(header.group(1))
    lines = _SCAN_PROGRESS.findall(log_text)
    if lines:
        cur, tot, sigs, fails, secs = lines[-1]      # 必须取最后一条
        current, total, elapsed = int(cur), int(tot), float(secs)
        extras = {"信号": f"{sigs} 条", "失败": f"{fails} 只"}
    phase = "扫描中" if (total is not None or current is not None) else STARTING
    # 拉清单的中间态：开始拉了、还没拉到 → 说清在等什么。
    # **刻意不给 current/total**，所以不会凭空造出百分比或 ETA（同 parse_daily_signal
    # 对不确定态的处理）。分钟数不在这里写死：那个值的唯一出处是
    # guide.FACTS["pool_fetch"]，而本模块是纯函数层、不许 import app。
    if _FETCH_LISTING.search(log_text) and not _GOT_LISTING.search(log_text):
        phase = "拉取全市场清单（首次或过期时才有，比扫描本身慢）"
    phase, extras, outputs = _saved_terminal(log_text, phase, extras)
    return Progress(current=current, total=total, elapsed_s=elapsed,
                    eta_s=_eta(current, total, elapsed), phase=phase, extras=extras,
                    outputs=outputs)


def parse_daily_signal(log_text: str) -> Progress:
    """每日信号（固定池、秒级）：没有可靠的进度行，靠阶段文字。

    current 按 `[data] …` 行计数——run_backtest.py 打这种行，run_daily_signal.py
    目前不打（它只在异常时打 `[warn]`），所以这里现实中恒为 None，属不确定态。
    total 无从得知（`===== X 信号 =====（扫描 N 只 …）` 是取数**之后**才打的，
    当分母毫无意义），故一律 None。
    """
    current = len(_DATA_LINE.findall(log_text)) or None
    phase = STARTING
    if log_text.strip():
        phase = "取数中"
    if _SIGNAL_HEADER.search(log_text):
        phase = "汇总信号"
    stale = _STALE.search(log_text)
    if stale:
        phase = f"数据未更新到 {stale.group(1)}（收盘后 17:30 起才有当日数据）"
    phase, extras, outputs = _saved_terminal(log_text, phase, {})
    return Progress(current=current, phase=phase, extras=extras, outputs=outputs)


def parse_backtest(log_text: str, total: int | None = None) -> Progress:
    """回测：`[data] …` 行计数当 current；total 只能由调用方给（日志里没有）。

    日志没有任何耗时字段，故 elapsed/ETA 一律 None——不许编。

    **完成判定只认循环外那一行"全部完成: N 个策略"**。"报告目录:" 打在 per-strategy
    循环内部，配置默认 ma_cross + donchian 两个策略，第一条落盘时 donchian 才刚要开始算
    （而且那一段整段静默，"标记在日志尾部"也区分不出来）——拿它判完成会：
    1) 回测跑到一半面板就显示"完成"；2) 只留第一个报告目录，donchian 的产物直接丢掉。
    """
    current = len(_DATA_LINE.findall(log_text)) or None
    phase = STARTING
    if log_text.strip():
        phase = "取数中"
    strat = _STRATEGY_HEADER.findall(log_text)
    if strat:
        phase = f"回测中: {strat[-1]}"
    extras: dict[str, str] = {}
    outputs = tuple(_REPORT_DIR.findall(log_text))    # 每个策略一个，全取
    if outputs:
        extras["报告目录"] = "、".join(outputs)
    if _ALL_DONE.search(log_text):
        phase = DONE
    crash = _crash(log_text)
    if crash:
        phase = CRASHED
        extras |= crash
    return Progress(current=current, total=total, phase=phase, extras=extras,
                    outputs=outputs)


def tail(log_text: str, n: int = 30) -> str:
    """日志尾部 n 行（含正在写的半行——面板要看的就是这一行）。"""
    if not log_text or n <= 0:
        return ""
    return "\n".join(log_text.splitlines()[-n:])
