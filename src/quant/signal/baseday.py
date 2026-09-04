"""「基准交易日」的两件小事，两个信号脚本共用（v0.5.0）。

- `latest_trading_day`：不带 `--date` 时该按哪天算——最近一个交易日（含今天）。
- `require_trading_day`：`--date` 是裸的 `date.fromisoformat`，写个周六/春节照样解析成功，
  必须先查一次日历再开始长跑。日期填错这种事要在几百毫秒内知道，不是两小时后。

抽到这里而不是留在 run_market_scan.py 里：run_daily_signal.py 也要 `--date`，
两份复制的日历逻辑迟早给出不同答案（一处改了窗口、另一处没改）。

这里**抛异常不 sys.exit**：src/ 下的模块不该替脚本决定进程怎么结束，
脚本各自 `except … sys.exit(str(e))`，终端上看到的那句话一字不变。
"""
from __future__ import annotations

from datetime import date, timedelta

#: 往回找最近交易日的窗口。21 个自然日盖得住春节（最长连休 9 天）加两个周末。
LOOKBACK_DAYS = 21


class CalendarError(ValueError):
    """交易日历取不到或不合理（断网、数据源抽风）。"""


class NotTradingDay(ValueError):
    """指定的基准日不是交易日。"""


def latest_trading_day(provider, today: date | None = None) -> date:
    """最近一个交易日（含今天）。今天是周末/节假日就退到上一个交易日。"""
    today = today or date.today()
    cal = provider.get_trade_calendar(today - timedelta(days=LOOKBACK_DAYS), today)
    if not cal:
        raise CalendarError(f"近 {LOOKBACK_DAYS} 天无交易日？交易日历异常，退出")
    return cal[-1]


def require_trading_day(provider, day: date) -> None:
    """`day` 必须真是交易日，否则抛 NotTradingDay（文案含可执行的下一步）。"""
    if day not in provider.get_trade_calendar(day, day):
        raise NotTradingDay(
            f"{day}（周{'一二三四五六日'[day.weekday()]}）不是交易日，"
            f"没有当日行情可算；换个交易日重跑（不带 --date 会自动取最近交易日）")


def weekday_note(day: date, today: date | None = None) -> str | None:
    """今天不是交易日时提醒一句；是交易日返回 None（调用方据此决定要不要打印）。"""
    today = today or date.today()
    if day == today:
        return None
    return f"[注意] 今天 {today} 非交易日，基准为最近交易日 {day}"
