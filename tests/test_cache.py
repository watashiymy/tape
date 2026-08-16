from quant.data.cache import BarCache
from tests.conftest import make_bars


def _row(d, px):
    return dict(date=d, open=px, high=px, low=px, close=px, volume=1000, amount=px * 1000)


def test_save_and_load_roundtrip(tmp_path):
    cache = BarCache(tmp_path)
    df = make_bars([_row("2024-01-02", 10.0), _row("2024-01-03", 11.0)])
    cache.save("600519", df)
    loaded = cache.load("600519")
    assert loaded is not None
    assert list(loaded.index) == list(df.index)
    assert loaded["close"].tolist() == [10.0, 11.0]


def test_load_missing_returns_none(tmp_path):
    assert BarCache(tmp_path).load("600519") is None


def test_merge_dedup_keeps_last(tmp_path):
    old = make_bars([_row("2024-01-02", 10.0), _row("2024-01-03", 11.0)])
    new = make_bars([_row("2024-01-03", 11.5), _row("2024-01-04", 12.0)])
    merged = BarCache(tmp_path).merge(old, new)
    assert len(merged) == 3
    assert merged.loc["2024-01-03", "close"] == 11.5  # 重叠日期以新数据为准
    assert merged.index.is_monotonic_increasing


def test_make_bars_fills_partially_specified_optional_columns():
    """make_bars 被 9 个后续测试文件依赖。只有部分行显式给了 trade_status 时，
    pandas 会把其余行填成 NaN——若不回填默认值，Task 3 的停牌过滤测试会把所有行滤光。"""
    df = make_bars([
        dict(date="2024-01-02", open=10, high=10, low=10, close=10, volume=1000, amount=1e4),
        dict(date="2024-01-03", open=10, high=10, low=10, close=10, volume=0, amount=0,
             trade_status=0),
    ])
    assert df["trade_status"].tolist() == [1, 0]   # 未给的那行必须是 1，不能是 NaN
    assert df["adj_factor"].tolist() == [1.0, 1.0]
    assert df["is_st"].tolist() == [0, 0]
    assert df.index.name == "date"
