# tests/test_service.py
import pandas as pd
from datetime import date, timedelta

from quant.data.cache import BarCache
from quant.data.provider import DataProvider
from quant.data.service import DataService, OVERLAP_DAYS
from tests.conftest import make_bars


def _row(d, px):
    return dict(date=d, open=px, high=px * 1.01, low=px * 0.99, close=px,
                volume=1000, amount=px * 1000)


ALL_DAYS = [d.date() for d in pd.bdate_range("2024-01-01", "2024-01-31")]


class FakeProvider(DataProvider):
    def __init__(self):
        self.calls: list[tuple] = []
        self.data = make_bars([_row(str(d), 10.0 + i) for i, d in enumerate(ALL_DAYS)])

    def get_daily_bars(self, symbol, start, end):
        self.calls.append((symbol, start, end))
        mask = (self.data.index.date >= start) & (self.data.index.date <= end)
        return self.data[mask]

    def get_index_daily(self, index_code, start, end):
        raise NotImplementedError

    def get_trade_calendar(self, start, end):
        return [d for d in ALL_DAYS if start <= d <= end]


def test_first_fetch_pulls_full_range_and_caches(tmp_path):
    provider, cache = FakeProvider(), BarCache(tmp_path)
    svc = DataService(provider, cache)
    df, _ = svc.get_bars("600519", date(2024, 1, 1), date(2024, 1, 31))
    assert len(df) == len(ALL_DAYS)
    assert provider.calls[0] == ("600519", date(2024, 1, 1), date(2024, 1, 31))
    assert cache.load("600519") is not None


def test_second_fetch_is_incremental_with_overlap(tmp_path):
    provider, cache = FakeProvider(), BarCache(tmp_path)
    svc = DataService(provider, cache)
    svc.get_bars("600519", date(2024, 1, 1), date(2024, 1, 20))
    svc.get_bars("600519", date(2024, 1, 1), date(2024, 1, 31))
    cached_max = date(2024, 1, 19)  # 2024-01-20 是周六，最后一个工作日为 19 日
    # 增量：不从头拉，但要回拉 OVERLAP_DAYS 天重叠（不是"最新日+1天"）
    assert provider.calls[1][1] == cached_max - timedelta(days=OVERLAP_DAYS)
    assert provider.calls[1][1] > date(2024, 1, 1)


def test_overlap_refetch_corrects_stale_intraday_bar(tmp_path):
    """盘中运行会把当天未收盘的 bar 写进缓存。回拉重叠 + merge 的 keep='last'
    必须能用收盘后的正确数据覆盖它——否则这根脏 bar 永久污染此后所有回测。"""
    provider, cache = FakeProvider(), BarCache(tmp_path)
    svc = DataService(provider, cache)
    svc.get_bars("600519", date(2024, 1, 1), date(2024, 1, 19))

    # 篡改缓存中最后一根，模拟盘中抓到的半截 K 线
    dirty = cache.load("600519")
    dirty.loc[dirty.index.max(), "close"] = 999.0
    cache.save("600519", dirty)
    assert cache.load("600519")["close"].iloc[-1] == 999.0

    df, _ = svc.get_bars("600519", date(2024, 1, 1), date(2024, 1, 31))
    good = provider.data.loc[pd.Timestamp("2024-01-19"), "close"]
    assert df.loc[pd.Timestamp("2024-01-19"), "close"] == good  # 已被修正


def test_refresh_forces_full_fetch(tmp_path):
    provider, cache = FakeProvider(), BarCache(tmp_path)
    svc = DataService(provider, cache)
    svc.get_bars("600519", date(2024, 1, 1), date(2024, 1, 31))
    svc.get_bars("600519", date(2024, 1, 1), date(2024, 1, 31), refresh=True)
    assert provider.calls[1][1] == date(2024, 1, 1)
