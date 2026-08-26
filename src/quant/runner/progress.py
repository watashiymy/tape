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

_TRACEBACK = "Traceback (most recent call last):"

# run_market_scan.py
_SCAN_HEADER = re.compile(r"^基准日 \S+，扫描池 (\d+) 只", re.M)
_SCAN_PROGRESS = re.compile(
    r"^\[(\d+)/(\d+)\] 信号 (\d+) 条，失败 (\d+) 只，耗时 (\d+)s$", re.M)
# run_daily_signal.py
_STALE = re.compile(r"^全部标的数据均未更新到 (\S+?)，", re.M)
_SIGNAL_HEADER = re.compile(r"^===== \S+ 信号 =====", re.M)
# run_backtest.py
_DATA_LINE = re.compile(r"^\[data\] \S+: \d+ 根K线", re.M)
_STRATEGY_HEADER = re.compile(r"^===== (\S+) =====$", re.M)
_REPORT_DIR = re.compile(r"^\s*报告目录: (\S+)$", re.M)
# 共用：两个信号脚本的收尾行
_SAVED = re.compile(r"^已保存: (\S+)$", re.M)


@dataclass(frozen=True)
class Progress:
    """current/total 缺一不可才谈得上百分比与 ETA；缺了就是不确定态（None）。"""
    current: int | None = None
    total: int | None = None
    elapsed_s: float | None = None
    eta_s: float | None = None
    phase: str = STARTING
    extras: dict[str, str] = field(default_factory=dict)


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


def _terminal(text: str, saved: re.Match | None, saved_key: str) -> tuple[str, dict] | None:
    """收尾态判定。traceback 优先于收尾行，且不比较两者的先后位置。

    三个脚本的 traceback 只会从顶层逃逸（单票失败是被 catch 计数的），出现即崩溃；
    而重定向到文件时 stdout 是块缓冲、stderr 不缓冲，崩溃前 print 的"已保存:"完全
    可能被冲刷到 traceback **之后**——按位置判先后会把崩溃读成完成。
    """
    if _TRACEBACK in text:
        return CRASHED, {"错误": _last_nonempty(text)}
    if saved is not None:
        return DONE, {saved_key: saved.group(1)}
    return None


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
    term = _terminal(log_text, _SAVED.search(log_text), "输出")
    if term:
        phase, term_extras = term
        extras |= term_extras
    return Progress(current=current, total=total, elapsed_s=elapsed,
                    eta_s=_eta(current, total, elapsed), phase=phase, extras=extras)


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
    extras: dict[str, str] = {}
    term = _terminal(log_text, _SAVED.search(log_text), "输出")
    if term:
        phase, extras = term
    return Progress(current=current, phase=phase, extras=extras)


def parse_backtest(log_text: str, total: int | None = None) -> Progress:
    """回测：`[data] …` 行计数当 current；total 只能由调用方给（日志里没有）。

    日志没有任何耗时字段，故 elapsed/ETA 一律 None——不许编。
    """
    current = len(_DATA_LINE.findall(log_text)) or None
    phase = STARTING
    if log_text.strip():
        phase = "取数中"
    strat = _STRATEGY_HEADER.findall(log_text)
    if strat:
        phase = f"回测中: {strat[-1]}"
    extras: dict[str, str] = {}
    term = _terminal(log_text, _REPORT_DIR.search(log_text), "报告目录")
    if term:
        phase, extras = term
    return Progress(current=current, total=total, phase=phase, extras=extras)


def tail(log_text: str, n: int = 30) -> str:
    """日志尾部 n 行（含正在写的半行——面板要看的就是这一行）。"""
    if not log_text or n <= 0:
        return ""
    return "\n".join(log_text.splitlines()[-n:])
