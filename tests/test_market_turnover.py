from datetime import date, datetime, time

from quant.market import turnover
from quant.market.sources import DailyBar

D23, D24 = date(2026, 9, 23), date(2026, 9, 24)
BARS_SH = [DailyBar(D23, 83_413_286.30), DailyBar(D24, 90_000_000.00)]   # 万元
BARS_SZ = [DailyBar(D23, 93_084_355.37), DailyBar(D24, 95_000_000.00)]


def test_previous_full_day_is_the_last_bar_strictly_before_the_trade_date():
    y = turnover.previous_day(BARS_SH, BARS_SZ, D24)
    assert y.day == D23 and abs(y.amount_wan - (83_413_286.30 + 93_084_355.37)) < 1e-6


def test_previous_day_none_when_no_earlier_bar():
    assert turnover.previous_day(BARS_SH[1:], BARS_SZ[1:], D24) is None


def test_consistency_check_uses_a_1_wan_tolerance():
    ok = turnover.consistent(BARS_SH, BARS_SZ, D24, quote_sh_wan=90_000_000.6, quote_sz_wan=95_000_000.2)
    assert ok is True
    assert turnover.consistent(BARS_SH, BARS_SZ, D24, 90_000_002.0, 95_000_000.0) is False
    assert turnover.consistent(BARS_SH, BARS_SZ, date(2026, 9, 25), 1, 1) is None   # 当天那根不存在 → 没法检查


def test_settled_flag_is_monotonic():
    assert turnover.settled(datetime(2026, 9, 24, 15, 31), D24, D24) is True     # 过截止
    assert turnover.settled(datetime(2026, 9, 24, 10, 0), D24, D24) is False
    assert turnover.settled(datetime(2026, 9, 25, 8, 0), D24, date(2026, 9, 25)) is True  # 数据属昨天
    assert turnover.settled(datetime(2026, 9, 26, 10, 0), D24, date(2026, 9, 26)) is True  # 休市


def test_settled_boundary_at_exactly_close_cutoff():
    assert turnover.settled(datetime(2026, 9, 24, 15, 30, 0), D24, D24) is True


def test_intraday_wording_has_no_percentage():
    text = turnover.line(now=datetime(2026, 9, 24, 12, 0), today=D24, trade_date=D24,
                         current_wan=1.47e8, current_ts=time(11, 30),
                         prev=DailyBar(D23, 2.14e8), pct_ok=None)
    assert text == "两市成交额 1.47 万亿（截至 11:30） · 9 月 23 日全天 2.14 万亿"   # 「 · 」两侧都有空格
    assert "%" not in text


def test_auction_is_still_intraday_wording():
    text = turnover.line(datetime(2026, 9, 24, 9, 20), D24, D24, 3.0e6, time(9, 20),
                         DailyBar(D23, 2.14e8), None)
    assert "截至 09:20" in text and "%" not in text


def test_after_close_gives_percentage_only_when_check_passed():
    kw = dict(today=D24, trade_date=D24, current_wan=2.30e8, current_ts=time(15, 0),
              prev=DailyBar(D23, 2.00e8))
    with_pct = turnover.line(datetime(2026, 9, 24, 20, 0), pct_ok=True, **kw)
    assert with_pct == "两市成交额 9 月 24 日全天 2.30 万亿 · 9 月 23 日全天 2.00 万亿 · +15.0%"
    no_pct = turnover.line(datetime(2026, 9, 24, 15, 10), pct_ok=None, **kw)   # 截止前：检查还没做
    assert "%" not in no_pct and "全天 2.30 万亿" in no_pct
    assert "%" not in turnover.line(datetime(2026, 9, 24, 20, 0), pct_ok=False, **kw)


def test_holiday_shows_two_full_days_from_the_data_date():
    text = turnover.line(datetime(2026, 9, 25, 10, 0), date(2026, 9, 25), D24, 2.30e8, time(15, 0),
                         DailyBar(D23, 2.00e8), True)
    assert text.startswith("两市成交额 9 月 24 日全天 2.30 万亿")


def test_missing_previous_day_shows_only_the_current_value():
    assert turnover.line(datetime(2026, 9, 24, 12, 0), D24, D24, 1.47e8, time(11, 30), None, None) \
        == "两市成交额 1.47 万亿（截至 11:30）"
