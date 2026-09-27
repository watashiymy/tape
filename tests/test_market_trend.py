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
