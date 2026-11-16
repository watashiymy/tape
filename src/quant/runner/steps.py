"""每日流水线三步的「就绪状态」文案（v0.5.0 设计 §6）。

控制台把三件事摆成一条链：

    ① 全市场扫描（发现） → ② 加进信号池（人工） → ③ 信号跟踪（每天跟踪）
    文案面向使用者：说「信号池」不说「固定池 / 池子」，说「报出 N 条信号」不说「报了 N 条」。

每一步顶上一行小字回答同一个问题：**这一步现在是什么状态，我该不该做它。**

判断与文案放在这里而不是页面里，是为了能单测：这几句话里全是数字（扫了多少只、
池子里几只、最新信号是哪天），而本项目对"页面上的数字"只有一条纪律——
**宁可说不知道，也不许猜**。所以每个函数都有一条"什么都没有"的分支，
返回的是"还没跑过"而不是零、空字符串或今天的日期。

纯函数、不碰 streamlit、不发起任何 IO 以外的动作；调用方负责把路径传进来。
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from quant.signal import scan_meta


@dataclass(frozen=True)
class StepStatus:
    """一步的就绪状态。

    `ready` 只影响措辞与配色，**不做任何闸门**：用户明确要求「不一定在功能上
    做严格的限制」，而且这三步本来就允许乱序跑（补扫历史某天、只想看信号）。
    """
    text: str
    ready: bool


UNKNOWN = "还没跑过"


def scan_status(scan_dir: Path | str, today: date | None = None) -> StepStatus:
    """① 全市场扫描：最近一份产物是哪天、什么范围、几条信号。

    直接复用 `scan_meta.scope_badge` 的说法（含 v0.5.0 起的失败数），
    页面上两处提到同一次扫描时不会各说一套。
    """
    latest = scan_meta.latest_scan(Path(scan_dir))
    if latest is None:
        return StepStatus(f"{UNKNOWN}——信号池之外的机会还没扫过", ready=False)
    day = scan_meta.scan_day(latest)
    meta = scan_meta.load_meta(latest)
    scope, _ = scan_meta.scope_badge(meta)
    # 用逗号而不是括号包 scope：scope 在有取数失败时自带一层括号
    # （「试跑 100 只（7 只取数失败）」），再套一层就成了双层括号。
    if meta is None:
        return StepStatus(f"最近一次 {day}，{scope}", ready=True)
    fresh = today is not None and day == today.isoformat()
    tail = "" if fresh else "——不是今天的"
    return StepStatus(f"最近一次 {day}，{scope}，报出 {meta.signals} 条信号{tail}", ready=True)


def pool_status(symbols: tuple[str, ...] | None) -> StepStatus:
    """② 信号池：现在盯着几只。

    `symbols` 为 None 表示**读不出来**（配置坏了），与"池子是空的"必须分开说：
    前者要去修文件，后者要去加票，给错提示等于把人支到另一个方向。
    池子来自哪个文件不再写在这里（2026-09-05）：那是实现细节，使用者关心的只是
    "现在盯着几只"；坏文件的名字由「信号池」页的错误信息指出。
    """
    if symbols is None:
        return StepStatus("读不到信号池配置——去「信号池」页看具体原因", ready=False)
    if not symbols:
        return StepStatus("信号池是空的——没有任何标的会被跟踪", ready=False)
    return StepStatus(f"正在跟踪 {len(symbols)} 只", ready=True)


def signal_status(signal_dir: Path | str, today: date | None = None) -> StepStatus:
    """③ 信号跟踪：最新一份信号清单是哪天的。

    **刻意只说日期，不说"今天有没有信号"**：没有产物与「今天确实没有新信号」
    是两件事，而后者是绝大多数日子的正常结果（说明页里那句话）。
    把两者混成一句"今日无信号"，正是本项目一路在防的那类误导。
    """
    # 只认**文件名就是交易日**的产物（run_daily_signal.py 落的就是 <date>.csv）。
    # 认不出日期的一律不算：把一个随手放进来的文件名当日期显示在页面上，
    # 就是本模块开头那条纪律要防的"编出来的数字"。
    # 判据用 scan_meta.is_day（三处共用一份，见那个函数的 docstring）。
    d = Path(signal_dir)
    files = sorted((p for p in d.glob("*.csv") if scan_meta.is_day(p)),
                   key=lambda p: p.stem, reverse=True) if d.is_dir() else []
    if not files:
        return StepStatus(f"{UNKNOWN}——信号池里的买卖信号还没人盯", ready=False)
    day = files[0].stem
    fresh = today is not None and day == today.isoformat()
    return StepStatus(f"最新一份是 {day}" + ("" if fresh else "——不是今天的"), ready=True)


def backtest_status(runs: Sequence[str]) -> StepStatus:
    """回测：最近一次是哪个策略、什么时候跑的，一共攒了几次（2026-11-16 审查后）。

    `runs` 是从新到旧的**显示名**（「策略 时间」，页面用 fmt.run_label 从结果目录名换出来）。
    回测不在每日那条链上，没有"就绪"可言，但控制台三张卡片标题下都有一行「最新结果」：
    它原先只在"面板没起过回测"时写一句"还没跑过"——磁盘上明明攒着几十次结果也这么说
    （命令行跑的、或状态文件被清过），而面板跑过一次之后那一行又整个消失。
    现在与另两张卡片同一个口径：按**结果**说话，不按"面板起没起过它"说话。
    """
    if not runs:
        return StepStatus(f"{UNKNOWN}——还没有任何回测结果", ready=False)
    return StepStatus(f"最近一次：{runs[0]}，共 {len(runs)} 次", ready=True)
