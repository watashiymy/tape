# tests/test_baostock_integration.py
from datetime import date

import pytest

from quant.data.baostock_provider import BaostockProvider

pytestmark = pytest.mark.network


def test_fetch_real_bars_and_calendar():
    with BaostockProvider() as p:
        df = p.get_daily_bars("600519", date(2024, 1, 1), date(2024, 1, 31))
        assert len(df) > 15
        assert {"open", "close", "adj_factor", "trade_status"} <= set(df.columns)
        assert (df["close"] > 0).all()
        cal = p.get_trade_calendar(date(2024, 1, 1), date(2024, 1, 31))
        assert len(cal) == len(df)  # 该月茅台无停牌，交易日数与K线行数一致
        idx = p.get_index_daily("000300", date(2024, 1, 1), date(2024, 1, 31))
        assert len(idx) == len(cal)


def test_large_range_crosses_pagination_boundary():
    """回归测试：baostock 0.9.3 的 get_data() 在翻页时用了 pandas 已删除的 DataFrame.append，
    首页恰好 2000 行就会抛 AttributeError。10 年日线约 2579 行必然触发，因此
    provider 必须走 _fetch() 的逐行迭代。区间务必 > 2000 行，否则测不到这个分支。"""
    with BaostockProvider() as p:
        df = p.get_daily_bars("600519", date(2016, 1, 1), date(2026, 8, 14))
        assert len(df) > 2000, f"区间太小测不到翻页分支（{len(df)} 行）"
        assert df.index.is_monotonic_increasing
        assert not df.index.has_duplicates
        assert df["adj_factor"].notna().all()
        cal = p.get_trade_calendar(date(2016, 1, 1), date(2026, 8, 14))
        assert len(cal) > 2000


def test_get_all_symbols_mainboard_universe():
    """v0.1.1 扫描池：>1000 只、全部主板前缀（60*/00*）、无 ST 名、无重复。
    2026-08-21 为已收盘交易日，query_all_stock 对历史交易日结果稳定。"""
    with BaostockProvider() as p:
        df = p.get_all_symbols(date(2026, 8, 21))
    assert len(df) > 1000
    assert list(df.columns) == ["symbol", "name"]
    assert df["symbol"].str.match(r"^(60|00)\d{4}$").all()
    assert not df["name"].str.contains("ST").any()
    assert df["symbol"].is_unique
