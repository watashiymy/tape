from datetime import date, datetime, time

import pytest

from quant.market import session
from quant.market.session import Calendar, State, TradeDay

WED = date(2026, 9, 23)          # 周三，交易日
SAT = date(2026, 9, 26)
CAL = Calendar(first=date(2026, 9, 1), last=date(2026, 10, 31),
               trading=frozenset({date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23),
                                  date(2026, 9, 24), date(2026, 9, 28)}))


def at(h, m, s=0, day=WED):
    return datetime.combine(day, time(h, m, s))


# ---------------------------------------------------------------- 三态判定链（§4.1）
@pytest.mark.parametrize("today,cal,ok,src,now_t,expect", [
    (SAT, None, True, SAT, time(10, 0), TradeDay.NON_TRADING),          # 周末一律休市
    (date(2026, 9, 25), CAL, True, WED, time(10, 0), TradeDay.NON_TRADING),  # 日历说中秋休市
    (WED, CAL, False, None, time(10, 0), TradeDay.TRADING),              # 日历说交易日，请求失败也算
    (WED, None, True, WED, time(9, 20), TradeDay.TRADING),               # 源日期 = 今天 → 交易日
    (WED, None, True, date(2026, 9, 22), time(9, 20), TradeDay.UNKNOWN), # 截止前源日期还旧 → 未知
    (WED, None, True, date(2026, 9, 22), time(9, 46), TradeDay.NON_TRADING),  # 过截止仍旧 → 非交易日
    (WED, None, False, None, time(9, 46), TradeDay.UNKNOWN),             # 请求失败永不计入
    (date(2026, 11, 5), CAL, True, None, time(9, 46), TradeDay.UNKNOWN), # 日历不覆盖 + 源失败
])
def test_trade_day_chain(today, cal, ok, src, now_t, expect):
    assert session.trade_day(today, cal, ok, src, now_t) is expect


def test_calendar_covers_only_its_own_range():
    assert CAL.covers(date(2026, 9, 25)) and not CAL.covers(date(2026, 11, 5))
    assert CAL.is_trading(WED) and not CAL.is_trading(date(2026, 9, 25))


# ---------------------------------------------------------------- 状态表（§4.2）
@pytest.mark.parametrize("now,td,expect,interval,ttl", [
    (at(10, 0, day=SAT), TradeDay.NON_TRADING, State.CLOSED_DAY, 60, 600),
    (at(8, 50), TradeDay.TRADING, State.PRE_OPEN, 60, 600),
    (at(9, 14), TradeDay.TRADING, State.AUCTION, 30, 25),
    (at(9, 26), TradeDay.TRADING, State.WAIT_OPEN, 30, 25),
    (at(9, 30), TradeDay.TRADING, State.TRADING, 30, 25),
    (at(11, 30, 0), TradeDay.TRADING, State.LUNCH, 60, 600),
    (at(12, 59, 59), TradeDay.TRADING, State.LUNCH, 60, 600),
    (at(13, 0), TradeDay.TRADING, State.TRADING, 30, 25),
    (at(14, 59, 59), TradeDay.TRADING, State.TRADING, 30, 25),
    (at(15, 0), TradeDay.TRADING, State.CLOSED, 60, 25),          # 收盘到截止：仍取数
    (at(15, 29, 59), TradeDay.TRADING, State.CLOSED, 60, 25),
    (at(15, 30), TradeDay.TRADING, State.CLOSED, 60, 600),        # 截止后：长缓存
    (at(20, 0), TradeDay.TRADING, State.CLOSED, 60, 600),
    (at(10, 0), TradeDay.UNKNOWN, State.UNKNOWN, 30, 25),         # 未知：节奏按交易日行，状态是未知
    (at(20, 0), TradeDay.UNKNOWN, State.UNKNOWN, 60, 600),
])
def test_state_table(now, td, expect, interval, ttl):
    d = session.decide(now, td)
    assert (d.state, d.interval_s, d.cache_ttl) == (expect, interval, ttl)


def test_non_trading_day_overrides_every_clock_row():
    for h in (9, 10, 12, 14, 16):
        assert session.decide(at(h, 15, day=SAT), TradeDay.NON_TRADING).state is State.CLOSED_DAY


