"""三个任务的定义与参数白名单（v0.2.0 设计 §3.3）。

面板能在本机起进程，这个模块就是**唯一的闸门**：只认白名单里的开关，
每个值都当敌意输入校验，校验不过一律拒绝（不做转义、不做"清洗后放行"）。
argv 全程是列表，`shell=False`（见 process.start），根本不存在 shell 解析这一步——
即便某个值漏了校验，`; rm -rf /` 也只会作为一个普通参数传给 argparse 然后报错。
`--config` 刻意不进 schema：暴露它等于开放任意路径读取。
"""
from __future__ import annotations

import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from quant.runner import progress
from quant.strategy import REGISTRY

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LIMIT_MAX = 10000


@dataclass(frozen=True)
class Param:
    name: str                       # 传给 build_argv 的键
    flag: str                       # 命令行开关
    kind: str                       # int | date | choice | flag
    label: str                      # 面板控件标题
    choices: tuple[str, ...] = ()   # kind=choice 的全部合法取值
    max_value: int | None = None    # kind=int 的上限


@dataclass(frozen=True)
class Job:
    name: str
    label: str
    script: str
    params: tuple[Param, ...]
    parser: Callable[..., progress.Progress]
    result_kind: str                # 面板完成后怎么渲染结果


def _bad(spec: Param, value, why: str) -> ValueError:
    return ValueError(f"{spec.label}（{spec.flag}）非法: {value!r} —— {why}")


def _as_int(spec: Param, value) -> str:
    """正整数且不超上限。bool 必须显式挡：isinstance(True, int) 为 True，
    放行会静默变成 --limit 1；字符串一概不收（哪怕全是数字），
    唯一进 argv 的整数来源只能是真 int。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _bad(spec, value, "必须是整数")
    if isinstance(value, float) and not value.is_integer():
        raise _bad(spec, value, "必须是整数")
    n = int(value)
    if n < 1:
        raise _bad(spec, value, "必须是正整数")
    if spec.max_value is not None and n > spec.max_value:
        raise _bad(spec, value, f"不得超过 {spec.max_value}")
    return str(n)


def _as_date(spec: Param, value) -> str:
    """正则 + fromisoformat 双重校验。

    只用 fromisoformat 不够：Python 3.11 起它认 "20260824" 这种紧凑格式，
    而脚本的 --date 只接受 YYYY-MM-DD；只用正则也不够：2026-13-45 能过正则。
    """
    if isinstance(value, datetime):     # datetime 是 date 的子类，必须先判
        value = value.date()
    if isinstance(value, date):
        value = value.isoformat()
    if not isinstance(value, str):
        raise _bad(spec, value, "必须是 YYYY-MM-DD 字符串或 date")
    if not _DATE_RE.fullmatch(value):
        raise _bad(spec, value, "必须形如 YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as e:
        raise _bad(spec, value, f"不是合法日期（{e}）") from e
    return value


def _as_choice(spec: Param, value) -> str:
    if not isinstance(value, str) or value not in spec.choices:
        raise _bad(spec, value, f"只能取 {list(spec.choices)}")
    return value


def _as_flag(spec: Param, value) -> bool:
    if not isinstance(value, bool):
        raise _bad(spec, value, "只能是 True/False")
    return value


_VALIDATORS = {"int": _as_int, "date": _as_date, "choice": _as_choice}

JOBS: dict[str, Job] = {
    "market_scan": Job(
        name="market_scan", label="全市场扫描",
        script=str(SCRIPTS / "run_market_scan.py"),
        params=(
            Param("limit", "--limit", "int", "只扫前 N 只（留空=全量）", max_value=LIMIT_MAX),
            Param("date", "--date", "date", "基准交易日（留空=最近交易日）"),
        ),
        parser=progress.parse_market_scan, result_kind="scan_csv"),
    "daily_signal": Job(
        name="daily_signal", label="每日信号",
        script=str(SCRIPTS / "run_daily_signal.py"),
        params=(),
        parser=progress.parse_daily_signal, result_kind="signal_csv"),
    "backtest": Job(
        name="backtest", label="回测",
        script=str(SCRIPTS / "run_backtest.py"),
        params=(
            Param("strategy", "--strategy", "choice", "策略（留空=全部）",
                  choices=tuple(REGISTRY)),
            Param("refresh", "--refresh", "flag", "强制全量刷新行情缓存"),
        ),
        parser=progress.parse_backtest, result_kind="backtest_run"),
}


def build_argv(job_name: str, params: dict | None = None) -> list[str]:
    """校验参数并拼出 argv 列表。任何不合法输入都抛 ValueError，绝不"尽力而为"。"""
    job = JOBS.get(job_name)
    if job is None:
        raise ValueError(f"未知任务: {job_name!r}，只能是 {list(JOBS)}")
    given = dict(params or {})
    specs = {p.name: p for p in job.params}
    unknown = sorted(set(given) - set(specs))
    if unknown:
        raise ValueError(
            f"{job.label}不接受这些参数: {unknown}（允许的只有 {list(specs) or '无'}）")
    # -u 不可省：stdout 重定向到日志文件时是块缓冲，实测起一次真实扫描 12 秒后
    # 日志文件仍然**一个字节都没有**（脚本正卡在 get_all_symbols，2-4 分钟），
    # 面板的"实时输出"会一直是空白；加 -u 后 login success! 立刻可见。
    argv = [sys.executable, "-u", job.script]   # sys.executable = 当前 .venv 解释器
    for spec in job.params:                 # 按 schema 顺序，argv 稳定可复现
        if spec.name not in given or given[spec.name] is None:
            continue
        value = given[spec.name]
        if spec.kind == "flag":
            if _as_flag(spec, value):
                argv.append(spec.flag)
            continue
        argv += [spec.flag, _VALIDATORS[spec.kind](spec, value)]
    return argv
