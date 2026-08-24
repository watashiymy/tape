# tests/test_service.py
import pandas as pd
import pytest
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


def test_incremental_still_works_when_start_is_a_holiday(tmp_path):
    """start 落在非交易日是生产常态（settings.yaml 的 2016-01-01 是元旦）。
    若用"缓存最早一根 bar 的日期 > start"判头部缺口，首根 bar 恒晚于 start，
    该条件永远为真 → 增量分支变死代码，每次运行全量重拉 10 年且无任何告警。"""
    provider, cache = FakeProvider(), BarCache(tmp_path)
    svc = DataService(provider, cache)
    holiday_start = date(2023, 12, 31)          # 周日；FakeProvider 首个交易日是 2024-01-01
    svc.get_bars("600519", holiday_start, date(2024, 1, 31))
    df, _ = svc.get_bars("600519", holiday_start, date(2024, 1, 31))
    cached_max = ALL_DAYS[-1]                   # 2024-01-31
    assert provider.calls[1][1] == cached_max - timedelta(days=OVERLAP_DAYS)
    assert len(df) == len(ALL_DAYS)             # 增量不能少给数据


def test_legacy_cache_without_meta_self_heals_after_one_full_fetch(tmp_path):
    """本地已有的老缓存没有 meta.json。允许它全量重拉一次补齐元信息，
    但必须**只此一次**——否则修复等于没修，每次运行照旧全量重拉。"""
    provider, cache = FakeProvider(), BarCache(tmp_path)
    svc = DataService(provider, cache)
    holiday_start = date(2023, 12, 31)           # 用非交易日，才能证伪"按首根 bar 判缺口"
    svc.get_bars("600519", holiday_start, date(2024, 1, 31))
    cache._meta_path("600519").unlink()          # 模拟修复前写下的老缓存
    svc.get_bars("600519", holiday_start, date(2024, 1, 31))
    assert provider.calls[1][1] == holiday_start                 # 第 2 次：全量，自愈
    svc.get_bars("600519", holiday_start, date(2024, 1, 31))
    assert provider.calls[2][1] == ALL_DAYS[-1] - timedelta(days=OVERLAP_DAYS)  # 第 3 次：增量


def test_head_backfill_then_next_run_is_incremental(tmp_path):
    """回补完头部缺口后，covered_start 必须记成更早的那个 start，
    否则下一次运行又被判成"头部有缺口"，永远全量重拉。"""
    provider, cache = FakeProvider(), BarCache(tmp_path)
    svc = DataService(provider, cache)
    holiday_start = date(2023, 12, 31)           # 用非交易日，才能证伪"按首根 bar 判缺口"
    svc.get_bars("600519", date(2024, 1, 22), date(2024, 1, 31))
    svc.get_bars("600519", holiday_start, date(2024, 1, 31))      # 回补头部
    svc.get_bars("600519", holiday_start, date(2024, 1, 31))
    assert provider.calls[1][1] == holiday_start
    assert provider.calls[2][1] == ALL_DAYS[-1] - timedelta(days=OVERLAP_DAYS)
    assert cache.load_meta("600519")["covered_start"] == "2023-12-31"


def test_refresh_resets_covered_start_to_the_new_request(tmp_path):
    """refresh 会丢掉旧缓存整表。covered_start 若仍停在更早的日期，
    缓存里其实没有的那段历史会被当成"已覆盖"，此后再也不会补。"""
    provider, cache = FakeProvider(), BarCache(tmp_path)
    svc = DataService(provider, cache)
    svc.get_bars("600519", date(2024, 1, 1), date(2024, 1, 31))
    svc.get_bars("600519", date(2024, 1, 22), date(2024, 1, 31), refresh=True)
    assert cache.load_meta("600519")["covered_start"] == "2024-01-22"
    df, _ = svc.get_bars("600519", date(2024, 1, 1), date(2024, 1, 31))
    assert provider.calls[2][1] == date(2024, 1, 1)   # 头部确实缺，必须回补
    assert len(df) == len(ALL_DAYS)


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


def test_refresh_with_empty_fetch_does_not_clobber_good_cache(tmp_path):
    """refresh=True 时 cached 为 None，若数据源抽风返回空表，先把空表落盘再抛错
    会毁掉磁盘上仅有的好缓存——报错必须发生在落盘之前。"""
    provider, cache = FakeProvider(), BarCache(tmp_path)
    svc = DataService(provider, cache)
    svc.get_bars("600519", date(2024, 1, 1), date(2024, 1, 31))
    original = cache.load("600519")
    original_meta = cache.load_meta("600519")

    provider.data = provider.data.iloc[0:0]        # 数据源临时抽风：返回空表
    with pytest.raises(ValueError, match="无可用数据"):
        svc.get_bars("600519", date(2024, 1, 1), date(2024, 1, 31), refresh=True)

    after = cache.load("600519")
    assert after is not None and len(after) == len(original)   # 好缓存必须原样还在
    assert after["close"].tolist() == original["close"].tolist()
    assert cache.load_meta("600519") == original_meta          # meta 同理不能被动过


def test_refresh_forces_full_fetch(tmp_path):
    provider, cache = FakeProvider(), BarCache(tmp_path)
    svc = DataService(provider, cache)
    svc.get_bars("600519", date(2024, 1, 1), date(2024, 1, 31))
    svc.get_bars("600519", date(2024, 1, 1), date(2024, 1, 31), refresh=True)
    assert provider.calls[1][1] == date(2024, 1, 1)


def test_returned_range_is_clamped_to_request(tmp_path):
    """缓存比请求区间长是常态。不夹住 end，样本外的行会被悄悄喂给回测且无告警。"""
    provider, cache = FakeProvider(), BarCache(tmp_path)
    svc = DataService(provider, cache)
    svc.get_bars("600519", date(2024, 1, 1), date(2024, 1, 31))   # 缓存整月
    df, _ = svc.get_bars("600519", date(2024, 1, 8), date(2024, 1, 10))
    assert df.index.min().date() >= date(2024, 1, 8)
    assert df.index.max().date() <= date(2024, 1, 10)   # 不夹 end 时这里会拿到 1/31


def test_earlier_start_backfills_cache_head(tmp_path):
    """先跑近期、后来想回溯更早——缓存头部的缺口必须回补，
    否则 get_bars 静默返回比请求区间更短的数据，回测在自己没要过的区间上出结论。"""
    provider, cache = FakeProvider(), BarCache(tmp_path)
    svc = DataService(provider, cache)
    svc.get_bars("600519", date(2024, 1, 22), date(2024, 1, 31))
    df, _ = svc.get_bars("600519", date(2024, 1, 1), date(2024, 1, 31))
    assert len(df) == len(ALL_DAYS)                     # 修复前只有 8 行且无告警
    assert provider.calls[1][1] == date(2024, 1, 1)
