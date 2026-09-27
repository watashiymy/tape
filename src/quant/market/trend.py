"""首页「趋势宽度」按钮区的状态判定（spec §5.4，按顺序命中即停）。纯函数。"""
from __future__ import annotations

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
                 now: datetime, ready: time) -> list[date] | None:
    """日历里截至「最近一个已结束交易日」的交易日 − 已有 − 永久缺口。日历不覆盖 → None（未知）。"""
    today = now.date()
    if calendar is None or not calendar.covers(today):
        return None
    last_done = today if (calendar.is_trading(today) and now.time() >= ready) \
        else calendar.previous_trading_day(today)
    have = set(snapshot_dates)
    return sorted(d for d in calendar.trading if d <= last_done and d not in have and d not in permanent)


def button_state(*, state_broken: bool, running: bool, busy_other: bool, last_failed: bool,
                 snapshot_dates: list[date], calendar: Calendar | None, permanent: set[date],
                 now: datetime, ready: time) -> ButtonState:
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
    missing = missing_days(snapshot_dates, calendar, permanent, now, ready)
    if missing is None:
        return ButtonState.UPDATE          # 不知道缺不缺 → 给按钮，让任务自己判
    if missing:
        return ButtonState.UPDATE
    today = now.date()
    if calendar.is_trading(today) and now.time() < ready and today not in set(snapshot_dates):
        return ButtonState.NOT_READY
    return ButtonState.UP_TO_DATE
