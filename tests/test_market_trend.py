from datetime import date, datetime, time

import pandas as pd
import pytest

from quant.market import trend
from quant.market.session import Calendar
from quant.market.trend import ButtonState as B

D = lambda s: date.fromisoformat(s)
CAL = Calendar(D("2026-09-01"), D("2026-10-31"),
               frozenset(D(f"2026-09-{d:02d}") for d in (21, 22, 23, 24, 28, 29, 30)))
READY = time(17, 30)


def _st(**kw):
    base = dict(state_broken=False, running=False, busy_other=False, last_failed=False,
                snapshot_dates=[], calendar=CAL, permanent=set(),
                now=datetime(2026, 9, 24, 20, 0), ready=READY)
    base.update(kw)
    return trend.button_state(**base)


def test_order_of_precedence():
    assert _st(state_broken=True, running=True) is B.BROKEN
    assert _st(running=True) is B.RUNNING
    assert _st(running=True, last_failed=True) is B.RUNNING  # 正在重跑的这一轮盖过上一轮失败
    assert _st(busy_other=True) is B.BUSY_OTHER
    assert _st(last_failed=True) is B.FAILED_START           # 0 份快照 + 上次失败 → 仍给「开始准备」
    assert _st(last_failed=True, snapshot_dates=[D("2026-09-23")]) is B.FAILED_UPDATE
    assert _st() is B.NEVER


def test_missing_days_and_up_to_date():
    have = [D(f"2026-09-{d:02d}") for d in (21, 22, 23)]
    assert _st(snapshot_dates=have) is B.UPDATE                        # 20:00 已过就绪 → 缺 9-24
    assert _st(snapshot_dates=have, now=datetime(2026, 9, 24, 16, 0)) is B.NOT_READY   # 交易日就绪前
    assert _st(snapshot_dates=have + [D("2026-09-24")]) is B.UP_TO_DATE
    assert _st(snapshot_dates=have + [D("2026-09-24")], now=datetime(2026, 9, 25, 10, 0)) is B.UP_TO_DATE  # 中秋
    assert _st(snapshot_dates=have, now=datetime(2026, 9, 25, 10, 0)) is B.UPDATE     # 休市日缺 9-24 照样可补


def test_permanent_gaps_do_not_count_as_missing():
    have = [D("2026-09-22"), D("2026-09-23"), D("2026-09-24")]
    assert _st(snapshot_dates=have, permanent={D("2026-09-21")}) is B.UP_TO_DATE
    assert _st(snapshot_dates=have) is B.UPDATE


def test_no_calendar_means_unknown_missing_days_but_still_offers_update():
    assert _st(snapshot_dates=[D("2026-09-23")], calendar=None) is B.UPDATE


def test_missing_days_before_any_trading_day_in_the_calendar_is_empty():
    """日历首日就是今天（或往前找不到交易日）且就绪前：没有「最近一个已结束交易日」，
    也就没有缺的日子——不许拿 None 去比日期炸掉。"""
    cal = Calendar(D("2026-09-24"), D("2026-10-31"), frozenset({D("2026-09-24"), D("2026-09-28")}))
    assert trend.missing_days([D("2026-09-24")], cal, set(), datetime(2026, 9, 24, 16, 0), READY) == []
    holiday_first = Calendar(D("2026-09-25"), D("2026-10-31"), frozenset({D("2026-09-28")}))
    assert trend.missing_days([], holiday_first, set(), datetime(2026, 9, 25, 10, 0), READY) == []
    assert _st(snapshot_dates=[D("2026-09-23")], calendar=cal,
               now=datetime(2026, 9, 24, 16, 0)) is B.NOT_READY


# ---------------------------------------------------------------- 卡片「—」写什么（spec §5.4）
def _weekdays(first: date, last: date) -> list[date]:
    out, d = [], first
    while d <= last:
        if d.weekday() < 5:
            out.append(d)
        d = date.fromordinal(d.toordinal() + 1)
    return out


WEEKDAYS = _weekdays(D("2025-06-02"), D("2026-10-30"))
BIG_CAL = Calendar(WEEKDAYS[0], WEEKDAYS[-1], frozenset(WEEKDAYS))
ASOF = D("2026-09-23")
UPTO = [d for d in WEEKDAYS if d <= ASOF]          # 截至 ASOF 的交易日，升序


@pytest.mark.parametrize("n,have,permanent,expect", [
    # 没有缺口、60 天里有 45 天 → 还差 15 天
    (60, set(UPTO[-45:]), set(), trend.Shortfall("short", 15)),
    # 20 日卡：ASOF 前第 10 个交易日是永久缺口 → 缺口之后有 10 天 → 还需 10 个交易日
    (20, set(UPTO[-40:]) - {UPTO[-11]}, {UPTO[-11]}, trend.Shortfall("gap", 10, (UPTO[-11],))),
    # 缺口在回看期之外（20 日卡看不到 25 个交易日前）→ 不算缺口，只数回看期里缺的
    (20, set(UPTO[-18:]), {UPTO[-25]}, trend.Shortfall("short", 2)),
    # 回看期里有两个缺口：从较新的那个之后数；两天都列出来
    (60, set(UPTO[-60:]) - {UPTO[-50], UPTO[-8]}, {UPTO[-50], UPTO[-8]},
     trend.Shortfall("gap", 53, (UPTO[-50], UPTO[-8]))),
    # 52 周卡（250 根）：有 200 天 → 还差 50 天
    (250, set(UPTO[-200:]), set(), trend.Shortfall("short", 50)),
    # 快照齐全但分母仍为 0（例如全是停牌）→ 至少写 1 天，不写「还差约 0 天」
    (20, set(UPTO[-20:]), set(), trend.Shortfall("short", 1)),
])
def test_shortfall_counts_the_cards_own_lookback(n, have, permanent, expect):
    assert trend.shortfall(n, ASOF, BIG_CAL, have, permanent) == expect


def test_shortfall_counts_trading_days_before_the_calendar_starts_as_missing():
    """日历只覆盖 100 个交易日、全都有快照：250 日卡还差 150 天（日历之前的日子也得补）。"""
    days = UPTO[-100:]
    short_cal = Calendar(days[0], D("2026-10-30"), frozenset(d for d in WEEKDAYS if d >= days[0]))
    assert trend.shortfall(250, ASOF, short_cal, set(days), set()) == trend.Shortfall("short", 150)


def test_shortfall_without_a_calendar_falls_back_to_counting_snapshots():
    have = set(UPTO[-45:]) | {D("2026-09-24")}           # asof 之后的快照不算
    assert trend.shortfall(60, ASOF, None, have, {UPTO[-10]}) == trend.Shortfall("short", 15)
    uncovered = Calendar(D("2026-10-01"), D("2026-10-30"), frozenset())
    assert trend.shortfall(60, ASOF, uncovered, have, set()) == trend.Shortfall("short", 15)
    assert trend.shortfall(20, ASOF, None, set(UPTO[-30:]), set()) == trend.Shortfall("short", 1)