@pytest.mark.parametrize("a,b,changes", [
    (at(11, 29, 50), at(11, 30, 20), True),      # 交易中 → 午休
    (at(12, 59, 50), at(13, 0, 20), True),       # 午休 → 交易中
    (at(8, 59), at(9, 14, 30), True),            # 盘前 → 集合竞价
    (at(20, 0), at(9, 15, day=date(2026, 9, 24)), True),   # 昨晚开着的页今早醒来
    (at(10, 0), at(10, 0, 30), False),
])
def test_transitions_change_the_decision(a, b, changes):
    da, db = session.decide(a, TradeDay.TRADING), session.decide(b, TradeDay.TRADING)
    assert (da != db) is changes


# ---------------------------------------------------------------- 过期判据（§4.3）
def test_stale_by_last_minute_point():
    # 最后一点标 10:00（表示 09:59-10:00 那分钟，数据新到 09:59）：
    # 10:02:00 → 落后 180 秒，不算；10:02:01 → 181 秒 → 延迟
    assert session.is_stale(at(10, 2, 1), at(10, 0), None) is True
    assert session.is_stale(at(10, 2, 0), at(10, 0), None) is False


def test_stale_falls_back_to_quote_timestamp_when_no_minute():
    assert session.is_stale(at(10, 3, 0), None, at(10, 0, 59)) is True
    assert session.is_stale(at(10, 1, 0), None, at(10, 0, 0)) is False


def test_stale_direction_is_not_reversed():
    """数据时刻晚于现在（时钟不同步）绝不能判成延迟。"""
    assert session.is_stale(at(10, 0), at(10, 5), None) is False


def test_stale_last_point_from_a_previous_day_is_always_stale():
    """I-2：last_point 只带 time 时，跨交易日会被 now.date() 借尸还魂成「今天的点」，
    永远判不出过期。现在 last_point 是完整日期时刻，昨天收盘的点摆到今天早盘必须判延迟。"""
    yesterday_close = datetime.combine(WED, time(15, 0))
    next_day_935 = datetime.combine(date(2026, 9, 24), time(9, 35))
    assert session.is_stale(next_day_935, yesterday_close, None) is True


@pytest.mark.parametrize("now", [at(9, 20), at(9, 30, 30), at(12, 0), at(14, 58), at(15, 10)])
def test_stale_is_only_judged_in_continuous_auction(now):
    assert session.is_stale(now, at(9, 0), at(9, 0)) is False


def test_stale_quote_ts_boundary_at_exactly_120s():
    assert session.is_stale(at(10, 2, 0), None, at(10, 0, 0)) is False    # 恰好 120 秒：不算
    assert session.is_stale(at(10, 2, 1), None, at(10, 0, 0)) is True     # 121 秒：延迟


def test_in_continuous_auction_boundary_at_lunch_end():
    assert session.in_continuous_auction(time(13, 0)) is False
    assert session.in_continuous_auction(time(13, 1)) is True


def test_clock_row_boundary_at_auction_end():
    state, _, _ = session._clock_row(time(9, 25, 0))
    assert state is State.WAIT_OPEN


def test_trade_day_boundary_at_non_trading_cutoff():
    assert session.trade_day(WED, None, True, date(2026, 9, 22), time(9, 45, 0)) is TradeDay.NON_TRADING


def test_expected_trade_day_order():
    e = session.expected_trade_day
    assert e(WED, CAL, time(10, 0), None, today=WED) == WED                       # 本轮源日期优先
    assert e(None, CAL, time(10, 0), None, today=WED) == WED                      # 日历：今天已开盘
    assert e(None, CAL, time(9, 0), None, today=WED) == date(2026, 9, 22)         # 开盘前 → 上一交易日
    assert e(None, None, time(10, 0), date(2026, 9, 22), today=WED) == date(2026, 9, 22)   # 上一轮成功的
    assert e(None, None, time(10, 0), None, today=WED) is None


def test_expected_trade_day_falls_back_to_last_known_when_calendar_has_no_earlier_day():
    """日历覆盖今天、今天不是交易日，但日历首日就是今天——previous_trading_day 往前找不到任何
    交易日，只能返回 None。不能把 None 直接吐出去，得退到上一轮成功的日期（Minor 项）。"""
    cal = Calendar(first=WED, last=date(2026, 10, 31), trading=frozenset())
    last_known = date(2026, 9, 20)
    assert session.expected_trade_day(None, cal, time(10, 0), last_known, today=WED) == last_known
