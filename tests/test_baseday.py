# 「基准交易日」两件小事（v0.5.0，src/quant/signal/baseday.py），两个信号脚本共用。
# 抽出来的理由就是"别让两份日历逻辑各自演化"，所以这里只测这一份。
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from quant.signal import baseday          # noqa: E402


class FakeProvider:
    """只回答交易日历。`days` 是全部交易日；get_trade_calendar 按区间筛。"""
    def __init__(self, days):
        self.days = sorted(days)
        self.calls: list[tuple[date, date]] = []

    def get_trade_calendar(self, start, end):
        self.calls.append((start, end))
        return [d for d in self.days if start <= d <= end]


FRI, SAT, SUN, MON = date(2026, 9, 4), date(2026, 9, 5), date(2026, 9, 6), date(2026, 9, 7)


def test_latest_trading_day_is_today_when_today_trades():
    p = FakeProvider([date(2026, 9, 3), FRI])
    assert baseday.latest_trading_day(p, today=FRI) == FRI


def test_latest_trading_day_falls_back_over_a_weekend():
    """周末/长假跑：退到上一个交易日，而不是报错——补跑上一交易日是真实用法。"""
    p = FakeProvider([date(2026, 9, 3), FRI])
    assert baseday.latest_trading_day(p, today=SUN) == FRI
    assert baseday.latest_trading_day(p, today=SAT) == FRI


def test_latest_trading_day_raises_when_the_calendar_is_empty():
    """日历空了（断网、数据源抽风）要响亮，不许静默拿 today 顶包。"""
    with pytest.raises(baseday.CalendarError):
        baseday.latest_trading_day(FakeProvider([]), today=FRI)


def test_lookback_covers_the_spring_festival_break():
    """窗口要盖得住春节（最长连休 9 天）加两个周末，否则节后第一天跑就是"日历异常"。"""
    assert baseday.LOOKBACK_DAYS >= 14
    p = FakeProvider([date(2026, 9, 4)])
    baseday.latest_trading_day(p, today=date(2026, 9, 18))
    start, end = p.calls[-1]
    assert (end - start).days == baseday.LOOKBACK_DAYS


def test_require_trading_day_accepts_a_trading_day_and_rejects_a_weekend():
    p = FakeProvider([FRI, MON])
    baseday.require_trading_day(p, FRI)                # 不抛
    with pytest.raises(baseday.NotTradingDay) as e:
        baseday.require_trading_day(p, SAT)
    msg = str(e.value)
    assert "2026-09-05" in msg and "不是交易日" in msg
    assert "--date" in msg, "报错要给可执行的下一步（不带 --date 会自动取最近交易日）"


def test_weekday_note_only_speaks_when_today_is_not_a_trading_day():
    assert baseday.weekday_note(FRI, today=FRI) is None
    note = baseday.weekday_note(FRI, today=SUN)
    assert note and "2026-09-06" in note and "2026-09-04" in note
