"""首页「趋势宽度」的判定（spec §5.4）：按钮区状态（按顺序命中即停）与分母为 0 的卡片
该写「还差约 N 天」还是「还需约 N 个交易日」。纯函数。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from enum import Enum

from quant.market.session import Calendar


class ButtonState(Enum):
    BROKEN = "broken"            # 任务状态文件损坏 → 报错、不给按钮
    RUNNING = "running"          # 进度 + 停止
    BUSY_OTHER = "busy_other"    # 有别的任务在跑
    FAILED_START = "failed_start"    # 上次失败且 0 份快照 → 错误 + 「开始准备」
    FAILED_UPDATE = "failed_update"  # 上次失败 → 错误 + 「更新收盘数据」
    NEVER = "never"              # 从没准备过 → 说明 + 「开始准备」
    UPDATE = "update"            # 缺日子且可补
    NOT_READY = "not_ready"      # 交易日、就绪前、已补到上一交易日
    UP_TO_DATE = "up_to_date"    # 什么都不显示（日期在标题旁）


def missing_days(snapshot_dates: list[date], calendar: Calendar | None, permanent: set[date],
                 now: datetime, ready: time, window: int) -> list[date] | None:
    """日历里截至「最近一个已结束交易日」的交易日 − 已有 − 永久缺口。日历不覆盖 → None（未知）。
    日历里找不到已结束的交易日（日历首日就是当前日期且未到就绪时刻，或首日之后还没开过市）→ 什么都不缺。
    只看最近 `window` 个交易日（与 run_market_breadth 补数时留的窗口一致）——窗口之前的日子
    补数任务本来就不会去补，日历比窗口更早不算缺。"""
    today = now.date()
    if calendar is None or not calendar.covers(today):
        return None
    last_done = today if (calendar.is_trading(today) and now.time() >= ready) \
        else calendar.previous_trading_day(today)
    if last_done is None:
        return []
    have = set(snapshot_dates)
    in_window = sorted(d for d in calendar.trading if d <= last_done)[-window:]
    return sorted(d for d in in_window if d not in have and d not in permanent)


def button_state(*, state_broken: bool, running: bool, busy_other: bool, last_failed: bool,
                 snapshot_dates: list[date], calendar: Calendar | None, permanent: set[date],
                 now: datetime, ready: time, window: int) -> ButtonState:
    if state_broken:
        return ButtonState.BROKEN
    if running:
        return ButtonState.RUNNING
    if busy_other:
        return ButtonState.BUSY_OTHER
    if last_failed:
        return ButtonState.FAILED_START if not snapshot_dates else ButtonState.FAILED_UPDATE
    if not snapshot_dates:
        return ButtonState.NEVER
    missing = missing_days(snapshot_dates, calendar, permanent, now, ready, window)
    if missing is None:
        return ButtonState.UPDATE          # 不知道缺不缺 → 给按钮，让任务自己判
    if missing:
        return ButtonState.UPDATE
    today = now.date()
    if calendar.is_trading(today) and now.time() < ready and today not in set(snapshot_dates):
        return ButtonState.NOT_READY
    return ButtonState.UP_TO_DATE


@dataclass(frozen=True)
class Shortfall:
    """分母为 0 的卡片缺什么。页面按 kind 选文案，只做映射。"""
    kind: str                          # "short"：补得上；"gap"：回看期里有补不上的永久缺口
    count: int                         # short：还差约 N 天；gap：还需约 N 个交易日
    gap_days: tuple[date, ...] = ()    # gap：落在这张卡回看期里的永久缺口，升序


def shortfall(n: int, asof: date, calendar: Calendar | None, have: set[date],
              permanent: set[date]) -> Shortfall:
    """按**这张卡自己的回看期**算（spec §5.4）：`recent` = 日历里截至 asof（含）的最近 n 个交易日。

    - 回看期里有永久缺口 → 要等窗口滚过最新的那个：还需 n −（它之后已有快照的天数）个交易日；
    - 否则 → 回看期里缺几天、加上日历起点之前还差的天数，至少 1；
    - 没有日历、或日历不覆盖 asof → 只能数快照：n −（asof 及之前的快照数），至少 1。"""
    if calendar is None or not calendar.covers(asof):
        return Shortfall("short", max(1, n - len({d for d in have if d <= asof})))
    recent = sorted(d for d in calendar.trading if d <= asof)[-n:]
    gaps = tuple(d for d in recent if d in permanent)
    if gaps:
        after = sum(1 for d in recent if d > gaps[-1] and d in have)
        return Shortfall("gap", n - after, gaps)
    missing = sum(1 for d in recent if d not in have) + max(0, n - len(recent))
    return Shortfall("short", max(1, missing))
